#!/usr/bin/env python3
"""Escenarios de ChatDigesto con los modelos de Liminar y sus bases: las preguntas de prueba de adaptacion/ con los mismos
fragmentos recuperados, el prompt de producción (`api._mensajes`) y greedy, como adaptacion/generar_respuestas.py. Así la
única diferencia con gpt-4.1-mini y con los escenarios ya medidos es el modelo que redacta. La carga y la plantilla de
cada familia (Qwen, Gemma, Mistral; el LoRA se fusiona) son las de linea_base.py. Escribe las mismas columnas que
generar_respuestas.py, así el resumen de los escenarios las lee igual.

Con --muestra N toma N preguntas en la proporción de cada tipo (temáticas, por identificador, sin respuesta), siempre las
mismas (semilla fija): una prueba rápida, para comparar entre nosotros; las 230 van con los modelos finales.

    python -m nacional.rag_escenarios --datos ~/rag-unlu-adaptacion/datos --salida DIR --modelo RUTA --nombre NOMBRE \
        [--adaptador DIR] [--muestra 60]
"""
import argparse
import glob
import json
import os
import random
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)


def muestra(filas, n, semilla=13):
    """n preguntas repartidas en la proporción de cada tipo; las mismas para todos los modelos."""
    por_tipo = {}
    for f in filas:
        por_tipo.setdefault(f['tipo'], []).append(f)
    elegidas = []
    for t, fs in sorted(por_tipo.items()):
        k = min(len(fs), round(n * len(fs) / len(filas)))
        elegidas += random.Random(f'{semilla}-{t}').sample(sorted(fs, key=lambda f: f['id']), k)
    return elegidas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--datos', required=True, help='carpeta con ejemplos_*.jsonl de adaptacion/armar_datos.py')
    ap.add_argument('--salida', required=True)
    ap.add_argument('--modelo', required=True)
    ap.add_argument('--adaptador', help='LoRA a fusionar sobre --modelo')
    ap.add_argument('--nombre', required=True)
    ap.add_argument('--particion', default='prueba')
    ap.add_argument('--muestra', type=int, default=0, help='cantidad de preguntas (0: todas)')
    ap.add_argument('--lote', type=int, default=4)
    ap.add_argument('--max-nuevos', type=int, default=900)
    a = ap.parse_args()
    os.makedirs(a.salida, exist_ok=True)
    os.environ['RAG_BD'] = os.path.join(a.salida, f'bd-{a.nombre}.sqlite')   # backend.api abre su base al importarse
    import torch
    from backend import api
    from adaptacion.armar_datos import actos_citados
    from nacional.linea_base import cargar, es_mistral_common

    filas = [json.loads(l) for r in sorted(glob.glob(os.path.join(a.datos, 'ejemplos_*.jsonl')))
             for l in open(r, encoding='utf-8')]
    filas = [f for f in filas if f['particion'] == a.particion and f.get('contexto')]
    if a.muestra:
        filas = muestra(filas, a.muestra)
    ruta = os.path.join(a.salida, f'{a.nombre}.jsonl')
    hechos = {json.loads(l)['id'] for l in open(ruta, encoding='utf-8')} if os.path.exists(ruta) else set()
    pend = sorted((f for f in filas if f['id'] not in hechos), key=lambda f: len(f['contexto']))   # menos relleno
    print(f'{a.nombre}: {len(pend)} pendientes de {len(filas)}', flush=True)
    if not pend:
        return
    tok, m = cargar(a.modelo, a.adaptador)
    qwen = 'qwen' in a.modelo.lower()
    mc = es_mistral_common(tok)
    # Fin de la respuesta: el de la configuración de generación de cada modelo; Qwen 3.5 trae <|endoftext|> como eos
    # y sin <|im_end|> no corta al terminar el turno (como en armar_datos.Maestro).
    e = getattr(getattr(m, 'generation_config', None), 'eos_token_id', None)
    fin = set(e if isinstance(e, (list, tuple)) else [] if e is None else [e])
    if tok.eos_token_id is not None:
        fin.add(tok.eos_token_id)
    if qwen:
        fin |= {tok.convert_tokens_to_ids('<|im_end|>'), tok.convert_tokens_to_ids('<|endoftext|>')}
    fin = sorted(t for t in fin if isinstance(t, int) and t >= 0)
    pad = tok.pad_token_id if tok.pad_token_id is not None else fin[0]

    def prompt(f):
        msgs = api._mensajes(f['pregunta'], f['contexto'])
        if mc:   # mistral-common arma la conversación directo a ids
            r = tok.apply_chat_template(msgs, tokenize=True)
            return list(r['input_ids'] if hasattr(r, 'keys') else r)
        return tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True,
                                       **({'enable_thinking': False} if qwen else {}))

    disp = m.get_input_embeddings().weight.device
    t0 = time.time()
    with open(ruta, 'a', encoding='utf-8') as out:
        for s in range(0, len(pend), a.lote):
            lote = pend[s:s + a.lote]
            textos = [prompt(f) for f in lote]
            if mc:   # relleno a la izquierda a mano
                L = max(map(len, textos))
                enc = {'input_ids': torch.tensor([[pad] * (L - len(x)) + x for x in textos]),
                       'attention_mask': torch.tensor([[0] * (L - len(x)) + [1] * len(x) for x in textos])}
            else:
                enc = tok(textos, return_tensors='pt', padding=True, add_special_tokens=False)
            enc = {k: v.to(disp) for k, v in enc.items()}
            t1 = time.time()
            with torch.no_grad():
                gen = m.generate(**enc, max_new_tokens=a.max_nuevos, do_sample=False, pad_token_id=pad,
                                 eos_token_id=fin)
            torch.xpu.synchronize()
            seg = (time.time() - t1) / len(lote)
            for f, fila in zip(lote, gen[:, enc['input_ids'].shape[1]:]):
                ids = fila.tolist()
                termino = any(t in fin for t in ids)
                txt = tok.decode([t for t in ids if t != pad], skip_special_tokens=True).strip()
                citas = actos_citados(txt)
                out.write(json.dumps({
                    'id': f['id'], 'modelo': a.nombre, 'tipo': f['tipo'], 'respuesta': txt, 'termino': termino,
                    'citas': sorted(citas), 'anclaje_ok': citas <= set(f['actos_en_contexto']),
                    'anclaje_texto': citas <= actos_citados(f['contexto']),
                    'cita_fuente': bool(citas & set(f['acto_fuente'])), 'segundos': round(seg, 2)},
                    ensure_ascii=False) + '\n')
            out.flush()
            print(f'  {s + len(lote)}/{len(pend)} · {seg:.1f} s por respuesta', flush=True)
    print(f'LISTO {a.nombre} en {time.time() - t0:.0f} s', flush=True)


if __name__ == '__main__':
    main()
