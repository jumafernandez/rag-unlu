#!/usr/bin/env python3
"""Ejemplos de la tarea RAG de ChatDigesto para entrenar.py (--rag): la consulta con los fragmentos recuperados, armada
con el prompt de producción (`backend.api._mensajes`: instrucción de sistema y CONTEXTO + PREGUNTA), y la respuesta
del Qwen 3.5 27B de adaptacion/armar_datos.py.

Entran los de la partición de entrenamiento con el mismo filtro que adaptacion/entrenar_lora.py: respuesta terminada
y todo acto citado presente en el texto del contexto. La partición es por documento fuente (ningún documento de las
230 de prueba aparece en el entrenamiento); además se descarta la consulta de entrenamiento que repita el texto de
una de prueba.

Motivo: ajustar solo con pares de pregunta y respuesta, sin documentos, puede empeorar el uso de lo recuperado; se
corrige entrenando también con el formato RAG (RAFT, Zhang et al., 2024).

    python -m nacional.rag_entrenamiento --datos ~/rag-unlu-adaptacion/datos --salida N/rag-entrenamiento.jsonl
"""
import argparse
import glob
import json
import os
import re
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--datos', required=True, help='carpeta con ejemplos_*.jsonl de adaptacion/armar_datos.py')
    ap.add_argument('--salida', required=True)
    a = ap.parse_args()
    # backend.api abre su base de historial al importarse: una temporal, que no se usa
    os.environ['RAG_BD'] = os.path.join(tempfile.mkdtemp(), 'no-se-usa.sqlite')
    from adaptacion.armar_datos import actos_citados
    from backend import api

    filas = [json.loads(l) for r in sorted(glob.glob(os.path.join(a.datos, 'ejemplos_*.jsonl')))
             for l in open(r, encoding='utf-8')]
    norm = lambda s: re.sub(r'\W+', ' ', (s or '').lower()).strip()
    prueba = {norm(f['pregunta']) for f in filas if f['particion'] == 'prueba'}
    cuenta = {'entrenamiento': 0, 'sin terminar': 0, 'cita fuera del contexto': 0, 'repite una de prueba': 0}
    with open(a.salida + '.tmp', 'w', encoding='utf-8') as out:
        n = 0
        for f in filas:
            if f['particion'] != 'entrenamiento':
                continue
            cuenta['entrenamiento'] += 1
            if not (f.get('respuesta') and f.get('termino') and f.get('contexto')):
                cuenta['sin terminar'] += 1
            elif not set(f['citas']) <= actos_citados(f['contexto']):
                cuenta['cita fuera del contexto'] += 1
            elif norm(f['pregunta']) in prueba:
                cuenta['repite una de prueba'] += 1
            else:
                out.write(json.dumps({'id': f['id'], 'tipo': f['tipo'], 'mensajes': api._mensajes(f['pregunta'], f['contexto']),
                                      'respuesta': f['respuesta']}, ensure_ascii=False) + '\n')
                n += 1
    os.replace(a.salida + '.tmp', a.salida)
    print(' · '.join(f'{k}: {v}' for k, v in cuenta.items()) + f' -> {n} ejemplos en {a.salida}')


if __name__ == '__main__':
    main()
