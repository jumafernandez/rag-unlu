#!/usr/bin/env python3
"""Textos de licencia abierta (o con permiso) en español de la Argentina, para la capa nacional.

La normativa es más formal que el habla corriente; estas fuentes suman prosa institucional y
periodística sobre la vida universitaria, en lenguaje claro:
  argentina  argentina.gob.ar, Creative Commons Atribución 4.0. Páginas de educación y noticias
             con palabras del ámbito universitario en la dirección. El robots.txt pide 10 s entre
             pedidos y se respeta.
  conicet    conicet.gov.ar, Creative Commons Atribución 2.5 Argentina. Noticias con palabras del
             ámbito universitario en la dirección.
  cin        cin.edu.ar, Consejo Interuniversitario Nacional, con permiso del CIN. Noticias y
             páginas, por su API de WordPress.

Escribe `<dir>/<fuente>.jsonl` (una fila por página: fuente, url, título, fecha, licencia, texto,
fecha de descarga) y guarda el HTML crudo en `<dir>/<fuente>/` para no volver a pedirlo. Retoma
lo que falte.

Uso:
    python -m nacional.abiertas argentina|conicet|cin [--dir nacional/datos/abiertas] [--limite N]
"""
import argparse
import hashlib
import html
import json
import os
import re
import time
import urllib.request
from datetime import date
from html.parser import HTMLParser

AQUI = os.path.dirname(os.path.abspath(__file__))
AGENTE = {'User-Agent': 'Mozilla/5.0 (corpus-universitario-abierto)'}
LICENCIAS = {
    'argentina': 'Creative Commons Atribución 4.0 Internacional (argentina.gob.ar)',
    'conicet': 'Creative Commons Atribución 2.5 Argentina (CONICET)',
    'cin': 'Uso autorizado por el Consejo Interuniversitario Nacional (CIN)',
}
PAUSA = {'argentina': 10.0, 'conicet': 3.0, 'cin': 2.0}
CLAVES = re.compile(r'universi|educacion-superior|becas|progresar|titulo|docente|nodocente|conadu|fedun|fatun|'
                    r'coneau|posgrado|doctorado|investigador|estudiante|carrera|facultad|rector|consejo-interuniversitario',
                    re.I)


def pedir(url, intentos=4):
    for k in range(intentos):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=AGENTE), timeout=90).read()
        except Exception as e:
            if k == intentos - 1:
                raise
            time.sleep(10 * (k + 1))


# ---------------------------------------------------------------- HTML mínimo (sin dependencias)
VACIOS = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr'}
BLOQUE = {'p', 'div', 'br', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'tr', 'section', 'article', 'blockquote',
          'ul', 'ol', 'table', 'header', 'time'}
FUERA = {'script', 'style', 'nav', 'footer', 'button', 'form', 'noscript', 'svg', 'iframe'}


class Nodo:
    def __init__(self, tag, attrs, padre=None):
        self.tag, self.attrs, self.hijos, self.padre = tag, dict(attrs), [], padre

    def clases(self):
        return (self.attrs.get('class') or '').split()


class Arbol(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.raiz = self.actual = Nodo('raiz', [])

    def handle_starttag(self, tag, attrs):
        n = Nodo(tag, attrs, self.actual)
        self.actual.hijos.append(n)
        if tag not in VACIOS:
            self.actual = n

    def handle_endtag(self, tag):
        n = self.actual
        while n is not None and n.tag != tag:
            n = n.padre
        if n is not None and n.padre is not None:
            self.actual = n.padre

    def handle_data(self, data):
        self.actual.hijos.append(data)


def arbol(crudo):
    a = Arbol()
    a.feed(crudo.decode('utf-8', errors='replace') if isinstance(crudo, bytes) else crudo)
    return a.raiz


def buscar(n, pred):
    for h in n.hijos:
        if isinstance(h, Nodo):
            if pred(h):
                yield h
            yield from buscar(h, pred)


def primero(n, pred):
    return next(buscar(n, pred), None)


def texto(n, excluir=lambda x: False):
    partes = []

    def rec(x):
        if isinstance(x, str):
            partes.append(x)
            return
        if x.tag in FUERA or excluir(x):
            return
        if x.tag in BLOQUE:
            partes.append('\n')
        for h in x.hijos:
            rec(h)
        if x.tag in BLOQUE:
            partes.append('\n')
    rec(n)
    t = re.sub(r'[ \t\xa0]+', ' ', ''.join(partes))
    t = '\n'.join(l.strip() for l in t.splitlines())
    return re.sub(r'\n{3,}', '\n\n', t).strip()


# ---------------------------------------------------------------- fuentes
def urls_sitemap(indice, filtro, pausa):
    """Recorre un índice de sitemaps (o un sitemap simple) y devuelve las direcciones que pasan el filtro."""
    t = pedir(indice).decode('utf-8', errors='replace')
    locs = re.findall(r'<loc>([^<]+)</loc>', t)
    if '<sitemapindex' in t:
        res = []
        for s in locs:
            time.sleep(pausa)
            res += urls_sitemap(html.unescape(s), filtro, pausa)
        return res
    return [html.unescape(u) for u in locs if filtro(html.unescape(u))]


def filtro_argentina(u):
    ruta = u.split('argentina.gob.ar', 1)[-1]
    return ruta.startswith('/educacion') or bool(CLAVES.search(ruta))


def prioridad_argentina(u):
    ruta = u.split('argentina.gob.ar', 1)[-1]
    if ruta.startswith('/educacion/universidades') or 'universi' in ruta:
        return 0
    if ruta.startswith('/educacion'):
        return 1
    return 2 if '/noticias/' not in ruta else 3


def extraer_pagina(crudo, fuente):
    """Título, fecha y texto del contenido principal (sin menús, botones para compartir ni listados)."""
    raiz = arbol(crudo)
    ruido = {'social-share', 'pane-listado', 'breadcrumb', 'section-actions', 'news__main-image-container',
             'sharedaddy', 'jp-relatedposts', 'wp-block-buttons', 'post-navigation', 'comments-area'}
    principal = (primero(raiz, lambda x: x.tag == 'article') or primero(raiz, lambda x: x.tag == 'main')
                 or primero(raiz, lambda x: x.tag == 'body') or raiz)
    h1 = primero(principal, lambda x: x.tag == 'h1') or primero(raiz, lambda x: x.tag == 'h1')
    titulo = texto(h1) if h1 else ''
    t = primero(principal, lambda x: x.tag == 'time')
    fecha = (t.attrs.get('datetime') or texto(t)) if t else ''
    cuerpo = texto(principal, excluir=lambda x: bool(ruido & set(x.clases())) or x is h1)
    cuerpo = '\n'.join(l for l in cuerpo.splitlines() if not l.startswith('Compartir en'))
    return titulo, fecha, cuerpo.strip()


def bajar_paginas(fuente, urls, d, limite):
    carpeta = os.path.join(d, fuente)
    os.makedirs(carpeta, exist_ok=True)
    salida = os.path.join(d, f'{fuente}.jsonl')
    hechas = {json.loads(l)['url'] for l in open(salida, encoding='utf-8')} if os.path.exists(salida) else set()
    pendientes = [u for u in urls if u not in hechas][:limite or None]
    print(f'{fuente}: {len(pendientes)} páginas por bajar ({len(hechas)} ya estaban)', flush=True)
    out = open(salida, 'a', encoding='utf-8')
    for k, u in enumerate(pendientes, 1):
        cache = os.path.join(carpeta, hashlib.sha1(u.encode()).hexdigest()[:16] + '.html')
        fila = {'fuente': fuente, 'url': u, 'licencia': LICENCIAS[fuente], 'descargado': date.today().isoformat()}
        try:
            if os.path.exists(cache):
                crudo = open(cache, 'rb').read()
            else:
                crudo = pedir(u)
                open(cache, 'wb').write(crudo)
                time.sleep(PAUSA[fuente])
            fila['titulo'], fila['fecha'], fila['texto'] = extraer_pagina(crudo, fuente)
        except Exception as e:
            fila['error'] = str(e)[:200]
        out.write(json.dumps(fila, ensure_ascii=False) + '\n')
        out.flush()
        if k % 50 == 0:
            print(f'  {k}/{len(pendientes)}', flush=True)
    print(f'LISTO {fuente}', flush=True)


def argentina(d, limite):
    lista = os.path.join(d, 'argentina-urls.txt')
    if not os.path.exists(lista):
        urls = urls_sitemap('https://www.argentina.gob.ar/sitemap.xml', filtro_argentina, PAUSA['argentina'])
        urls = sorted(set(urls), key=lambda u: (prioridad_argentina(u), u))
        open(lista, 'w').write('\n'.join(urls) + '\n')
    urls = [l.strip() for l in open(lista) if l.strip()]
    bajar_paginas('argentina', urls, d, limite)


def conicet(d, limite):
    lista = os.path.join(d, 'conicet-urls.txt')
    if not os.path.exists(lista):
        urls = urls_sitemap('https://www.conicet.gov.ar/sitemap.xml',
                            lambda u: '/post-sitemap' in u or bool(CLAVES.search(u.split('conicet.gov.ar', 1)[-1])),
                            PAUSA['conicet'])
        urls = sorted({u for u in urls if 'sitemap' not in u})
        open(lista, 'w').write('\n'.join(urls) + '\n')
    urls = [l.strip() for l in open(lista) if l.strip()]
    bajar_paginas('conicet', urls, d, limite)


def cin(d, limite):
    """Por la API de WordPress: el contenido ya viene separado del resto de la página."""
    salida = os.path.join(d, 'cin.jsonl')
    hechas = {json.loads(l)['url'] for l in open(salida, encoding='utf-8')} if os.path.exists(salida) else set()
    out = open(salida, 'a', encoding='utf-8')
    n = 0
    for tipo in ('posts', 'pages'):
        pagina = 1
        while True:
            u = (f'https://www.cin.edu.ar/wp-json/wp/v2/{tipo}?per_page=100&page={pagina}'
                 '&_fields=id,date,modified,link,title,content')
            try:
                items = json.loads(pedir(u))
            except Exception:
                break   # la API responde 400 al pasar la última página
            if not items:
                break
            for it in items:
                if it['link'] in hechas:
                    continue
                cuerpo = texto(arbol(it['content']['rendered']))
                out.write(json.dumps({'fuente': 'cin', 'tipo': tipo[:-1], 'url': it['link'],
                                      'titulo': html.unescape(re.sub(r'<[^>]+>', '', it['title']['rendered'])),
                                      'fecha': it['date'], 'licencia': LICENCIAS['cin'],
                                      'descargado': date.today().isoformat(), 'texto': cuerpo},
                                     ensure_ascii=False) + '\n')
                n += 1
            out.flush()
            print(f'  {tipo} página {pagina}: {n} en total', flush=True)
            if limite and n >= limite:
                break
            pagina += 1
            time.sleep(PAUSA['cin'])
    print('LISTO cin', flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('fuente', choices=['argentina', 'conicet', 'cin'])
    ap.add_argument('--dir', default=os.path.join(AQUI, 'datos', 'abiertas'))
    ap.add_argument('--limite', type=int, default=0)
    a = ap.parse_args()
    os.makedirs(a.dir, exist_ok=True)
    {'argentina': argentina, 'conicet': conicet, 'cin': cin}[a.fuente](a.dir, a.limite)


if __name__ == '__main__':
    main()
