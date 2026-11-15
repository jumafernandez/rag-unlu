#!/usr/bin/env python3
"""Prueba en seco del armado de datos de entrenar.py (sin modelo, solo el tokenizador; va en un nodo de CPU).

Cuenta los ejemplos por fuente, las fases y la validación, y controla que ninguna pregunta retenida (el benchmark)
haya quedado en el entrenamiento.

    python -m nacional.prueba_armado --modelo .../Qwen3.5-9B --datos N --generados ... --abiertas N/abiertas \
        --aumentacion N/aumentacion/prueba --retener N/linea_base-preguntas.jsonl --identidad ident.json
"""
import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)


def main():
    from transformers import AutoTokenizer
    sys.argv += ['--salida', '/tmp/no-se-usa']
    from nacional import entrenar
    a = entrenar.args()
    tok = AutoTokenizer.from_pretrained(a.modelo)
    fmt = entrenar.Formato(tok)
    tr, va, largos = entrenar.armar_ejemplos(a, fmt)
    qa = [x for x in tr if x.get('qa')]
    tokens = sum(len(x['input_ids']) for x in tr)
    print(f'entrenamiento: {len(tr)} ejemplos ({tokens / 1e6:.1f} M tokens), {len(qa)} conversaciones; '
          f'validación: {len(va)}; descartados por largo: {largos}')
    if a.retener:
        dec = lambda x: tok.decode([t for t, l in zip(x['input_ids'], x['labels']) if l == -100])
        val = {dec(x) for x in va}
        fugas = sum(dec(x) in val for x in qa)
        ids = [json.loads(l)['id_pregunta'] for l in open(a.retener, encoding='utf-8')]
        print(f'retenidas: {len(ids)} en el archivo, {len(va)} encontradas; prompts de validación que también están '
              f'en el entrenamiento: {fugas}')


if __name__ == '__main__':
    main()
