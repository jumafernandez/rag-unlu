"""Rescata los actos cuyo PDF no dio texto, usando el HTML que publica el portal.

Hay actos que el sistema tiene catalogados, descargados y procesados, y sobre los que sin
embargo no puede decir nada: el PDF es un escaneo sin texto legible, o la extracción
falló. En la UNLu son 353 sobre 21.452. No son un problema de calidad ---son documentos
que directamente no existen para quien consulta--- y ninguna mejora del ranking los va a
traer, porque no hay un solo fragmento suyo en el índice.

El portal publica, junto a cada documento, el cuerpo del acto en HTML (`atributos.contenido`).
Es texto del editor, no una extracción: viene limpio, sin encabezados de página
intercalados en medio de las oraciones. Se verificó que existe en las dos instalaciones
medidas ---UNLu y UNSL--- así que esto no es un parche para una universidad.

Lo que NO trae es los anexos, que en muchos actos son la parte sustantiva. Por eso el
orden es: manda el PDF, y esto entra solo cuando el PDF no dio nada. Un acto sin anexos
indexado es preferible a un acto ausente; un acto CON anexos extraído del PDF es
preferible a los dos.

    python -m pipeline.rescatar_html --metadatos scrapers/metadatos_nuevo.csv \\
        --actos faltantes.txt --salida data/rescatados

`--actos` es una lista de id_documento, uno por línea: la que produce la consulta de los
actos indexados sin fragmentos.
"""
import argparse
import csv
import html as _html
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                'scrapers'))
from conf import PORTAL_URL  # noqa: E402

API = PORTAL_URL.split('/sudocu/')[0] + '/sudocu/api'
TOPE = 15

# Las secciones del acto, tal como aparecen al principio de un párrafo. El fragmentador
# espera markdown con esta jerarquía (## Visto, ## Considerando, ### Artículo N), así que
# la conversión no es "HTML a texto" sino "HTML a la misma forma que produce el extractor
# de PDF": lo que sigue tiene que poder mezclarse con lo ya indexado sin distinguirse.
RE_VISTO = re.compile(r'^\s*VISTO\s*:?', re.IGNORECASE)
RE_CONSIDERANDO = re.compile(r'^\s*CONSIDERANDO\s*:?', re.IGNORECASE)
RE_ARTICULO = re.compile(r'^\s*ART[IÍ]CULO\s*(\d+)\s*[°ºª]?\s*[.\-:]?', re.IGNORECASE)
RE_ANEXO = re.compile(r'^\s*ANEXO\s*([IVXLC0-9]*)', re.IGNORECASE)
# El pie de firma digital, que cada instalación redacta distinto pero siempre marca.
RE_FIRMAS = re.compile(r'^\s*(Documento firmado digitalmente|Firmado digitalmente)',
                       re.IGNORECASE)


def parrafos_de_html(bruto):
    """El HTML del portal convertido a párrafos de texto plano.

    Se corta por `<p>` y `<br>` y NO por cualquier etiqueta: el editor parte una misma
    oración en varios `<span>` con formato, así que tratar toda etiqueta como salto de
    línea deja "Leonardo VARELA" en un renglón suelto y rompe las oraciones que después
    hay que fragmentar.
    """
    t = bruto or ''
    t = re.sub(r'(?i)<\s*br\s*/?>', '\n', t)
    t = re.sub(r'(?i)</\s*p\s*>', '\n\n', t)
    t = re.sub(r'<[^>]+>', '', t)
    t = _html.unescape(t)
    t = t.replace('\xa0', ' ')

    parrafos = []
    for crudo in re.split(r'\n\s*\n+', t):
        limpio = re.sub(r'[ \t]+', ' ', crudo.replace('\n', ' ')).strip()
        if limpio:
            parrafos.append(limpio)
    return parrafos


def markdown_del_acto(parrafos):
    """Los párrafos del acto con el esqueleto que espera el fragmentador.

    Devuelve (markdown, cuántas secciones se reconocieron). Lo segundo importa: si no se
    reconoció ninguna, el acto se convirtió en un bloque de texto sin estructura y
    conviene saberlo antes de meterlo al índice, no después.
    """
    lineas, reconocidas = [], 0
    en_firmas = False

    for p in parrafos:
        if RE_FIRMAS.match(p):
            lineas += ['', '## Firmas', '', p]
            reconocidas += 1
            en_firmas = True
            continue
        if en_firmas:
            lineas.append(p)
            continue

        m = RE_ARTICULO.match(p)
        if m:
            lineas += ['', f'### Artículo {m.group(1)}', '', p]
            reconocidas += 1
            continue
        if RE_VISTO.match(p):
            lineas += ['', '## Visto', '', p]
            reconocidas += 1
            continue
        if RE_CONSIDERANDO.match(p):
            lineas += ['', '## Considerando', '', p]
            reconocidas += 1
            continue
        m = RE_ANEXO.match(p)
        if m and len(p) < 120:
            lineas += ['', f'# Anexo {m.group(1)}'.strip(), '', p]
            reconocidas += 1
            continue
        lineas.append(p)

    md = re.sub(r'\n{3,}', '\n\n', '\n'.join(lineas)).strip() + '\n'
    return md, reconocidas


def canonico(fila, reconocidas):
    """La metadata estructural que acompaña al markdown.

    Va deliberadamente flaca: la identidad del acto ---código, número, fecha, título--- la
    pisa después `metadata_desde_catalogo` con la del portal, que es la autoritativa. Acá
    solo hace falta lo que el fragmentador necesita para armar el documento, más la marca
    de procedencia.
    """
    codigo = (fila.get('Codigo') or '').strip()
    nro, anio = (fila.get('Nro') or '').strip(), (fila.get('Anio') or '').strip()
    base = (fila.get('Archivo') or '').rsplit('.', 1)[0]
    return {
        'document_id': f'{codigo}_{nro}_{anio}'.lower() or base.lower(),
        'source_pdf': fila.get('Archivo') or f'{base}.pdf',
        # La procedencia queda escrita en el propio documento: un fragmento rescatado del
        # HTML no tiene anexos, y quien lo lea después tiene que poder saberlo sin
        # reconstruir cómo se generó.
        'source_system': 'portal_html',
        'document_type': 'disposicion' if codigo.upper().startswith('DIS') else 'resolucion',
        'document_code': codigo,
        'document_number': f'{nro}/{anio}' if nro and anio else '',
        'date_issued': _iso(fila.get('Fecha acto') or fila.get('Fecha')),
        'year': anio,
        'issuing_body': fila.get('Tipo de documento') or '',
        'titulo': fila.get('Titulo') or '',
        'has_annexes': False,
        'annex_count': 0,
        'secciones_reconocidas': reconocidas,
    }


def _iso(fecha):
    partes = (fecha or '').split('/')
    return f'{partes[2]}-{partes[1]}-{partes[0]}' if len(partes) == 3 else ''


def pedir(cid, offset, intentos=3):
    url = (f'{API}/mpd/guest/documentos?dir=DESC&idContenedor={cid}'
           f'&limit={TOPE}&limitOptions=10&limitOptions=15&offset={offset}&orden=nombre_plural')
    for n in range(intentos):
        try:
            p = urllib.request.Request(url, headers={'User-Agent': 'rag-unlu/1.0'})
            with urllib.request.urlopen(p, timeout=180) as r:
                cuerpo = r.read().decode('utf-8', 'replace').strip()
            return json.loads(cuerpo) if cuerpo else None
        except Exception:
            if n == intentos - 1:
                return None
            time.sleep(3 * (n + 1))
    return None


def escribir(destino, fila, parrafos):
    """Deja el par que el fragmentador consume: <base>.md y <base>_canonico.yaml."""
    import yaml

    base = (fila.get('Archivo') or '').rsplit('.', 1)[0]
    if not base:
        return None
    md, reconocidas = markdown_del_acto(parrafos)
    if not md.strip():
        return None
    sub = os.path.join(destino, base[:2].lower() or 'xx')
    os.makedirs(sub, exist_ok=True)
    with open(os.path.join(sub, f'{base}.md'), 'w', encoding='utf-8') as f:
        f.write(md)
    with open(os.path.join(sub, f'{base}_canonico.yaml'), 'w', encoding='utf-8') as f:
        yaml.safe_dump(canonico(fila, reconocidas), f, allow_unicode=True, sort_keys=False)
    return reconocidas


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--metadatos', required=True, help='catálogo CSV')
    p.add_argument('--actos',
                   help='archivo con un id_documento por línea, o "-" para leer de stdin')
    p.add_argument('--desde-catalogo', metavar='SQLITE',
                   help='toma los actos directamente de la base del catálogo: los que '
                        'figuran indexados y no aportaron ningún fragmento')
    p.add_argument('--salida', required=True, help='dir donde dejar los .md y _canonico.yaml')
    p.add_argument('--tope-paginas', type=int, default=2000)
    p.add_argument('--paciencia', type=int, default=25,
                   help='respuestas vacías seguidas antes de abandonar una carpeta')
    p.add_argument('--solo-medir', action='store_true',
                   help='no escribe nada: informa cuántos de los actos pedidos tienen '
                        'contenido en el portal y con cuánta estructura')
    a = p.parse_args()

    with open(a.metadatos, encoding='utf-8-sig') as f:
        catalogo = {r['id_documento']: r for r in csv.DictReader(f) if r.get('id_documento')}

    if a.desde_catalogo:
        # La lista sale de la propia base en vez de un archivo intermedio: quien corre
        # esto no tiene por qué saber la consulta, y un archivo con ids sueltos se
        # desactualiza en cuanto se vuelve a indexar.
        import sqlite3
        con = sqlite3.connect(f'file:{a.desde_catalogo}?mode=ro', uri=True)
        buscados = {f[0] for f in con.execute(
            'SELECT id_documento FROM acto '
            'WHERE indexado_en IS NOT NULL AND COALESCE(fragmentos, 0) = 0'
            '  AND id_documento IS NOT NULL')}
        con.close()
        print(f'{len(buscados)} actos figuran indexados sin haber aportado fragmentos',
              flush=True)
    elif a.actos:
        fuente = sys.stdin if a.actos == '-' else open(a.actos, encoding='utf-8')
        buscados = {l.strip() for l in fuente if l.strip()}
        if a.actos != '-':
            fuente.close()
    else:
        p.error('hace falta --actos o --desde-catalogo')

    if not buscados:
        print('no hay actos para rescatar')
        return 0

    conocidos = {i for i in buscados if i in catalogo}

    # Reanudable. Contra un portal que corta a mitad de camino, una corrida sola no alcanza
    # y hay que repetirla; sin esto cada repetición volvería a recorrer lo ya resuelto.
    if not a.solo_medir and os.path.isdir(a.salida):
        hechos = {f[:-3] for _, _, fs in os.walk(a.salida) for f in fs if f.endswith('.md')}
        antes = len(conocidos)
        conocidos = {i for i in conocidos
                     if (catalogo[i].get('Archivo') or '').rsplit('.', 1)[0] not in hechos}
        if antes != len(conocidos):
            print(f'{antes - len(conocidos)} ya estaban rescatados de una corrida anterior',
                  flush=True)
    if len(conocidos) < len(buscados):
        print(f'aviso: {len(buscados) - len(conocidos)} actos pedidos no están en el '
              f'catálogo; se ignoran', flush=True)

    # Se agrupan por carpeta para recorrer solo las que hacen falta, y se corta el recorrido
    # apenas se encontraron todos los actos buscados de esa carpeta. Sin eso, rescatar tres
    # documentos de Secretarías costaría paginar sus cinco mil.
    from recolectar_api import carpetas_del_portal, frontera_de_numero
    carpetas = carpetas_del_portal()
    por_nombre = {nombre: cid for cid, nombre in carpetas.items()}

    por_carpeta = {}
    for idd in conocidos:
        seccion = catalogo[idd].get('Seccion') or ''
        cid = por_nombre.get(seccion)
        if cid is None:
            print(f'aviso: sin carpeta para la sección {seccion!r}', flush=True)
            continue
        por_carpeta.setdefault(cid, set()).add(idd)

    if not a.solo_medir:
        os.makedirs(a.salida, exist_ok=True)
    rescatados = sin_contenido = no_encontrados = 0
    sin_estructura = []

    def procesar(docs, pendientes):
        """Los actos buscados que vengan en esta página, rescatados o medidos."""
        nonlocal rescatados, sin_contenido
        for x in docs:
            idd = x.get('id')
            if idd not in pendientes:
                continue
            pendientes.discard(idd)
            parrafos = parrafos_de_html((x.get('atributos') or {}).get('contenido'))
            if not parrafos:
                sin_contenido += 1
                continue
            if a.solo_medir:
                # Medir antes de escribir. La pregunta que decide si esto sirve es si los
                # actos SIN texto en el PDF tienen texto en el portal, y no hay motivo para
                # suponer que sí: un PDF escaneado sugiere un acto subido como archivo en
                # vez de redactado en el editor, que es justamente el caso donde `contenido`
                # viene vacío. En una muestra de 96 actos cualesquiera, 12 no tenían
                # contenido; sobre esta población podría ser mucho peor.
                _, rec = markdown_del_acto(parrafos)
                rescatados += 1
                if rec == 0:
                    sin_estructura.append(catalogo[idd].get('Archivo'))
                continue
            rec = escribir(a.salida, catalogo[idd], parrafos)
            if rec is None:
                sin_contenido += 1
                continue
            rescatados += 1
            if rec == 0:
                sin_estructura.append(catalogo[idd].get('Archivo'))

    def ir_derecho(cid, pendientes):
        """Salta a la posición de cada acto en vez de recorrer la carpeta hasta encontrarlo.

        El portal ordena el listado por número de acto, así que la posición de un acto se
        puede ubicar por búsqueda binaria en vez de paginando desde el principio. La
        diferencia no es de estilo: los actos a rescatar suelen ser de este año, o sea de
        número bajo, o sea del final del listado. Rescatar tres disposiciones de Ciencias
        Sociales ---números 40, 52 y 53--- costaba recorrer 177 páginas de las 178 que tiene
        la carpeta, contra un portal que corta las consultas pesadas a los 20 segundos.

        No reemplaza al recorrido completo: lo que no aparezca acá lo sigue buscando el
        recorrido lineal, que es el que cubre los casos raros ---un acto sin número, o una
        carpeta que dejó de venir ordenada---.
        """
        primera = (pedir(cid, 0) or {}).get('documents') or []
        if not primera:
            return
        try:
            total = int(primera[0].get('total') or 0)
        except (TypeError, ValueError):
            total = 0
        procesar(primera, pendientes)
        if not total:
            return

        # De mayor a menor número: así el recorrido va hacia el fondo del listado y las
        # búsquedas sucesivas caen cerca unas de otras.
        con_numero = []
        for idd in list(pendientes):
            try:
                n = int(catalogo[idd].get('Nro') or 0)
            except (TypeError, ValueError):
                n = 0
            if n:
                con_numero.append((n, idd))
        for n, idd in sorted(con_numero, reverse=True):
            if idd not in pendientes:
                continue                      # apareció buscando a otro
            frontera = frontera_de_numero(cid, n, total)
            if frontera is None:
                return                        # el portal dejó de contestar: al lineal
            arranque = (frontera // TOPE) * TOPE
            # Dos páginas desde la frontera: el acto está entre los que comparten su número
            # ---uno por año--- y esos entran de sobra en treinta registros.
            for salto in (0, TOPE):
                if idd not in pendientes:
                    break
                docs = (pedir(cid, arranque + salto) or {}).get('documents') or []
                if docs:
                    procesar(docs, pendientes)

    for cid, pendientes in sorted(por_carpeta.items()):
        nombre = carpetas.get(cid, str(cid))
        print(f'\n=== [{cid}] {nombre}: {len(pendientes)} actos a rescatar ===', flush=True)
        ir_derecho(cid, pendientes)
        if pendientes:
            print(f'  {len(pendientes)} no aparecieron en su posición; se recorre la '
                  f'carpeta', flush=True)
        offset, vacias = 0, 0
        while pendientes and offset < a.tope_paginas * TOPE:
            d = pedir(cid, offset)
            docs = (d or {}).get('documents') or []
            if not docs:
                # El portal corta las consultas pesadas a los 20 segundos y devuelve un
                # 200 vacío. Con poca paciencia el recorrido se abandona a mitad de las
                # carpetas grandes ---que son justamente donde están el 90% de los actos a
                # rescatar--- y el resultado parece "no se pudieron recuperar" cuando en
                # realidad fue "no llegué a mirarlos".
                vacias += 1
                if vacias >= a.paciencia:
                    print(f'  abandono en offset {offset}: {a.paciencia} respuestas '
                          f'vacías seguidas', flush=True)
                    break
                time.sleep(min(5 * vacias, 45))
                continue
            vacias = 0
            procesar(docs, pendientes)
            offset += TOPE
            if offset % (TOPE * 40) == 0:
                print(f'  offset {offset} · quedan {len(pendientes)}', flush=True)
        no_encontrados += len(pendientes)

    verbo = 'con contenido' if a.solo_medir else 'rescatados'
    print(f'\n=== {verbo} {rescatados} · sin contenido en el portal {sin_contenido} · '
          f'no encontrados {no_encontrados} ===')
    pedidos = rescatados + sin_contenido + no_encontrados
    if pedidos:
        print(f'techo del rescate: {100 * rescatados / pedidos:.0f}% de los actos pedidos')
    if sin_estructura:
        # Un acto sin ninguna sección reconocida entra al índice como un bloque de texto
        # plano. Sirve ---es mejor que no estar--- pero conviene saber cuáles son.
        print(f'{len(sin_estructura)} quedaron sin estructura reconocida (bloque único):')
        for x in sin_estructura[:10]:
            print(f'   {x}')
    return 0 if rescatados else 1


if __name__ == '__main__':
    sys.exit(main())
