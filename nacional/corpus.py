#!/usr/bin/env python3
"""Corpus de la capa nacional a partir de lo que bajó `infoleg.py` y del OCR de los anexos.

Pasos:
  corpus      Dataset de normativa (dataset/normativa.jsonl): un registro por norma curada con
              sus atributos de Infoleg, el texto completo, los anexos con su procedencia
              (texto del PDF, OCR o ambos) y las relaciones con otras normas.
  documentos  Un documento por texto principal de cada norma y por cada anexo (los que vienen
              dentro del HTML y los PDF), con su ficha, su índice de títulos y capítulos y su
              preámbulo (visto y considerandos). Escribe documentos.jsonl.
  fragmentos  Parte cada documento por su estructura: artículos consecutivos del mismo título
              y capítulo hasta --caracteres, sin cortar nunca un artículo (salvo que él solo
              pase el máximo); sin artículos, por párrafos. Escribe fragmentos.jsonl.
  fichas      Una ficha por norma curada, armada sin modelo: identificación, resumen de
              Infoleg y relaciones con otras normas (qué modifica y qué la modifica), desde
              las tablas de vínculos de la base. Escribe fichas.jsonl.

Uso:
    python -m nacional.corpus corpus     [--dir nacional/datos] [--ocr DIR]
    python -m nacional.corpus documentos [--dir ...]
    python -m nacional.corpus fragmentos [--dir ...] [--caracteres 7000]
    python -m nacional.corpus fichas     [--dir ...]
"""
import argparse
import csv
import glob
import json
import os
import re
import sys
from collections import defaultdict

csv.field_size_limit(sys.maxsize)
AQUI = os.path.dirname(os.path.abspath(__file__))

# Encabezados que Infoleg repite en el HTML de cada norma y que no son parte del texto.
RUIDO = re.compile(r'^(infoleg\b.*|ministerio de (justicia|econom[ií]a)[^\n]{0,60}argentina)$', re.I)


def limpiar(t):
    lineas = [l for l in (t or '').splitlines() if not RUIDO.match(l.strip())]
    t = '\n'.join(lineas)
    return re.sub(r'\n\s*\n\s*\n+', '\n\n', t).strip()


def leer_jsonl(ruta):
    return [json.loads(l) for l in open(ruta, encoding='utf-8')] if os.path.exists(ruta) else []


def curadas(d):
    return {r['id_infoleg']: r for r in csv.DictReader(open(os.path.join(AQUI, 'curadas.csv'), encoding='utf-8'))}


# ---------------------------------------------------------------- corpus
def corpus(d, dir_ocr):
    """Dataset de normativa: un registro por norma curada con sus atributos de Infoleg, el texto
    completo, los anexos (con la procedencia de cada uno: texto del PDF, OCR o ambos) y las
    relaciones con otras normas. Es lo que se publica y de donde sale todo lo demás."""
    import fitz
    from datetime import date
    cur = curadas(d)
    normas = {r['id']: r for r in leer_jsonl(os.path.join(d, 'normas.jsonl'))}
    base = {}
    with open(os.path.join(d, 'normas.csv'), encoding='utf-8-sig', errors='replace', newline='') as f:
        for r in csv.DictReader(f):
            base[r['id_norma']] = r
    modifica, modificada_por = defaultdict(set), defaultdict(set)
    with open(os.path.join(d, 'modificadas.csv'), encoding='utf-8-sig', errors='replace', newline='') as f:
        for r in csv.DictReader(f):
            x, y = r.get('id_norma_modificada'), r.get('id_norma_modificatoria')
            if x and y and x in base and y in base:
                modificada_por[x].add(y)
                modifica[y].add(x)
    version_base = json.load(open(os.path.join(d, 'base.json'))).get('modificado', '') if os.path.exists(
        os.path.join(d, 'base.json')) else ''
    ocr = {}
    for r in (json.loads(l) for f in glob.glob(os.path.join(dir_ocr or '', 'parte_*.jsonl')) for l in open(f)):
        if r.get('origen') == 'ocr':
            ocr[(os.path.basename(r['pdf']), r['pagina'])] = r['texto']
    anexos = defaultdict(list)
    for x in leer_jsonl(os.path.join(d, 'anexos.jsonl')):
        if x['id'] in cur and not x.get('error'):
            anexos[x['id']].append(x['url'])
    os.makedirs(os.path.join(d, 'dataset'), exist_ok=True)
    out = open(os.path.join(d, 'dataset', 'normativa.jsonl'), 'w', encoding='utf-8')
    n_ocr = 0
    for i, c in cur.items():
        n, b = normas.get(i, {}), base.get(i, {})
        regs = []
        for url in anexos.get(i, []):
            nombre = f'{i}-{os.path.basename(url)}'
            ruta = os.path.join(d, 'pdf', nombre)
            if not os.path.exists(ruta):
                continue
            paginas, de_ocr = [], 0
            with fitz.open(ruta) as doc:
                total = len(doc)
                for k, p in enumerate(doc):
                    propio = p.get_text().strip()
                    if len(propio) < 200 and (nombre, k) in ocr:
                        propio, de_ocr = ocr[(nombre, k)], de_ocr + 1
                    if propio and propio != '[página sin texto]':
                        paginas.append(propio)
            n_ocr += de_ocr
            origen = ('ocr' if de_ocr and de_ocr == total else 'pdf+ocr' if de_ocr else
                      'pdf' if paginas else 'sin texto')
            regs.append({'url': url, 'paginas': total, 'paginas_ocr': de_ocr, 'origen': origen,
                         'texto': limpiar('\n\n'.join(paginas))})
        out.write(json.dumps({
            'id_infoleg': i, 'tipo': c['tipo'], 'numero': c['numero'], 'anio': c['anio'],
            'fecha_sancion': b.get('fecha_sancion') or None, 'fecha_boletin': b.get('fecha_boletin') or None,
            'numero_boletin': b.get('numero_boletin') or None, 'organismo': c['organismo'],
            'titulo': c['titulo'], 'titulo_sumario': b.get('titulo_sumario') or None,
            'resumen_infoleg': b.get('texto_resumido') or None, 'grupo': c['grupo'],
            'motivo_inclusion': c['motivo'], 'enlace_infoleg': c['enlace'],
            'version_texto': n.get('version') if n.get('texto') else None, 'texto_url': n.get('url') or None,
            'texto': limpiar(n.get('texto', '')), 'anexos': regs,
            'modifica': sorted(modifica[i], key=int), 'modificada_por': sorted(modificada_por[i], key=int),
            'licencia': 'Creative Commons Atribución 4.0 (Base Infoleg, Ministerio de Justicia de la Nación)',
            'fuente': f'Base Infoleg de Normativa Nacional, datos.jus.gob.ar (versión {version_base[:10]})',
            'ocr': 'Qwen3.5-9B, transcripción de páginas escaneadas' if any(r['paginas_ocr'] for r in regs) else None,
            'descargado': date.today().isoformat()}, ensure_ascii=False) + '\n')
    print(f'{len(cur)} normas en dataset/normativa.jsonl; {n_ocr} páginas de anexos tomadas del OCR')


# ---------------------------------------------------------------- fragmentos
def partir(texto, maximo):
    """Párrafos acumulados hasta `maximo` caracteres; un párrafo más largo se corta por oraciones."""
    piezas = []
    for par in re.split(r'\n\s*\n', texto):
        par = par.strip()
        if len(par) <= maximo:
            piezas.append(par)
            continue
        actual = ''
        for o in re.split(r'(?<=[.;:])\s+', par):
            if actual and len(actual) + len(o) + 1 > maximo:
                piezas.append(actual)
                actual = ''
            actual = f'{actual} {o}'.strip()
        if actual:
            piezas.append(actual)
    frag, actual = [], ''
    for p in piezas:
        if actual and len(actual) + len(p) + 2 > maximo:
            frag.append(actual)
            actual = ''
        actual = f'{actual}\n\n{p}'.strip()
    if actual:
        frag.append(actual)
    return [f for f in frag if len(f) >= 300]


# ---------------------------------------------------------------- estructura
# Un artículo empieza en una línea que arranca con "ARTICULO 5º", "Artículo 5.-", "Art. 5°"…
# Infoleg corta las líneas a mitad de oración, así que una línea que empieza con "artículo 29"
# puede ser la continuación de un párrafo ("…lo dispuesto en el\nartículo 29 de la Ley…"): solo
# cuenta como comienzo de artículo si va en mayúscula inicial y la línea anterior cerró la
# oración (o era un encabezado o una línea vacía).
RE_ARTICULO = re.compile(r'^\s*(?:ART[IÍ]CULO|Art[ií]culo|ART\.|Art\.)\s*(\d+|[IVXLC]+\b|[ÚU]NICO|[ÚU]nico)'
                         r'\s*(?:º|°|o\b)?\s*(bis|ter|qu[aá]ter|quinquies|sexies)?', re.I)
RE_ENCABEZADO = re.compile(r'^\s*(t[ií]tulo|cap[ií]tulo|secci[oó]n|parte|libro)\s+([ivxlcdm]+|\d+|[úu]nico|preliminar|'
                           r'primer[oa]?|segund[oa]|tercer[oa]?|cuart[oa]|quint[oa]|sext[oa]|s[eé]ptim[oa]|octav[oa]|'
                           r'noven[oa]|d[eé]cim[oa])\b\.?\s*[-–—:.]?\s*(.*)$', re.I)
RE_ANEXO = re.compile(r'^\s*anexo\s*([ivxlc]+|\d+|[úu]nico|[a-h])?\s*[-–—:.]?\s*$', re.I)
NIVEL = {'libro': 0, 'parte': 0, 'titulo': 1, 'capitulo': 2, 'seccion': 3}


def sin_tildes(t):
    return t.lower().translate(str.maketrans('áéíóú', 'aeiou'))


def es_nombre(linea):
    """La línea siguiente a "TITULO I" suele ser su nombre ("Disposiciones preliminares")."""
    l = linea.strip()
    return 0 < len(l) <= 120 and not RE_ARTICULO.match(l) and not RE_ENCABEZADO.match(l) and not l.endswith(',')


def partir_anexos(texto):
    """Separa el texto principal de los anexos que vienen dentro del mismo HTML ("ANEXO I" en su línea)."""
    partes, etiqueta, actual = [], None, []
    for l in texto.splitlines():
        m = RE_ANEXO.match(l)
        if m:
            partes.append((etiqueta, '\n'.join(actual).strip()))
            etiqueta, actual = (m.group(1) or '').upper() or 'único', []
            continue
        actual.append(l)
    partes.append((etiqueta, '\n'.join(actual).strip()))
    return [(e, t) for e, t in partes if t]


def unidades(texto):
    """Preámbulo, artículos y encabezados, en orden. Cada artículo lleva el camino de títulos y capítulos."""
    camino, res, actual = {}, [], None
    lineas = texto.splitlines()
    previa = ''
    i = 0
    while i < len(lineas):
        l = lineas[i].strip()
        i += 1
        if not l:
            if actual:
                actual['lineas'].append('')
            previa = ''
            continue
        m = RE_ENCABEZADO.match(l)
        if m and len(l) < 150 and (not previa or previa[-1] in '.:;'):
            nombre_enc = m.group(3).strip()
            j = i
            while j < len(lineas) and not lineas[j].strip():   # el nombre puede venir tras una línea vacía
                j += 1
            if not nombre_enc and j < len(lineas) and es_nombre(lineas[j]):
                nombre_enc = lineas[j].strip()
                i = j + 1
            nivel = NIVEL[sin_tildes(m.group(1))]
            camino = {k: v for k, v in camino.items() if k < nivel}
            camino[nivel] = f'{m.group(1).upper()} {m.group(2).upper()}' + (f' ({nombre_enc})' if nombre_enc else '')
            actual = None
            previa = '.'
            continue
        m = RE_ARTICULO.match(l)
        if m and l[0].isupper() and (not previa or previa[-1] in '.:;'):
            etiqueta = f'Art. {m.group(1)}' + (f' {m.group(2).lower()}' if m.group(2) else '')
            actual = {'tipo': 'articulo', 'etiqueta': etiqueta, 'camino': [camino[k] for k in sorted(camino)],
                      'lineas': [l]}
            res.append(actual)
        elif actual is None:
            actual = {'tipo': 'preambulo' if not any(u['tipo'] == 'articulo' for u in res) else 'texto',
                      'etiqueta': '', 'camino': [camino[k] for k in sorted(camino)], 'lineas': [l]}
            res.append(actual)
        else:
            actual['lineas'].append(l)
        previa = l
    for u in res:
        u['texto'] = re.sub(r'\n\s*\n\s*\n+', '\n\n', '\n'.join(u.pop('lineas'))).strip()
    return [u for u in res if u['texto']]


def agrupar(us, maximo):
    """Artículos consecutivos del mismo camino, hasta `maximo` caracteres; nunca corta un artículo salvo
    que él solo pase el máximo (entonces se parte por párrafos)."""
    grupos, actual = [], None
    for u in us:
        piezas = [u['texto']] if len(u['texto']) <= maximo else partir(u['texto'], maximo)
        for k, p in enumerate(piezas):
            et = u['etiqueta'] + (f' (parte {k + 1})' if len(piezas) > 1 else '')
            mismo = actual and actual['camino'] == u['camino'] and actual['tipo'] == u['tipo']
            if mismo and len(actual['texto']) + len(p) + 2 <= maximo:
                actual['texto'] += '\n\n' + p
                actual['etiquetas'].append(et)
            else:
                actual = {'tipo': u['tipo'], 'camino': u['camino'], 'etiquetas': [et], 'texto': p}
                grupos.append(actual)
    return grupos


def indice(us):
    vistos = []
    for u in us:
        for c in u['camino']:
            if c not in vistos:
                vistos.append(c)
    return vistos


def documentos(d):
    """Un documento por texto principal de cada norma y por cada anexo (del HTML o de los PDF)."""
    fichas_ = {r['id']: r['ficha'] for r in leer_jsonl(os.path.join(d, 'fichas.jsonl'))}
    out = open(os.path.join(d, 'documentos.jsonl'), 'w', encoding='utf-8')
    n = 0
    for doc in leer_jsonl(os.path.join(d, 'dataset', 'normativa.jsonl')):
        doc['id'] = doc['id_infoleg']
        norma = f'{doc["tipo"]} {doc["numero"]}/{doc["anio"]}'
        de_la = 'del' if doc['tipo'].lower().startswith('decreto') else 'de la'
        partes = partir_anexos(doc['texto']) if doc['texto'] else [(None, '')]
        textos_pdf = [x['texto'] for x in doc['anexos'] if x['texto']]
        partes += [(str(k + 1 + sum(1 for e, _ in partes if e)), t) for k, t in enumerate(textos_pdf)]
        for k, (etiqueta, texto) in enumerate(partes):
            us = unidades(texto) if texto else []
            pre = next((u['texto'] for u in us if u['tipo'] == 'preambulo'), '')
            nombre_doc = norma if etiqueta is None else f'Anexo {etiqueta} {de_la} {norma}'
            out.write(json.dumps({'id_doc': f'{doc["id"]}-{k}', 'id': doc['id'], 'norma': norma, 'nombre': nombre_doc,
                                  'es_anexo': etiqueta is not None, 'titulo': doc['titulo'], 'grupo': doc['grupo'],
                                  'ficha': fichas_.get(doc['id'], ''), 'indice': indice(us),
                                  'preambulo': pre[:3000], 'articulos': sum(1 for u in us if u['tipo'] == 'articulo'),
                                  'texto': texto}, ensure_ascii=False) + '\n')
            n += 1
    print(f'{n} documentos')


def fragmentos(d, maximo):
    out = open(os.path.join(d, 'fragmentos.jsonl'), 'w', encoding='utf-8')
    n = con_estructura = 0
    for doc in leer_jsonl(os.path.join(d, 'documentos.jsonl')):
        if not doc['texto']:
            continue
        us = unidades(doc['texto'])
        if any(u['tipo'] == 'articulo' for u in us):
            grupos = agrupar(us, maximo)
            con_estructura += 1
        else:
            grupos = [{'tipo': 'texto', 'camino': [], 'etiquetas': [], 'texto': p} for p in partir(doc['texto'], maximo)]
        for k, g in enumerate(grupos):
            if g['tipo'] != 'articulo' and len(g['texto']) < 200:   # un artículo corto se queda
                continue
            ets = [e for e in g['etiquetas'] if e]
            out.write(json.dumps({'id_fragmento': f'{doc["id_doc"]}-{k:03d}', 'id_doc': doc['id_doc'], 'id': doc['id'],
                                  'documento': doc['nombre'], 'norma': doc['norma'], 'titulo': doc['titulo'],
                                  'tipo': g['tipo'], 'ubicacion': ' › '.join(g['camino']),
                                  'articulos': (ets[0] if len(ets) == 1 else f'{ets[0]} a {ets[-1]}') if ets else '',
                                  'texto': g['texto']}, ensure_ascii=False) + '\n')
            n += 1
    print(f'{n} fragmentos; {con_estructura} documentos con artículos')


# ---------------------------------------------------------------- fichas
MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre',
         'noviembre', 'diciembre']


def fecha_es(iso):
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', iso or '')
    return f'{int(m.group(3))} de {MESES[int(m.group(2)) - 1]} de {m.group(1)}' if m else iso


def nombre(f):
    anio = (f.get('fecha_sancion') or '')[:4]
    return f'{f["tipo_norma"]} {f["numero_norma"]}' + (f'/{anio}' if anio else '')


def fichas(d):
    cur = curadas(d)
    base = {}
    with open(os.path.join(d, 'normas.csv'), encoding='utf-8-sig', errors='replace', newline='') as f:
        for r in csv.DictReader(f):
            base[r['id_norma']] = r
    modifica, modificada_por = defaultdict(set), defaultdict(set)
    with open(os.path.join(d, 'modificadas.csv'), encoding='utf-8-sig', errors='replace', newline='') as f:
        for r in csv.DictReader(f):
            a, b = r.get('id_norma_modificada'), r.get('id_norma_modificatoria')
            if a and b:
                modificada_por[a].add(b)
                modifica[b].add(a)
    out = open(os.path.join(d, 'fichas.jsonl'), 'w', encoding='utf-8')
    for i, c in cur.items():
        b = base.get(i, {})
        lineas = [f'{nombre(b) if b else c["tipo"] + " " + c["numero"]}.',
                  f'Organismo: {(b.get("organismo_origen") or c["organismo"]).strip()}.']
        if b.get('fecha_sancion'):
            lineas.append(f'Fecha de sanción: {fecha_es(b["fecha_sancion"])}.')
        if b.get('fecha_boletin'):
            lineas.append(f'Publicada en el Boletín Oficial el {fecha_es(b["fecha_boletin"])}.')
        if (b.get('titulo_resumido') or c['titulo']).strip():
            lineas.append(f'Título: {(b.get("titulo_resumido") or c["titulo"]).strip().capitalize()}.')
        if (b.get('texto_resumido') or '').strip():
            lineas.append(f'Resumen: {b["texto_resumido"].strip().capitalize()}')
        for etiqueta, conj in (('Modifica o complementa a', modifica[i]), ('Es modificada o complementada por', modificada_por[i])):
            otras = sorted((base[j] for j in conj if j in base), key=lambda r: r.get('fecha_sancion') or '')
            if otras:
                muestra = '; '.join(f'{nombre(r)} ({(r.get("titulo_resumido") or "").strip().lower()})' for r in otras[:40])
                extra = f' y {len(otras) - 40} más' if len(otras) > 40 else ''
                lineas.append(f'{etiqueta}: {muestra}{extra}.')
        out.write(json.dumps({'id': i, 'norma': nombre(b) if b else f'{c["tipo"]} {c["numero"]}',
                              'ficha': '\n'.join(lineas)}, ensure_ascii=False) + '\n')
    print(f'{len(cur)} fichas')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('paso', choices=['corpus', 'documentos', 'fragmentos', 'fichas'])
    ap.add_argument('--dir', default=os.path.join(AQUI, 'datos'))
    ap.add_argument('--ocr', default=os.path.join(AQUI, 'datos', 'ocr'))
    ap.add_argument('--caracteres', type=int, default=7000)
    a = ap.parse_args()
    {'corpus': lambda: corpus(a.dir, a.ocr), 'documentos': lambda: documentos(a.dir),
     'fragmentos': lambda: fragmentos(a.dir, a.caracteres), 'fichas': lambda: fichas(a.dir)}[a.paso]()


if __name__ == '__main__':
    main()
