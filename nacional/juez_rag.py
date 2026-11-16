#!/usr/bin/env python3
"""Juez de los escenarios de ChatDigesto. Puntúa cada respuesta con la rúbrica de la evaluación humana de ChatDigesto
(evaluacion/cuestionario-humano.md): utilidad, fidelidad, completitud y claridad de 1 a 5, y si afirma algo incorrecto.
El juez ve la consulta, los fragmentos recuperados (lo único que el asistente podía usar) y la respuesta, sin saber qué
modelo la escribió. Juez fijo para todo Liminar: Llama 3.3 70B, de una familia distinta de todos los evaluados (Qwen,
Gemma, Mistral, OpenAI), para que no haya autopreferencia. Retoma por (modelo, id) y al final resume sobre las
preguntas que tienen juicio de todos los modelos. Pueden correr varios jueces a la vez con los mismos argumentos: cada
modelo lleva un candado POSIX (lockf: en el GPFS de Clementina es coherente entre nodos, flock no; se suelta solo si
el proceso cae) y cada juez saltea los modelos que otro está juzgando.

    python -m nacional.juez_rag --datos ~/rag-unlu-adaptacion/datos --respuestas 'DIR/*.jsonl' 'OTRO/gpt-*.jsonl' \
        --salida DIR [--muestra 60]
"""
import argparse
import fcntl
import glob
import json
import os
import re
import statistics as st
import sys
from collections import defaultdict

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

JUEZ = """Vas a evaluar la respuesta de un asistente que contesta consultas sobre el Digesto (la normativa) de la Universidad Nacional de Luján. El asistente solo podía usar los fragmentos de normativa que recuperó el sistema, que se muestran abajo, y debía citar los actos de los que sale cada dato.

CONSULTA:
{pregunta}

FRAGMENTOS RECUPERADOS:
<<<
{contexto}
>>>

RESPUESTA DEL ASISTENTE:
<<<
{respuesta}
>>>

Puntuá la respuesta de 1 a 5 en cada dimensión:
- utilidad: 1 = no responde lo que se preguntó; 5 = resuelve la consulta por completo.
- fidelidad: 1 = afirma cosas que los fragmentos no respaldan o cita actos que no corresponden; 5 = todo lo afirmado está en los fragmentos y las citas corresponden.
- completitud: 1 = le falta información relevante para la consulta que está en los fragmentos; 5 = no le falta nada relevante.
- claridad: 1 = confusa o difícil de leer; 5 = clara y bien organizada.
Además, "incorrecto" es true si la respuesta afirma algo que contradice los fragmentos o inventa datos (fechas, nombres, números o actos); si no, false.
Si los fragmentos no tienen lo que se pregunta, lo correcto es decirlo sin inventar: esa respuesta puede tener utilidad y completitud altas.

Respondé solo con un objeto JSON: {{"utilidad": n, "fidelidad": n, "completitud": n, "claridad": n, "incorrecto": true o false}}"""

DIMENSIONES = ('utilidad', 'fidelidad', 'completitud', 'claridad')


def leer_juicio(texto):
    m = re.search(r'\{[^{}]*\}', texto or '')
    try:
        d = json.loads(m.group()) if m else None
    except ValueError:
        return None
    if not isinstance(d, dict) or not all(isinstance(d.get(k), int) and 1 <= d[k] <= 5 for k in DIMENSIONES) \
            or not isinstance(d.get('incorrecto'), bool):
        return None
    return {k: d[k] for k in DIMENSIONES + ('incorrecto',)}


def resumen(salida):
    js = defaultdict(dict)
    for ruta in sorted(glob.glob(os.path.join(salida, '*.jsonl'))):
        for l in open(ruta, encoding='utf-8'):
            j = json.loads(l)
            if j.get('juicio'):
                js[j['modelo']][j['id']] = j
    if not js:
        return
    comunes = set.intersection(*(set(v) for v in js.values()))
    print(f'\n{len(comunes)} preguntas juzgadas para los {len(js)} modelos (1 a 5; incorrecto en %)')
    print(f'{"modelo":34} ' + ' '.join(f'{d[:11]:>11}' for d in DIMENSIONES) + f' {"incorrecto":>10}')
    for mo in sorted(js, key=lambda mo: -st.mean(js[mo][i]['juicio']['utilidad'] for i in comunes)):
        xs = [js[mo][i]['juicio'] for i in sorted(comunes)]
        print(f'{mo:34} ' + ' '.join(f'{st.mean(x[d] for x in xs):>11.2f}' for d in DIMENSIONES)
              + f' {100 * sum(x["incorrecto"] for x in xs) / len(xs):>9.0f} %')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--datos', required=True, help='carpeta con ejemplos_*.jsonl (pregunta y fragmentos recuperados)')
    ap.add_argument('--respuestas', nargs='+', required=True, help='jsonl de respuestas (globs), columna "modelo"')
    ap.add_argument('--salida', required=True)
    ap.add_argument('--juez', default='/data/contrib/pci_146/hf-models/Llama-3.3-70B-Instruct')
    ap.add_argument('--muestra', type=int, default=0, help='solo las preguntas de rag_escenarios --muestra N')
    ap.add_argument('--lote', type=int, default=4)
    ap.add_argument('--solo-resumen', action='store_true')
    a = ap.parse_args()
    os.makedirs(a.salida, exist_ok=True)
    if a.solo_resumen:
        return resumen(a.salida)
    from nacional.linea_base import cargar, generar, plantilla
    from nacional.rag_escenarios import muestra

    ej = [json.loads(l) for r in sorted(glob.glob(os.path.join(a.datos, 'ejemplos_*.jsonl')))
          for l in open(r, encoding='utf-8')]
    ej = [f for f in ej if f['particion'] == 'prueba' and f.get('contexto')]
    if a.muestra:
        ej = muestra(ej, a.muestra)
    por_id = {f['id']: f for f in ej}
    pend = defaultdict(list)
    for ruta in sorted(r for g in a.respuestas for r in glob.glob(g)):
        for l in open(ruta, encoding='utf-8'):
            r = json.loads(l)
            if r['id'] in por_id:
                pend[r['modelo']].append(r)
    tok = m = None
    for mo, rs in sorted(pend.items()):
        destino = os.path.join(a.salida, f'{mo}.jsonl')
        candado = open(destino + '.candado', 'a')
        try:
            fcntl.lockf(candado, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError):
            print(f'{mo}: lo está juzgando otro proceso, se saltea', flush=True)
            candado.close()
            continue
        hechos = {json.loads(l)['id'] for l in open(destino, encoding='utf-8')} if os.path.exists(destino) else set()
        rs = [r for r in {r['id']: r for r in rs}.values() if r['id'] not in hechos]
        print(f'{mo}: {len(rs)} por juzgar', flush=True)
        if not rs:
            candado.close()
            continue
        if m is None:
            tok, m = cargar(a.juez)
        # se escribe cada cuatro lotes: si el proceso cae (el runtime de las Intel a veces aborta), se retoma desde ahí
        with open(destino, 'a', encoding='utf-8') as out:
            for i in range(0, len(rs), 4 * a.lote):
                parte = rs[i:i + 4 * a.lote]
                textos = [plantilla(tok, JUEZ.format(pregunta=por_id[r['id']]['pregunta'],
                                                     contexto=por_id[r['id']]['contexto'],
                                                     respuesta=r['respuesta'][:6000]), a.juez) for r in parte]
                salidas, _, _ = generar(tok, m, textos, 80, a.lote)
                for r, s in zip(parte, salidas):
                    out.write(json.dumps({'id': r['id'], 'modelo': mo, 'tipo': r['tipo'], 'juicio': leer_juicio(s),
                                          'crudo': s}, ensure_ascii=False) + '\n')
                out.flush()
        candado.close()   # suelta el candado
    resumen(a.salida)


if __name__ == '__main__':
    main()
