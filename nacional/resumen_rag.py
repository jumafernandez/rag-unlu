#!/usr/bin/env python3
"""Resumen de los escenarios de ChatDigesto (rag_escenarios.py) contra las referencias ya medidas (gpt-4.1-mini y las
demás respuestas de adaptacion/): solo sobre las preguntas que respondieron todos, con las métricas de los escenarios.

    python nacional/resumen_rag.py --respuestas DIR [--referencias 'RUTA/gpt-4.1-mini_*.jsonl' ...]
"""
import argparse
import glob
import json
import re
import statistics as st
from collections import defaultdict

# como en comparar.py de los escenarios: la respuesta dice que no encontró lo que se pregunta
NIEGA = re.compile(r'no encontr[ée]|no (se )?(figura|surge|consta|menciona|encuentra|hay normativa|incluye|contiene|'
                   r'especifica)', re.I)


def pct(xs):
    return f'{100 * sum(xs) / len(xs):.0f} %' if xs else '-'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--respuestas', required=True, help='carpeta con <nombre>.jsonl de rag_escenarios.py')
    ap.add_argument('--referencias', nargs='*', default=[], help='otros jsonl con las mismas columnas (globs)')
    a = ap.parse_args()
    filas = defaultdict(dict)
    for ruta in sorted(glob.glob(f'{a.respuestas}/*.jsonl')) + [r for g in a.referencias for r in sorted(glob.glob(g))]:
        for l in open(ruta, encoding='utf-8'):
            g = json.loads(l)
            filas[g['modelo']][g['id']] = g
    comunes = set.intersection(*(set(v) for v in filas.values()))
    print(f'{len(comunes)} preguntas comunes a {len(filas)} modelos')
    for tipo in (None, 'tematica', 'identificador', 'sin_respuesta'):
        print(f'\n## {tipo or "todas"}')
        print(f'{"modelo":34} {"n":>3} {"terminó":>8} {"anclaje":>8} {"anc_txt":>8} {"cita_f":>7} {"niega":>6} '
              f'{"largo":>6} {"s/resp":>7}')
        for m in sorted(filas):
            xs = [filas[m][i] for i in sorted(comunes) if tipo is None or filas[m][i]['tipo'] == tipo]
            if not xs:
                continue
            seg = [g['segundos'] for g in xs if g.get('segundos')]
            print(f'{m:34} {len(xs):>3} {pct([g["termino"] for g in xs]):>8} {pct([g["anclaje_ok"] for g in xs]):>8} '
                  f'{pct([g["anclaje_texto"] for g in xs]):>8} {pct([g["cita_fuente"] for g in xs]):>7} '
                  f'{pct([bool(NIEGA.search(g["respuesta"][:300])) for g in xs]):>6} '
                  f'{st.median(len(g["respuesta"]) for g in xs):>6.0f} {(st.mean(seg) if seg else 0):>7.1f}')


if __name__ == '__main__':
    main()
