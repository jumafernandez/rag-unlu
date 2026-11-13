"""Copia congelada del índice y del catálogo, con su manifiesto.

Existe para poder decir "así estaba el sistema antes de X" y volver ahí si hace falta. El
caso que lo motivó: incorporar el digesto histórico (116.895 actos, siete veces el corpus)
al índice del portal. Antes de mezclar hay que guardar el estado "solo SUDOCU" con su fecha
de corte, porque la actualización incremental va a tener que retomar desde ese punto.

Copia lo que hace falta para reconstruir el servicio tal cual estaba ---los artefactos del
índice, la base del catálogo, el CSV de metadatos y los totales recordados--- a un directorio
fechado dentro de `datos/respaldos/`, que es donde el servicio tiene permiso de escritura. Y
deja un MANIFIESTO con lo que un humano necesita para saber qué es esta copia sin abrirla:
cuántos documentos, hasta qué fecha llega la normativa, cuándo se generó el índice, de qué
fuente vienen los fragmentos (portal, HTML rescatado, digesto histórico) y el hash de cada
archivo.

    python -m pipeline.respaldar [--etiqueta sudocu]
"""
import argparse, datetime, hashlib, json, os, shutil, sqlite3, sys, collections

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sha256(ruta, bloque=1 << 20):
    h = hashlib.sha256()
    with open(ruta, 'rb') as f:
        for trozo in iter(lambda: f.read(bloque), b''):
            h.update(trozo)
    return h.hexdigest()


def resumen_del_indice(indice):
    """Documentos, fechas y procedencia, leídos del propio índice."""
    r = {}
    try:
        with open(os.path.join(indice, 'indice.json'), encoding='utf-8') as f:
            r['indice.json'] = json.load(f)
    except Exception:
        pass
    sq = os.path.join(indice, 'chunks.sqlite')
    if os.path.exists(sq):
        c = sqlite3.connect(f'file:{sq}?mode=ro', uri=True)
        r['fragmentos'] = c.execute('SELECT COUNT(*) FROM chunk').fetchone()[0]
        r['documentos'] = c.execute('SELECT COUNT(DISTINCT documento) FROM chunk').fetchone()[0]
        r['fecha_maxima_acto'] = c.execute("SELECT MAX(date_issued) FROM chunk WHERE LENGTH(date_issued)=10").fetchone()[0]
        r['fecha_minima_acto'] = c.execute("SELECT MIN(date_issued) FROM chunk WHERE LENGTH(date_issued)=10").fetchone()[0]
        cols = {x[1] for x in c.execute('PRAGMA table_info(chunk)')}
        if 'source_system' in cols:
            r['fragmentos_por_procedencia'] = dict(c.execute(
                "SELECT COALESCE(source_system,'?'), COUNT(*) FROM chunk GROUP BY 1").fetchall())
        c.close()
    return r


def resumen_del_catalogo(ruta):
    r = {}
    if os.path.exists(ruta):
        c = sqlite3.connect(f'file:{ruta}?mode=ro', uri=True)
        r['actos'] = c.execute('SELECT COUNT(*) FROM acto').fetchone()[0]
        r['indexados'] = c.execute('SELECT COUNT(*) FROM acto WHERE indexado_en IS NOT NULL').fetchone()[0]
        u = c.execute('SELECT MAX(indexado_en) FROM acto').fetchone()[0]
        # En el catálogo es un instante en segundos; acá se guarda legible.
        r['ultimo_indexado_en'] = datetime.datetime.fromtimestamp(u).isoformat(timespec='minutes') if u else None
        c.close()
    return r


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--etiqueta', default='sudocu', help='qué es esta copia (sudocu, sudocu+digesto...)')
    p.add_argument('--destino', default='datos/respaldos')
    a = p.parse_args()

    indice = os.path.join(RAIZ, os.environ.get('RAG_INDICE', 'indice'))
    catalogo = os.path.join(RAIZ, os.environ.get('RAG_CATALOGO', 'datos/catalogo.sqlite'))
    csv_meta = os.path.join(RAIZ, 'scrapers', 'metadatos_nuevo.csv')
    totales = os.path.join(RAIZ, 'scrapers', 'totales.json')

    sello = datetime.datetime.now().strftime('%Y-%m-%d-%H%M')
    destino = os.path.join(RAIZ, a.destino, f'{a.etiqueta}-{sello}')
    os.makedirs(destino, exist_ok=False)
    print(f'respaldo: {destino}', flush=True)

    libre = shutil.disk_usage(RAIZ).free
    necesario = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(indice) for f in fs) if os.path.isdir(indice) else 0
    necesario += sum(os.path.getsize(x) for x in (catalogo, csv_meta, totales) if os.path.exists(x))
    print(f'a copiar: {necesario/1e9:.2f} GB · libre: {libre/1e9:.2f} GB', flush=True)
    if necesario * 1.05 > libre:
        raise SystemExit('no hay espacio suficiente en disco para el respaldo: no se copia nada')

    manifiesto = {'etiqueta': a.etiqueta, 'creado': sello, 'archivos': {}}
    # El índice entero, tal cual: son los artefactos que el servicio abre al arrancar.
    if os.path.isdir(indice):
        shutil.copytree(indice, os.path.join(destino, 'indice'), ignore=shutil.ignore_patterns('*.antes-de-fusionar'))
        manifiesto['indice'] = resumen_del_indice(indice)
        print('  índice copiado', flush=True)
    for origen, nombre in ((catalogo, 'catalogo.sqlite'), (csv_meta, 'metadatos_nuevo.csv'), (totales, 'totales.json')):
        if os.path.exists(origen):
            shutil.copy2(origen, os.path.join(destino, nombre))
            print(f'  {nombre} copiado', flush=True)
    manifiesto['catalogo'] = resumen_del_catalogo(catalogo)

    for r, _, fs in os.walk(destino):
        for f in fs:
            ruta = os.path.join(r, f)
            manifiesto['archivos'][os.path.relpath(ruta, destino)] = {'bytes': os.path.getsize(ruta), 'sha256': sha256(ruta)}
    with open(os.path.join(destino, 'MANIFIESTO.json'), 'w', encoding='utf-8') as f:
        json.dump(manifiesto, f, ensure_ascii=False, indent=1, sort_keys=True)

    i, c = manifiesto.get('indice', {}), manifiesto.get('catalogo', {})
    print('\n=== manifiesto ===')
    print(f"  documentos en el índice : {i.get('documentos')} · fragmentos {i.get('fragmentos')}")
    print(f"  actos del catálogo      : {c.get('actos')} · indexados {c.get('indexados')} · último indexado {c.get('ultimo_indexado_en')}")
    print(f"  normativa               : de {i.get('fecha_minima_acto')} a {i.get('fecha_maxima_acto')}")
    print(f"  procedencia             : {i.get('fragmentos_por_procedencia')}")
    print(f"  total copiado           : {sum(v['bytes'] for v in manifiesto['archivos'].values())/1e9:.2f} GB en {len(manifiesto['archivos'])} archivos")
    print(f'=== respaldo listo en {destino} ===')


if __name__ == '__main__':
    main()
