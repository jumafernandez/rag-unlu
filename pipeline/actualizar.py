#!/usr/bin/env python3
"""Actualización incremental completa: del portal al índice servido, en una corrida.

Es LA rutina de mantenimiento del corpus: la que el panel lanza con el botón
"Actualización completa" y la que se programa para correr todas las semanas. Encadena los
mismos scripts que se corren a mano ---no reimplementa ninguno--- y se detiene en el primer
paso que falla, dejando el índice como estaba: los pasos destructivos (fusión y
reconstrucción de artefactos) van al final, cuando todo lo anterior ya salió bien.

Los pasos:

    1. recolectar    el catálogo completo del portal (recolectar_api.py)
    2. importar      la recolección al catálogo de actos (catalogo importar)
    3. descargar     los PDF que no estén en disco (bajar_pdfs.py)
    4. conciliar     el ciclo de vida con lo descargado (catalogo conciliar)
    5. procesar      SOLO los actos descargados y no indexados (extractor)
    6. chunkear      lo procesado, con su metadata autoritativa
    7. vectorizar    los chunks nuevos (BGE-m3; minutos para una semana de actos)
    8. fusionar      chunks y vectores nuevos al índice (fusionar_indice --aplicar)
    9. refrescar     la metadata de TODOS los fragmentos (actualizar_metadata --aplicar)
   10. reconstruir   chunks.sqlite y vectores.faiss (construir_indice, swap atómico)
   11. conciliar     de nuevo, para marcar como indexado lo que entró
   12. recargar      el índice de la API en caliente, si la API está corriendo

Cada tanda trabaja en un directorio propio (data/tandas/AAAA-MM-DD-HHMMSS/): los PDF de la
tanda entran como enlaces simbólicos y los intermedios (procesados, chunks, vectores)
quedan ahí. Eso hace cada corrida autocontenida e inspeccionable, y evita el error clásico
de re-chunkear un directorio acumulado y duplicar fragmentos en la fusión.

Si no hay actos nuevos, la corrida termina en el paso 5 sin tocar el índice.

Uso:
    python -m pipeline.actualizar                  # la rutina completa
    python -m pipeline.actualizar --sin-recolectar # reusar la última recolección
    python -m pipeline.actualizar --limite 5       # procesar pocos, para probar

Los pasos también se pueden correr por separado ---es lo que hace el panel cuando se lanza
un paso suelto---:

    --sin-recolectar --sin-descargar --sin-indexar   solo vectorización (pasos 4 a 9)
    --solo-indexar                                   solo artefactos y recarga (10 a 12)
"""
import argparse
import datetime
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

METADATOS = 'scrapers/metadatos_nuevo.csv'
DESCARGAS = 'data/portal-incremental'
LOG_DESCARGAS = 'data/descargas.jsonl'
DIRS_PDF = ('data/portal', 'data/portal-incremental')


def paso(nombre, comando, **kwargs):
    print(f'\n=== {nombre} ===', flush=True)
    print('$', ' '.join(comando), flush=True)
    t0 = time.time()
    r = subprocess.run(comando, cwd=RAIZ, **kwargs)
    print(f'--- {nombre}: {"ok" if r.returncode == 0 else f"ERROR ({r.returncode})"} '
          f'en {time.time() - t0:.0f}s', flush=True)
    if r.returncode != 0:
        sys.exit(r.returncode)


def pendientes_de_indexar():
    """Actos descargados y sin indexar, según el catálogo. (id_archivo, archivo)."""
    ruta = os.environ.get('RAG_CATALOGO', 'datos/catalogo.sqlite')
    c = sqlite3.connect(os.path.join(RAIZ, ruta))
    c.row_factory = sqlite3.Row
    filas = c.execute('SELECT id_archivo, archivo FROM acto '
                      'WHERE descargado_en IS NOT NULL AND indexado_en IS NULL').fetchall()
    c.close()
    return [(f['id_archivo'], f['archivo']) for f in filas]


def armar_tanda(actos, limite=None):
    """Directorio de la tanda con enlaces a los PDF pendientes."""
    sello = datetime.datetime.now().strftime('%Y-%m-%d-%H%M%S')
    tanda = os.path.join(RAIZ, 'data', 'tandas', sello)
    pdfs = os.path.join(tanda, 'pdfs')
    os.makedirs(pdfs)

    enlazados = 0
    for _, archivo in actos:
        if limite and enlazados >= limite:
            break
        for d in DIRS_PDF:
            origen = os.path.join(RAIZ, d, archivo)
            if os.path.exists(origen):
                os.symlink(origen, os.path.join(pdfs, archivo))
                enlazados += 1
                break
    return tanda, enlazados


def recargar_api():
    """Le pide a la API que adopte el índice nuevo. Que no esté corriendo no es error."""
    puerto = os.environ.get('RAG_PUERTO', '8000')
    clave = os.environ.get('RAG_CLAVE_INTERNA', '')
    pedido = urllib.request.Request(f'http://localhost:{puerto}/admin/indice/recargar',
                                    method='POST',
                                    headers={'X-Clave-Interna': clave})
    try:
        with urllib.request.urlopen(pedido, timeout=120) as r:
            print('API recargada:', r.read().decode()[:200], flush=True)
    except Exception as e:
        print(f'(la API no se recargó: {e} — al reiniciarla va a tomar el índice nuevo)',
              flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--sin-recolectar', action='store_true',
                   help='reusar la última recolección en vez de pedirla al portal')
    p.add_argument('--sin-descargar', action='store_true',
                   help='no bajar PDF: trabajar con lo que ya está en disco')
    p.add_argument('--sin-indexar', action='store_true',
                   help='fusionar sin reconstruir los artefactos de búsqueda')
    p.add_argument('--solo-indexar', action='store_true',
                   help='solo reconstruir artefactos y recargar la API')
    p.add_argument('--limite', type=int, default=None,
                   help='procesar a lo sumo N actos nuevos (para probar)')
    p.add_argument('--paciencia', type=int, default=25,
                   help='tolerancia del recolector a respuestas vacías del portal')
    a = p.parse_args()

    t0 = time.time()

    comando_conciliar = [PY, '-m', 'pipeline.catalogo', 'conciliar',
                         '--indice', 'indice/chunks.jsonl', '--log', LOG_DESCARGAS]
    for d in DIRS_PDF:
        comando_conciliar += ['--descargas', d]

    if a.solo_indexar:
        paso('reconstruir artefactos', [PY, '-m', 'pipeline.construir_indice'])
        paso('conciliar', comando_conciliar)
        recargar_api()
        print(f'\n=== indexación en {(time.time() - t0) / 60:.1f} min ===', flush=True)
        return

    # 1. recolectar --- el portal responde intermitente; la paciencia alta es deliberada
    if not a.sin_recolectar:
        paso('recolectar', [PY, 'scrapers/recolectar_api.py', '--todas',
                            '--salida', METADATOS,
                            '--traza', 'scrapers/traza.jsonl',
                            '--paciencia', str(a.paciencia)])
    elif not os.path.exists(os.path.join(RAIZ, METADATOS)):
        sys.exit(f'--sin-recolectar pero no existe {METADATOS}')

    # 2. importar al catálogo y conciliar ANTES de descargar: la conciliación marca
    # qué actos ya están indexados, y la descarga usa esa marca para no volver a bajar
    # PDFs cuyo archivo en disco tiene otro nombre (los del corpus viejo, posicionales).
    paso('importar', [PY, '-m', 'pipeline.catalogo', 'importar', '--metadatos', METADATOS])
    paso('conciliar previo', comando_conciliar)

    # 3. descargar lo que falte
    if not a.sin_descargar:
        paso('descargar', [PY, 'scrapers/bajar_pdfs.py', '--metadatos', METADATOS,
                           '--destino', DESCARGAS, '--log', LOG_DESCARGAS,
                           '--saltar-indexados'])

    # 4. conciliar
    paso('conciliar', comando_conciliar)

    # 5. ¿hay algo para indexar?
    actos = pendientes_de_indexar()
    if not actos:
        print(f'\nSin actos nuevos. El índice queda como está. '
              f'({time.time() - t0:.0f}s)', flush=True)
        return
    tanda, enlazados = armar_tanda(actos, a.limite)
    print(f'\n{len(actos)} actos pendientes de indexar; esta tanda toma {enlazados}.',
          flush=True)
    print(f'tanda: {tanda}', flush=True)
    if not enlazados:
        print('Ninguno tiene su PDF en disco todavía; nada que hacer.', flush=True)
        return

    rel = os.path.relpath(tanda, RAIZ)

    # 5. procesar (extractor + post-procesador + canónicos)
    paso('procesar', [PY, 'pipeline/procesar_corpus.py',
                      '--pdfs', os.path.join(rel, 'pdfs'),
                      '--salida', os.path.join(rel, 'procesados'),
                      '--sin-scratch'])

    # 6. metadata autoritativa + chunking
    paso('metadata', [PY, '-m', 'pipeline.metadata_desde_catalogo',
                      '--metadatos', METADATOS,
                      '--pdfs', os.path.join(rel, 'pdfs'),
                      '--salida', os.path.join(rel, 'metadata.csv')])
    paso('chunkear', [PY, 'pipeline/chunkear.py',
                      '--resultados', os.path.join(rel, 'procesados'),
                      '--salida', os.path.join(rel, 'chunks.jsonl'),
                      '--metadata', os.path.join(rel, 'metadata.csv')])

    # 7. vectorizar --- embeddings.py escribe en un DIRECTORIO: densos.npy, el
    # chunks.jsonl definitivo (sin texto_indexado) e indice.json, alineados entre sí
    paso('vectorizar', [PY, 'pipeline/embeddings.py',
                        '--chunks', os.path.join(rel, 'chunks.jsonl'),
                        '--salida', os.path.join(rel, 'vectores')])

    # 8. fusionar al índice: chunks y vectores DE LA MISMA SALIDA, que es lo único
    # que garantiza el alineamiento por posición
    paso('fusionar', [PY, '-m', 'pipeline.fusionar_indice',
                      '--chunks-nuevos', os.path.join(rel, 'vectores', 'chunks.jsonl'),
                      '--densos-nuevos', os.path.join(rel, 'vectores', 'densos.npy'),
                      '--aplicar'])

    # 9. refrescar la metadata de todos los fragmentos (URLs, fechas de acto)
    paso('refrescar metadata', [PY, '-m', 'pipeline.actualizar_metadata',
                                '--metadatos', METADATOS, '--aplicar'])

    if a.sin_indexar:
        print('\nFusionado sin reconstruir artefactos: falta el paso de indexación '
              'para que lo nuevo se sirva.', flush=True)
        return

    # 10. reconstruir artefactos (sqlite + faiss, con swap atómico)
    paso('reconstruir artefactos', [PY, '-m', 'pipeline.construir_indice'])

    # 11. conciliar de nuevo: ahora lo fusionado figura en chunks.jsonl
    paso('conciliar final', comando_conciliar)

    # 12. recarga en caliente
    recargar_api()

    print(f'\n=== actualización completa en {(time.time() - t0) / 60:.1f} min ===',
          flush=True)


if __name__ == '__main__':
    main()
