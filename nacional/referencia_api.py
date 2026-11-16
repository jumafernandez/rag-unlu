#!/usr/bin/env python3
"""Respuestas de un modelo servido por API (OpenAI o compatible, p. ej. gpt-4.1-mini de ChatDigesto) a los dos
benchmarks de Liminar, con el mismo formato y el mismo prompt que los modelos locales, para compararlo con ellos:

  normativa  el benchmark a libro cerrado (linea_base): la misma INSTRUCCION como único mensaje del usuario,
             temperatura 0 y hasta 400 tokens, como `linea_base responder`. Lo califica después el mismo juez.
  opcion     MMMLU ES-LA (generales): la misma CONSIGNA_OPCION; se pide un solo token con sus log-probabilidades y se
             elige la letra (A-D, con o sin espacio adelante) más probable, como `generales opcion` con los locales.

Retoma: no repite preguntas que ya estén en la salida. La clave sale del entorno (OPENAI_API_KEY) o del .env de la raíz
del repositorio, y no se muestra.

Uso:
    python -m nacional.referencia_api normativa --preguntas linea_base-preguntas-v2.jsonl --salida DIR --modelo gpt-4.1-mini
    python -m nacional.referencia_api opcion --preguntas mmmlu-es_la-1026.jsonl --salida DIR --modelo gpt-4.1-mini
"""
import argparse
import json
import math
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from nacional.generales import CONSIGNA_OPCION, LETRAS  # noqa: E402
from nacional.linea_base import INSTRUCCION  # noqa: E402


def leer(ruta):
    return [json.loads(l) for l in open(ruta, encoding='utf-8') if l.strip()] if os.path.exists(ruta) else []


def cliente(base_url):
    from openai import OpenAI
    if not os.environ.get('OPENAI_API_KEY') and not base_url:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(RAIZ, '.env'))
    return OpenAI(base_url=base_url or None)


def con_reintentos(f, intentos=5):
    for k in range(intentos):
        try:
            return f()
        except Exception as e:   # límite de pedidos, cortes de red
            if k == intentos - 1:
                raise
            print(f'  reintento {k + 1}: {type(e).__name__}', flush=True)
            time.sleep(2 ** (k + 1))


def correr(items, clave, hacer, ruta, hilos):
    hechos = {r[clave] for r in leer(ruta)}
    pend = [x for x in items if x[clave] not in hechos]
    print(f'{len(pend)} por hacer ({len(hechos)} ya estaban)', flush=True)
    candado = threading.Lock()
    n = [0]

    def uno(x):
        fila = hacer(x)
        with candado:
            with open(ruta, 'a', encoding='utf-8') as out:
                out.write(json.dumps(fila, ensure_ascii=False) + '\n')
            n[0] += 1
            if n[0] % 50 == 0:
                print(f'  {n[0]}/{len(pend)}', flush=True)

    with ThreadPoolExecutor(hilos) as ex:
        list(ex.map(uno, pend))


def normativa(a, cl):
    qs = leer(a.preguntas)
    ruta = os.path.join(a.salida, f'{a.nombre}.jsonl')
    tokens = [0]
    t0 = time.time()

    def hacer(q):
        r = con_reintentos(lambda: cl.chat.completions.create(
            model=a.modelo, temperature=0, max_tokens=400,
            messages=[{'role': 'user', 'content': INSTRUCCION.format(pregunta=q['pregunta'])}]))
        tokens[0] += r.usage.completion_tokens
        return {'id_pregunta': q['id_pregunta'], 'modelo': a.nombre, 'respuesta': r.choices[0].message.content}

    correr(qs, 'id_pregunta', hacer, ruta, a.hilos)
    seg = time.time() - t0
    json.dump({'modelo': a.nombre, 'ruta': f'api:{a.modelo}', 'preguntas': len(qs), 'tokens': tokens[0],
               'segundos': round(seg, 1), 'tokens_por_segundo': None, 'nota': 'API: la velocidad no es comparable'},
              open(os.path.join(a.salida, f'{a.nombre}.velocidad.json'), 'w'), indent=1)
    print(f'LISTO {a.nombre}: {len(leer(ruta))} respuestas')


def opcion(a, cl):
    qs = leer(a.preguntas)
    for q in qs:   # el id se repite entre materias: la clave es materia + id
        q['_clave'] = f"{q['materia']}|{q['id']}"
    ruta = os.path.join(a.salida, f'{a.nombre}.jsonl')
    previas = leer(ruta)
    for r in previas:
        r['_clave'] = f"{r['materia']}|{r['id']}"
    if previas:   # correr() mira la clave en la salida: se reescribe con ella y se le saca al final
        with open(ruta, 'w', encoding='utf-8') as out:
            for r in previas:
                out.write(json.dumps(r, ensure_ascii=False) + '\n')

    def hacer(q):
        r = con_reintentos(lambda: cl.chat.completions.create(
            model=a.modelo, temperature=0, max_tokens=1, logprobs=True, top_logprobs=20,
            messages=[{'role': 'user', 'content': CONSIGNA_OPCION.format(
                pregunta=q['pregunta'], **{x: q[x] for x in LETRAS})}]))
        c = r.choices[0]
        tops = c.logprobs.content[0].top_logprobs if c.logprobs and c.logprobs.content else []
        punt = {x: max([t.logprob for t in tops if t.token in (x, ' ' + x)], default=-math.inf) for x in LETRAS}
        if all(v == -math.inf for v in punt.values()):
            dicho = (c.message.content or '').strip()[:1].upper()
            pred = dicho if dicho in LETRAS else None
        else:
            pred = max(punt, key=punt.get)
        return {'id': q['id'], 'materia': q['materia'], 'respuesta': q['respuesta'], 'prediccion': pred,
                'acierto': pred == q['respuesta'], '_clave': q['_clave'],
                'logprob': {x: (round(v, 3) if v > -math.inf else None) for x, v in punt.items()}}

    correr(qs, '_clave', hacer, ruta, a.hilos)
    filas = leer(ruta)
    with open(ruta, 'w', encoding='utf-8') as out:
        for f in filas:
            f.pop('_clave', None)
            out.write(json.dumps(f, ensure_ascii=False) + '\n')
    print(f'{a.nombre}: exactitud {100 * sum(f["acierto"] for f in filas) / len(filas):.1f} % en {len(filas)} preguntas')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('paso', choices=['normativa', 'opcion'])
    ap.add_argument('--preguntas', required=True)
    ap.add_argument('--salida', required=True)
    ap.add_argument('--modelo', default='gpt-4.1-mini')
    ap.add_argument('--nombre', help='por omisión, el modelo')
    ap.add_argument('--base-url', help='servidor compatible con OpenAI; sin esto, api.openai.com')
    ap.add_argument('--hilos', type=int, default=8)
    a = ap.parse_args()
    a.nombre = a.nombre or a.modelo
    os.makedirs(a.salida, exist_ok=True)
    cl = cliente(a.base_url)
    normativa(a, cl) if a.paso == 'normativa' else opcion(a, cl)


if __name__ == '__main__':
    main()
