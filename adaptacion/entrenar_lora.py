#!/usr/bin/env python3
"""Adapta con LoRA un modelo chico (Qwen 3.5 9B) a la generación de ChatDigesto.

Los ejemplos salen de armar_datos.py: el prompt es exactamente el de producción (el del
sistema más CONTEXTO y PREGUNTA, armado por `api._mensajes`) y la salida es la respuesta
del maestro. La pérdida se calcula solo sobre la respuesta. Entran los ejemplos de la
partición de entrenamiento cuya respuesta terminó y cita únicamente actos que estaban en
el contexto; de esos se reservan algunos actos enteros para la pérdida de validación.

Un proceso por tile con DDP (backend xccl), como en llm-tod: el 9B entra entero en un
tile. Se guarda el adaptador cada `--guardar-cada` pasos y se reanuda desde ahí.

Uso (desde adaptacion/entrenar_lora.sbatch):
    torchrun --standalone --nproc_per_node 8 -m adaptacion.entrenar_lora \
        --datos DIR --salida DIR [--epocas 2] [--max-ejemplos N]
"""
import argparse
import glob
import json
import math
import os
import random
import sys
import time

import numpy as np
import torch
import torch.distributed as dist

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from adaptacion.armar_datos import actos_citados  # noqa: E402


def args():
    ap = argparse.ArgumentParser()
    ap.add_argument('--datos', required=True)
    ap.add_argument('--salida', required=True)
    ap.add_argument('--modelo', default=os.path.expanduser('~/hf-models/Qwen3.5-9B'))
    ap.add_argument('--epocas', type=float, default=2.0)
    ap.add_argument('--max-ejemplos', type=int, default=0, help='limita los ejemplos (canario)')
    ap.add_argument('--frac-val', type=float, default=0.05)
    ap.add_argument('--tam-lote', type=int, default=1)
    ap.add_argument('--acum', type=int, default=2)
    ap.add_argument('--lr', type=float, default=1e-4)
    ap.add_argument('--calentamiento', type=float, default=0.03)
    ap.add_argument('--rango', type=int, default=16)
    ap.add_argument('--max-tokens', type=int, default=8192)
    ap.add_argument('--eval-cada', type=int, default=50)
    ap.add_argument('--guardar-cada', type=int, default=50)
    ap.add_argument('--semilla', type=int, default=0)
    ap.add_argument('--particiones', default='entrenamiento',
                    help='particiones de las que se entrena, separadas por coma (el canario usa prueba)')
    return ap.parse_args()


def leer_ejemplos(directorio):
    filas = []
    for ruta in sorted(glob.glob(os.path.join(directorio, 'ejemplos_*.jsonl'))):
        filas += [json.loads(l) for l in open(ruta, encoding='utf-8')]
    return filas


def utilizable(f, particiones):
    """Respuesta terminada que no menciona ningún acto ausente del contexto.

    El anclaje se mide contra el TEXTO del contexto y no solo contra las citas de los
    fragmentos: una respuesta que dice "en el marco de la Resolución RESHCS 162/2025",
    cuando ese acto aparece en el considerando recuperado, no está inventando nada. Medido
    sobre los primeros 605 ejemplos: de 47 respuestas que fallaban el anclaje estricto, 42
    mencionaban actos que estaban en el texto del contexto.
    """
    return (f['particion'] in particiones and f.get('respuesta') and f.get('termino')
            and set(f['citas']) <= actos_citados(f['contexto']))


def codificar(tok, mensajes, respuesta, max_tokens):
    """ids y etiquetas: el prompt con -100, la respuesta y el cierre del turno con su token."""
    prompt = tok.apply_chat_template(mensajes, tokenize=False, add_generation_prompt=True,
                                     enable_thinking=False)
    p = tok(prompt, add_special_tokens=False)['input_ids']
    r = tok(respuesta + '<|im_end|>', add_special_tokens=False)['input_ids']
    if len(p) + len(r) > max_tokens:
        return None
    return {'input_ids': p + r, 'labels': [-100] * len(p) + r}


def lote(items, pad):
    L = max(len(x['input_ids']) for x in items)
    ids = torch.full((len(items), L), pad)
    lab = torch.full((len(items), L), -100)
    att = torch.zeros((len(items), L), dtype=torch.long)
    for j, x in enumerate(items):
        n = len(x['input_ids'])
        ids[j, :n] = torch.tensor(x['input_ids'])
        lab[j, :n] = torch.tensor(x['labels'])
        att[j, :n] = 1
    return ids, lab, att


def perdida(modelo, ids, lab, att, disp):
    """Entropía cruzada solo sobre la respuesta, calculando logits únicamente en esas
    posiciones: con prompts de ~6.000 tokens y un vocabulario de ~248.000, materializar
    los logits de todo el prompt no entra en memoria."""
    pos = torch.nonzero((lab[:, 1:] != -100).any(0)).squeeze(1)
    out = modelo(input_ids=ids.to(disp), attention_mask=att.to(disp), logits_to_keep=pos.to(disp))
    y = lab[:, 1:][:, pos].to(disp)
    logits = out.logits.float()
    return torch.nn.functional.cross_entropy(logits.reshape(-1, logits.shape[-1]), y.reshape(-1),
                                             ignore_index=-100), int((y != -100).sum())


def cargar_modelo(ruta, rango, dev):
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM
    m = AutoModelForCausalLM.from_pretrained(ruta, dtype=torch.bfloat16).to(dev)
    lineales = [n for n, mod in m.named_modules() if isinstance(mod, torch.nn.Linear)
                and 'visual' not in n and 'vision' not in n and not n.endswith('lm_head')]
    m = get_peft_model(m, LoraConfig(r=rango, lora_alpha=2 * rango, lora_dropout=0.05,
                                     target_modules=lineales, task_type='CAUSAL_LM'))
    m.gradient_checkpointing_enable(gradient_checkpointing_kwargs={'use_reentrant': False})
    m.enable_input_require_grads()
    return m


def main():
    a = args()
    ddp = int(os.environ.get('WORLD_SIZE', '1')) > 1
    rank, mundo, local = 0, 1, 0
    if ddp:
        dist.init_process_group(backend='xccl')
        rank, mundo, local = dist.get_rank(), dist.get_world_size(), int(os.environ['LOCAL_RANK'])
    torch.xpu.set_device(local)
    dev = torch.device('xpu', local)
    principal = rank == 0
    log = (lambda *x: print(*x, flush=True)) if principal else (lambda *x: None)
    os.makedirs(a.salida, exist_ok=True)

    from transformers import AutoTokenizer
    # El backend abre su base de ajustes al importarse: una por proceso, para que ocho
    # procesos no se disputen el mismo archivo de SQLite al crear el esquema.
    os.environ['RAG_BD'] = os.path.join(a.salida, f'bd-{local}.sqlite')
    from backend import api
    tok = AutoTokenizer.from_pretrained(a.modelo)
    pad = tok.pad_token_id if tok.pad_token_id is not None else tok.eos_token_id

    filas = [f for f in leer_ejemplos(a.datos) if utilizable(f, a.particiones.split(','))]
    docs = sorted({f['documento'] for f in filas})
    random.Random(a.semilla).shuffle(docs)
    val_docs = set(docs[:max(1, int(a.frac_val * len(docs)))])
    ejemplos = {'entrenamiento': [], 'validacion': []}
    largos = 0
    for f in filas:
        x = codificar(tok, api._mensajes(f['pregunta'], f['contexto']), f['respuesta'], a.max_tokens)
        if x is None:
            largos += 1
            continue
        ejemplos['validacion' if f['documento'] in val_docs else 'entrenamiento'].append(x)
    tr, va = ejemplos['entrenamiento'], ejemplos['validacion']
    if a.max_ejemplos:
        tr = random.Random(a.semilla).sample(tr, min(a.max_ejemplos, len(tr)))
    log(f'ejemplos: entrenamiento {len(tr)} · validación {len(va)} · descartados por largo {largos} · '
        f'tokens máx {max(len(x["input_ids"]) for x in tr + va)}')

    modelo = cargar_modelo(a.modelo, a.rango, dev)
    if principal:
        modelo.print_trainable_parameters()
    entrenables = [p for p in modelo.parameters() if p.requires_grad]
    if ddp:
        modelo = torch.nn.parallel.DistributedDataParallel(modelo, device_ids=[local])
    base = modelo.module if ddp else modelo

    por_paso = a.tam_lote * a.acum * mundo
    pasos_epoca = len(tr) // por_paso
    assert pasos_epoca > 0, f'hacen falta al menos {por_paso} ejemplos de entrenamiento'
    total = max(1, int(pasos_epoca * a.epocas))
    calent = max(1, int(a.calentamiento * total))
    opt = torch.optim.AdamW(entrenables, lr=a.lr, weight_decay=0.0)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: (s + 1) / calent if s < calent else
        0.5 * (1 + math.cos(math.pi * (s - calent) / max(1, total - calent))))

    paso = 0
    ultimo = os.path.join(a.salida, 'ultimo')
    if os.path.exists(os.path.join(ultimo, 'estado.pt')):
        from peft import set_peft_model_state_dict
        from safetensors.torch import load_file
        set_peft_model_state_dict(base, load_file(os.path.join(ultimo, 'adapter_model.safetensors')))
        est = torch.load(os.path.join(ultimo, 'estado.pt'), map_location='cpu', weights_only=False)
        opt.load_state_dict(est['opt'])
        sched.load_state_dict(est['sched'])
        paso = est['paso']
        log(f'reanudado en el paso {paso}')
    log(f'procesos {mundo} · ejemplos por paso {por_paso} · pasos {total} ({a.epocas} épocas)')

    def guardar(destino):
        if principal:
            os.makedirs(destino, exist_ok=True)
            base.save_pretrained(destino)
            torch.save({'opt': opt.state_dict(), 'sched': sched.state_dict(), 'paso': paso},
                       os.path.join(destino, 'estado.pt'))

    @torch.no_grad()
    def validar():
        modelo.eval()
        tot = torch.zeros(2, device=dev)
        for x in va[rank::mundo]:
            ids, lab, att = lote([x], pad)
            loss, n = perdida(base, ids, lab, att, dev)
            tot += torch.tensor([float(loss) * n, n], device=dev)
        if ddp:
            dist.all_reduce(tot)
        modelo.train()
        return float(tot[0] / tot[1])

    registro = open(os.path.join(a.salida, 'registro.jsonl'), 'a') if principal else None
    if paso == 0:
        p0 = validar()
        log(f'pérdida de validación inicial: {p0:.4f}')
        registro and registro.write(json.dumps({'paso': 0, 'val': p0}) + '\n')
    modelo.train()
    torch.xpu.reset_peak_memory_stats(dev)
    tiempos, perdidas = [], []
    while paso < total:
        epoca, en_epoca = divmod(paso, pasos_epoca)
        orden = np.random.default_rng(a.semilla + epoca).permutation(len(tr))
        t1 = time.time()
        acum = 0.0
        for m_ in range(a.acum):
            ini = (en_epoca * a.acum + m_) * a.tam_lote * mundo + rank * a.tam_lote
            sincronizar = (m_ == a.acum - 1) or not ddp
            ctx = modelo.no_sync() if (ddp and not sincronizar) else torch.enable_grad()
            with ctx:
                ids, lab, att = lote([tr[i] for i in orden[ini:ini + a.tam_lote]], pad)
                loss, _ = perdida(modelo, ids, lab, att, dev)
                (loss / a.acum).backward()
            acum += loss.detach().float().item() / a.acum
        torch.nn.utils.clip_grad_norm_(entrenables, 1.0)
        opt.step()
        sched.step()
        opt.zero_grad(set_to_none=True)
        paso += 1
        tiempos.append(time.time() - t1)
        perdidas.append(acum)
        if paso % 10 == 0 or paso == 1:
            mem = torch.xpu.max_memory_allocated(dev) / 2**30
            log(f'  paso {paso}/{total}: pérdida {np.mean(perdidas[-10:]):.4f} · '
                f'{np.mean(tiempos[-10:]):.1f} s/paso · lr {sched.get_last_lr()[0]:.2e} · memoria {mem:.1f} GiB')
            registro and registro.write(json.dumps({'paso': paso, 'perdida': float(np.mean(perdidas[-10:])),
                                                    's_paso': float(np.mean(tiempos[-10:]))}) + '\n')
            registro and registro.flush()
        if paso % a.eval_cada == 0 or paso == total:
            pv = validar()
            log(f'  validación en el paso {paso}: {pv:.4f}')
            registro and registro.write(json.dumps({'paso': paso, 'val': pv}) + '\n')
        if paso % a.guardar_cada == 0 or paso == total:
            guardar(ultimo)
    guardar(os.path.join(a.salida, 'final'))
    if principal:
        with open(os.path.join(a.salida, 'resumen.json'), 'w') as fh:
            json.dump({'pasos': total, 'ejemplos': len(tr), 'validacion': len(va), 'procesos': mundo,
                       'ejemplos_por_paso': por_paso, 'epocas': a.epocas, 'modelo': a.modelo,
                       'rango': a.rango, 'lr': a.lr, 's_por_paso': float(np.mean(tiempos)) if tiempos else None,
                       'memoria_max_gib': torch.xpu.max_memory_allocated(dev) / 2**30}, fh, indent=1)
        log('ENTRENAMIENTO-OK')
    if ddp:
        dist.barrier()
        dist.destroy_process_group()


if __name__ == '__main__':
    main()
