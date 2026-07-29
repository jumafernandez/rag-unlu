#!/usr/bin/env python3
"""
Parte los documentos en chunks siguiendo la estructura del acto administrativo.

Por qué no chunking de tamaño fijo
----------------------------------
En normativa la unidad de respuesta es el ARTÍCULO: "¿qué dice el artículo 2 de la
disposición 528/2025?" se responde con ese artículo, no con 500 caracteres que empiezan
a la mitad del considerando y terminan a la mitad del artículo. El parser ya deja el
esqueleto marcado (## Visto, ## Considerando, ### Artículo N, ## Firmas, # Anexo), así
que conviene cortar por ahí.

Cada chunk sale con:
  - el texto de su unidad,
  - la metadata del documento (autoritativa si vino del scraper),
  - una CITA legible ("Disposición DISPCD-CB 528/2025 — Artículo 2") para que la
    respuesta pueda atribuirse, que es el requisito principal en un digesto legal.

Los artículos muy largos se subdividen por párrafos, conservando el encabezado en cada
parte para que el chunk no pierda su identidad.

Uso:
    python chunkear.py --resultados DIR --salida chunks.jsonl [--metadata join.csv]
"""

import argparse
import csv
import glob
import json
import os
import re

try:
    import yaml
except ImportError:
    raise SystemExit('falta PyYAML')

MAX_CARACTERES = 1800   # techo por chunk antes de subdividir
MIN_CARACTERES = 80     # por debajo de esto no vale la pena indexar solo

RE_HEADER = re.compile(r'^(#{1,3})\s+(.*)$')

# Secciones sin valor para recuperación: son ruido de trámite, no contenido normativo.
SECCIONES_IGNORADAS = {'firmas', 'hoja de firmas'}


def tipo_de_seccion(titulo):
    t = titulo.strip().lower()
    if t.startswith('artículo') or t.startswith('articulo'):
        return 'articulo'
    if t.startswith('anexo'):
        return 'anexo'
    if 'visto' in t:
        return 'visto'
    if 'considerando' in t:
        return 'considerando'
    if 'resolutiva' in t or 'dispositiva' in t:
        return 'parte_resolutiva'
    if t in SECCIONES_IGNORADAS:
        return 'firmas'
    return 'otra'


def partir_markdown(md):
    """[(nivel, titulo, cuerpo)] respetando el orden del documento."""
    secciones, actual = [], None
    for linea in md.split('\n'):
        m = RE_HEADER.match(linea)
        if m:
            if actual:
                secciones.append(actual)
            actual = [len(m.group(1)), m.group(2).strip(), []]
        elif actual:
            actual[2].append(linea)
        else:
            actual = [0, '', [linea]]
    if actual:
        secciones.append(actual)
    return [(n, t, '\n'.join(c).strip()) for n, t, c in secciones]


def subdividir(texto, maximo=MAX_CARACTERES):
    """Corta por párrafos sin partir oraciones al medio."""
    if len(texto) <= maximo:
        return [texto]
    partes, actual = [], ''
    for parrafo in re.split(r'\n\s*\n', texto):
        if len(actual) + len(parrafo) + 2 > maximo and actual:
            partes.append(actual.strip())
            actual = parrafo
        else:
            actual = f'{actual}\n\n{parrafo}' if actual else parrafo
    if actual.strip():
        partes.append(actual.strip())
    return partes


def cargar_metadata_autoritativa(ruta):
    if not ruta or not os.path.exists(ruta):
        return {}
    out = {}
    with open(ruta, encoding='utf-8') as fh:
        for r in csv.DictReader(fh):
            if r.get('confianza') in ('alta', 'media'):
                out[r['archivo']] = r
    return out


def cita_de(meta, seccion_titulo, tipo):
    """'Disposición DISPCD-CB 528/2025 — Artículo 2'"""
    tipo_doc = (meta.get('document_type') or 'documento').capitalize()
    codigo = meta.get('document_code') or ''
    numero = meta.get('document_number') or ''
    cabeza = ' '.join(x for x in (tipo_doc, codigo, numero) if x and x != 'unknown').strip()
    if tipo in ('articulo', 'anexo') and seccion_titulo:
        return f'{cabeza} — {seccion_titulo}'
    if seccion_titulo and tipo != 'otra':
        return f'{cabeza} — {seccion_titulo}'
    return cabeza or 'documento sin identificar'


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--resultados', required=True, help='dir con *_canonico.yaml y *.md')
    p.add_argument('--salida', required=True)
    p.add_argument('--metadata', default=None, help='CSV de unir_metadata.py')
    p.add_argument('--max', type=int, default=MAX_CARACTERES)
    a = p.parse_args()

    autoritativa = cargar_metadata_autoritativa(a.metadata)
    yamls = sorted(glob.glob(os.path.join(a.resultados, '**', '*_canonico.yaml'), recursive=True))
    if not yamls:
        raise SystemExit(f'no encontré *_canonico.yaml en {a.resultados}')

    n_chunks = n_docs = 0
    por_tipo = {}
    con_titulo = 0

    with open(a.salida, 'w', encoding='utf-8') as out:
        for ruta_yaml in yamls:
            base = os.path.basename(ruta_yaml)[: -len('_canonico.yaml')]
            try:
                meta = yaml.safe_load(open(ruta_yaml, encoding='utf-8')) or {}
            except Exception:
                continue

            ruta_md = os.path.join(os.path.dirname(ruta_yaml), f'{base}.md')
            md = open(ruta_md, encoding='utf-8').read() if os.path.exists(ruta_md) else \
                (meta.get('content_markdown') or '')
            if not md.strip():
                continue

            # La metadata del sistema fuente pisa a la inferida del texto: la fecha y el
            # título del portal son el dato bueno, los del parser son una estimación.
            fuente = autoritativa.get(f'{base}.pdf')
            if fuente:
                if fuente.get('fecha'):
                    d, m_, y_ = (fuente['fecha'].split('/') + ['', '', ''])[:3]
                    if d and m_ and y_:
                        meta['date_issued'] = f'{y_}-{m_.zfill(2)}-{d.zfill(2)}'
                        meta['year'] = int(y_) if y_.isdigit() else meta.get('year')
                if fuente.get('titulo'):
                    meta['titulo'] = fuente['titulo']
                    con_titulo += 1
                if fuente.get('estado'):
                    meta['estado'] = fuente['estado']
                if fuente.get('tipo_documento'):
                    meta['tipo_documento_fuente'] = fuente['tipo_documento']
                meta['metadata_confianza'] = fuente.get('confianza')

                # La IDENTIDAD del acto también sale de la fuente, no del PDF.
                #
                # Antes se conservaba lo que hubiera leído el parser, y eso dejaba dos
                # errores que después no se pueden reparar: 598 fragmentos con código y
                # número 'unknown' ---documentos donde el extractor no encontró el
                # encabezado--- y códigos truncados, como 'CS' en lugar de 'DISPCD-CS',
                # que vuelven indistinguibles tres actos distintos con el mismo número.
                #
                # El portal publica el número completo junto al documento. Tomarlo de ahí
                # elimina las dos cosas: la identidad deja de depender de cómo salió el PDF.
                m_num = re.match(r'\s*([A-ZÑ0-9][A-ZÑ0-9-]*)\s*:?\s*(\d+)\s*/\s*(\d{2,4})\s*$',
                                 (fuente.get('numero') or '').strip(), re.IGNORECASE)
                if m_num:
                    anio = m_num.group(3)
                    meta['document_code'] = m_num.group(1).upper()
                    meta['document_number'] = f'{m_num.group(2)}/{anio}'
                    if anio.isdigit():
                        meta['year'] = int(anio if len(anio) == 4 else '20' + anio)

                # Enlace al PDF publicado e identificadores del portal. Van con el fragmento
                # desde el principio: así la trazabilidad queda armada en una sola pasada y
                # no hay que recorrer el índice después para agregarla.
                for destino, origen in (('url_documento', 'url_documento'),
                                        ('id_archivo', 'id_archivo'),
                                        ('id_documento', 'id_documento'),
                                        ('fecha_acto', 'fecha_acto')):
                    if fuente.get(origen):
                        meta[destino] = fuente[origen]

            n_docs += 1
            base_meta = {k: meta.get(k) for k in (
                'document_id', 'document_code', 'document_number', 'document_type',
                'issuing_body', 'date_issued', 'year', 'titulo', 'estado',
                'tipo_documento_fuente', 'metadata_confianza', 'source_pdf',
                'url_documento', 'id_archivo', 'id_documento', 'fecha_acto')}

            for _, titulo, cuerpo in partir_markdown(md):
                tipo = tipo_de_seccion(titulo)
                if tipo == 'firmas' or len(cuerpo) < MIN_CARACTERES:
                    continue
                for i, parte in enumerate(subdividir(cuerpo, a.max)):
                    if len(parte) < MIN_CARACTERES:
                        continue
                    n_chunks += 1
                    por_tipo[tipo] = por_tipo.get(tipo, 0) + 1
                    cita = cita_de(meta, titulo, tipo)
                    # El texto indexado lleva el título del acto y la cita adelante: ayuda
                    # a que el embedding capture de qué trata, no solo el fragmento suelto.
                    encabezado = ' | '.join(x for x in (meta.get('titulo'), cita) if x)
                    out.write(json.dumps({
                        'chunk_id': f'{base}::{tipo}::{i}',
                        'documento': base,
                        'seccion': titulo or tipo,
                        'tipo_seccion': tipo,
                        'cita': cita,
                        'texto': parte,
                        'texto_indexado': f'{encabezado}\n\n{parte}' if encabezado else parte,
                        **base_meta,
                    }, ensure_ascii=False) + '\n')

    print(f'documentos : {n_docs}')
    print(f'chunks     : {n_chunks}  ({n_chunks/max(1,n_docs):.1f} por documento)')
    print(f'con título autoritativo: {con_titulo}/{n_docs}')
    print('por tipo de sección:')
    for t, k in sorted(por_tipo.items(), key=lambda x: -x[1]):
        print(f'   {t:18s} {k}')
    print(f'\n-> {a.salida}')


if __name__ == '__main__':
    main()
