"""Piezas compartidas por los recolectores: identidad de los actos y forma del CSV.

Viven acá y no en un recolector concreto para que el que usa la API no dependa del que
usa el navegador ---ni de Selenium---. Antes importaba de él y no se podía correr sin
tener Chrome instalado, que es lo contrario de lo que se quería.
"""
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone

import conf

API = conf.PORTAL_URL.split('/sudocu/')[0] + '/sudocu/api'

# El portal expresa las fechas en UTC pero las del acto son medianoche de Argentina
# (03:00Z). Convertir con este desplazamiento evita que un acto del 29 aparezca como 28.
AR = timezone(timedelta(hours=-3))

COLUMNAS = [
    # Compatibles con el pipeline existente (unir_metadata.py las consume por nombre).
    'Tipo de documento', 'Numero', 'Estado', 'Fecha', 'Titulo', 'ID PDF',
    # El vínculo explícito con el archivo: esto es lo que vuelve innecesaria toda
    # reconstrucción posicional.
    'Archivo', 'sha256',
    # Identidad en el sistema de origen y URL permanente al documento oficial.
    'id_archivo', 'id_documento', 'URL',
    # Desglose del número, que el portal ya trae separado en lugar de en una cadena.
    'Seccion', 'Codigo', 'Nro', 'Anio', 'Organismo',
    # Identificador del tipo de documento en la instalación de origen. Es la clave estable
    # del organismo emisor: el NOMBRE cambia de forma entre universidades y hasta entre
    # registros de la misma ---"DISPOSICION SECRETARIA DE CIENCIA Y TECNOLOGIA" y
    # "DISPOSICIONES SECRETARÍA DE CIENCIA, TECNOLOGÍA E INNOVACIÓN" son dos---, y el
    # código puede repetirse. Se guarda aunque hoy no se use: capturarlo cuesta un campo y
    # recuperarlo después cuesta recolectar el portal entero de nuevo.
    'id_tipo',
    # La fecha del acto, distinta de la de autorización que muestra la tabla del portal:
    # 'Fecha' es cuándo se firmó, 'Fecha acto' es la que está impresa en el documento.
    'Fecha acto',
]


def _fecha(iso, formato='%d/%m/%Y'):
    if not iso:
        return ''
    try:
        return datetime.fromisoformat(str(iso).replace('Z', '+00:00')).astimezone(AR).strftime(formato)
    except (ValueError, TypeError):
        return ''


def _limpio(t):
    """Nombre de archivo seguro y estable a partir del identificador del acto."""
    t = unicodedata.normalize('NFD', str(t or ''))
    t = ''.join(c for c in t if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^A-Za-z0-9]+', '-', t).strip('-').upper()


def fila_a_registro(d, seccion):
    """Traduce el objeto que el portal tiene atado a la fila a una fila de metadatos.

    Se toma del objeto y no del texto renderizado en la tabla: el número viene ya
    desglosado en tipo, número, año y organismo, y no hay que volver a parsear una cadena
    que el portal arma para mostrar.
    """
    nro = d.get('nro') or {}
    id_archivo, id_documento = d.get('documento'), d.get('id')
    return {
        'Tipo de documento': d.get('tipo') or '',
        'Numero': re.sub(r'\s+', ' ', str(d.get('numero_asignado') or '')).strip(),
        'Estado': (d.get('estado') or {}).get('nombre') or '',
        'Fecha': _fecha(d.get('fecha_autorizacion')),
        'Titulo': d.get('titulo') or '',
        'ID PDF': '',                      # se completa al escribir, por sección
        'Archivo': '',                     # idem
        'sha256': '',
        'id_archivo': id_archivo or '',
        'id_documento': id_documento or '',
        'URL': url_documento(id_archivo, id_documento) if id_archivo and id_documento else '',
        'Seccion': seccion,
        # El código del acto viene con clave distinta según la instalación: UNLu lo
        # llama 'tipo', UNSL 'codigo_tipo_documento'. Mismo dato; se prueban en orden.
        'Codigo': nro.get('tipo') or nro.get('codigo_tipo_documento') or '',
        'Nro': nro.get('nro') or '',
        'Anio': nro.get('anio') or '',
        'Organismo': nro.get('organismo') or '',
        'id_tipo': d.get('id_tipo') or '',
        'Fecha acto': _fecha(d.get('fecha')),
    }


def url_documento(id_archivo, id_documento):
    return (f'{API}/archivos/publico/{id_archivo}'
            f'?id_archivo={id_archivo}&id_documento={id_documento}')


def nombre_archivo(reg, tomados):
    """Nombre derivado de la identidad del acto, no de su posición en la descarga.

    Ante dos actos con el mismo código, número y año ---posible entre organismos--- se
    desempata con el identificador del archivo, que es único. Nunca con un contador.
    """
    base = '_'.join(x for x in (_limpio(reg['Codigo']), str(reg['Nro']), str(reg['Anio'])) if x)
    base = base or _limpio(reg['id_archivo'])[:16]
    nombre = f'{base}.pdf'
    if nombre in tomados:
        nombre = f'{base}__{reg["id_archivo"][:8]}.pdf'
    tomados.add(nombre)
    return nombre


