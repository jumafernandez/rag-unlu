"""Construye los artefactos de búsqueda a partir de chunks.jsonl y densos.npy.

Genera dos archivos que reemplazan la carga completa en memoria:

    indice/chunks.sqlite   el texto y la metadata de cada fragmento
    indice/vectores.faiss  el índice de vectores

Por qué. Hoy el proceso mantiene los 140.902 fragmentos como objetos de Python: 2,1 GB de
memoria, de los cuales solo 577 MB son los vectores. El resto es texto que se consulta
apenas para los ocho fragmentos que se devuelven. Con el digesto histórico ---unos 120.000
documentos más--- eso serían doce gigabytes de texto en RAM antes de que los vectores
lleguen a ser un problema.

La clave del diseño: **la posición manda**. El fragmento i de chunks.jsonl es la fila i de
densos.npy, es el rowid i+1 de la tabla y es el vector i del índice FAISS. Esa
correspondencia es lo único que ata las tres piezas, así que se construyen juntas y en
orden, y se verifica al terminar.

Uso:
    python -m pipeline.construir_indice
    python -m pipeline.construir_indice --tipo ivf     # aproximado, para corpus grandes
"""
import argparse
import json
import os
import sqlite3
import time

import numpy as np

ESQUEMA = """
CREATE TABLE IF NOT EXISTS chunk (
    i             INTEGER PRIMARY KEY,   -- posición: coincide con la fila de densos.npy
    chunk_id      TEXT,
    documento     TEXT,
    seccion       TEXT,
    tipo_seccion  TEXT,
    cita          TEXT,
    titulo        TEXT,
    texto         TEXT,
    document_code TEXT,
    document_number TEXT,
    date_issued   TEXT,
    fecha_acto    TEXT,
    estado        TEXT,
    metadata_confianza TEXT,
    source_pdf    TEXT,
    url_documento TEXT,
    id_archivo    TEXT,
    id_documento  TEXT
);
CREATE INDEX IF NOT EXISTS chunk_documento ON chunk (documento);
CREATE INDEX IF NOT EXISTS chunk_identidad ON chunk (document_code, document_number);
"""

COLUMNAS = ['chunk_id', 'documento', 'seccion', 'tipo_seccion', 'cita', 'titulo', 'texto',
            'document_code', 'document_number', 'date_issued', 'fecha_acto', 'estado',
            'metadata_confianza', 'source_pdf', 'url_documento', 'id_archivo', 'id_documento']


FTS = """
-- Búsqueda léxica delegada a SQLite. `tokenchars '-/'` es lo que hace viable el cambio:
-- sin eso, el tokenizador parte los identificadores normativos ---528/2025, DISPCD-CB,
-- RESHCS-LUJ--- que son justamente lo que hay que poder buscar de forma exacta.
-- Verificado: con esa opción quedan como un solo token.
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    contenido,
    content='',                       -- sin copia del texto: solo el índice invertido
    tokenize="unicode61 tokenchars '-/'"
);
"""


def construir_fts(ruta_bd):
    """Índice invertido para BM25, en disco.

    Se separa del texto (`content=''`) porque el texto ya está en la tabla `chunk`: se
    guarda solo lo necesario para buscar, y el rowid del índice coincide con la posición
    del fragmento, que es lo que ata todo.
    """
    c = sqlite3.connect(ruta_bd)
    c.execute('PRAGMA synchronous = OFF')
    c.executescript(FTS)
    c.execute('DELETE FROM chunk_fts')
    filas = c.execute('SELECT i, titulo, cita, texto FROM chunk ORDER BY i')
    lote, n = [], 0
    for fila in filas:
        lote.append((fila[0], ' '.join(x or '' for x in fila[1:])))
        n += 1
        if len(lote) >= 5000:
            c.executemany('INSERT INTO chunk_fts (rowid, contenido) VALUES (?,?)', lote)
            lote = []
    if lote:
        c.executemany('INSERT INTO chunk_fts (rowid, contenido) VALUES (?,?)', lote)
    c.commit()
    c.execute("INSERT INTO chunk_fts(chunk_fts) VALUES('optimize')")
    c.commit()
    c.close()
    return n


def construir_sqlite(ruta_jsonl, ruta_bd):
    if os.path.exists(ruta_bd):
        os.remove(ruta_bd)
    for extra in ('-wal', '-shm'):
        if os.path.exists(ruta_bd + extra):
            os.remove(ruta_bd + extra)

    c = sqlite3.connect(ruta_bd)
    c.executescript(ESQUEMA)
    # Construcción de una sola vez: no hace falta pagar durabilidad por fila.
    c.execute('PRAGMA synchronous = OFF')
    c.execute('PRAGMA journal_mode = MEMORY')

    marcadores = ', '.join('?' * (len(COLUMNAS) + 1))
    sql = f"INSERT INTO chunk (i, {', '.join(COLUMNAS)}) VALUES ({marcadores})"
    n, lote = 0, []
    with open(ruta_jsonl, encoding='utf-8') as f:
        for i, linea in enumerate(f):
            ch = json.loads(linea)
            lote.append([i] + [ch.get(k) for k in COLUMNAS])
            n += 1
            if len(lote) >= 5000:
                c.executemany(sql, lote); lote = []
    if lote:
        c.executemany(sql, lote)
    c.commit()
    c.execute('PRAGMA journal_mode = WAL')
    c.close()
    return n


def construir_faiss(ruta_npy, ruta_indice, tipo='plano'):
    import faiss
    densos = np.load(ruta_npy).astype('float32')
    # Se normaliza acá, una sola vez: con vectores normalizados el producto interno ES la
    # similitud coseno, que es lo que usa la búsqueda.
    faiss.normalize_L2(densos)
    n, dim = densos.shape

    if tipo == 'plano':
        # Exacto. A esta escala tarda milisegundos y no introduce error, así que no hay
        # motivo para aproximar todavía.
        indice = faiss.IndexFlatIP(dim)
    else:
        # Aproximado, para cuando el corpus crezca. La raíz de n es la regla habitual para
        # la cantidad de listas invertidas.
        listas = max(1, int(np.sqrt(n)))
        indice = faiss.IndexIVFFlat(faiss.IndexFlatIP(dim), dim, listas, faiss.METRIC_INNER_PRODUCT)
        indice.train(densos)
        indice.nprobe = max(1, listas // 20)

    indice.add(densos)
    faiss.write_index(indice, ruta_indice)
    return n, dim, tipo


def verificar(ruta_bd, ruta_indice, ruta_npy, muestras=5):
    """La correspondencia por posición es lo único que ata las tres piezas: se comprueba."""
    import faiss
    c = sqlite3.connect(ruta_bd)
    filas = c.execute('SELECT COUNT(*) FROM chunk').fetchone()[0]
    ix = faiss.read_index(ruta_indice)
    densos = np.load(ruta_npy).astype('float32')
    faiss.normalize_L2(densos)

    problemas = []
    if filas != ix.ntotal or filas != densos.shape[0]:
        problemas.append(f'cantidades distintas: sqlite={filas} faiss={ix.ntotal} npy={densos.shape[0]}')

    # Cada vector debe recuperarse a sí mismo como vecino más cercano, y en esa posición
    # la tabla debe tener el fragmento correspondiente.
    paso = max(1, filas // muestras)
    for i in range(0, filas, paso):
        sims, idx = ix.search(densos[i:i + 1], 1)
        j = int(idx[0][0])
        # Que devuelva OTRO índice no es error si el vector es el mismo: el corpus tiene
        # fragmentos repetidos ---el mismo texto en dos documentos--- y ante vectores
        # idénticos la búsqueda puede devolver cualquiera de los dos. Lo que sí sería un
        # error es que devolviera un vector distinto.
        if j != i and float(sims[0][0]) < 0.9999:
            problemas.append(f'el vector {i} recupera {j}, que no es igual '
                             f'(similitud {float(sims[0][0]):.4f})')
        if not c.execute('SELECT 1 FROM chunk WHERE i=?', (i,)).fetchone():
            problemas.append(f'falta la fila {i} en sqlite')
    c.close()
    return problemas


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--indice', default='indice')
    p.add_argument('--tipo', choices=('plano', 'ivf'), default='plano')
    a = p.parse_args()

    jsonl = os.path.join(a.indice, 'chunks.jsonl')
    npy = os.path.join(a.indice, 'densos.npy')
    bd = os.path.join(a.indice, 'chunks.sqlite')
    fx = os.path.join(a.indice, 'vectores.faiss')

    t0 = time.time()
    n = construir_sqlite(jsonl, bd)
    print(f'sqlite : {n} fragmentos · {os.path.getsize(bd)/1e6:.0f} MB · {time.time()-t0:.0f}s')

    t2 = time.time()
    nf = construir_fts(bd)
    print(f'fts5   : {nf} fragmentos indexados · base ahora {os.path.getsize(bd)/1e6:.0f} MB '
          f'· {time.time()-t2:.0f}s')

    t1 = time.time()
    n2, dim, tipo = construir_faiss(npy, fx, a.tipo)
    print(f'faiss  : {n2} vectores de {dim} · tipo {tipo} · '
          f'{os.path.getsize(fx)/1e6:.0f} MB · {time.time()-t1:.0f}s')

    problemas = verificar(bd, fx, npy)
    if problemas:
        print('\nPROBLEMAS:')
        for x in problemas:
            print('  -', x)
        raise SystemExit(1)
    print('\nverificación: la correspondencia por posición se mantiene en las tres piezas')


if __name__ == '__main__':
    main()
