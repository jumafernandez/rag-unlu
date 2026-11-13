#!/usr/bin/env python3
"""Transcribe con Qwen 3.5 (multimodal) los anexos PDF escaneados de Infoleg.

Los anexos de muchas normas son imágenes sin capa de texto (el convenio colectivo docente
del Decreto 1246/2015, por ejemplo). Cada página se renderiza con PyMuPDF y el modelo la
transcribe tal como aparece. Escribe una fila por página en `<salida>/parte_<i>_de_<n>.jsonl`
y retoma lo que falte si se corta. Las páginas que ya tienen texto en el PDF se copian sin
pasar por el modelo.

Uso (un tile por proceso, ZE_AFFINITY_MASK lo fija el trabajador del piloto):
    python -m nacional.ocr_qwen --manifiesto M.jsonl --salida DIR --parte 0 --partes 8
El manifiesto tiene una fila por tramo de PDF: {"id": ..., "pdf": ruta, "desde": k, "hasta": m}
(páginas k a m-1; sin desde/hasta, el PDF entero). Los tramos permiten repartir un PDF largo
entre varias partes.
"""
import argparse
import io
import json
import os
import sys
import time

INSTRUCCION = (
    'Transcribí todo el texto de esta página de un documento oficial argentino, tal como aparece y en orden '
    'de lectura. No agregues comentarios, títulos ni explicaciones que no estén en la página. Conservá la '
    'numeración de artículos e incisos y los saltos de párrafo. Si hay una tabla, transcribila fila por fila '
    "separando las columnas con ' | '. Si la página no tiene texto legible, respondé solo: [página sin texto]"
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--manifiesto', required=True)
    ap.add_argument('--salida', required=True)
    ap.add_argument('--parte', type=int, default=0)
    ap.add_argument('--partes', type=int, default=1)
    ap.add_argument('--modelo', default=os.path.expanduser('~/hf-models/Qwen3.5-9B'))
    ap.add_argument('--dpi', type=int, default=150)
    ap.add_argument('--max-nuevos', type=int, default=3000)
    ap.add_argument('--limite-paginas', type=int, default=0, help='para pruebas: corta después de N páginas')
    a = ap.parse_args()

    import pymupdf as fitz
    import torch
    from PIL import Image
    from transformers import AutoModelForImageTextToText, AutoProcessor

    pdfs = [json.loads(l) for l in open(a.manifiesto, encoding='utf-8')]
    pdfs = [p for k, p in enumerate(pdfs) if k % a.partes == a.parte]
    os.makedirs(a.salida, exist_ok=True)
    ruta = os.path.join(a.salida, f'parte_{a.parte}_de_{a.partes}.jsonl')
    hechas = set()
    if os.path.exists(ruta):
        for l in open(ruta, encoding='utf-8'):
            r = json.loads(l)
            hechas.add((r['pdf'], r['pagina']))
    print(f'parte {a.parte}/{a.partes}: {len(pdfs)} PDF, {len(hechas)} páginas ya hechas', flush=True)

    procesador = AutoProcessor.from_pretrained(a.modelo)
    modelo = AutoModelForImageTextToText.from_pretrained(a.modelo, dtype=torch.bfloat16).to('xpu:0').eval()
    out = open(ruta, 'a', encoding='utf-8')
    n = 0
    for p in pdfs:
        with fitz.open(p['pdf']) as doc:
            for k in range(p.get('desde', 0), min(p.get('hasta', len(doc)), len(doc))):
                pagina = doc[k]
                if (p['pdf'], k) in hechas:
                    continue
                t0 = time.time()
                propio = pagina.get_text().strip()
                if len(propio) >= 200:   # el PDF ya trae texto: no hace falta el modelo
                    fila = {'id': p['id'], 'pdf': p['pdf'], 'pagina': k, 'texto': propio, 'origen': 'pdf'}
                else:
                    imagen = Image.open(io.BytesIO(pagina.get_pixmap(dpi=a.dpi).tobytes('png'))).convert('RGB')
                    mensajes = [{'role': 'user', 'content': [{'type': 'image', 'image': imagen},
                                                             {'type': 'text', 'text': INSTRUCCION}]}]
                    entrada = procesador.apply_chat_template(mensajes, add_generation_prompt=True, tokenize=True,
                                                            return_dict=True, return_tensors='pt',
                                                            enable_thinking=False).to('xpu:0')
                    with torch.no_grad():
                        salida = modelo.generate(**entrada, max_new_tokens=a.max_nuevos, do_sample=False)
                    nuevos = salida[0, entrada['input_ids'].shape[1]:]
                    texto = procesador.decode(nuevos, skip_special_tokens=True).strip()
                    fila = {'id': p['id'], 'pdf': p['pdf'], 'pagina': k, 'texto': texto, 'origen': 'ocr',
                            'tokens_entrada': int(entrada['input_ids'].shape[1]), 'tokens_salida': int(len(nuevos)),
                            'corto': int(len(nuevos)) >= a.max_nuevos}
                fila['segundos'] = round(time.time() - t0, 2)
                out.write(json.dumps(fila, ensure_ascii=False) + '\n')
                out.flush()
                n += 1
                if n % 20 == 0:
                    print(f'  {n} páginas · última {fila["segundos"]} s', flush=True)
                if a.limite_paginas and n >= a.limite_paginas:
                    print(f'LISTO (límite de {n} páginas)', flush=True)
                    return
    print(f'LISTO parte {a.parte}: {n} páginas nuevas', flush=True)


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        import traceback
        traceback.print_exc()
        sys.stdout.flush(); sys.stderr.flush()
        os._exit(1)
