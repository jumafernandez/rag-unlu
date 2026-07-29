"""Baja los PDF que figuran en metadatos.csv y todavía no están en disco.

Cada fila del CSV trae la URL permanente del documento en el portal, así que la descarga
es una petición HTTP común: no hace falta navegador ni manejar descargas del navegador.

Trazabilidad: por cada archivo se registra una línea en un log JSONL con la URL pedida, el
código de respuesta, el tamaño, el SHA-256 y el tiempo. Con ese log se puede reconstruir
qué se bajó, de dónde y cuándo, sin depender de la memoria de nadie.

Es reanudable: un archivo ya presente no se vuelve a pedir. Y verifica que lo recibido sea
realmente un PDF, porque el servidor a veces responde 200 con un JSON de error.

Uso:
    python bajar_pdfs.py --destino ../data/portal-incremental
    python bajar_pdfs.py --destino ../data/portal-incremental --desde 07/04/2026
    python bajar_pdfs.py --destino ../data/portal-incremental --limite 20   # prueba
"""
import argparse
import csv
import hashlib
import json
import os
import time
import urllib.request
from datetime import datetime


def bajar(url, destino, intentos=3, espera=120):
    """Devuelve (sha256, bytes, codigo). Lanza excepción si no logra un PDF válido."""
    ultimo = None
    for n in range(intentos):
        try:
            pedido = urllib.request.Request(url, headers={'User-Agent': 'rag-unlu/1.0'})
            with urllib.request.urlopen(pedido, timeout=espera) as r:
                codigo, datos = r.getcode(), r.read()
            if not datos.startswith(b'%PDF'):
                raise ValueError(f'la respuesta no es un PDF ({datos[:100]!r})')
            os.makedirs(os.path.dirname(destino) or '.', exist_ok=True)
            # Se escribe con nombre temporal y se renombra al final: si el proceso muere a
            # mitad de la escritura no queda un PDF truncado con nombre definitivo, que
            # después la reanudación daría por bueno.
            tmp = destino + '.parcial'
            with open(tmp, 'wb') as f:
                f.write(datos)
            os.replace(tmp, destino)
            return hashlib.sha256(datos).hexdigest(), len(datos), codigo
        except Exception as e:
            ultimo = e
            if n < intentos - 1:
                time.sleep(3 * (n + 1))
    raise ultimo


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--metadatos', default='metadatos.csv')
    p.add_argument('--destino', required=True)
    p.add_argument('--log', default='descargas.jsonl')
    p.add_argument('--desde', help='solo actos con Fecha posterior a esta (dd/mm/aaaa)')
    p.add_argument('--limite', type=int, help='cortar después de N descargas (para probar)')
    a = p.parse_args()

    corte = datetime.strptime(a.desde, '%d/%m/%Y') if a.desde else None

    with open(a.metadatos, encoding='utf-8-sig') as f:
        filas = [r for r in csv.DictReader(f) if r.get('URL')]

    if corte:
        def posterior(r):
            try:
                return datetime.strptime(r.get('Fecha') or '', '%d/%m/%Y') > corte
            except ValueError:
                return False
        filas = [r for r in filas if posterior(r)]

    print(f'candidatos: {len(filas)}', flush=True)

    bajados = saltados = fallidos = 0
    t0 = time.time()
    with open(a.log, 'a', encoding='utf-8') as log:
        for r in filas:
            destino = os.path.join(a.destino, r['Archivo'])
            if os.path.exists(destino):
                saltados += 1
                continue
            t1 = time.time()
            try:
                sha, tam, codigo = bajar(r['URL'], destino)
                bajados += 1
                registro = {'archivo': r['Archivo'], 'numero': r.get('Numero'),
                            'fecha': r.get('Fecha'), 'seccion': r.get('Seccion'),
                            'url': r['URL'], 'http': codigo, 'bytes': tam, 'sha256': sha,
                            'segundos': round(time.time() - t1, 2), 'estado': 'ok'}
            except Exception as e:
                fallidos += 1
                registro = {'archivo': r['Archivo'], 'numero': r.get('Numero'),
                            'url': r['URL'], 'estado': 'error',
                            'error': f'{type(e).__name__}: {e}'[:300],
                            'segundos': round(time.time() - t1, 2)}
            log.write(json.dumps(registro, ensure_ascii=False) + '\n')
            log.flush()

            if (bajados + fallidos) % 100 == 0:
                print(f'  {bajados} bajados · {fallidos} con error · '
                      f'{time.time() - t0:.0f}s', flush=True)
            if a.limite and bajados >= a.limite:
                break

    print(f'\nbajados {bajados} · ya estaban {saltados} · con error {fallidos} '
          f'· {time.time() - t0:.0f}s', flush=True)
    print(f'log: {a.log}', flush=True)


if __name__ == '__main__':
    main()
