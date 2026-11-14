"""Descriptivo antes/después del benchmark de la línea de base, sin juez.

Por modelo: palabras por respuesta, % de respuestas que citan una norma y una aproximación de acierto
del número de norma: entre las preguntas no deícticas cuya referencia nombra una norma "N/AAAA" y cuyo
texto NO trae ese número (si lo trae, el modelo lo repite), si la respuesta menciona ese mismo número.
No mide si el contenido es correcto: eso lo hace el juez.

    python -m nacional.descriptivo --preguntas linea_base-preguntas.jsonl --respuestas linea_base/respuestas
"""
import argparse
import json
import os
import re
import statistics

MODELOS = ('gemma-4-e4b', 'ministral-3-8b', 'qwen3.5-9b', 'gemma-4-12b', 'mistral-small-3.2-24b', 'qwen3.5-27b',
           'gemma-4-31b', 'qwen3.5-122b-a10b')
DEICTICO = re.compile(r'\b(esta|este|estas|estos|dicha|dicho|la presente|el presente|esa|ese|la misma|el mismo)\s+'
                      r'(norma|normas|resoluci[oó]n|ley|decreto|disposici[oó]n|anexo|reglamento|estatuto|convenio|'
                      r'documento|r[eé]gimen|acta|ordenanza|instrumento|texto|art[ií]culo)\b', re.I)
CITA = re.compile(r'\b(Ley|Decreto|Resoluci[oó]n|Disposici[oó]n|Ordenanza)\s+(N[°º.]?\s*)?\d', re.I)
NUM = re.compile(r'\b(\d{1,5})\s*/\s*(\d{2,4})\b')


def leer(ruta):
    if not os.path.exists(ruta):
        return None
    return {r['id_pregunta']: r['respuesta'] for r in map(json.loads, open(ruta, encoding='utf-8'))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--preguntas', required=True)
    ap.add_argument('--respuestas', required=True)
    a = ap.parse_args()
    qs = {q['id_pregunta']: q for q in map(json.loads, open(a.preguntas, encoding='utf-8'))}
    print(f'{"modelo":24} {"":8} {"palabras":>8} {"cita norma":>10} {"acierta n.º de norma":>21}')
    ev = {}   # id -> números de la referencia, solo si la pregunta no es deíctica y no trae el número
    for k, q in qs.items():
        nums = {f'{x}/{y}' for x, y in NUM.findall(q['referencia'])}
        if nums and not DEICTICO.search(q['pregunta']) and not any(x in q['pregunta'].replace(' ', '') for x in nums):
            ev[k] = nums
    for base in MODELOS:
        for etiqueta, nombre in (('antes', base), ('después', base + '-nacional')):
            rs = leer(os.path.join(a.respuestas, f'{nombre}.jsonl'))
            if rs is None:
                continue
            v = list(rs.values())
            tot = sum(k in rs for k in ev)
            ok = sum(any(x in rs[k].replace(' ', '') for x in ev[k]) for k in ev if k in rs)
            print(f'{base:24} {etiqueta:8} {statistics.mean(len(x.split()) for x in v):8.1f} '
                  f'{100 * sum(bool(CITA.search(x)) for x in v) / len(v):9.1f}% {100 * ok / max(tot, 1):11.1f}% (de {tot})')


if __name__ == '__main__':
    main()
