#!/usr/bin/env python3
"""Preguntas autocontenidas: a las que no nombran su norma les antepone el documento del que salieron.

Las preguntas se generaron con el documento a la vista (`generar.py`) y una parte dice "esta norma", "esta ley",
"el presente decreto" sin nombrarlo: a libro cerrado no tienen respuesta posible, y como datos de entrenamiento le
enseñan al modelo a contestar con seguridad sobre una norma que no sabe cuál es (inventa números). El arreglo es
anteponer el documento: "Sobre la Resolución 76/2024: ¿esta resolución suspende…?".

Modos:
  deicticas  solo las que se refieren a la norma con un deíctico ("esta norma", "dicho decreto", "la presente"…)
  todas      todas las que no nombran una norma por tipo y número

Uso (genera una versión nueva del conjunto de la línea de base, sin tocar el original):
    python -m nacional.autocontener --preguntas linea_base-preguntas.jsonl --documentos documentos.jsonl \
        --modo todas --salida linea_base-preguntas-autocontenidas.jsonl
"""
import argparse
import json
import re

DEICTICO = re.compile(r'\b(esta|este|estas|estos|dicha|dicho|dichas|dichos|la presente|el presente|esa|ese|la misma|'
                      r'el mismo)\s+(norma|normas|resoluci[oó]n|ley|decreto|disposici[oó]n|anexo|reglamento|estatuto|'
                      r'convenio|documento|r[eé]gimen|acta|ordenanza|instrumento|texto|art[ií]culo)\b', re.I)
NOMBRA = re.compile(r'\b(Ley|Decreto|Resoluci[oó]n|Disposici[oó]n|Ordenanza|Acuerdo|Decisi[oó]n|Acordada)\s+'
                    r'(N[°º.]?\s*)?\d', re.I)
ARTICULO = {'ley': 'la', 'resolución': 'la', 'disposición': 'la', 'decisión': 'la', 'ordenanza': 'la',
            'acordada': 'la', 'nota': 'la', 'comunicación': 'la', 'circular': 'la', 'instrucción': 'la',
            'decreto': 'el', 'anexo': 'el', 'acta': 'el', 'convenio': 'el', 'acuerdo': 'el', 'reglamento': 'el',
            'estatuto': 'el', 'texto': 'el'}


def necesita(pregunta, modo):
    if modo == 'deicticas':
        return bool(DEICTICO.search(pregunta))
    return not NOMBRA.search(pregunta)


def con_documento(documento):
    """'Resolución 76/2024' → 'la Resolución 76/2024'; 'Anexo I de la Resolución X' → 'el Anexo I de la …'."""
    primera = documento.split()[0].lower() if documento.strip() else ''
    articulo = ARTICULO.get(primera)
    return f'{articulo} {documento}' if articulo else f'la norma {documento}'


def autocontener(pregunta, documento, modo='todas'):
    if not documento or not necesita(pregunta, modo):
        return pregunta
    return f'Sobre {con_documento(documento)}: {pregunta}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--preguntas', required=True)
    ap.add_argument('--documentos', required=True, help='documentos.jsonl de corpus.py (id_doc → nombre)')
    ap.add_argument('--modo', choices=['deicticas', 'todas'], default='todas')
    ap.add_argument('--salida', required=True)
    a = ap.parse_args()
    nombres = {d['id_doc']: d['nombre'] for d in map(json.loads, open(a.documentos, encoding='utf-8'))}
    cambiadas = total = 0
    with open(a.salida, 'w', encoding='utf-8') as out:
        for q in map(json.loads, open(a.preguntas, encoding='utf-8')):
            nueva = autocontener(q['pregunta'], nombres.get(q['id_doc'], ''), a.modo)
            cambiadas += nueva != q['pregunta']
            total += 1
            out.write(json.dumps({**q, 'pregunta': nueva, 'pregunta_original': q['pregunta']}, ensure_ascii=False) + '\n')
    print(f'{total} preguntas · {cambiadas} con el documento antepuesto ({100 * cambiadas / max(total, 1):.0f} %) · {a.salida}')


if __name__ == '__main__':
    main()
