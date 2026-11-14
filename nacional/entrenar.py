#!/usr/bin/env python3
"""Entrena la capa nacional (ajuste completo o LoRA) con la normativa universitaria nacional.

Mezcla tres tipos de ejemplo, armados por `corpus.py`, `generar.py` y `abiertas.py`:
  - texto: los fragmentos de las normas, las fichas y las páginas de fuentes abiertas
    (argentina.gob.ar, CONICET, CIN), tal cual (pérdida sobre todo el texto);
  - explicación: "¿qué es / qué establece …?" → el resumen del documento o la explicación del
    fragmento que escribió el 27B (pérdida solo sobre la respuesta);
  - pregunta: las preguntas "a libro cerrado" con su respuesta (pérdida solo sobre la respuesta).
Las preguntas marcadas como evaluación no se entrenan: una muestra de ellas da la pérdida de
validación (preguntas sobre normas que el modelo leyó, pero que nunca vio respondidas).

La plantilla de chat, el fin de documento y el BOS salen del tokenizador de cada modelo (Qwen,
Gemma, Mistral), así que el mismo código sirve para todas las familias.

Modos:
  --modo lora        adaptador LoRA sobre las lineales del modelo de texto (el codificador visual
                     queda afuera; con --expertos, también sobre los expertos de un MoE).
  --modo completo    se entrenan todos los pesos del modelo de texto; el codificador visual queda
                     congelado y se guarda tal cual, así el resultado tiene la misma forma que el
                     modelo original y sirve con las mismas herramientas.
Estrategias:
  --estrategia ddp   una copia del modelo por tile (LoRA de modelos que entran en un tile).
  --estrategia fsdp  FSDP2: pesos, gradientes y estado del optimizador repartidos entre los tiles.
                     Con el ajuste completo, los pesos maestros van en fp32 y el cómputo en bf16;
                     con LoRA, el modelo base queda en bf16 y solo se sincronizan las matrices LoRA.

Lotes armados por presupuesto de tokens (los ejemplos se agrupan por largo para no rellenar de
más), un proceso por tile sobre oneCCL. Reutiliza la pérdida y el armado de lotes de
`adaptacion/entrenar_lora.py`.

Uso (desde nacional/sbatch/entrenar.sbatch):
    torchrun --standalone --nproc_per_node 8 -m nacional.entrenar --datos DIR --generados DIR,DIR --salida DIR \
        [--modo completo --estrategia fsdp]
"""
import argparse
import gc
import glob
import hashlib
import json
import math
import os
import random
import re
import shutil
import sys
import time
from datetime import timedelta

import numpy as np
import torch
import torch.distributed as dist

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from adaptacion.entrenar_lora import cargar_modelo, lote, perdida  # noqa: E402

PEDIDOS_RESUMEN = ['¿Qué es y qué establece {d}?', '¿De qué trata {d}?', 'Explicame en pocas palabras {d}.',
                   '¿Qué regula {d} y a quién alcanza?']
PEDIDOS_EXPLICACION = ['¿Qué establece {d}{u}?', 'Explicá en lenguaje claro {d}{u}.',
                       '¿Qué dispone {d}{u}?', 'Resumí qué dice {d}{u}.']
# Partes de un modelo multimodal que no son el modelo de texto: no se entrenan ni llevan LoRA.
NO_TEXTO = re.compile(r'visual|vision|audio|multi_modal_projector')


def elegir(opciones, clave):
    return opciones[int(hashlib.sha1(clave.encode()).hexdigest()[:6], 16) % len(opciones)]


def args():
    ap = argparse.ArgumentParser()
    ap.add_argument('--datos', required=True, help='carpeta con fragmentos.jsonl, documentos.jsonl y fichas.jsonl')
    ap.add_argument('--generados', required=True, help='carpetas de salida de generar.py, separadas por coma')
    ap.add_argument('--salida', required=True)
    ap.add_argument('--abiertas', help='carpeta con los .jsonl de abiertas.py (argentina.gob.ar, CONICET, CIN): '
                                       'entran como texto plano')
    ap.add_argument('--modelo', default=os.path.expanduser('~/hf-models/Qwen3.5-9B'))
    ap.add_argument('--modo', choices=['lora', 'completo'], default='lora')
    ap.add_argument('--estrategia', choices=['ddp', 'fsdp'], default='ddp')
    ap.add_argument('--congelar', help='ajuste completo: expresión regular de parámetros que no se entrenan (p. ej. '
                                       'embed_tokens_per_layer, la tabla de 2.800 M de los Gemma 4 E: su gradiente en '
                                       'fp32 agotaba la memoria del tile)')
    ap.add_argument('--expertos', action='store_true', help='MoE: LoRA también sobre los parámetros de los expertos')
    ap.add_argument('--rango-expertos', type=int, default=8,
                    help='rango del LoRA de cada experto (PEFT arma uno por experto: con 256 expertos y rango 64 el '
                         'adaptador del Qwen 3.5 122B pasaría de 7.000 M de parámetros)')
    ap.add_argument('--epocas', type=float, default=2.0)
    ap.add_argument('--lr', type=float, default=None, help='por omisión 1e-4 con LoRA y 1e-5 con ajuste completo')
    ap.add_argument('--calentamiento', type=float, default=0.03)
    ap.add_argument('--rango', type=int, default=64)
    ap.add_argument('--max-tokens', type=int, default=3072)
    ap.add_argument('--tokens-por-lote', type=int, default=4096,
                    help='tokens (con relleno) por lote y por proceso. En el texto plano la pérdida va sobre todos los '
                         'tokens y los logits (vocabularios de 131.000 a 262.000) dominan la memoria: con 8.192 el '
                         'tile se agotó')
    ap.add_argument('--acumulacion', type=int, default=1,
                    help='lotes por paso y por proceso (cada uno con tokens-por-lote): baja el pico de memoria sin '
                         'achicar el paso, si se divide tokens-por-lote por lo mismo')
    ap.add_argument('--max-validacion', type=int, default=400)
    ap.add_argument('--max-ejemplos', type=int, default=0, help='limita los ejemplos (canario)')
    ap.add_argument('--max-pasos', type=int, default=0, help='corta el entrenamiento en N pasos (canario)')
    ap.add_argument('--eval-cada', type=int, default=200)
    ap.add_argument('--guardar-cada', type=int, default=200)
    ap.add_argument('--cargas-simultaneas', type=int, default=0,
                    help='fsdp: procesos que leen el modelo a la vez en la memoria del nodo (0: según el tamaño)')
    ap.add_argument('--sin-mmap', action='store_true',
                    help='lee los archivos del modelo enteros en vez de mapearlos (con el Gemma 4, transformers retiene '
                         'los archivos mapeados después de soltar los pesos y la memoria de la CPU se agota)')
    ap.add_argument('--fla', action='store_true',
                    help='Qwen 3.5: núcleos de flash-linear-attention para la atención lineal en XPU')
    ap.add_argument('--verificar', action='store_true', help='al terminar, relee lo guardado y lo controla')
    ap.add_argument('--flujos-separados', action='store_true',
                    help='fsdp: comunicaciones en flujos propios, como FSDP2 por omisión (en las XPU dio fallas de página)')
    ap.add_argument('--colectivas-asincronas', action='store_true',
                    help='fsdp: no esperar a que termine cada colectiva (en las XPU, con oneCCL, eso dio fallas de página)')
    ap.add_argument('--vaciar-cache', action='store_true',
                    help='diagnóstico (fsdp): torch.xpu.empty_cache() después de cada paso')
    ap.add_argument('--memoria-pasos', type=int, default=0,
                    help='diagnóstico: registra la memoria del tile (también la de fuera de torch) en los primeros N pasos')
    ap.add_argument('--sin-checkpointing', action='store_true', help='diagnóstico: sin recomputar activaciones')
    ap.add_argument('--sin-reshard', action='store_true',
                    help='diagnóstico (fsdp): los pesos juntados quedan en el tile entre la pasada hacia adelante y la '
                         'de atrás (reshard_after_forward=False)')
    ap.add_argument('--semilla', type=int, default=0)
    a = ap.parse_args()
    if a.lr is None:
        a.lr = 1e-5 if a.modo == 'completo' else 1e-4
    if a.modo == 'completo' and a.estrategia != 'fsdp':
        ap.error('el ajuste completo va con --estrategia fsdp')
    return a


def leer(ruta):
    return [json.loads(l) for l in open(ruta, encoding='utf-8')] if os.path.exists(ruta) else []


class Formato:
    """Lo que depende de la familia del modelo: el cierre del turno del asistente en la plantilla de chat
    (<|im_end|> en Qwen, <turn|> en Gemma 4, </s> en Mistral), el fin de documento para el texto plano y
    el BOS, que Gemma y Mistral esperan al comienzo de cada secuencia y Qwen no usa. Con el tokenizador de
    mistral-common (Mistral Small 3.2 solo trae tekken.json) la conversación se codifica directo a ids."""
    MARCA = 'RESPUESTA-DE-PRUEBA'

    def __init__(self, tok):
        self.tok = tok
        self.mistral = type(tok).__name__.startswith('MistralCommon')
        if self.mistral:
            self.cierre, self.cierre_ids = tok.eos_token, [tok.eos_token_id]
            self.bos, self.fin = [tok.bos_token_id], tok.eos_token_id
            return
        t = tok.apply_chat_template([{'role': 'user', 'content': 'Hola'},
                                     {'role': 'assistant', 'content': self.MARCA}], tokenize=False)
        self.cierre = t[t.rindex(self.MARCA) + len(self.MARCA):].strip()
        if not self.cierre:
            raise ValueError(f'no encuentro el cierre del turno del asistente en la plantilla: {t[-150:]!r}')
        self.cierre_ids = tok(self.cierre, add_special_tokens=False)['input_ids']
        self.bos = [tok.bos_token_id] if tok.bos_token and t.startswith(tok.bos_token) else []
        fin = tok.convert_tokens_to_ids('<|endoftext|>')
        self.fin = fin if isinstance(fin, int) and fin != tok.unk_token_id else tok.eos_token_id

    def ids_prompt(self, pregunta):
        msgs = [{'role': 'user', 'content': pregunta}]
        if self.mistral:
            r = self.tok.apply_chat_template(msgs, tokenize=True)
            return list(r['input_ids'] if hasattr(r, 'keys') else r)
        prompt = self.tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        return self.tok(prompt, add_special_tokens=False)['input_ids']

    def chat(self, pregunta, respuesta, max_tokens):
        """ids y etiquetas: el prompt con -100, la respuesta y el cierre del turno con su token."""
        p = self.ids_prompt(pregunta)
        r = self.tok(respuesta, add_special_tokens=False)['input_ids'] + self.cierre_ids
        if len(p) + len(r) > max_tokens:
            return None
        return {'input_ids': p + r, 'labels': [-100] * len(p) + r}

    def texto(self, t, max_tokens):
        cuerpo = self.tok(t, add_special_tokens=False)['input_ids'][:max_tokens - 1 - len(self.bos)]
        ids = self.bos + cuerpo + [self.fin]
        return {'input_ids': ids, 'labels': list(ids)}


def armar_ejemplos(a, fmt):
    """Devuelve (entrenamiento, validación) como listas de {'input_ids', 'labels'}."""
    tr, va, largos = [], [], 0

    def chat(pregunta, respuesta, destino):
        nonlocal largos
        x = fmt.chat(pregunta, respuesta, a.max_tokens)
        if x is None:
            largos += 1
        else:
            destino.append(x)

    for f in leer(os.path.join(a.datos, 'fragmentos.jsonl')):
        ub = ', '.join(x for x in (f['ubicacion'], f['articulos']) if x)
        tr.append(fmt.texto(f'{f["documento"]}' + (f' — {ub}' if ub else '') + f'\n\n{f["texto"]}', a.max_tokens))
    for f in leer(os.path.join(a.datos, 'fichas.jsonl')):
        tr.append(fmt.texto(f['ficha'], a.max_tokens))
    if a.abiertas:
        for ruta in sorted(glob.glob(os.path.join(a.abiertas, '*.jsonl'))):
            for r in leer(ruta):
                if r.get('texto') and len(r['texto']) >= 300 and not r.get('error'):
                    tr.append(fmt.texto(f'{r.get("titulo") or ""}\n\n{r["texto"]}'.strip(), a.max_tokens))
    docs = {d['id_doc']: d for d in leer(os.path.join(a.datos, 'documentos.jsonl'))}
    frags = {f['id_fragmento']: f for f in leer(os.path.join(a.datos, 'fragmentos.jsonl'))}

    rutas = [r for c in a.generados.split(',') for r in glob.glob(os.path.join(c, 'parte_*.jsonl'))]
    preguntas_val = []
    for r in (json.loads(l) for ruta in rutas for l in open(ruta, encoding='utf-8')):
        d = r.get('datos') or {}
        if not r.get('json_ok') or not r.get('termino'):
            continue
        destino = preguntas_val if r['evaluacion'] else tr
        if 'id_fragmento' in r:
            f = frags.get(r['id_fragmento'])
            if f and d.get('explicacion') and not r['evaluacion']:
                ub = ', '.join(x for x in (f['ubicacion'], f['articulos']) if x)
                chat(elegir(PEDIDOS_EXPLICACION, r['id_fragmento']).format(d=f['documento'], u=f' ({ub})' if ub else ''),
                     d['explicacion'], tr)
        else:
            doc = docs.get(r['id_doc'])
            if doc and d.get('resumen') and not r['evaluacion']:
                chat(elegir(PEDIDOS_RESUMEN, r['id_doc']).format(d=doc['nombre']), d['resumen'], tr)
        for q in d.get('preguntas') or []:
            if isinstance(q, dict) and q.get('pregunta') and q.get('respuesta'):
                chat(q['pregunta'], q['respuesta'], destino)
    random.Random(a.semilla).shuffle(preguntas_val)
    va = preguntas_val[:a.max_validacion]
    return tr, va, largos


def lotes_por_tokens(items, presupuesto, semilla):
    """Agrupa por largo y arma lotes cuyo tamaño con relleno no pase el presupuesto."""
    orden = sorted(range(len(items)), key=lambda i: len(items[i]['input_ids']))
    res, actual, mx = [], [], 0
    for i in orden:
        n = len(items[i]['input_ids'])
        if actual and max(mx, n) * (len(actual) + 1) > presupuesto:
            res.append(actual)
            actual, mx = [], 0
        actual.append(i)
        mx = max(mx, n)
    if actual:
        res.append(actual)
    random.Random(semilla).shuffle(res)
    return res


# ---------------------------------------------------------------- FSDP
def activar_fla():
    """Qwen 3.5: núcleos de flash-linear-attention (Triton) para la atención lineal en XPU; transformers solo
    los activa con CUDA. Igual que llm-tod/nucleos.py: hay que llamarlo antes de crear el modelo."""
    from fla.modules import FusedRMSNormGated
    from fla.ops.gated_delta_rule import chunk_gated_delta_rule, fused_recurrent_gated_delta_rule
    from transformers.models.qwen3_5 import modeling_qwen3_5
    from transformers.models.qwen3_5_moe import modeling_qwen3_5_moe
    for m in (modeling_qwen3_5, modeling_qwen3_5_moe):
        for nombre, f in (('chunk_gated_delta_rule', chunk_gated_delta_rule),
                          ('fused_recurrent_gated_delta_rule', fused_recurrent_gated_delta_rule),
                          ('FusedRMSNormGated', FusedRMSNormGated)):
            if hasattr(m, nombre):
                setattr(m, nombre, f)


def flujo_unico():
    """FSDP2 hace las comunicaciones en flujos propios (juntar pesos, reducir gradientes) para solaparlas con el
    cómputo. En las XPU, con oneCCL, eso dio fallas de página intermitentes en el primer paso de entrenamiento
    (escrituras en memoria ya liberada; la validación, que no reduce gradientes, andaba): todo va al flujo de
    cómputo, a costa del solapamiento."""
    from torch.distributed.fsdp._fully_shard import _fsdp_param_group as g
    original = g.FSDPCommContext.lazy_init

    def lazy_init(self, device):
        original(self, device)
        f = self.device_handle.current_stream()
        self.all_gather_copy_in_stream = self.all_gather_stream = f
        self.reduce_scatter_stream = self.all_reduce_stream = f
    g.FSDPCommContext.lazy_init = lazy_init


def colectivas_sincronas():
    """Cada colectiva de FSDP (juntar pesos, reducir gradientes) espera a terminar antes de seguir. oneCCL las ejecuta
    en su propia cola sin avisarle al asignador de torch, y FSDP libera y reusa los búferes apenas las encola: sin
    esperar, en las XPU hubo fallas de página intermitentes en el primer paso (Qwen 3.5 27B y Gemma 4 31B con LoRA,
    y a veces el 9B completo). Con la espera, el 27B con lotes de 4.096 tokens anduvo a 16 s por paso."""
    from torch.distributed.fsdp._fully_shard import _fsdp_collectives as c
    for nombre in ('DefaultAllGather', 'DefaultReduceScatter', 'ProcessGroupAllocAllGather',
                   'ProcessGroupAllocReduceScatter'):
        clase = getattr(c, nombre, None)
        if clase is None:
            continue
        original = clase.__call__

        def envuelto(self, *args, _original=original, **kwargs):
            trabajo = _original(self, *args, **kwargs)
            if trabajo is not None:
                trabajo.wait()
            torch.xpu.synchronize()
            return trabajo
        clase.__call__ = envuelto


def clase_del_modelo(ruta):
    """La clase de la arquitectura de config.json (p. ej. Qwen3_5ForConditionalGeneration): así lo guardado
    tiene los mismos nombres de pesos que el modelo publicado."""
    import transformers
    arq = (getattr(transformers.AutoConfig.from_pretrained(ruta), 'architectures', None) or [''])[0]
    return getattr(transformers, arq, None) or transformers.AutoModelForCausalLM


def preparar(a, clase):
    """Carga el modelo en la CPU (bf16) y marca qué se entrena. Devuelve (modelo, tipos originales, resumen)."""
    m = clase.from_pretrained(a.modelo, dtype=torch.bfloat16, disable_mmap=a.sin_mmap)
    for c in (m.config, getattr(m.config, 'text_config', None)):
        if c is not None:
            c.use_cache = False
    tipos = {n: p.dtype for n, p in m.named_parameters()}
    if a.modo == 'lora':
        from peft import LoraConfig, get_peft_model
        lineales = [n for n, mod in m.named_modules() if isinstance(mod, torch.nn.Linear) and not NO_TEXTO.search(n)
                    and not n.endswith('lm_head') and not n.endswith(('.mlp.gate', 'shared_expert_gate'))]
        conf = dict(r=a.rango, lora_alpha=2 * a.rango, lora_dropout=0.05, target_modules=lineales,
                    task_type='CAUSAL_LM')
        if a.expertos:   # los enrutadores del MoE quedan congelados
            conf['target_parameters'] = ['mlp.experts.gate_up_proj', 'mlp.experts.down_proj']
            # La clave con que PEFT busca el rango puede ser el módulo (…mlp.experts) o el parámetro
            # (…mlp.experts.gate_up_proj): con solo 'experts' no coincidió y quedó el rango 64 (7.400 M de parámetros).
            claves = ('experts', 'experts.gate_up_proj', 'experts.down_proj')
            conf['rank_pattern'] = {k: a.rango_expertos for k in claves}
            conf['alpha_pattern'] = {k: 2 * a.rango_expertos for k in claves}
            conf['lora_dropout'] = 0.0   # PEFT: el LoRA sobre parámetros (ParamWrapper) no admite dropout
        m = get_peft_model(m, LoraConfig(**conf))
    else:
        congelar = re.compile(a.congelar) if a.congelar else None
        for n, p in m.named_parameters():
            p.requires_grad_(not NO_TEXTO.search(n) and not (congelar and congelar.search(n)))
    if not a.sin_checkpointing:
        m.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
    entrenables = sum(p.numel() for p in m.parameters() if p.requires_grad)
    total = sum(p.numel() for p in m.parameters())
    return m, tipos, f'parámetros entrenables {entrenables / 1e9:.3f} G de {total / 1e9:.2f} G ({100 * entrenables / total:.2f} %)'


def repartir(m, malla, completo, reshard=True):
    """FSDP2 por capa (las capas de decodificador y del codificador visual, que nunca se ejecutan y quedan
    repartidas) y el resto en la raíz. Lo que se entrena en el ajuste completo pasa a fp32 (pesos maestros)
    justo antes de repartirse, así en la CPU nunca hay una copia entera en fp32."""
    from torch.distributed.fsdp import MixedPrecisionPolicy, fully_shard
    from torch.distributed.tensor import DTensor
    from transformers.modeling_layers import GradientCheckpointingLayer
    mp = MixedPrecisionPolicy(param_dtype=torch.bfloat16, reduce_dtype=torch.float32)

    def a_fp32(mod):
        if completo:
            for p in mod.parameters():
                if p.requires_grad and not isinstance(p, DTensor) and p.dtype != torch.float32:
                    p.data = p.data.float()

    unidades = [x for x in m.modules() if isinstance(x, GradientCheckpointingLayer)]
    for u in reversed(unidades):   # las internas antes que las que las contienen
        a_fp32(u)
        fully_shard(u, mesh=malla, mp_policy=mp, reshard_after_forward=reshard)
    a_fp32(m)
    fully_shard(m, mesh=malla, mp_policy=mp, reshard_after_forward=reshard)
    return len(unidades)


def liberar_cpu():
    """Devuelve al sistema lo liberado en la CPU y dice cuánto ocupa el proceso. glibc retiene los bloques
    medianos ya liberados: sin malloc_trim, el modelo cargado en la CPU seguía ocupando su tamaño después de
    repartirlo entre los tiles (65 GB por proceso con el Gemma 31B) y los turnos de carga no servían."""
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL('libc.so.6').malloc_trim(0)
    except (OSError, AttributeError):
        pass
    try:
        rss = next(int(l.split()[1]) for l in open('/proc/self/status') if l.startswith('VmRSS:')) / 2**20
    except (OSError, StopIteration):
        rss = float('nan')
    return rss


def cargas_simultaneas(a, mundo):
    """Cuántos procesos pueden tener el modelo entero en la CPU a la vez sin pasarse de la memoria del trabajo."""
    if a.cargas_simultaneas:
        return a.cargas_simultaneas
    tam = sum(os.path.getsize(f) for f in glob.glob(os.path.join(a.modelo, '*.safetensors')))
    mem = int(os.environ.get('SLURM_MEM_PER_NODE', 400 * 1024)) * 2**20
    return max(1, min(mundo, int(0.8 * mem / (1.3 * max(tam, 1)))))


def completar_desde_base(base, destino):
    """El ajuste completo cambia solo los pesos: config.json, tokenizador, plantilla de chat y procesador son
    los del modelo base (save_pretrained escribiría en config.json la clase que arma FSDP)."""
    for f in sorted(os.listdir(base)):
        origen = os.path.join(base, f)
        if (os.path.isfile(origen) and not f.endswith('.safetensors') and not f.startswith('sha256')
                and f not in ('model.safetensors.index.json', 'README.md', '.gitattributes')):
            shutil.copy2(origen, os.path.join(destino, f))


def verificar(a, clase, destino, sd, log):
    """Relee lo guardado: el adaptador (claves y matrices B distintas de cero) o el modelo completo (claves
    que faltan o sobran, y un peso entrenado idéntico a lo que había en memoria)."""
    if a.modo == 'lora':
        from safetensors.torch import load_file
        w = load_file(os.path.join(destino, 'adapter_model.safetensors'))
        b = [k for k in w if 'lora_B' in k]
        ceros = sum(1 for k in b if float(w[k].float().abs().max()) == 0)
        cfg = json.load(open(os.path.join(destino, 'adapter_config.json')))
        log(f'verificación: {len(w)} tensores en el adaptador, {len(b)} matrices B ({ceros} en cero) · '
            f'base {cfg.get("base_model_name_or_path")} · clase {(cfg.get("auto_mapping") or {}).get("base_model_class")}')
        return
    m, info = clase.from_pretrained(destino, dtype=torch.bfloat16, output_loading_info=True)
    faltan, sobran = list(info.get('missing_keys') or []), list(info.get('unexpected_keys') or [])
    pesos = dict(m.state_dict())
    n = next((k for k in sd if 'layers.0.' in k and sd[k].dim() == 2 and not NO_TEXTO.search(k)), None)
    igual = n is not None and n in pesos and torch.equal(pesos[n].to(sd[n].dtype), sd[n])
    log(f'verificación: faltan {len(faltan)} claves {faltan[:3]} · sobran {len(sobran)} {sobran[:3]} · '
        f'{n}: {"igual" if igual else "DISTINTO o ausente"} a lo entrenado · '
        f'arquitectura {json.load(open(os.path.join(destino, "config.json"))).get("architectures")}')


def main():
    a = args()
    distribuido = int(os.environ.get('WORLD_SIZE', '1')) > 1
    fsdp = a.estrategia == 'fsdp'
    rank, mundo, local = 0, 1, 0
    if distribuido:
        # Plazo largo: con FSDP los procesos leen el modelo por turnos y los primeros esperan a los últimos.
        # Con FSDP, el grupo lleva también gloo para la CPU: torch.distributed.checkpoint coordina con
        # operaciones de objetos que, con un solo backend, irían por oneCCL como punto a punto, y oneCCL no las
        # admite en el modo de intercambio por sockets. Un grupo gloo aparte (new_group) no sirve: al relanzar,
        # torchrun deja sus datos viejos en el almacén y la conexión falla.
        dist.init_process_group(backend='xpu:xccl,cpu:gloo' if fsdp else 'xccl', timeout=timedelta(hours=2))
        rank, mundo, local = dist.get_rank(), dist.get_world_size(), int(os.environ['LOCAL_RANK'])
    if fsdp and not distribuido:
        raise SystemExit('fsdp necesita varios procesos (torchrun)')
    torch.xpu.set_device(local)
    dev = torch.device('xpu', local)
    principal = rank == 0
    log = (lambda *x: print(*x, flush=True)) if principal else (lambda *x: None)
    os.makedirs(a.salida, exist_ok=True)
    if a.fla:
        activar_fla()
    if fsdp and not a.flujos_separados:
        flujo_unico()
    if fsdp and not a.colectivas_asincronas:
        colectivas_sincronas()

    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(a.modelo)
    pad = tok.pad_token_id if tok.pad_token_id is not None else tok.eos_token_id
    fmt = Formato(tok)
    log(f'modelo {a.modelo} · modo {a.modo} · estrategia {a.estrategia} · cierre {fmt.cierre!r} · '
        f'fin de texto {fmt.fin} · bos {fmt.bos}')
    tr, va, largos = armar_ejemplos(a, fmt)
    if a.max_ejemplos:
        tr = random.Random(a.semilla).sample(tr, min(a.max_ejemplos, len(tr)))
    tokens = sum(len(x['input_ids']) for x in tr)
    log(f'ejemplos: entrenamiento {len(tr)} ({tokens / 1e6:.1f} M tokens) · validación {len(va)} · '
        f'descartados por largo {largos}')

    clase, tipos = None, {}
    t0 = time.time()
    if fsdp:
        from torch.distributed.device_mesh import init_device_mesh
        malla = init_device_mesh('xpu', (mundo,))
        clase = clase_del_modelo(a.modelo)
        k = cargas_simultaneas(a, mundo)
        log(f'clase {clase.__name__} · {k} procesos leen el modelo a la vez')
        modelo = None
        for turno in range(0, mundo, k):
            if turno <= rank < turno + k:
                modelo, tipos, resumen_param = preparar(a, clase)
                if principal:
                    print(resumen_param, flush=True)
                n_unidades = repartir(modelo, malla, a.modo == 'completo', reshard=not a.sin_reshard)
                print(f'  proceso {rank}: repartido, memoria de la CPU {liberar_cpu():.1f} GiB', flush=True)
                if rank == 0:   # ¿quedan tensores en la CPU que retengan el modelo? (diagnóstico)
                    vivos = [o for o in gc.get_objects() if torch.is_tensor(o) and o.device.type == 'cpu']
                    tam = sum(o.untyped_storage().nbytes() for o in vivos) / 2**30
                    grandes = sorted(vivos, key=lambda o: -o.untyped_storage().nbytes())[:5]
                    print(f'  proceso 0: {len(vivos)} tensores en la CPU, {tam:.1f} GiB en sus almacenamientos; '
                          f'los mayores: {[(tuple(o.shape), str(o.dtype)) for o in grandes]}', flush=True)
            dist.barrier()
        log(f'modelo repartido en {mundo} tiles ({n_unidades} capas con FSDP) en {time.time() - t0:.0f} s')
        base = modelo
    else:
        modelo = cargar_modelo(a.modelo, a.rango, dev)
        if principal:
            modelo.print_trainable_parameters()
        if distribuido:
            # Buckets grandes: menos intercambios entre procesos (con oneCCL sobre sockets, cada uno abre
            # descriptores; con el LoRA de rango 64 los buckets por omisión agotaron el límite del nodo).
            modelo = torch.nn.parallel.DistributedDataParallel(modelo, device_ids=[local], bucket_cap_mb=256,
                                                               gradient_as_bucket_view=True)
        base = modelo.module if distribuido else modelo
    entrenables = [p for p in modelo.parameters() if p.requires_grad]

    k_acum = max(1, a.acumulacion)
    pasos_epoca = len(lotes_por_tokens(tr, a.tokens_por_lote, a.semilla)) // (mundo * k_acum)
    assert pasos_epoca > 0, 'no alcanzan los ejemplos para un paso'
    total = max(1, int(pasos_epoca * a.epocas))
    if a.max_pasos:
        total = min(total, a.max_pasos)
    calent = max(1, int(a.calentamiento * total))
    opt = torch.optim.AdamW(entrenables, lr=a.lr, weight_decay=0.0)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: (s + 1) / calent if s < calent else
        0.5 * (1 + math.cos(math.pi * (s - calent) / max(1, total - calent))))

    paso = 0
    ultimo = os.path.join(a.salida, 'ultimo')

    def estados_fsdp():
        from torch.distributed.checkpoint.state_dict import get_state_dict
        msd, osd = get_state_dict(modelo, opt)
        if a.modo == 'lora':   # el modelo base no cambia: solo se guardan las matrices LoRA
            msd = {k_: v for k_, v in msd.items() if 'lora_' in k_}
        return msd, osd

    if fsdp and os.path.exists(os.path.join(ultimo, '.metadata')):
        import torch.distributed.checkpoint as dcp
        from torch.distributed.checkpoint.state_dict import StateDictOptions, set_state_dict
        msd, osd = estados_fsdp()
        dcp.load({'modelo': msd, 'opt': osd}, checkpoint_id=ultimo)
        set_state_dict(modelo, opt, model_state_dict=msd, optim_state_dict=osd,
                       options=StateDictOptions(strict=False))
        est = torch.load(os.path.join(ultimo, 'estado.pt'), map_location='cpu', weights_only=False)
        sched.load_state_dict(est['sched'])
        paso = est['paso']
        log(f'reanudado en el paso {paso}')
    elif not fsdp and os.path.exists(os.path.join(ultimo, 'estado.pt')):
        from peft import set_peft_model_state_dict
        from safetensors.torch import load_file
        set_peft_model_state_dict(base, load_file(os.path.join(ultimo, 'adapter_model.safetensors')))
        est = torch.load(os.path.join(ultimo, 'estado.pt'), map_location='cpu', weights_only=False)
        opt.load_state_dict(est['opt'])
        sched.load_state_dict(est['sched'])
        paso = est['paso']
        log(f'reanudado en el paso {paso}')
    log(f'procesos {mundo} · lotes por paso y proceso {k_acum} · pasos por época {pasos_epoca} · pasos {total} '
        f'({a.epocas} épocas) · lr {a.lr:.1e}')

    def guardar(destino):
        """Punto de reanudación. Con FSDP, cada proceso escribe su parte (torch.distributed.checkpoint) en una
        carpeta nueva que después reemplaza a la anterior, así un corte a mitad de camino no la rompe."""
        if not fsdp:
            if principal:
                os.makedirs(destino, exist_ok=True)
                base.save_pretrained(destino)
                torch.save({'opt': opt.state_dict(), 'sched': sched.state_dict(), 'paso': paso},
                           os.path.join(destino, 'estado.pt'))
            return
        import torch.distributed.checkpoint as dcp
        msd, osd = estados_fsdp()
        nuevo = destino + '.nuevo'
        if principal and os.path.exists(nuevo):
            shutil.rmtree(nuevo)
        dist.barrier()
        dcp.save({'modelo': msd, 'opt': osd}, checkpoint_id=nuevo)
        if principal:
            torch.save({'sched': sched.state_dict(), 'paso': paso}, os.path.join(nuevo, 'estado.pt'))
            if os.path.exists(destino):
                shutil.rmtree(destino)
            os.rename(nuevo, destino)
        dist.barrier()

    def guardar_final(destino):
        """El resultado en el formato de Hugging Face: el adaptador LoRA, o el modelo completo en bf16 con los
        archivos del modelo base. Con FSDP se juntan los pesos tensor por tensor (todos los procesos participan)."""
        if not fsdp:
            guardar(destino)
            return None
        from torch.distributed.tensor import DTensor
        sd, vistos = {}, set()
        for n, t in modelo.state_dict(keep_vars=True).items():
            if id(t) in vistos:   # pesos atados (embeddings y cabeza): una sola vez
                continue
            vistos.add(id(t))
            if a.modo == 'lora' and 'lora_' not in n:
                continue
            c = t.full_tensor() if isinstance(t, DTensor) else t
            if principal:
                c = c.detach().to('cpu')
                if a.modo == 'completo' and n in tipos:
                    c = c.to(tipos[n])   # vuelve al tipo del modelo original (bf16)
                sd[n] = c
            del c
        if principal:
            os.makedirs(destino, exist_ok=True)
            modelo.save_pretrained(destino, state_dict=sd)
            if a.modo == 'completo':
                completar_desde_base(a.modelo, destino)
        dist.barrier()
        return sd

    @torch.no_grad()
    def validar():
        if not va:
            return float('nan')
        modelo.eval()
        tot = torch.zeros(2, device=dev)
        propios = va[rank::mundo]
        # Con FSDP cada pasada junta pesos entre todos los procesos: todos hacen la misma cantidad.
        vueltas = math.ceil(len(va) / mundo) if fsdp else len(propios)
        for j in range(vueltas):
            x = propios[j] if j < len(propios) else va[0]
            ids, lab, att = lote([x], pad)
            loss, n = perdida(base, ids, lab, att, dev)
            if j < len(propios):
                tot += torch.tensor([float(loss) * n, n], device=dev)
        if distribuido:
            dist.all_reduce(tot)
        modelo.train()
        return float(tot[0] / max(tot[1], 1))

    registro = open(os.path.join(a.salida, 'registro.jsonl'), 'a') if principal else None
    if paso == 0:
        p0 = validar()
        log(f'pérdida de validación inicial: {p0:.4f}')
        registro and registro.write(json.dumps({'paso': 0, 'val': p0}) + '\n')
    modelo.train()
    torch.xpu.reset_peak_memory_stats(dev)
    tiempos, perdidas = [], []
    def memoria(etapa, siempre=False):   # en el primer paso, en cada proceso: dónde está el pico (y dónde se cae)
        if paso == 0 or siempre:
            torch.xpu.synchronize(dev)
            libre, total = torch.xpu.mem_get_info(dev)
            fuera = (total - libre - torch.xpu.memory_reserved(dev)) / 2**30
            print(f'    proceso {rank}: memoria tras {etapa}: {torch.xpu.memory_allocated(dev) / 2**30:.1f} GiB '
                  f'(pico {torch.xpu.max_memory_allocated(dev) / 2**30:.1f}; reservada '
                  f'{torch.xpu.memory_reserved(dev) / 2**30:.1f}; fuera de torch {fuera:.1f}; libre {libre / 2**30:.1f})',
                  flush=True)

    while paso < total:
        epoca, en_epoca = divmod(paso, pasos_epoca)
        grupos = lotes_por_tokens(tr, a.tokens_por_lote, a.semilla + epoca)
        t1 = time.time()
        acum = 0.0
        for j in range(k_acum):
            ids, lab, att = lote([tr[i] for i in grupos[(en_epoca * mundo + rank) * k_acum + j]], pad)
            forma = tuple(ids.shape)
            if paso < a.memoria_pasos:   # diagnóstico: qué lote tiene cada proceso en cada paso
                print(f'    proceso {rank}: paso {paso + 1} lote {forma[0]}x{forma[1]} '
                      f'(con pérdida {int((lab[:, 1:] != -100).any(0).sum())} posiciones)', flush=True)
            # Con DDP, los gradientes se sincronizan solo en el último lote del paso; FSDP los reparte en cada uno.
            ctx = modelo.no_sync() if (not fsdp and distribuido and j < k_acum - 1) else torch.enable_grad()
            with ctx:
                loss, _ = perdida(modelo, ids, lab, att, dev)
                memoria('la pasada hacia adelante')
                (loss / k_acum).backward()
                memoria('la pasada hacia atrás')
            acum += loss.detach().float().item() / k_acum
        torch.nn.utils.clip_grad_norm_(entrenables, 1.0)
        opt.step()
        memoria('el paso del optimizador', siempre=paso < a.memoria_pasos)
        sched.step()
        opt.zero_grad(set_to_none=True)
        if fsdp and a.vaciar_cache:
            # Diagnóstico: devolver lo reservado al dispositivo. Con oneCCL es contraproducente: los otros procesos
            # conservan abiertos (en caché) los handles de los búferes liberados y esa memoria queda ocupada fuera de
            # torch (en el Gemma 4 12B creció 1 → 18 GB en 4 pasos).
            torch.xpu.empty_cache()
        paso += 1
        tiempos.append(time.time() - t1)
        perdidas.append(acum)
        if paso % 10 == 0 or paso <= 3:
            mem = torch.xpu.max_memory_allocated(dev) / 2**30
            reservada = torch.xpu.memory_reserved(dev) / 2**30
            libre = torch.xpu.mem_get_info(dev)[0] / 2**30
            # La reservada y la libre importan: si un proceso se queda sin lugar, el asignador devuelve bloques que
            # los demás tienen mapeados por oneCCL y se caen todos (falla de página).
            log(f'  paso {paso}/{total}: pérdida {np.mean(perdidas[-10:]):.4f} · '
                f'{np.mean(tiempos[-10:]):.1f} s/paso · lr {sched.get_last_lr()[0]:.2e} · memoria {mem:.1f} GiB '
                f'(reservada {reservada:.1f}, libre {libre:.1f}) · lote {forma[0]}x{forma[1]}')
            registro and registro.write(json.dumps({'paso': paso, 'perdida': float(np.mean(perdidas[-10:])),
                                                    's_paso': float(np.mean(tiempos[-10:])),
                                                    'reservada_gib': round(reservada, 1),
                                                    'libre_gib': round(libre, 1)}) + '\n')
            registro and registro.flush()
        if paso % a.eval_cada == 0 or paso == total:
            pv = validar()
            log(f'  validación en el paso {paso}: {pv:.4f}')
            registro and registro.write(json.dumps({'paso': paso, 'val': pv}) + '\n')
        if paso % a.guardar_cada == 0 or paso == total:
            t2 = time.time()
            guardar(ultimo)
            log(f'  punto de reanudación guardado ({time.time() - t2:.0f} s)')
    t2 = time.time()
    final = os.path.join(a.salida, 'final')
    sd = guardar_final(final)
    log(f'guardado en {final} ({time.time() - t2:.0f} s)')
    if principal:
        with open(os.path.join(a.salida, 'resumen.json'), 'w') as fh:
            json.dump({'pasos': total, 'ejemplos': len(tr), 'tokens': tokens, 'validacion': len(va), 'procesos': mundo,
                       'tokens_por_lote': a.tokens_por_lote, 'acumulacion': k_acum, 'epocas': a.epocas,
                       'modelo': a.modelo,
                       'modo': a.modo, 'estrategia': a.estrategia, 'expertos': a.expertos,
                       'rango': a.rango if a.modo == 'lora' else None, 'lr': a.lr,
                       's_por_paso': float(np.mean(tiempos)) if tiempos else None,
                       'memoria_max_gib': torch.xpu.max_memory_allocated(dev) / 2**30}, fh, indent=1)
        if a.verificar and fsdp:
            verificar(a, clase, final, sd, log)
        log('ENTRENAMIENTO-OK')
    if distribuido:
        dist.barrier()
        dist.destroy_process_group()


if __name__ == '__main__':
    main()
