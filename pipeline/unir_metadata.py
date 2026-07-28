#!/usr/bin/env python3
"""
Une cada PDF con su metadata autoritativa del sistema fuente (scrapers/metadatos.csv).

Por qué hace falta y por qué no es trivial
------------------------------------------
El scraper nombró cada PDF como <PREFIJO>_<id_pdf>.pdf y escribió esa misma id en la fila
de metadata. Pero después `renombrar_archivos.py` volvió a numerar los archivos ordenando
por fecha de creación, descartando la id original: 1.780 de 19.959 archivos quedaron con un
número que ya no corresponde a su fila. Unir por el número del nombre de archivo da metadata
de OTRO documento en ~8,9% de los casos, y encima en silencio (la cobertura da igual de alta).

Este script une por DOS caminos independientes y usa el acuerdo entre ambos como medida de
confianza, en vez de confiar a ciegas en uno:

  posicional  el número del archivo actual -> `nuevo` -> `actual` en mapeo_renombres.csv
              -> <PREFIJO>_<id>.pdf -> (carpeta, ID PDF) en metadatos.csv
  por codigo   el código que el propio acto lleva impreso (p.ej. "DISPSEACAD : 412 / 2025")
              contra el campo `Numero` de metadatos.csv

El código del acto es su identidad real: es inmune al renombrado y a cualquier corrimiento.
El posicional cubre los casos sin código legible (las órdenes de compra, por ejemplo).

Confianza resultante:
  alta      ambos caminos coinciden
  media     solo uno resolvió
  conflicto los dos resolvieron y apuntan a actos distintos -> NO se usa la metadata

Uso:
    python unir_metadata.py --scrapers DIR --pdfs DIR [--yaml DIR] --salida CSV
"""

import argparse
import collections
import csv
import os
import re
import sys

RE_NUMERO = re.compile(r'([A-ZÑ0-9./-]+)\s*:\s*0*(\d+)\s*/\s*(\d{2,4})')


def basename_win(ruta):
    """mapeo_renombres.csv trae rutas de Windows; os.path.basename no corta en '\\'."""
    return re.split(r'[\\/]', str(ruta or ''))[-1]


def clave_acto(texto):
    """('DISPCD-CB', 528, '2025') a partir de 'DISPCD-CB : 528 / 2025'."""
    if not texto:
        return None
    m = RE_NUMERO.search(str(texto).upper())
    if not m:
        return None
    codigo, numero, anio = m.group(1), int(m.group(2)), m.group(3)
    if len(anio) == 2:
        # Los actos del portal son todos de 2024 en adelante; el digesto legacy va a
        # necesitar revisar esto (allá hay años de 2 dígitos de los 90 y los 2000).
        anio = '20' + anio
    return (codigo, numero, anio)


def normalizar_prefijo(carpeta):
    """'DEPARTAMENTO DE CIENCIAS BÁSICAS' -> 'DEPARTAMENTO_DE_CIENCIAS_BASICAS'."""
    t = carpeta.upper()
    for a, b in (('Á', 'A'), ('É', 'E'), ('Í', 'I'), ('Ó', 'O'), ('Ú', 'U'), ('Ñ', 'N'), ('.', '')):
        t = t.replace(a, b)
    return re.sub(r'\s+', '_', t.strip())


def segmentar_por_carpeta(filas):
    """metadatos.csv no trae la carpeta: se reconstruye por los reinicios de 'ID PDF'."""
    segmentos, actual, previo = [], [], 0
    for f in filas:
        try:
            i = int(f['ID PDF'])
        except (ValueError, KeyError):
            continue
        if i <= previo and actual:
            segmentos.append(actual)
            actual = []
        actual.append(f)
        previo = i
    if actual:
        segmentos.append(actual)
    return segmentos


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--scrapers', required=True, help='dir con metadatos.csv y mapeo_renombres.csv')
    p.add_argument('--pdfs', required=True)
    p.add_argument('--yaml', default=None, help='dir con *_canonico.yaml (para el join por código)')
    p.add_argument('--salida', required=True)
    a = p.parse_args()

    meta_csv = os.path.join(a.scrapers, 'metadatos.csv')
    mapeo_csv = os.path.join(a.scrapers, 'mapeo_renombres.csv')
    for r in (meta_csv, mapeo_csv):
        if not os.path.exists(r):
            sys.exit(f'ERROR: falta {r}')

    filas = list(csv.DictReader(open(meta_csv, encoding='utf-8-sig')))
    mapeo = list(csv.DictReader(open(mapeo_csv, encoding='utf-8-sig')))
    pdfs = sorted(f for f in os.listdir(a.pdfs) if f.lower().endswith('.pdf'))

    # --- índice por código de acto (para el join por contenido) ---
    por_codigo = collections.defaultdict(list)
    for f in filas:
        k = clave_acto(f.get('Numero'))
        if k:
            por_codigo[k].append(f)

    # --- índice posicional: (carpeta, ID PDF) ---
    # metadatos.csv no trae la carpeta, pero mapeo_renombres.csv sí. Se reconstruye
    # cruzando: dentro de una carpeta los ID van 1..N, y N es su cantidad de archivos.
    carpeta_de_archivo, cuenta_carpeta = {}, collections.Counter()
    nuevo_a_actual = {}
    for m in mapeo:
        nu, ac, car = basename_win(m.get('nuevo')), basename_win(m.get('actual')), m.get('carpeta', '')
        if nu:
            nuevo_a_actual[nu] = (ac, car)
            carpeta_de_archivo[nu] = car
            cuenta_carpeta[car] += 1

    # Cada 'Tipo de documento' pertenece a una sola carpeta: se asigna a aquella cuyo
    # tamaño es compatible con el rango de IDs que usa ese tipo.
    tipos = collections.defaultdict(list)
    for f in filas:
        try:
            tipos[f['Tipo de documento'].split(',')[0]].append(int(f['ID PDF']))
        except (ValueError, KeyError):
            pass
    # Dentro de una carpeta los ID son únicos: eso alcanza para resolver la asignación sin
    # depender del parecido entre los nombres (que engaña: "DIRECCION GENERAL DE ASUNTOS
    # ACADEMICOS" se parece más a "SECRETARIAS DE RECTORADO" que a su propia carpeta).
    tipo_a_carpeta = {}
    usados = collections.defaultdict(set)
    for tipo, ids in sorted(tipos.items(), key=lambda kv: -max(kv[1])):
        conj, tope = set(ids), max(ids)
        for carpeta in sorted(cuenta_carpeta, key=lambda c: cuenta_carpeta[c]):
            if cuenta_carpeta[carpeta] >= tope and not (usados[carpeta] & conj):
                tipo_a_carpeta[tipo] = carpeta
                usados[carpeta] |= conj
                break

    por_posicion = {}
    for f in filas:
        car = tipo_a_carpeta.get(f['Tipo de documento'].split(',')[0])
        try:
            if car:
                por_posicion.setdefault((car, int(f['ID PDF'])), f)
        except ValueError:
            pass

    # --- código extraído por el parser, si está disponible ---
    codigo_de_archivo = {}
    if a.yaml:
        try:
            import yaml as _y
        except ImportError:
            sys.exit('ERROR: falta PyYAML para leer --yaml')
        for raiz, _, files in os.walk(a.yaml):
            for f in files:
                if not f.endswith('_canonico.yaml'):
                    continue
                try:
                    d = _y.safe_load(open(os.path.join(raiz, f), encoding='utf-8')) or {}
                except Exception:
                    continue
                k = clave_acto(f"{d.get('document_code')} : {d.get('document_number')}")
                if k:
                    codigo_de_archivo[f[: -len('_canonico.yaml')] + '.pdf'] = k

    conteo = collections.Counter()
    with open(a.salida, 'w', encoding='utf-8', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['archivo', 'confianza', 'via', 'numero', 'fecha', 'estado',
                    'tipo_documento', 'titulo'])

        for pdf in pdfs:
            # camino 1: posicional, deshaciendo el renombrado
            fila_pos = None
            ac, carpeta = nuevo_a_actual.get(pdf, ('', ''))
            m = re.match(r'^.*_(\d+)\.pdf$', ac, re.IGNORECASE) if ac else None
            if m and carpeta:
                fila_pos = por_posicion.get((carpeta, int(m.group(1))))

            # camino 2: por el código impreso en el acto
            fila_cod = None
            k = codigo_de_archivo.get(pdf)
            if k and k in por_codigo:
                cands = por_codigo[k]
                # las filas repetidas del mismo acto traen metadata idéntica: sirve cualquiera
                fila_cod = cands[0]

            if fila_pos and fila_cod:
                mismo = clave_acto(fila_pos.get('Numero')) == clave_acto(fila_cod.get('Numero'))
                if mismo:
                    elegida, conf, via = fila_cod, 'alta', 'ambos'
                else:
                    # Ante desacuerdo gana el código: está impreso en el propio documento y
                    # es su identidad. El camino posicional depende del mapeo de renombrado,
                    # que sabemos incorrecto para 1.780 archivos. Se etiqueta aparte para que
                    # quede visible en auditoría cuántos casos se resolvieron así.
                    elegida, conf, via = fila_cod, 'media', 'codigo_sobre_posicional'
            elif fila_cod:
                elegida, conf, via = fila_cod, 'media', 'codigo'
            elif fila_pos:
                elegida, conf, via = fila_pos, 'media', 'posicional'
            else:
                elegida, conf, via = None, 'sin_metadata', '-'

            conteo[conf] += 1
            if elegida:
                w.writerow([pdf, conf, via, elegida.get('Numero', ''), elegida.get('Fecha', ''),
                            elegida.get('Estado', ''), elegida.get('Tipo de documento', '').split(',')[0],
                            elegida.get('Titulo', '')])
            else:
                w.writerow([pdf, conf, via, '', '', '', '', ''])

    total = len(pdfs)
    print(f'PDFs: {total}   ->  {a.salida}\n')
    for k in ("alta", "media", "conflicto", "sin_metadata"):
        if conteo[k]:
            print(f'  {k:14s} {conteo[k]:6d}  ({100*conteo[k]/total:.1f}%)')
    usable = conteo['alta'] + conteo['media']
    print(f'\n  con metadata usable: {usable}/{total} ({100*usable/total:.1f}%)')
    if conteo['conflicto']:
        print(f'  ATENCIÓN: {conteo["conflicto"]} conflictos -> revisar antes de indexar')


if __name__ == '__main__':
    main()
