#!/usr/bin/env python3
"""Depura el índice: saca los actos duplicados y repara las citas degradadas.

Por qué existe. El índice acumuló dos defectos medibles que ninguna actualización
incremental va a arreglar sola:

  - **Actos duplicados** (2.412 actos, 17.469 fragmentos de más al momento de escribir
    esto): el mismo acto entró más de una vez, sea porque el corpus viejo lo bajó dos
    veces con nombres posicionales distintos, sea porque la fusión incremental re-agregó
    algo que ya estaba. El efecto visible: la misma disposición aparece dos veces entre
    las fuentes de una respuesta.

  - **Citas degradadas** ('Orden_compra', 'Unknown'): fragmentos cuyo documento no tenía
    identidad cuando se chunkeó. El efecto es peor que el estético: el modelo, al citar,
    INVENTA identificadores con forma verosímil ("ODC 16/26") porque la cita real no le
    dice nada. Con el catálogo completo, la identidad de la mayoría se conoce por
    id_archivo y se puede escribir la cita verdadera.

Cambiar la cita cambia el texto que se embebe (`título | cita` encabeza cada fragmento),
así que los fragmentos reparados se RE-EMBEBEN acá mismo, con el mismo modelo del
índice. Son pocos miles: minutos de CPU.

La regla de deduplicación: de cada identidad de acto se conserva UNA copia. Se prefiere
la que tiene URL al portal; después la de metadata con más confianza; después la que
tiene más fragmentos; y como último desempate, el nombre de documento mayor (la
recolección más nueva). Determinista: correrlo dos veces da lo mismo.

Uso:
    python -m pipeline.depurar_indice --metadatos scrapers/metadatos_nuevo.csv
    python -m pipeline.depurar_indice --metadatos ... --aplicar
"""
import argparse
import collections
import csv
import json
import os
import sys

import numpy as np

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

DEGRADADAS = {'unknown', 'orden_compra', ''}


def identidad_de(cita):
    return (cita or '').split('—')[0].strip().lower()


def cargar_catalogo(ruta):
    """id_archivo -> identidad del acto según el portal."""
    catalogo = {}
    with open(ruta, newline='', encoding='utf-8-sig') as f:
        for fila in csv.DictReader(f):
            if fila.get('id_archivo'):
                catalogo[fila['id_archivo']] = fila
    return catalogo


def cita_desde_catalogo(fila, sufijo):
    tipo = (fila.get('Tipo de documento') or 'Acto').strip().title()
    codigo = (fila.get('Codigo') or '').strip().upper()
    nro, anio = (fila.get('Nro') or '').strip(), (fila.get('Anio') or '').strip()
    if not (nro and anio):
        return None
    base = ' '.join(x for x in (tipo, codigo, f'{nro}/{anio}') if x)
    return f'{base} — {sufijo}' if sufijo else base


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--indice', default='indice')
    p.add_argument('--metadatos', required=True,
                   help='CSV de la recolección completa (identidad autoritativa)')
    p.add_argument('--aplicar', action='store_true')
    a = p.parse_args()

    ruta_jsonl = os.path.join(a.indice, 'chunks.jsonl')
    ruta_npy = os.path.join(a.indice, 'densos.npy')
    chunks = [json.loads(l) for l in open(ruta_jsonl, encoding='utf-8') if l.strip()]
    densos = np.load(ruta_npy)
    assert len(chunks) == densos.shape[0], \
        f'desalineado: {len(chunks)} chunks vs {densos.shape[0]} vectores'
    catalogo = cargar_catalogo(a.metadatos)
    print(f'{len(chunks)} fragmentos; catálogo con {len(catalogo)} actos', flush=True)

    # ---------------- 1. reparar citas degradadas ----------------
    reparados = []
    sin_reparar = 0
    for i, c in enumerate(chunks):
        if identidad_de(c.get('cita')) not in DEGRADADAS:
            continue
        fila = catalogo.get(c.get('id_archivo') or '')
        if not fila:
            sin_reparar += 1
            continue
        sufijo = ''
        if '—' in (c.get('cita') or ''):
            sufijo = c['cita'].split('—', 1)[1].strip()
        elif c.get('seccion') and c['seccion'] not in (c.get('cita'), c.get('documento')):
            sufijo = c['seccion']
        nueva = cita_desde_catalogo(fila, sufijo)
        if not nueva:
            sin_reparar += 1
            continue
        c['cita'] = nueva
        if fila.get('Titulo'):
            c['titulo'] = fila['Titulo']
        reparados.append(i)
    print(f'citas reparadas: {len(reparados)}  |  sin identidad en el catálogo: {sin_reparar}',
          flush=True)

    # ---------------- 2. deduplicar por identidad de acto ----------------
    # identidad -> {documento: [indices]}. Los que siguen degradados agrupan por su
    # propio documento: no hay identidad para afirmarlos duplicados de nada.
    grupos = collections.defaultdict(lambda: collections.defaultdict(list))
    for i, c in enumerate(chunks):
        ident = identidad_de(c.get('cita'))
        if ident in DEGRADADAS:
            ident = f"__doc__{c.get('documento')}"
        grupos[ident][c.get('documento')].append(i)

    def puntaje_copia(indices):
        c0 = chunks[indices[0]]
        return (1 if c0.get('url_documento') else 0,
                1 if (c0.get('metadata_confianza') or '') in ('catalogo', 'alta') else 0,
                len(indices),
                c0.get('documento') or '')

    conservar = set()
    actos_duplicados = 0
    for ident, copias in grupos.items():
        if len(copias) == 1:
            conservar.update(next(iter(copias.values())))
            continue
        actos_duplicados += 1
        ganadora = max(copias.values(), key=puntaje_copia)
        conservar.update(ganadora)

    descartados = len(chunks) - len(conservar)
    print(f'actos con copias de más: {actos_duplicados}  |  fragmentos que se descartan: '
          f'{descartados}', flush=True)

    if not a.aplicar:
        print('\n(informe solamente; correr con --aplicar para escribir)', flush=True)
        return

    # ---------------- 3. re-embeber los reparados ----------------
    # El texto embebido es 'título | cita' + fragmento: cambió la cita, cambia el vector.
    a_reembeber = [i for i in reparados if i in conservar]
    if a_reembeber:
        print(f're-embebiendo {len(a_reembeber)} fragmentos reparados…', flush=True)
        from backend import api as backend_api
        cod = backend_api.codificador()
        textos = []
        for i in a_reembeber:
            c = chunks[i]
            encabezado = ' | '.join(x for x in (c.get('titulo'), c.get('cita')) if x)
            textos.append(f"{encabezado}\n\n{c['texto']}" if encabezado else c['texto'])
        nuevos = cod.encode(textos, batch_size=16, normalize_embeddings=True,
                            show_progress_bar=True, convert_to_numpy=True).astype(np.float32)
        densos = densos.copy()
        for fila, i in enumerate(a_reembeber):
            densos[i] = nuevos[fila]

    # ---------------- 4. escribir filtrado, alineado, con respaldo ----------------
    orden = sorted(conservar)
    for ruta in (ruta_jsonl, ruta_npy):
        respaldo = ruta + '.antes-de-depurar'
        if not os.path.exists(respaldo):
            os.rename(ruta, respaldo)
    with open(ruta_jsonl, 'w', encoding='utf-8') as f:
        for i in orden:
            f.write(json.dumps(chunks[i], ensure_ascii=False) + '\n')
    np.save(ruta_npy, densos[orden])

    # verificación elemental de alineación
    n = sum(1 for _ in open(ruta_jsonl, encoding='utf-8'))
    m = np.load(ruta_npy, mmap_mode='r').shape[0]
    assert n == m == len(orden), (n, m, len(orden))
    print(f'\nescrito: {n} fragmentos (antes {len(chunks)}). Respaldos en '
          f'*.antes-de-depurar', flush=True)
    print('siguiente paso: pipeline.actualizar_metadata --aplicar y '
          'pipeline.construir_indice', flush=True)


if __name__ == '__main__':
    main()
