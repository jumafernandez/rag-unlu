#!/usr/bin/env python3
"""Normativa nacional desde Infoleg, sin listas armadas a mano.

Tres pasos, cada uno reanudable y reutilizable:

  base       Baja la Base Infoleg de Normativa Nacional del portal de datos abiertos del
             Ministerio de Justicia (datos.jus.gob.ar, API CKAN): la tabla de normas y las
             dos tablas de vínculos (qué norma modifica a cuál). Se actualiza cada mes; las
             URL se piden a la API, no están fijas en el código.
  seleccion  Elige las normas según los criterios de un archivo JSON (palabras en título y
             resumen, organismos, normas semilla y expansión por vínculos) y escribe
             seleccion.csv con el motivo de cada elección.
  planilla   Arma curacion.xlsx para la curación a mano: una fila por norma, agrupadas
             para decidir en bloque, con las columnas "incluir" y "comentario" para completar.
             Las etapas siguientes usan solo las marcadas con "sí" (ver `curadas`).
  textos     Baja el texto de cada norma seleccionada desde el HTML de Infoleg (el texto
             actualizado si existe; si no, el original), lo limpia y lo agrega a
             normas.jsonl. Guarda el HTML crudo para no volver a pedirlo.
  anexos     Baja los anexos PDF que `textos` dejó anotados y extrae su texto (anexos.jsonl).

Uso:
    python -m nacional.infoleg base      [--dir nacional/datos]
    python -m nacional.infoleg seleccion [--dir ...] --criterios nacional/criterios.json
    python -m nacional.infoleg planilla  [--dir ...]
    python -m nacional.infoleg textos    [--dir ...] [--pausa 1.0] [--limite N]
    python -m nacional.infoleg anexos    [--dir ...] [--pausa 1.0] [--limite N]
"""
import argparse
import csv
import html
import io
import json
import os
import re
import sys
import time
import urllib.request
import zipfile
from collections import defaultdict, deque

CKAN = 'https://datos.jus.gob.ar/api/3/action/package_show?id=base-de-datos-legislativos-infoleg'
RECURSOS = {  # nombre del recurso en la API -> archivo local
    'Base Infoleg Normativa Nacional': 'normas.csv',
    'Base Complementaria Infoleg de Normas Modificadas': 'modificadas.csv',
    'Base Complementaria Infoleg de Normas Modificatorias': 'modificatorias.csv',
}
AGENTE = {'User-Agent': 'Mozilla/5.0 (corpus-normativa-nacional)'}
csv.field_size_limit(sys.maxsize)


def pedir(url, intentos=4):
    for k in range(intentos):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=AGENTE), timeout=120).read()
        except Exception as e:  # red inestable: reintentar con espera creciente
            if k == intentos - 1:
                raise
            print(f'  reintento {k + 1} ({e}): {url}', flush=True)
            time.sleep(5 * (k + 1))


# ---------------------------------------------------------------- base
def base(d):
    os.makedirs(d, exist_ok=True)
    paquete = json.loads(pedir(CKAN))['result']
    for r in paquete['resources']:
        nombre = r.get('name', '').strip()
        if nombre not in RECURSOS or r.get('format', '').upper() != 'ZIP':
            continue
        print(f'{nombre}: {r["url"]} (actualizado {r.get("last_modified") or r.get("created")})', flush=True)
        z = zipfile.ZipFile(io.BytesIO(pedir(r['url'])))
        csvs = [n for n in z.namelist() if n.lower().endswith('.csv')]
        destino = os.path.join(d, RECURSOS[nombre])
        with z.open(csvs[0]) as f, open(destino, 'wb') as out:
            out.write(f.read())
        print(f'  -> {destino}', flush=True)
    json.dump({'paquete': paquete['name'], 'modificado': paquete.get('metadata_modified')},
              open(os.path.join(d, 'base.json'), 'w'), indent=1)


def leer(ruta):
    with open(ruta, encoding='utf-8-sig', errors='replace', newline='') as f:
        yield from csv.DictReader(f)


# ---------------------------------------------------------------- seleccion
def normalizar(t):
    t = (t or '').upper()
    for a, b in zip('ÁÉÍÓÚÜ', 'AEIOUU'):
        t = t.replace(a, b)
    return t


def resolver(semilla, indice):
    """'804' (id), 'Ley 24521' o 'Decreto 1246/2015' -> ids de la base."""
    s = str(semilla).strip()
    if s.isdigit():
        return [s]
    m = re.match(r'(.+?)\s+(\d+)(?:/(\d{4}))?$', s)
    if not m:
        return []
    tipo, num, anio = normalizar(m.group(1)), m.group(2), m.group(3)
    return [i for i, f in indice.get((tipo, num), []) if not anio or f['fecha_sancion'][:4] == anio]


def seleccion(d, criterios):
    c = json.load(open(criterios, encoding='utf-8'))
    palabras = [normalizar(p) for p in c.get('palabras', [])]
    organismos = [normalizar(o) for o in c.get('organismos', [])]
    excluir = [normalizar(p) for p in c.get('excluir', [])]
    tipos = set(c.get('tipos', []))

    normas = {}
    indice = defaultdict(list)
    for f in leer(os.path.join(d, 'normas.csv')):
        normas[f['id_norma']] = f
        indice[(normalizar(f['tipo_norma']), f['numero_norma'].strip())].append((f['id_norma'], f))

    # Semillas: las normas transversales que entran sí o sí (por id o por "Tipo número[/año]").
    semillas, no_resueltas = {}, []
    for grupo, lista in c.get('semillas', {}).items():
        for s in lista:
            ids = resolver(s, indice)
            if len(ids) == 1:
                semillas[ids[0]] = f'semilla:{grupo}:{s}'
            else:
                no_resueltas.append((s, len(ids)))
    expandir = {i for s in c.get('expandir_desde', []) for i in resolver(s, indice)}

    def excluida(f):
        t = normalizar(' '.join([f['titulo_resumido'], f['titulo_sumario'], f['texto_resumido']]))
        return any(p in t for p in excluir)

    elegidas = dict(semillas)
    for i, f in normas.items():
        if i in elegidas or (tipos and f['tipo_norma'] not in tipos) or excluida(f):
            continue
        texto = normalizar(' '.join([f['titulo_resumido'], f['titulo_sumario'], f['texto_resumido']]))
        org = normalizar(f['organismo_origen'])
        if (p := next((p for p in palabras if p in texto), None)):
            elegidas[i] = f'palabra:{p}'
        elif (o := next((o for o in organismos if o in org), None)):
            elegidas[i] = f'organismo:{o}'

    # Expansión por vínculos desde las normas de `expandir_desde`: lo que las modifica o
    # complementa, y lo que ellas modifican, también entra (hasta la profundidad pedida),
    # para que cada norma llegue con su historia. Solo desde esas: la Ley 19.549, por
    # ejemplo, tiene cientos de vínculos que no son universitarios.
    vecinos = defaultdict(set)
    for nombre, a, b in (('modificadas.csv', 'id_norma_modificada', 'id_norma_modificatoria'),
                         ('modificatorias.csv', 'id_norma_modificatoria', 'id_norma_modificada')):
        ruta = os.path.join(d, nombre)
        if not os.path.exists(ruta):
            continue
        for f in leer(ruta):
            ida, idb = f.get(a) or f.get('﻿' + a), f.get(b)
            if ida and idb:
                vecinos[ida].add(idb)
                vecinos[idb].add(ida)
    prof = c.get('profundidad_vinculos', 0)
    cola = deque((s, 0) for s in expandir)
    while cola:
        i, k = cola.popleft()
        if k >= prof:
            continue
        for j in vecinos.get(i, ()):
            f = normas.get(j)
            if f and j not in elegidas and (not tipos or f['tipo_norma'] in tipos) and not excluida(f):
                elegidas[j] = f'vinculo:{normas[i]["tipo_norma"]} {normas[i]["numero_norma"]}'
                cola.append((j, k + 1))
    campos = ['id_norma', 'motivo', 'tipo_norma', 'numero_norma', 'fecha_sancion', 'organismo_origen',
              'titulo_resumido', 'texto_actualizado', 'texto_original']
    with open(os.path.join(d, 'seleccion.csv'), 'w', encoding='utf-8', newline='') as out:
        w = csv.DictWriter(out, campos)
        w.writeheader()
        for i, motivo in sorted(elegidas.items(), key=lambda x: int(x[0])):
            if i in normas:
                w.writerow({**{k: normas[i].get(k, '') for k in campos}, 'id_norma': i, 'motivo': motivo})
    por = defaultdict(int)
    for m in elegidas.values():
        por[m.split(':')[0]] += 1
    print(f'{len(elegidas)} normas elegidas de {len(normas)}: {dict(por)}')
    for s, n in no_resueltas:
        print(f'semilla sin resolver ({n} coincidencias): {s}')


# ---------------------------------------------------------------- planilla
# Grupos para curar en bloque. Gana la primera regla que coincide, así que las más
# específicas van primero. Las semillas conservan el grupo con el que se declararon.
GRUPOS = [
    ('radiodifusión universitaria', ['RADIO', 'TELEVISION', 'FRECUENCIA', 'SERVICIOS DE COMUNICACION AUDIOVISUAL', 'ENACOM', 'COMFER']),
    ('salud y obras sociales', ['OBRA SOCIAL', 'OBRAS SOCIALES', 'SUPERINTENDENCIA DE SERVICIOS DE SALUD', 'HOSPITAL']),
    ('auspicios y declaraciones de interés', ['AUSPICIO', 'DECLARASE DE INTERES', 'DECLARAR DE INTERES', 'INTERES NACIONAL', 'INTERES EDUCATIVO']),
    ('creación y autorización de universidades', ['CREASE LA UNIVERSIDAD', 'CREASE EL INSTITUTO UNIVERSITARIO', 'CREACION DE LA UNIVERSIDAD',
                                                   'CREACION DEL INSTITUTO UNIVERSITARIO', 'AUTORIZASE PROVISORIAMENTE', 'AUTORIZASE PROVISIONALMENTE',
                                                   'RECONOCIMIENTO DEFINITIVO', 'PUESTA EN MARCHA', 'PROYECTO INSTITUCIONAL', 'NACIONALIZ']),
    ('estatutos', ['ESTATUTO']),
    ('evaluación y acreditación (CONEAU)', ['CONEAU', 'ACREDITACION', 'EVALUACION INSTITUCIONAL', 'EVALUACION EXTERNA', 'AGENCIA PRIVADA DE EVALUACION']),
    ('posgrado', ['POSGRADO', 'DOCTORADO', 'MAESTRIA', 'ESPECIALIZACION']),
    ('títulos, carreras y alcances', ['TITULO', 'CARRERA', 'VALIDEZ NACIONAL', 'RECONOCIMIENTO OFICIAL', 'ACTIVIDADES RESERVADAS',
                                      'ACTIVIDADES PROFESIONALES', 'ALCANCES', 'PLAN DE ESTUDIO', 'EDUCACION A DISTANCIA']),
    ('personal, salarios y convenios colectivos', ['CONVENIO COLECTIVO', 'CONVENCION COLECTIVA', 'PARITARI', 'REMUNERACION', 'SALARI',
                                                   'NO DOCENTE', 'NODOCENTE', 'DOCENTES', 'INCENTIVO', 'HOMOLOG', 'ESCALAFON', 'JUBILACI']),
    ('presupuesto y financiamiento', ['PRESUPUESTO', 'FINANCIAMIENTO', 'CREDITO', 'TRANSFIERESE', 'TRANSFERENCIA', 'FONDOS', 'APORTE',
                                      'SUBSIDIO', 'PARTIDA']),
    ('convenios con organismos', ['CONVENIO', 'ACUERDO', 'COOPERACION', 'ASISTENCIA TECNICA', 'CONTRATO', 'CONTRATACION']),
]


def grupo_de(f):
    if f['motivo'].startswith('semilla:'):
        return 'semilla: ' + f['motivo'].split(':')[1].replace('_', ' ')
    if f['tipo_norma'] == 'Ley' and f['fecha_sancion'][:4] and f['fecha_sancion'][:4] < '1960' \
            and not (f['texto_actualizado'] or f['texto_original']):
        return 'leyes históricas sin texto'
    t = normalizar(' '.join([f['titulo_resumido'], f['titulo_sumario'], f['texto_resumido'], f['organismo_origen']]))
    for g, claves in GRUPOS:
        if any(normalizar(k) in t for k in claves):
            return g
    return 'otros'


def planilla(d):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    textos_por_id = {}
    ruta_textos = os.path.join(d, 'normas.jsonl')
    if os.path.exists(ruta_textos):
        for l in open(ruta_textos, encoding='utf-8'):
            r = json.loads(l)
            textos_por_id[r['id']] = len(r.get('texto') or '')
    normas = {f['id_norma']: f for f in leer(os.path.join(d, 'normas.csv'))}
    filas = []
    for s in leer(os.path.join(d, 'seleccion.csv')):
        f = {**normas[s['id_norma']], 'motivo': s['motivo']}
        tiene = 'actualizado' if f['texto_actualizado'] else ('original' if f['texto_original'] else 'sin texto')
        filas.append({'grupo': grupo_de(f), 'motivo': s['motivo'], 'tipo': f['tipo_norma'], 'numero': f['numero_norma'],
                      'anio': f['fecha_sancion'][:4], 'organismo': f['organismo_origen'], 'titulo': f['titulo_resumido'],
                      'resumen': f['texto_resumido'], 'texto': tiene, 'caracteres': textos_por_id.get(f['id_norma']),
                      'id': f['id_norma']})
    orden = {'semilla': 0, 'vinculo': 1, 'palabra': 2}
    filas.sort(key=lambda r: (orden.get(r['motivo'].split(':')[0], 3), r['grupo'], r['anio'], r['tipo'], r['numero']))

    fuente, negrita = Font(name='Arial', size=10), Font(name='Arial', size=10, bold=True)
    amarillo = PatternFill('solid', start_color='FFF2CC')
    wb = Workbook()

    ins = wb.active
    ins.title = 'Instrucciones'
    lineas = [
        ('Curación de la normativa nacional para la capa nacional del modelo', negrita),
        ('', fuente),
        ('Cada fila de la hoja Normas es una norma de Infoleg preseleccionada por el script nacional/infoleg.py.', fuente),
        ('Se completan solo las dos columnas amarillas de la hoja Normas:', fuente),
        ('   incluir: "sí" o "no" (lista desplegable). Vacío = sin decidir.', fuente),
        ('   comentario: texto libre, opcional (por qué se excluye, dudas, etc.).', fuente),
        ('El resto de las columnas no se edita: el pipeline vuelve a leer la planilla por id.', fuente),
        ('', fuente),
        ('Orden sugerido: primero las semillas (normas transversales), después las que entraron por vínculo con la', fuente),
        ('LES o los convenios colectivos, y al final las que entraron por palabra clave, agrupadas para decidir en bloque', fuente),
        ('con el filtro de la columna grupo. La hoja Resumen cuenta cuántas quedan por decidir en cada grupo.', fuente),
        ('', fuente),
        ('Columna texto: "actualizado" (texto con modificaciones incorporadas), "original" o "sin texto" en Infoleg.', fuente),
        ('Columna motivo: semilla:<grupo>:<norma>, vinculo:<norma de la que proviene> o palabra:<palabra que coincidió>.', fuente),
        ('', fuente),
        ('Ejemplo de una fila completada:', negrita),
    ]
    for k, (t, fnt) in enumerate(lineas, 1):
        ins.cell(row=k, column=1, value=t).font = fnt
    ejemplo_cab = ['incluir', 'comentario', 'grupo', 'tipo', 'número', 'año', 'título']
    ejemplo = ['no', 'convenio técnico del INASE, la universidad es solo contraparte', 'convenios con organismos',
               'Resolución', '325', '2019', 'AVENA']
    for j, (c, v) in enumerate(zip(ejemplo_cab, ejemplo), 1):
        ins.cell(row=len(lineas) + 1, column=j, value=c).font = negrita
        ins.cell(row=len(lineas) + 2, column=j, value=v).font = fuente
    ins.column_dimensions['A'].width = 14
    ins.column_dimensions['B'].width = 60

    ws = wb.create_sheet('Normas')
    cab = ['incluir', 'comentario', 'grupo', 'motivo', 'tipo', 'número', 'año', 'organismo', 'título', 'resumen',
           'texto', 'Infoleg', 'id']
    anchos = [9, 30, 34, 30, 14, 9, 7, 34, 45, 70, 11, 9, 9]
    for j, (c, a) in enumerate(zip(cab, anchos), 1):
        celda = ws.cell(row=1, column=j, value=c)
        celda.font = negrita
        ws.column_dimensions[get_column_letter(j)].width = a
    for i, r in enumerate(filas, 2):
        valores = ['', '', r['grupo'], r['motivo'], r['tipo'], r['numero'], r['anio'], r['organismo'], r['titulo'],
                   r['resumen'], r['texto'], 'ver', int(r['id'])]
        for j, v in enumerate(valores, 1):
            celda = ws.cell(row=i, column=j, value=v)
            celda.font = fuente
            if j <= 2:
                celda.fill = amarillo
        ws.cell(row=i, column=12).hyperlink = f'https://servicios.infoleg.gob.ar/infolegInternet/verNorma.do?id={r["id"]}'
        ws.cell(row=i, column=12).font = Font(name='Arial', size=10, color='0563C1', underline='single')
        ws.cell(row=i, column=10).alignment = Alignment(wrap_text=False)
    dv = DataValidation(type='list', formula1='"sí,no"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f'A2:A{len(filas) + 1}')
    ws.freeze_panes = 'C2'
    ws.auto_filter.ref = f'A1:{get_column_letter(len(cab))}{len(filas) + 1}'

    rs = wb.create_sheet('Resumen')
    for j, c in enumerate(['grupo', 'normas', 'sí', 'no', 'sin decidir'], 1):
        rs.cell(row=1, column=j, value=c).font = negrita
    grupos = sorted({r['grupo'] for r in filas}, key=lambda g: (not g.startswith('semilla'), g))
    n = len(filas) + 1
    for i, g in enumerate(grupos, 2):
        rs.cell(row=i, column=1, value=g).font = fuente
        rs.cell(row=i, column=2, value=f'=COUNTIF(Normas!$C$2:$C${n},A{i})').font = fuente
        rs.cell(row=i, column=3, value=f'=COUNTIFS(Normas!$C$2:$C${n},A{i},Normas!$A$2:$A${n},"sí")').font = fuente
        rs.cell(row=i, column=4, value=f'=COUNTIFS(Normas!$C$2:$C${n},A{i},Normas!$A$2:$A${n},"no")').font = fuente
        rs.cell(row=i, column=5, value=f'=B{i}-C{i}-D{i}').font = fuente
    t = len(grupos) + 2
    rs.cell(row=t, column=1, value='total').font = negrita
    for j, col in enumerate('BCDE', 2):
        rs.cell(row=t, column=j, value=f'=SUM({col}2:{col}{t - 1})').font = negrita
    rs.column_dimensions['A'].width = 44

    destino = os.path.join(d, 'curacion.xlsx')
    wb.save(destino)
    print(f'{destino}: {len(filas)} normas en {len(grupos)} grupos')


def curadas(ruta):
    """Ids marcados con "sí" en la planilla curada (xlsx o csv exportado de Google Sheets)."""
    if ruta.endswith('.xlsx'):
        from openpyxl import load_workbook
        ws = load_workbook(ruta, read_only=True, data_only=True)['Normas']
        filas = ws.iter_rows(values_only=True)
        cab = list(next(filas))
        return {str(r[cab.index('id')]) for r in filas if normalizar(str(r[0] or '')).strip() in ('SI', 'X', '1')}
    return {r['id'] for r in csv.DictReader(open(ruta, encoding='utf-8-sig'))
            if normalizar(r.get('incluir', '')).strip() in ('SI', 'X', '1')}


# ---------------------------------------------------------------- textos
def decodificar(crudo):
    """Infoleg sirve la mayoría de las páginas en Windows-1252 (no latin-1: los guiones largos y las
    comillas tipográficas caen en 0x80-0x9F); algunas recientes vienen en UTF-8."""
    try:
        return crudo.decode('utf-8')
    except UnicodeDecodeError:
        return crudo.decode('cp1252', errors='replace')


def a_texto(crudo):
    t = decodificar(crudo)
    t = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', t)
    t = re.sub(r'(?i)<br\s*/?>|</p>|</div>|</tr>|</h\d>|</li>', '\n', t)
    t = re.sub(r'<[^>]+>', ' ', t)
    t = html.unescape(t).replace('\xa0', ' ')
    t = re.sub(r'[ \t]+', ' ', t)
    t = re.sub(r'\n\s*\n\s*\n+', '\n\n', t)
    return '\n'.join(l.strip() for l in t.splitlines()).strip()


def anexos(crudo, url):
    """Enlaces del HTML a anexos de la misma norma (otros .htm o PDF en su carpeta)."""
    base_url = url.rsplit('/', 1)[0] + '/'
    vistos = []
    for href in re.findall(r'(?i)href=["\']([^"\']+)["\']', decodificar(crudo)):
        if href.startswith(('http', '//', '#', 'mailto')) or '..' in href:
            continue
        if href.lower().endswith(('.htm', '.html', '.pdf')) and href not in vistos:
            vistos.append(href)
    return [base_url + h for h in vistos]


def textos(d, pausa, limite):
    crudos = os.path.join(d, 'html')
    os.makedirs(crudos, exist_ok=True)
    salida = os.path.join(d, 'normas.jsonl')
    hechos = {json.loads(l)['id'] for l in open(salida, encoding='utf-8')} if os.path.exists(salida) else set()
    filas = [f for f in leer(os.path.join(d, 'seleccion.csv')) if f['id_norma'] not in hechos]
    if limite:
        filas = filas[:limite]
    print(f'{len(filas)} normas por bajar ({len(hechos)} ya estaban)', flush=True)
    out = open(salida, 'a', encoding='utf-8')
    for k, f in enumerate(filas, 1):
        url = (f['texto_actualizado'] or f['texto_original']).replace('http://', 'https://')
        fila = {'id': f['id_norma'], 'tipo': f['tipo_norma'], 'numero': f['numero_norma'],
                'fecha': f['fecha_sancion'], 'organismo': f['organismo_origen'],
                'titulo': f['titulo_resumido'], 'motivo': f['motivo'],
                'version': 'actualizado' if f['texto_actualizado'] else 'original', 'url': url,
                'texto': '', 'anexos_pdf': []}
        if url:
            try:
                cache = os.path.join(crudos, f'{f["id_norma"]}.htm')
                if os.path.exists(cache):
                    crudo = open(cache, 'rb').read()
                else:
                    crudo = pedir(url)
                    open(cache, 'wb').write(crudo)
                    time.sleep(pausa)
                partes = [a_texto(crudo)]
                for a in anexos(crudo, url):
                    if a.lower().endswith('.pdf'):
                        fila['anexos_pdf'].append(a)  # los PDF quedan anotados; se extraen aparte
                        continue
                    nombre = os.path.join(crudos, f'{f["id_norma"]}-{os.path.basename(a)}')
                    if not os.path.exists(nombre):
                        open(nombre, 'wb').write(pedir(a))
                        time.sleep(pausa)
                    partes.append(a_texto(open(nombre, 'rb').read()))
                fila['texto'] = '\n\n'.join(p for p in partes if p)
            except Exception as e:
                fila['error'] = str(e)[:200]
        out.write(json.dumps(fila, ensure_ascii=False) + '\n')
        out.flush()
        if k % 50 == 0:
            print(f'  {k}/{len(filas)}', flush=True)
    print('LISTO textos', flush=True)


# ---------------------------------------------------------------- anexos
def bajar_anexos(d, pausa, limite, curadas=None):
    """Baja los anexos PDF anotados por `textos` y extrae su texto con PyMuPDF.

    Muchas normas tienen lo sustantivo en un anexo (el convenio colectivo docente, por
    ejemplo, es un PDF anexo al Decreto 1246/2015). Escribe anexos.jsonl con una fila por
    PDF; las páginas casi sin texto quedan contadas para un OCR posterior.
    """
    import fitz
    carpeta = os.path.join(d, 'pdf')
    os.makedirs(carpeta, exist_ok=True)
    salida = os.path.join(d, 'anexos.jsonl')
    hechos = {json.loads(l)['url'] for l in open(salida, encoding='utf-8')} if os.path.exists(salida) else set()
    ids = {r['id_infoleg'] for r in csv.DictReader(open(curadas, encoding='utf-8'))} if curadas else None
    pendientes = [(r['id'], u) for r in map(json.loads, open(os.path.join(d, 'normas.jsonl'), encoding='utf-8'))
                  if ids is None or r['id'] in ids
                  for u in r.get('anexos_pdf') or [] if u not in hechos]
    if limite:
        pendientes = pendientes[:limite]
    print(f'{len(pendientes)} anexos por bajar ({len(hechos)} ya estaban)', flush=True)
    out = open(salida, 'a', encoding='utf-8')
    for k, (i, url) in enumerate(pendientes, 1):
        fila = {'id': i, 'url': url, 'texto': '', 'paginas': 0, 'paginas_sin_texto': 0}
        try:
            ruta = os.path.join(carpeta, f'{i}-{os.path.basename(url)}')
            if not os.path.exists(ruta):
                open(ruta, 'wb').write(pedir(url))
                time.sleep(pausa)
            with fitz.open(ruta) as doc:
                paginas = [p.get_text() for p in doc]
            fila['paginas'] = len(paginas)
            fila['paginas_sin_texto'] = sum(1 for p in paginas if len(p.strip()) < 40)
            fila['texto'] = re.sub(r'\n\s*\n\s*\n+', '\n\n', '\n'.join(paginas)).strip()
        except Exception as e:
            fila['error'] = str(e)[:200]
        out.write(json.dumps(fila, ensure_ascii=False) + '\n')
        out.flush()
        if k % 25 == 0:
            print(f'  {k}/{len(pendientes)}', flush=True)
    print('LISTO anexos', flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('paso', choices=['base', 'seleccion', 'planilla', 'textos', 'anexos'])
    ap.add_argument('--dir', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'datos'))
    ap.add_argument('--criterios', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'criterios.json'))
    ap.add_argument('--pausa', type=float, default=1.0, help='segundos entre pedidos a Infoleg')
    ap.add_argument('--limite', type=int, default=0)
    ap.add_argument('--curadas', help='CSV con la columna id_infoleg: limita `anexos` a esas normas')
    a = ap.parse_args()
    if a.paso == 'base':
        base(a.dir)
    elif a.paso == 'seleccion':
        seleccion(a.dir, a.criterios)
    elif a.paso == 'planilla':
        planilla(a.dir)
    elif a.paso == 'anexos':
        bajar_anexos(a.dir, a.pausa, a.limite, a.curadas)
    else:
        textos(a.dir, a.pausa, a.limite)


if __name__ == '__main__':
    main()
