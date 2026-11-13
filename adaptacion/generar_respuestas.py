#!/usr/bin/env python3
"""Genera respuestas sobre los ejemplos de prueba con un modelo local, con o sin adaptador.

Usa los mismos contextos que armar_datos.py guardó para cada ejemplo, así que la única
diferencia entre modelos es la generación: la recuperación es idéntica. Prompt de
producción (`api._mensajes`), greedy como la temperatura 0 de producción.

Mide lo mismo que evaluacion_automatica/verificar_citas.py, sin juez:
    anclaje_ok     todo acto citado es uno de los fragmentos recuperados (citar otro es
                   inventarlo, a los efectos de quien lee).
    anclaje_texto  todo acto mencionado aparece en algún lugar del contexto, aunque sea
                   dentro del texto de un fragmento ("en el marco de la Resolución...").
    cita_fuente    se citó el acto del que salió la pregunta.

Un proceso por tile; cada uno toma su parte y escribe su archivo.

Uso:
    ZE_AFFINITY_MASK=3 python -m adaptacion.generar_respuestas --datos DIR --salida DIR \
        --parte 3 --partes 8 [--adaptador DIR] [--nombre 9b-lora]
"""
import argparse
import glob
import json
import os
import sys
import time

import torch

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from adaptacion.armar_datos import actos_citados  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--datos', required=True)
    ap.add_argument('--salida', required=True)
    ap.add_argument('--modelo', default=os.path.expanduser('~/hf-models/Qwen3.5-9B'))
    ap.add_argument('--adaptador')
    ap.add_argument('--nombre', required=True)
    ap.add_argument('--particion', default='prueba')
    ap.add_argument('--parte', type=int, default=0)
    ap.add_argument('--partes', type=int, default=1)
    ap.add_argument('--lote', type=int, default=4)
    ap.add_argument('--max-nuevos', type=int, default=900)
    a = ap.parse_args()
    os.makedirs(a.salida, exist_ok=True)
    if not torch.xpu.is_available():
        raise SystemExit(f'sin XPU visible en {os.uname().nodename}')
    os.environ['RAG_BD'] = os.path.join(a.salida, f'bd-{a.nombre}-{a.parte}.sqlite')
    from backend import api
    from transformers import AutoModelForCausalLM, AutoTokenizer

    filas = []
    for ruta in sorted(glob.glob(os.path.join(a.datos, 'ejemplos_*.jsonl'))):
        filas += [json.loads(l) for l in open(ruta, encoding='utf-8')]
    filas = [f for f in filas if f['particion'] == a.particion and f.get('contexto')]
    filas.sort(key=lambda f: f['id'])
    mias = filas[a.parte::a.partes]
    ruta = os.path.join(a.salida, f'{a.nombre}_{a.parte}.jsonl')
    hechos = {json.loads(l)['id'] for l in open(ruta, encoding='utf-8')} if os.path.exists(ruta) else set()
    mias = [f for f in mias if f['id'] not in hechos]
    print(f'{a.nombre} parte {a.parte}/{a.partes}: {len(mias)} pendientes', flush=True)
    if not mias:
        return

    tok = AutoTokenizer.from_pretrained(a.modelo)
    tok.padding_side = 'left'
    m = AutoModelForCausalLM.from_pretrained(a.modelo, dtype=torch.bfloat16).to('xpu:0')
    if a.adaptador:
        from peft import PeftModel
        m = PeftModel.from_pretrained(m, a.adaptador)
    m.eval()
    fin = sorted(t for t in {tok.eos_token_id, tok.convert_tokens_to_ids('<|im_end|>'),
                             tok.convert_tokens_to_ids('<|endoftext|>')} if isinstance(t, int) and t >= 0)

    salida = open(ruta, 'a', encoding='utf-8')
    t0 = time.time()
    # Lotes de largo parecido: menos relleno a la izquierda.
    mias.sort(key=lambda f: len(f['contexto']))
    for s in range(0, len(mias), a.lote):
        lote = mias[s:s + a.lote]
        textos = [tok.apply_chat_template(api._mensajes(f['pregunta'], f['contexto']), tokenize=False,
                                          add_generation_prompt=True, enable_thinking=False) for f in lote]
        enc = tok(textos, return_tensors='pt', padding=True, add_special_tokens=False).to('xpu:0')
        t1 = time.time()
        with torch.no_grad():
            out = m.generate(**enc, max_new_tokens=a.max_nuevos, do_sample=False,
                             pad_token_id=tok.pad_token_id or fin[0], eos_token_id=fin)
        segundos = (time.time() - t1) / len(lote)
        for f, fila in zip(lote, out[:, enc['input_ids'].shape[1]:]):
            ids = fila.tolist()
            txt = tok.decode(ids, skip_special_tokens=True).strip()
            citas = actos_citados(txt)
            salida.write(json.dumps({
                'id': f['id'], 'modelo': a.nombre, 'tipo': f['tipo'], 'respuesta': txt,
                'termino': any(t in fin for t in ids), 'citas': sorted(citas),
                'anclaje_ok': citas <= set(f['actos_en_contexto']),
                'anclaje_texto': citas <= actos_citados(f['contexto']),
                'cita_fuente': bool(citas & set(f['acto_fuente'])),
                'segundos': round(segundos, 2)}, ensure_ascii=False) + '\n')
        salida.flush()
        print(f'  {s + len(lote)}/{len(mias)} · {segundos:.1f} s por respuesta', flush=True)
    print(f'LISTO {a.nombre} parte {a.parte} en {time.time() - t0:.0f} s', flush=True)


if __name__ == '__main__':
    main()
