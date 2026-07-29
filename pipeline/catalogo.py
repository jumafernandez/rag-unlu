"""Catálogo de actos: qué publicó el portal y en qué estado está cada acto acá.

Por qué existe. Hasta ahora el estado de la ingesta estaba repartido en tres lugares que no
se hablaban entre sí: un CSV al que se le agregaban filas, los archivos que hubiera en
disco, y un log de descargas. Para una actualización manual alcanzaba. Para un proceso que
corra todas las noches, no: hace falta poder preguntar "qué actos hay en el portal que no
tengo", "cuáles bajé pero no indexé", "cuáles fallaron y hace cuánto", y ninguna de esas
preguntas se responde mirando un CSV.

El catálogo es una tabla con una fila por acto, con el identificador del portal como clave.
No reemplaza al índice: el índice sigue siendo `chunks.jsonl` más `densos.npy`. Este archivo
responde por el CICLO DE VIDA de cada acto, no por su contenido.

Se guarda aparte de `chatdigesto.sqlite` a propósito: esa base tiene datos personales
---quién consultó qué--- y esta tiene datos públicos del digesto. Distinto respaldo, distinto
tratamiento, y no conviene mezclarlos.

Uso:
    python -m pipeline.catalogo importar --metadatos scrapers/metadatos.csv
    python -m pipeline.catalogo conciliar --descargas data/portal-incremental --indice indice/chunks.jsonl
    python -m pipeline.catalogo estado
    python -m pipeline.catalogo pendientes --etapa descarga --limite 20
"""
import argparse
import csv
import glob
import json
import os
import re
import sqlite3
import sys
import time

RUTA = os.environ.get('RAG_CATALOGO', 'datos/catalogo.sqlite')

ESQUEMA = """
CREATE TABLE IF NOT EXISTS acto (
    id_archivo    TEXT PRIMARY KEY,   -- identificador del archivo en el portal
    id_documento  TEXT,               -- identificador del acto en el portal
    codigo        TEXT,               -- DISPCD-CB, RESHCS, DGAA...
    nro           TEXT,
    anio          TEXT,
    numero        TEXT,               -- como lo muestra el portal: "DISPCD-CB : 528 / 2025"
    tipo          TEXT,
    titulo        TEXT,
    seccion       TEXT,
    estado_portal TEXT,               -- "Autorizado" y demás. NO indica vigencia.
    fecha         TEXT,               -- de autorización (la que muestra la tabla del portal)
    fecha_acto    TEXT,               -- la impresa en el documento
    url           TEXT,
    archivo       TEXT,               -- nombre de archivo derivado de la identidad del acto

    -- ciclo de vida
    visto_en      INTEGER,            -- última vez que apareció en la recolección
    descargado_en INTEGER,
    indexado_en   INTEGER,
    sha256        TEXT,
    bytes         INTEGER,
    error         TEXT                -- último fallo, para no reintentar a ciegas
);
CREATE INDEX IF NOT EXISTS acto_identidad ON acto (codigo, nro, anio);
CREATE INDEX IF NOT EXISTS acto_seccion   ON acto (seccion);
CREATE INDEX IF NOT EXISTS acto_pendiente ON acto (descargado_en, indexado_en);
"""

CAMPOS_CSV = {
    'id_archivo': 'id_archivo', 'id_documento': 'id_documento', 'codigo': 'Codigo',
    'nro': 'Nro', 'anio': 'Anio', 'numero': 'Numero', 'tipo': 'Tipo de documento',
    'titulo': 'Titulo', 'seccion': 'Seccion', 'estado_portal': 'Estado',
    'fecha': 'Fecha', 'fecha_acto': 'Fecha acto', 'url': 'URL', 'archivo': 'Archivo',
}


def bd(ruta=None):
    ruta = ruta or RUTA
    os.makedirs(os.path.dirname(ruta) or '.', exist_ok=True)
    c = sqlite3.connect(ruta)
    c.row_factory = sqlite3.Row
    # WAL: los lectores dejan de bloquearse con el escritor. En un proceso nocturno que
    # escribe mientras la API consulta, es la diferencia entre convivir y trabarse.
    c.execute('PRAGMA journal_mode = WAL')
    c.execute('PRAGMA busy_timeout = 10000')
    c.executescript(ESQUEMA)
    return c


def importar(c, ruta_csv):
    """Incorpora la recolección del portal. Actualiza lo que cambió y no pisa el ciclo de vida."""
    ahora, nuevos, actualizados = int(time.time()), 0, 0
    with open(ruta_csv, encoding='utf-8-sig') as f:
        for fila in csv.DictReader(f):
            id_a = (fila.get('id_archivo') or '').strip()
            if not id_a:
                continue
            datos = {k: (fila.get(v) or '').strip() for k, v in CAMPOS_CSV.items()}
            existe = c.execute('SELECT 1 FROM acto WHERE id_archivo=?', (id_a,)).fetchone()
            columnas = ', '.join(f'{k}=?' for k in datos if k != 'id_archivo')
            if existe:
                c.execute(f'UPDATE acto SET {columnas}, visto_en=? WHERE id_archivo=?',
                          [v for k, v in datos.items() if k != 'id_archivo'] + [ahora, id_a])
                actualizados += 1
            else:
                cols = list(datos) + ['visto_en']
                c.execute(f"INSERT INTO acto ({', '.join(cols)}) "
                          f"VALUES ({', '.join('?' * len(cols))})",
                          [datos[k] for k in datos] + [ahora])
                nuevos += 1
    c.commit()
    return nuevos, actualizados


def conciliar(c, dirs_descarga, ruta_indice):
    """Pone al día el ciclo de vida con lo que hay en disco y en el índice.

    Se necesita porque el catálogo llega después que los datos: hay 19.959 documentos
    bajados e indexados desde abril, y un log de descargas de julio. Sin esta pasada, el
    catálogo creería que no se bajó ni se indexó nada y volvería a pedir todo.
    """
    ahora = int(time.time())

    # --- descargados: por nombre de archivo en los directorios indicados ---
    presentes = set()
    for d in dirs_descarga:
        for ruta in glob.glob(os.path.join(d, '*.pdf')):
            presentes.add(os.path.basename(ruta))
    marcados = 0
    for fila in c.execute('SELECT id_archivo, archivo FROM acto WHERE descargado_en IS NULL').fetchall():
        if fila['archivo'] in presentes:
            c.execute('UPDATE acto SET descargado_en=? WHERE id_archivo=?', (ahora, fila['id_archivo']))
            marcados += 1

    # --- indexados: los que ya tienen fragmentos en el índice ---
    en_indice = set()
    if ruta_indice and os.path.exists(ruta_indice):
        with open(ruta_indice, encoding='utf-8') as f:
            for linea in f:
                ia = json.loads(linea).get('id_archivo')
                if ia:
                    en_indice.add(ia)
    indexados = 0
    if en_indice:
        for ia in en_indice:
            cur = c.execute('UPDATE acto SET indexado_en=?, descargado_en=COALESCE(descargado_en,?) '
                            'WHERE id_archivo=? AND indexado_en IS NULL', (ahora, ahora, ia))
            indexados += cur.rowcount
    c.commit()
    return marcados, indexados


def incorporar_log(c, ruta_log):
    """Toma el SHA-256 y el tamaño del log de descargas, que el disco no conserva."""
    if not os.path.exists(ruta_log):
        return 0
    n = 0
    with open(ruta_log, encoding='utf-8') as f:
        for linea in f:
            try:
                d = json.loads(linea)
            except ValueError:
                continue
            if d.get('estado') != 'ok' or not d.get('archivo'):
                continue
            cur = c.execute('UPDATE acto SET sha256=?, bytes=?, error=NULL WHERE archivo=? '
                            'AND (sha256 IS NULL OR sha256="")',
                            (d.get('sha256'), d.get('bytes'), d['archivo']))
            n += cur.rowcount
    c.commit()
    return n


def estado(c):
    t = lambda q: c.execute(q).fetchone()[0]
    return {
        'actos en el catálogo': t('SELECT COUNT(*) FROM acto'),
        'descargados': t('SELECT COUNT(*) FROM acto WHERE descargado_en IS NOT NULL'),
        'indexados': t('SELECT COUNT(*) FROM acto WHERE indexado_en IS NOT NULL'),
        'pendientes de descarga': t('SELECT COUNT(*) FROM acto WHERE descargado_en IS NULL'),
        'pendientes de indexar': t('SELECT COUNT(*) FROM acto '
                                   'WHERE descargado_en IS NOT NULL AND indexado_en IS NULL'),
        'con error': t('SELECT COUNT(*) FROM acto WHERE error IS NOT NULL AND error<>""'),
    }


def pendientes(c, etapa, limite=None):
    if etapa == 'descarga':
        q = 'SELECT * FROM acto WHERE descargado_en IS NULL ORDER BY fecha_acto DESC'
    elif etapa == 'indexar':
        q = ('SELECT * FROM acto WHERE descargado_en IS NOT NULL AND indexado_en IS NULL '
             'ORDER BY fecha_acto DESC')
    else:
        raise ValueError('etapa debe ser "descarga" o "indexar"')
    if limite:
        q += f' LIMIT {int(limite)}'
    return c.execute(q).fetchall()


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--catalogo', default=RUTA)
    sub = p.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('importar', help='incorporar la recolección del portal')
    s.add_argument('--metadatos', required=True)

    s = sub.add_parser('conciliar', help='poner al día el ciclo de vida con disco e índice')
    s.add_argument('--descargas', action='append', required=True,
                   help='directorio con PDF (repetible)')
    s.add_argument('--indice', default='indice/chunks.jsonl')
    s.add_argument('--log', default='data/descargas.jsonl')

    sub.add_parser('estado', help='resumen')

    s = sub.add_parser('pendientes', help='listar lo que falta')
    s.add_argument('--etapa', choices=('descarga', 'indexar'), required=True)
    s.add_argument('--limite', type=int)

    a = p.parse_args()
    c = bd(a.catalogo)

    if a.cmd == 'importar':
        nuevos, act = importar(c, a.metadatos)
        print(f'nuevos: {nuevos} · actualizados: {act}')
    elif a.cmd == 'conciliar':
        d, i = conciliar(c, a.descargas, a.indice)
        h = incorporar_log(c, a.log)
        print(f'marcados descargados: {d} · marcados indexados: {i} · con sha256 del log: {h}')
    elif a.cmd == 'estado':
        for k, v in estado(c).items():
            print(f'  {k:24s}: {v}')
    elif a.cmd == 'pendientes':
        for fila in pendientes(c, a.etapa, a.limite):
            print(f"  {fila['numero'] or fila['archivo']:32s} {fila['fecha_acto'] or '':11s} "
                  f"{(fila['seccion'] or '')[:34]}")


if __name__ == '__main__':
    main()
