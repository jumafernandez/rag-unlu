#!/usr/bin/env python3
"""Tiempo de respuesta de un modelo local, como lo vería quien consulta ChatDigesto.

generar_respuestas.py mide rendimiento: lotes de 4 y segundos por respuesta promediados.
Acá se mide la espera de una persona: una pregunta por vez, con la respuesta saliendo por
streaming como en producción. Por cada ejemplo de prueba se registra cuánto tarda en
aparecer el primer texto, cuánto la respuesta completa y cuántos tokens se generaron.
Mismos contextos, mismo prompt (`api._mensajes`), greedy como la temperatura 0 de
producción. Las respuestas quedan guardadas con los mismos campos que las demás, así que
también sirven para mostrar las opciones sin desplegarlas.

Con más de un tile visible el modelo se reparte entre ellos (el 27B ocupa una GPU entera,
sus dos tiles); con uno, va entero a ese tile. Un adaptador LoRA se fusiona al cargar: es
como se serviría, y no suma tiempo por respuesta.

Uso:
    ZE_AFFINITY_MASK=0,1 python -m adaptacion.medir_tiempos --datos DIR --salida DIR \
        --modelo ~/hf-models/Qwen3.5-27B --nombre 27b
    ZE_AFFINITY_MASK=3 python -m adaptacion.medir_tiempos ... --adaptador DIR --nombre 9b-lora
"""
import argparse
import glob
import json
import os
import sys
import threading
import time

import torch

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from adaptacion.armar_datos import actos_citados  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--datos', required=True)
    ap.add_argument('--salida', required=True)
    ap.add_argument('--modelo', required=True)
    ap.add_argument('--adaptador')
    ap.add_argument('--nombre', required=True)
    ap.add_argument('--particion', default='prueba')
    ap.add_argument('--max-nuevos', type=int, default=1500)
    ap.add_argument('--limite', type=int, default=0)
    a = ap.parse_args()
    os.makedirs(a.salida, exist_ok=True)
    if not torch.xpu.is_available():
        raise SystemExit(f'sin XPU visible en {os.uname().nodename}')
    os.environ['RAG_BD'] = os.path.join(a.salida, f'bd-{a.nombre}.sqlite')
    from backend import api
    from transformers import AutoModelForCausalLM, AutoTokenizer, TextIteratorStreamer

    filas = []
    for ruta in sorted(glob.glob(os.path.join(a.datos, 'ejemplos_*.jsonl'))):
        filas += [json.loads(l) for l in open(ruta, encoding='utf-8')]
    filas = sorted((f for f in filas if f['particion'] == a.particion and f.get('contexto')),
                   key=lambda f: f['id'])
    if a.limite:
        filas = filas[:a.limite]
    ruta = os.path.join(a.salida, f'{a.nombre}.jsonl')
    hechos = {json.loads(l)['id'] for l in open(ruta, encoding='utf-8')} if os.path.exists(ruta) else set()
    pendientes = [f for f in filas if f['id'] not in hechos]
    tiles = torch.xpu.device_count()
    print(f'{a.nombre}: {len(pendientes)} pendientes de {len(filas)}; tiles visibles {tiles}', flush=True)
    if not pendientes:
        return

    tok = AutoTokenizer.from_pretrained(a.modelo)
    if tiles > 1:
        m = AutoModelForCausalLM.from_pretrained(a.modelo, dtype=torch.bfloat16, device_map='balanced')
    else:
        m = AutoModelForCausalLM.from_pretrained(a.modelo, dtype=torch.bfloat16).to('xpu:0')
    if a.adaptador:
        from peft import PeftModel
        m = PeftModel.from_pretrained(m, a.adaptador).merge_and_unload()
    m.eval()
    entrada = m.get_input_embeddings().weight.device
    fin = sorted(t for t in {tok.eos_token_id, tok.convert_tokens_to_ids('<|im_end|>'),
                             tok.convert_tokens_to_ids('<|endoftext|>')} if isinstance(t, int) and t >= 0)

    def responder(f):
        texto = tok.apply_chat_template(api._mensajes(f['pregunta'], f['contexto']), tokenize=False,
                                        add_generation_prompt=True, enable_thinking=False)
        ids = tok(texto, return_tensors='pt', add_special_tokens=False)['input_ids'].to(entrada)
        flujo = TextIteratorStreamer(tok, skip_prompt=True, skip_special_tokens=True)
        salida = {}

        def generar():
            with torch.no_grad():
                salida['ids'] = m.generate(input_ids=ids, attention_mask=torch.ones_like(ids),
                                           max_new_tokens=a.max_nuevos, do_sample=False,
                                           pad_token_id=tok.pad_token_id or fin[0], eos_token_id=fin,
                                           streamer=flujo)
        torch.xpu.synchronize()
        t0 = time.time()
        hilo = threading.Thread(target=generar)
        hilo.start()
        primero, partes = None, []
        for parte in flujo:
            if parte and primero is None:
                primero = time.time() - t0
            partes.append(parte)
        hilo.join()
        torch.xpu.synchronize()
        total = time.time() - t0
        nuevos = salida['ids'][0, ids.shape[1]:].tolist()
        return ''.join(partes).strip(), primero, total, ids.shape[1], nuevos

    # Dos respuestas sin medir: la primera pasada compila y reserva memoria.
    for f in pendientes[:2]:
        responder(f)
    out = open(ruta, 'a', encoding='utf-8')
    t_inicio = time.time()
    for k, f in enumerate(pendientes, 1):
        txt, primero, total, n_entrada, nuevos = responder(f)
        citas = actos_citados(txt)
        out.write(json.dumps({
            'id': f['id'], 'modelo': a.nombre, 'tipo': f['tipo'], 'respuesta': txt,
            'termino': any(t in fin for t in nuevos), 'citas': sorted(citas),
            'anclaje_ok': citas <= set(f['actos_en_contexto']),
            'anclaje_texto': citas <= actos_citados(f['contexto']),
            'cita_fuente': bool(citas & set(f['acto_fuente'])),
            'segundos': round(total, 2), 'segundos_primer_texto': round(primero or total, 2),
            'tokens_entrada': n_entrada, 'tokens_salida': len(nuevos)}, ensure_ascii=False) + '\n')
        out.flush()
        if k % 10 == 0:
            print(f'  {k}/{len(pendientes)} · última: {primero or total:.1f} s al primer texto, '
                  f'{total:.1f} s en total, {len(nuevos)} tokens', flush=True)
    print(f'LISTO {a.nombre} en {time.time() - t_inicio:.0f} s', flush=True)


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        import traceback
        traceback.print_exc()
        sys.stdout.flush(); sys.stderr.flush()
        os._exit(1)
