#!/usr/bin/env python3
"""Entrena con LoRA la capa nacional: el 9B (o el 27B) con la normativa universitaria nacional.

Mezcla tres tipos de ejemplo, todos armados por `corpus.py` y `generar.py`:
  - texto: los fragmentos de las normas y las fichas, tal cual (pérdida sobre todo el texto);
  - explicación: "¿qué es / qué establece …?" → el resumen del documento o la explicación del
    fragmento que escribió el 27B (pérdida solo sobre la respuesta);
  - pregunta: las preguntas "a libro cerrado" con su respuesta (pérdida solo sobre la respuesta).
Las preguntas marcadas como evaluación no se entrenan: una muestra de ellas da la pérdida de
validación (preguntas sobre normas que el modelo leyó, pero que nunca vio respondidas).

Lotes armados por presupuesto de tokens (los ejemplos se agrupan por largo para no rellenar de
más); un proceso por tile con DDP sobre oneCCL, como `adaptacion/entrenar_lora.py`, del que
reutiliza la pérdida, el armado de lotes y la carga del modelo.

Uso (desde nacional/sbatch/entrenar.sbatch):
    torchrun --standalone --nproc_per_node 8 -m nacional.entrenar --datos DIR --generados DIR,DIR --salida DIR
"""
import argparse
import glob
import hashlib
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

from adaptacion.entrenar_lora import cargar_modelo, codificar, lote, perdida  # noqa: E402

PEDIDOS_RESUMEN = ['¿Qué es y qué establece {d}?', '¿De qué trata {d}?', 'Explicame en pocas palabras {d}.',
                   '¿Qué regula {d} y a quién alcanza?']
PEDIDOS_EXPLICACION = ['¿Qué establece {d}{u}?', 'Explicá en lenguaje claro {d}{u}.',
                       '¿Qué dispone {d}{u}?', 'Resumí qué dice {d}{u}.']


def elegir(opciones, clave):
    return opciones[int(hashlib.sha1(clave.encode()).hexdigest()[:6], 16) % len(opciones)]


def args():
    ap = argparse.ArgumentParser()
    ap.add_argument('--datos', required=True, help='carpeta con fragmentos.jsonl, documentos.jsonl y fichas.jsonl')
    ap.add_argument('--generados', required=True, help='carpetas de salida de generar.py, separadas por coma')
    ap.add_argument('--salida', required=True)
    ap.add_argument('--modelo', default=os.path.expanduser('~/hf-models/Qwen3.5-9B'))
    ap.add_argument('--epocas', type=float, default=2.0)
    ap.add_argument('--lr', type=float, default=1e-4)
    ap.add_argument('--calentamiento', type=float, default=0.03)
    ap.add_argument('--rango', type=int, default=64)
    ap.add_argument('--max-tokens', type=int, default=4096)
    ap.add_argument('--tokens-por-lote', type=int, default=8192, help='tokens (con relleno) por lote y por proceso')
    ap.add_argument('--max-validacion', type=int, default=400)
    ap.add_argument('--max-ejemplos', type=int, default=0, help='limita los ejemplos (canario)')
    ap.add_argument('--eval-cada', type=int, default=200)
    ap.add_argument('--guardar-cada', type=int, default=200)
    ap.add_argument('--semilla', type=int, default=0)
    return ap.parse_args()


def leer(ruta):
    return [json.loads(l) for l in open(ruta, encoding='utf-8')] if os.path.exists(ruta) else []


def armar_ejemplos(a, tok):
    """Devuelve (entrenamiento, validación) como listas de {'input_ids', 'labels'}."""
    eot = tok.convert_tokens_to_ids('<|endoftext|>')
    tr, va, largos = [], [], 0

    def texto_plano(t):
        ids = tok(t, add_special_tokens=False)['input_ids'][:a.max_tokens - 1] + [eot]
        return {'input_ids': ids, 'labels': list(ids)}

    def chat(pregunta, respuesta, destino):
        nonlocal largos
        x = codificar(tok, [{'role': 'user', 'content': pregunta}], respuesta, a.max_tokens)
        if x is None:
            largos += 1
        else:
            destino.append(x)

    for f in leer(os.path.join(a.datos, 'fragmentos.jsonl')):
        ub = ', '.join(x for x in (f['ubicacion'], f['articulos']) if x)
        tr.append(texto_plano(f'{f["documento"]}' + (f' — {ub}' if ub else '') + f'\n\n{f["texto"]}'))
    for f in leer(os.path.join(a.datos, 'fichas.jsonl')):
        tr.append(texto_plano(f['ficha']))
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
    tok = AutoTokenizer.from_pretrained(a.modelo)
    pad = tok.pad_token_id if tok.pad_token_id is not None else tok.eos_token_id
    tr, va, largos = armar_ejemplos(a, tok)
    if a.max_ejemplos:
        tr = random.Random(a.semilla).sample(tr, min(a.max_ejemplos, len(tr)))
    tokens = sum(len(x['input_ids']) for x in tr)
    log(f'ejemplos: entrenamiento {len(tr)} ({tokens / 1e6:.1f} M tokens) · validación {len(va)} · '
        f'descartados por largo {largos}')

    modelo = cargar_modelo(a.modelo, a.rango, dev)
    if principal:
        modelo.print_trainable_parameters()
    entrenables = [p for p in modelo.parameters() if p.requires_grad]
    if ddp:
        modelo = torch.nn.parallel.DistributedDataParallel(modelo, device_ids=[local])
    base = modelo.module if ddp else modelo

    pasos_epoca = len(lotes_por_tokens(tr, a.tokens_por_lote, a.semilla)) // mundo
    assert pasos_epoca > 0, 'no alcanzan los ejemplos para un paso'
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
    log(f'procesos {mundo} · pasos por época {pasos_epoca} · pasos {total} ({a.epocas} épocas)')

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
        return float(tot[0] / max(tot[1], 1))

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
        grupos = lotes_por_tokens(tr, a.tokens_por_lote, a.semilla + epoca)
        t1 = time.time()
        ids, lab, att = lote([tr[i] for i in grupos[en_epoca * mundo + rank]], pad)
        loss, _ = perdida(modelo, ids, lab, att, dev)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(entrenables, 1.0)
        opt.step()
        sched.step()
        opt.zero_grad(set_to_none=True)
        paso += 1
        tiempos.append(time.time() - t1)
        perdidas.append(loss.detach().float().item())
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
            json.dump({'pasos': total, 'ejemplos': len(tr), 'tokens': tokens, 'validacion': len(va), 'procesos': mundo,
                       'tokens_por_lote': a.tokens_por_lote, 'epocas': a.epocas, 'modelo': a.modelo,
                       'rango': a.rango, 'lr': a.lr, 's_por_paso': float(np.mean(tiempos)) if tiempos else None,
                       'memoria_max_gib': torch.xpu.max_memory_allocated(dev) / 2**30}, fh, indent=1)
        log('ENTRENAMIENTO-OK')
    if ddp:
        dist.barrier()
        dist.destroy_process_group()


if __name__ == '__main__':
    main()
