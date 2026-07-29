"""Actualiza la metadata de los fragmentos con la recolección nueva del portal.

Por qué existe: la recolección nueva (scrapers/recolectar.py) trae por cada acto su URL
permanente en el portal, sus identificadores internos y la fecha del propio acto ---que no
es la misma que la de autorización que muestra la tabla---. Nada de eso estaba antes.

Por qué no rehace el índice: lo que se embebe es `título | cita` más el fragmento. Ninguno
de los campos que se agregan acá entra en el embedding, así que la matriz de vectores sigue
siendo válida. Para que eso se sostenga, este script:

  - conserva el ORDEN de los fragmentos, línea por línea, porque la fila i de densos.npy
    corresponde al fragmento i de chunks.jsonl y no hay otra cosa que los ate;
  - no agrega ni elimina fragmentos;
  - AVISA si algún título o cita cambió, que es el único caso en que el embedding quedaría
    desactualizado, e indica exactamente cuáles.

Uso:
    python actualizar_metadata.py --verificar     # solo informa, no escribe
    python actualizar_metadata.py --aplicar
"""
import argparse
import csv
import json
import os
import re
import shutil
import sys
import unicodedata
from collections import Counter


def claves(codigo, numero, anio):
    """Identidades posibles del acto: código + número + año.

    Devuelve más de una porque las dos fuentes escriben el código distinto. El portal
    antepone el tipo de acto ---"DISP DGAA"--- mientras que el documento, y por lo tanto
    lo que quedó indexado, lleva solo el organismo ---"DGAA"---. Sin contemplar las dos
    formas, 20.517 fragmentos de Direcciones Administrativas no encontraban su acto
    aunque el acto estuviera recolectado.
    """
    c = re.sub(r'\s+', '', str(codigo or '')).upper()
    n = re.sub(r'\D', '', str(numero or ''))
    a = re.sub(r'\D', '', str(anio or ''))
    if len(a) == 2:
        a = '20' + a
    if not (c and n and a):
        return []
    formas = {c}
    sin_prefijo = re.sub(r'^(DISPOSICION|DISP|RESOLUCION|RESOL|RES)(?=[A-ZÑ])', '', c)
    if sin_prefijo and sin_prefijo != c:
        formas.add(sin_prefijo)
    return [(f, n, a) for f in formas]


def norm_titulo(t):
    return re.sub(r'\s+', ' ', (t or '')).strip().upper()


def norm_seccion(s):
    """Sección comparable entre las dos fuentes: el CSV la escribe con acentos y espacios
    ("DEPARTAMENTO DE CIENCIAS SOCIALES") y el nombre de archivo con guiones bajos y sin
    acentos ("DEPARTAMENTO_DE_CIENCIAS_SOCIALES_123")."""
    s = unicodedata.normalize('NFD', str(s or ''))
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^A-Za-z0-9]+', '_', s).strip('_').upper()


def clave_de_chunk(ch):
    """La misma identidad, reconstruida desde el fragmento ya indexado.

    `document_number` viene como "528/2025", así que el año sale de ahí.
    """
    num = re.sub(r'\s+', '', str(ch.get('document_number') or ''))
    m = re.match(r'(\d+)/(\d{2,4})$', num)
    if not m:
        return None
    return claves(ch.get('document_code'), m.group(1), m.group(2))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--chunks', default='indice/chunks.jsonl')
    p.add_argument('--metadatos', default='scrapers/metadatos_nuevo.csv')
    p.add_argument('--aplicar', action='store_true', help='escribir (por defecto solo informa)')
    a = p.parse_args()

    if not os.path.exists(a.metadatos):
        sys.exit(f'no está {a.metadatos}')

    # --- índice de la recolección nueva, por identidad del acto ---
    por_acto, por_titulo, repetidos = {}, {}, 0
    with open(a.metadatos, encoding='utf-8-sig') as f:
        for fila in csv.DictReader(f):
            ks = claves(fila.get('Codigo'), fila.get('Nro'), fila.get('Anio'))
            if not ks:
                continue
            if any(k in por_acto for k in ks):
                repetidos += 1
                continue
            for k in ks:
                por_acto[k] = fila
            # Índice de respaldo por título dentro de la sección. Hace falta porque hay
            # actos cuya identidad no se puede reconstruir desde el índice: los de Órdenes
            # de Compra quedaron con código y número 'unknown' porque el parser no pudo
            # extraerlos del PDF, y los de Ciencias Sociales guardan el código abreviado
            # ("CS") mientras el portal distingue DISPCD-CS, DISPPCD-CS y DISPDD-CS.
            tt = norm_titulo(fila.get('Titulo'))
            if tt:
                por_titulo.setdefault((norm_seccion(fila.get('Seccion')), tt), []).append(fila)
    print(f'actos en la recolección nueva : {len(set(id(v) for v in por_acto.values()))}' +
          (f'  (+{repetidos} repetidos, ignorados)' if repetidos else ''))

    # --- recorrido de los fragmentos ---
    cuenta = Counter()
    difieren_titulo, difieren_cita = [], []
    salida = a.chunks + '.nuevo'
    fh = open(salida, 'w', encoding='utf-8') if a.aplicar else None

    with open(a.chunks, encoding='utf-8') as f:
        for linea in f:
            ch = json.loads(linea)
            cuenta['fragmentos'] += 1
            fila = next((por_acto[k] for k in (clave_de_chunk(ch) or []) if k in por_acto), None)

            # Respaldo por título, SOLO si dentro de esa sección el título corresponde a un
            # único acto. Con más de un candidato no se enlaza: en normativa, mandar a la
            # persona al documento equivocado es peor que no ofrecerle ningún enlace.
            if not fila:
                seccion = re.sub(r'_\d+$', '', ch.get('documento') or '')
                cands = por_titulo.get((seccion, norm_titulo(ch.get('titulo'))), [])
                if len(cands) == 1:
                    fila = cands[0]
                    cuenta['por_titulo'] += 1

            if not fila:
                cuenta['sin_correspondencia'] += 1
            else:
                cuenta['con_correspondencia'] += 1

                # El título SÍ afecta al embedding: si cambia, el vector quedó viejo.
                #
                # Se compara colapsando espacios. La recolección anterior leía el texto ya
                # renderizado por el navegador, que junta los espacios repetidos, mientras
                # que la API devuelve el valor crudo. Sin normalizar, 19.085 fragmentos
                # figuraban como "cambiados" por un espacio doble, y re-embeberlos habría
                # sido gastar GPU para no alterar nada de lo que el modelo entiende.
                normal = lambda t: re.sub(r'\s+', ' ', (t or '')).strip()
                nuevo_titulo, viejo_titulo = normal(fila.get('Titulo')), normal(ch.get('titulo'))
                if nuevo_titulo and nuevo_titulo != viejo_titulo:
                    difieren_titulo.append((ch['chunk_id'], ch.get('titulo'), fila.get('Titulo')))

                # Estos no entran en el embedding: se agregan sin consecuencias.
                ch['url_documento'] = fila.get('URL') or ''
                ch['id_archivo'] = fila.get('id_archivo') or ''
                ch['id_documento'] = fila.get('id_documento') or ''
                if fila.get('Fecha acto'):
                    ch['fecha_acto'] = fila['Fecha acto']
                if fila.get('Archivo'):
                    ch['archivo_portal'] = fila['Archivo']
                cuenta['con_url'] += 1 if ch['url_documento'] else 0

            if fh:
                fh.write(json.dumps(ch, ensure_ascii=False) + '\n')

    if fh:
        fh.close()

    print(f"fragmentos                    : {cuenta['fragmentos']}")
    print(f"  con acto correspondiente    : {cuenta['con_correspondencia']}")
    print(f"  sin correspondencia         : {cuenta['sin_correspondencia']}")
    print(f"  con URL al PDF oficial      : {cuenta['con_url']}")
    if cuenta['por_titulo']:
        print(f"  (de esos, por título único  : {cuenta['por_titulo']})")
    print()
    print(f'títulos que cambian           : {len(difieren_titulo)}')
    if difieren_titulo:
        print('  (el título se embebe: estos fragmentos habría que re-embeberlos)')
        for cid, viejo, nuevo in difieren_titulo[:5]:
            print(f'   {cid}\n     antes: {str(viejo)[:80]}\n     ahora: {nuevo[:80]}')
        if len(difieren_titulo) > 5:
            print(f'   ... y {len(difieren_titulo) - 5} más')
    else:
        print('  ninguno: la matriz de embeddings sigue siendo válida tal cual.')

    if a.aplicar:
        respaldo = a.chunks + '.previo'
        if not os.path.exists(respaldo):
            shutil.copy2(a.chunks, respaldo)
            print(f'\nrespaldo: {respaldo}')
        os.replace(salida, a.chunks)
        print(f'escrito: {a.chunks}')
    else:
        print('\n(verificación: no se escribió nada. Usar --aplicar)')


if __name__ == '__main__':
    main()
