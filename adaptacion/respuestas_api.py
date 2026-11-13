#!/usr/bin/env python3
"""Genera las respuestas de prueba con un modelo servido por una API compatible con OpenAI.

Es la contraparte de generar_respuestas.py para los modelos que no corren en Clementina
(gpt-4.1-mini de producción) o que ya están servidos: mismos ejemplos, mismos contextos,
mismo prompt (`api._mensajes`) y la temperatura de producción. Como en producción, no se
limita el largo de la respuesta. Escribe una fila por ejemplo con los mismos campos que
generar_respuestas.py, así comparar.py las lee igual.

Con --stream la respuesta llega por partes, como en ChatDigesto, y se registra además
cuánto tarda en aparecer el primer texto. Para medir la espera de una persona va con
--hilos 1: una pregunta por vez, igual que medir_tiempos.py con los modelos locales.

La clave sale del entorno (OPENAI_API_KEY, o la del .env que carga backend.api); con
--base-url de un servidor propio no hace falta.

Uso:
    python -m adaptacion.respuestas_api --datos DIR --salida DIR --modelo gpt-4.1-mini \
        [--nombre gpt-4.1-mini] [--base-url URL] [--hilos 8] [--stream] [--limite N]
"""
import argparse
import glob
import json
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from evaluacion_automatica.verificar_citas import RE_ACTO  # noqa: E402


def clave_acto(codigo, numero, anio):
    anio = anio if len(anio) == 4 else '20' + anio
    return f'{codigo.upper()}|{int(numero)}|{anio}'


def actos_citados(texto):
    return {clave_acto(*m.groups()) for m in RE_ACTO.finditer(texto or '')}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--datos', required=True)
    ap.add_argument('--salida', required=True)
    ap.add_argument('--modelo', default='gpt-4.1-mini')
    ap.add_argument('--nombre')
    ap.add_argument('--base-url')
    ap.add_argument('--particion', default='prueba')
    ap.add_argument('--temperatura', type=float, default=0.0)
    ap.add_argument('--hilos', type=int, default=8)
    ap.add_argument('--stream', action='store_true')
    ap.add_argument('--limite', type=int, default=0)
    a = ap.parse_args()
    nombre = a.nombre or a.modelo
    os.makedirs(a.salida, exist_ok=True)
    # backend.api abre su base al importarse: una descartable, nunca la del repo.
    os.environ['RAG_BD'] = os.path.join(tempfile.mkdtemp(), 'bd.sqlite')
    from backend import api
    from openai import OpenAI

    filas = []
    for ruta in sorted(glob.glob(os.path.join(a.datos, 'ejemplos_*.jsonl'))):
        filas += [json.loads(l) for l in open(ruta, encoding='utf-8')]
    filas = sorted((f for f in filas if f['particion'] == a.particion and f.get('contexto')),
                   key=lambda f: f['id'])
    if a.limite:
        filas = filas[:a.limite]
    ruta = os.path.join(a.salida, f'{nombre}_0.jsonl')
    hechos = {json.loads(l)['id'] for l in open(ruta, encoding='utf-8')} if os.path.exists(ruta) else set()
    pendientes = [f for f in filas if f['id'] not in hechos]
    print(f'{nombre}: {len(pendientes)} pendientes de {len(filas)}', flush=True)
    if not pendientes:
        return

    cliente = OpenAI(base_url=a.base_url or None,
                     api_key=os.environ.get('OPENAI_API_KEY') or ('sin-clave' if a.base_url else None))
    salida = open(ruta, 'a', encoding='utf-8')
    candado = threading.Lock()
    uso = {'entrada': 0, 'salida': 0, 'hechos': 0}

    def una(f):
        mensajes = api._mensajes(f['pregunta'], f['contexto'])
        t0 = time.time()
        primero = None
        if a.stream:
            partes, fin_razon, usage = [], None, None
            for c in cliente.chat.completions.create(model=a.modelo, temperature=a.temperatura,
                                                     messages=mensajes, stream=True,
                                                     stream_options={'include_usage': True}):
                if c.usage:
                    usage = c.usage
                if c.choices:
                    d = c.choices[0].delta.content
                    if d:
                        primero = primero or time.time() - t0
                        partes.append(d)
                    fin_razon = c.choices[0].finish_reason or fin_razon
            txt = ''.join(partes).strip()
        else:
            r = cliente.chat.completions.create(model=a.modelo, temperature=a.temperatura, messages=mensajes)
            txt, fin_razon, usage = (r.choices[0].message.content or '').strip(), r.choices[0].finish_reason, r.usage
        segundos = time.time() - t0
        citas = actos_citados(txt)
        fila = {'id': f['id'], 'modelo': nombre, 'tipo': f['tipo'], 'respuesta': txt,
                'termino': fin_razon == 'stop', 'citas': sorted(citas),
                'anclaje_ok': citas <= set(f['actos_en_contexto']),
                'anclaje_texto': citas <= actos_citados(f['contexto']),
                'cita_fuente': bool(citas & set(f['acto_fuente'])),
                'segundos': round(segundos, 2),
                'segundos_primer_texto': round(primero, 2) if primero else None,
                'tokens_entrada': usage.prompt_tokens if usage else None,
                'tokens_salida': usage.completion_tokens if usage else None}
        with candado:
            salida.write(json.dumps(fila, ensure_ascii=False) + '\n')
            salida.flush()
            uso['entrada'] += fila['tokens_entrada'] or 0
            uso['salida'] += fila['tokens_salida'] or 0
            uso['hechos'] += 1
            if uso['hechos'] % 25 == 0:
                print(f"  {uso['hechos']}/{len(pendientes)}", flush=True)

    with ThreadPoolExecutor(a.hilos) as ex:
        for fut in [ex.submit(una, f) for f in pendientes]:
            fut.result()
    print(f"LISTO {nombre}: tokens de entrada {uso['entrada']}, de salida {uso['salida']}", flush=True)


if __name__ == '__main__':
    main()
