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

# Dos anclas distintas a propósito. Los SCRIPTS viven en el repo (REPO); los DATOS de la
# instancia viven en su directorio de trabajo (RAIZ = cwd). En la instancia histórica de
# la UNLu ambos coinciden; en una instalación (instalaciones/unsl) el código es el del
# repo y el corpus, el catálogo y el índice quedan en la carpeta de esa instalación.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAIZ = os.getcwd()
PY = sys.executable

METADATOS = 'scrapers/metadatos_nuevo.csv'
DESCARGAS = 'data/portal-incremental'
LOG_DESCARGAS = 'data/descargas.jsonl'
DIRS_PDF = ('data/portal', 'data/portal-incremental')


def paso(nombre, comando, **kwargs):
    print(f'\n=== {nombre} ===', flush=True)
    print('$', ' '.join(comando), flush=True)
    t0 = time.time()
    ambiente = {**os.environ,
                'PYTHONPATH': REPO + os.pathsep + os.environ.get('PYTHONPATH', '')}
    r = subprocess.run(comando, cwd=RAIZ, env=ambiente, **kwargs)
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


def reconstruir_todo(a, t0, comando_conciliar):
    """Rehace el índice entero desde los actos ya procesados.

    No es la rutina de mantenimiento: es la que corresponde cuando cambia algo que afecta a
    TODO el corpus y no solo a lo nuevo ---el fragmentador, la forma de la cita, el modelo
    de embeddings---. Ahí una fusión incremental no sirve, porque dejaría el corpus mitad
    con un criterio y mitad con otro.

    Vuelve a fragmentar y a vectorizar todo, y ADOPTA el resultado como índice en lugar de
    fusionarlo. El índice anterior se conserva al lado, con su fecha: es la vuelta atrás si
    la reconstrucción sale peor, y el punto de comparación para medirla.
    """
    import glob as _glob
    import shutil

    procesados = a.procesados if os.path.isabs(a.procesados) else os.path.join(RAIZ, a.procesados)
    canonicos = _glob.glob(os.path.join(procesados, '**', '*_canonico.yaml'), recursive=True)
    if not canonicos:
        raise SystemExit(f'no hay actos procesados en {procesados}\n'
                         'Indicá el directorio correcto con --procesados.')

    sello = datetime.datetime.now().strftime('%Y-%m-%d-%H%M%S')
    tanda = os.path.join(RAIZ, 'data', 'tandas', f'{sello}-reconstruccion')
    os.makedirs(tanda, exist_ok=True)
    rel = os.path.relpath(tanda, RAIZ)
    print(f'reconstrucción de {len(canonicos)} actos procesados', flush=True)
    print(f'tanda: {tanda}', flush=True)

    # La metadata autoritativa le da a cada fragmento su identidad, su título y su enlace
    # permanente. Sin ella el índice se construye igual, pero las citas quedan mudas.
    if a.metadata:
        metadata = a.metadata if os.path.isabs(a.metadata) else os.path.join(RAIZ, a.metadata)
        if not os.path.exists(metadata):
            raise SystemExit(f'no existe la metadata indicada: {metadata}')
        metadata = os.path.relpath(metadata, RAIZ)
        print(f'metadata autoritativa: {metadata} (provista)', flush=True)
    else:
        metadata = os.path.join(rel, 'metadata.csv')
        for d in DIRS_PDF:
            if os.path.isdir(os.path.join(RAIZ, d)):
                paso('metadata autoritativa',
                     [PY, os.path.join(REPO, 'pipeline', 'metadata_desde_catalogo.py'),
                      '--metadatos', METADATOS, '--pdfs', d,
                      '--salida', metadata])
                break
        # Un cruce vacío no es un detalle: significa que los PDF no se llaman como el
        # catálogo espera, y seguir produciría un índice sin códigos de acto ni enlaces,
        # o sea peor que el que ya está sirviendo. Mejor frenar acá que descubrirlo
        # después de horas de cómputo.
        completo = os.path.join(RAIZ, metadata)
        filas = sum(1 for _ in open(completo, encoding='utf-8')) - 1 if os.path.exists(completo) else 0
        if filas <= 0:
            raise SystemExit(
                'la metadata autoritativa salió vacía: ningún PDF cruzó con el catálogo.\n'
                'Suele pasar cuando el corpus llegó por una vía distinta a la del portal y '
                'los archivos no se llaman igual.\n'
                'Si ya tenés un CSV de metadata armado, pasalo con --metadata.')
        print(f'metadata autoritativa: {filas} actos', flush=True)
    orden = [PY, os.path.join(REPO, 'pipeline', 'chunkear.py'),
             '--resultados', os.path.relpath(procesados, RAIZ),
             '--salida', os.path.join(rel, 'chunks.jsonl')]
    orden += ['--metadata', metadata]
    paso('fragmentar todo', orden)

    vectorizar = [PY, os.path.join(REPO, 'pipeline', 'embeddings.py'),
                  '--chunks', os.path.join(rel, 'chunks.jsonl'),
                  '--salida', os.path.join(rel, 'vectores')]
    if a.modelo:
        vectorizar += ['--modelo', a.modelo]
    if a.dispositivo:
        vectorizar += ['--dispositivo', a.dispositivo]
    if a.batch:
        vectorizar += ['--batch', str(a.batch)]
    paso('vectorizar todo', vectorizar)

    # Adopción con respaldo. El índice viejo no se borra: queda al lado, fechado.
    indice = os.path.join(RAIZ, 'indice')
    if os.path.isdir(indice):
        respaldo = os.path.join(RAIZ, f'indice.previo-{sello}')
        shutil.copytree(indice, respaldo,
                        ignore=shutil.ignore_patterns('*.faiss', '*.sqlite'))
        print(f'\níndice anterior respaldado en {os.path.basename(respaldo)}', flush=True)
    os.makedirs(indice, exist_ok=True)
    for nombre in ('chunks.jsonl', 'densos.npy', 'indice.json'):
        shutil.copy(os.path.join(RAIZ, rel, 'vectores', nombre), os.path.join(indice, nombre))
    print('=== índice adoptado desde la reconstrucción ===', flush=True)

    # El catálogo puede no existir ---una instalación que armó su base por otra vía--- y
    # eso no invalida la reconstrucción: lo que aporta es refrescar enlaces y estados, no
    # el contenido. Se avisa y se sigue, en vez de abortar con el índice ya adoptado.
    if os.path.exists(os.path.join(RAIZ, METADATOS)):
        paso('refrescar metadata', [PY, '-m', 'pipeline.actualizar_metadata', '--aplicar'])
    else:
        print(f'\nsin catálogo en {METADATOS}: se omite el refresco de metadata', flush=True)

    paso('reconstruir artefactos', [PY, '-m', 'pipeline.construir_indice'])

    if os.path.exists(os.path.join(RAIZ, METADATOS)):
        paso('conciliar', comando_conciliar)
    recargar_api()
    print(f'\n=== reconstrucción en {(time.time() - t0) / 60:.1f} min ===', flush=True)


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
    p.add_argument('--reconstruir', action='store_true',
                   help='rehacer el índice COMPLETO desde los actos ya procesados, sin '
                        'recolectar ni descargar. Es lo que corresponde cuando cambia el '
                        'fragmentador o el modelo de embeddings.')
    p.add_argument('--procesados', default='data/procesados',
                   help='con --reconstruir: directorio de los actos ya procesados '
                        '(*_canonico.yaml y *.md)')
    p.add_argument('--metadata', default=None,
                   help='con --reconstruir: CSV de metadata autoritativa ya armado. Si no '
                        'se pasa, se genera desde el catálogo. Hace falta cuando los PDF '
                        'no se llaman como el catálogo espera ---por ejemplo si el corpus '
                        'llegó por otra vía--- y por lo tanto no cruzan por nombre.')
    p.add_argument('--dispositivo', default=None,
                   help='dónde calcular los embeddings: cpu, cuda, xpu, mps. Por omisión '
                        'lo detecta solo.')
    p.add_argument('--batch', type=int, default=None,
                   help='tamaño de lote de los embeddings')
    p.add_argument('--modelo', default=os.environ.get('RAG_MODELO_EMB'),
                   help='modelo de embeddings: nombre en Hugging Face o ruta local. Por '
                        'omisión toma RAG_MODELO_EMB, la misma variable que usa la API, '
                        'para que el índice se construya con el modelo que después lo '
                        'consulta. En una máquina sin salida a internet hay que apuntar a '
                        'los pesos locales o el paso falla buscándolos afuera.')
    a = p.parse_args()

    t0 = time.time()

    comando_conciliar = [PY, '-m', 'pipeline.catalogo', 'conciliar',
                         '--indice', 'indice/chunks.jsonl', '--log', LOG_DESCARGAS]
    for d in DIRS_PDF:
        comando_conciliar += ['--descargas', d]

    if a.reconstruir:
        reconstruir_todo(a, t0, comando_conciliar)
        return

    if a.solo_indexar:
        paso('reconstruir artefactos', [PY, '-m', 'pipeline.construir_indice'])
        paso('conciliar', comando_conciliar)
        recargar_api()
        print(f'\n=== indexación en {(time.time() - t0) / 60:.1f} min ===', flush=True)
        return

    # 1. recolectar --- el portal responde intermitente; la paciencia alta es deliberada
    if not a.sin_recolectar:
        paso('recolectar', [PY, os.path.join(REPO, 'scrapers', 'recolectar_api.py'), '--todas',
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
        paso('descargar', [PY, os.path.join(REPO, 'scrapers', 'bajar_pdfs.py'), '--metadatos', METADATOS,
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
    paso('procesar', [PY, os.path.join(REPO, 'pipeline', 'procesar_corpus.py'),
                      '--pdfs', os.path.join(rel, 'pdfs'),
                      '--salida', os.path.join(rel, 'procesados'),
                      '--sin-scratch'])

    # 6. metadata autoritativa + chunking
    paso('metadata', [PY, '-m', 'pipeline.metadata_desde_catalogo',
                      '--metadatos', METADATOS,
                      '--pdfs', os.path.join(rel, 'pdfs'),
                      '--salida', os.path.join(rel, 'metadata.csv')])
    paso('chunkear', [PY, os.path.join(REPO, 'pipeline', 'chunkear.py'),
                      '--resultados', os.path.join(rel, 'procesados'),
                      '--salida', os.path.join(rel, 'chunks.jsonl'),
                      '--metadata', os.path.join(rel, 'metadata.csv')])

    # 7. vectorizar --- embeddings.py escribe en un DIRECTORIO: densos.npy, el
    # chunks.jsonl definitivo (sin texto_indexado) e indice.json, alineados entre sí
    paso('vectorizar', [PY, os.path.join(REPO, 'pipeline', 'embeddings.py'),
                        '--chunks', os.path.join(rel, 'chunks.jsonl'),
                        '--salida', os.path.join(rel, 'vectores')])

    # 8. fusionar al índice: chunks y vectores DE LA MISMA SALIDA, que es lo único
    # que garantiza el alineamiento por posición. Si el índice todavía no existe ---la
    # primera base de una instalación nueva--- no hay nada que fusionar: la tanda ES el
    # índice y se adopta entera.
    ruta_chunks = os.path.join(RAIZ, 'indice', 'chunks.jsonl')
    if not os.path.exists(ruta_chunks):
        import shutil
        os.makedirs(os.path.join(RAIZ, 'indice'), exist_ok=True)
        shutil.copy(os.path.join(RAIZ, rel, 'vectores', 'chunks.jsonl'), ruta_chunks)
        shutil.copy(os.path.join(RAIZ, rel, 'vectores', 'densos.npy'),
                    os.path.join(RAIZ, 'indice', 'densos.npy'))
        shutil.copy(os.path.join(RAIZ, rel, 'vectores', 'indice.json'),
                    os.path.join(RAIZ, 'indice', 'indice.json'))
        print('\n=== primera base: la tanda se adopta como índice inicial ===', flush=True)
    else:
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
