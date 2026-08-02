#!/usr/bin/env python3
"""Sonda de portabilidad: ¿este portal SUDOCU sirve para montar el asistente?

Hace, en solo lectura y con TRES pedidos, las comprobaciones de las que depende todo el
pipeline. Sirve para responder con evidencia ---y no con fe--- la pregunta "¿esto funciona
en otra universidad?" antes de comprometerse a nada:

    1. contenedores    ¿expone la lista de carpetas? ¿cuáles son públicas?
    2. documentos      ¿el listado trae los campos de los que depende el recolector?
                       (id, documento, total, nro desglosado, fechas, tipo)
    3. archivo         ¿la URL pública del PDF responde con un PDF de verdad?

El tráfico es equivalente a abrir la portada en un navegador y tocar una carpeta. Aun
así: es un sistema institucional ajeno, conviene avisar antes de correr nada más grande
que esta sonda (ver docs/recoleccion.md).

Uso:
    python probar_portal.py --portal https://portal.unlu.edu.ar/sudocu/mpd/#!/mpd/portada
    python probar_portal.py --portal https://<otra-universidad>/sudocu/mpd/
"""
import argparse
import json
import sys
import urllib.request

CAMPOS_DOCUMENTO = ('id', 'documento', 'total', 'titulo', 'tipo', 'numero_asignado',
                    'fecha', 'fecha_autorizacion', 'estado', 'nro')


def pedir(url, timeout=40):
    pedido = urllib.request.Request(url, headers={'User-Agent': 'rag-unlu-sonda/1.0'})
    with urllib.request.urlopen(pedido, timeout=timeout) as r:
        return r.status, r.read()


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--portal', required=True,
                   help='URL del MPD (con o sin #!/mpd/portada)')
    a = p.parse_args()

    base = a.portal.split('/sudocu/')[0].rstrip('/')
    api = f'{base}/sudocu/api'
    fallas = []

    # --- 1. carpetas ---
    print(f'portal:  {base}')
    try:
        estado, cuerpo = pedir(f'{api}/mpd/contenedores/?id_area=0')
        carpetas = json.loads(cuerpo).get('folders', [])
    except Exception as e:
        print(f'\n1. contenedores: FALLÓ ({type(e).__name__}: {e})')
        print('   Sin lista de carpetas no hay recolección posible. FIN.')
        sys.exit(1)
    publicas = [c for c in carpetas if c.get('publico') and not c.get('eliminado')]
    print(f'\n1. contenedores: {len(carpetas)} carpetas, {len(publicas)} públicas')
    for c in publicas:
        print(f'   [{c["id"]:>3}] {c.get("contenedor", "?")}')
    if not publicas:
        print('   No hay carpetas públicas: no hay nada que recolectar. FIN.')
        sys.exit(1)

    # --- 2. listado de documentos de una carpeta ---
    carpeta = publicas[0]
    url = (f'{api}/mpd/guest/documentos?dir=DESC&idContenedor={carpeta["id"]}'
           f'&limit=15&offset=0&orden=nombre_plural')
    try:
        estado, cuerpo = pedir(url)
        docs = (json.loads(cuerpo) or {}).get('documents') or []
    except Exception as e:
        print(f'\n2. documentos: FALLÓ ({type(e).__name__}: {e})')
        sys.exit(1)
    print(f'\n2. documentos ({carpeta.get("contenedor")}): {len(docs)} en la primera página')
    if not docs:
        print('   Vacío. Con este portal puede ser intermitencia: probar de nuevo.')
        sys.exit(1)
    d = docs[0]
    for campo in CAMPOS_DOCUMENTO:
        presente = campo in d and d[campo] is not None
        print(f'   {"ok " if presente else "FALTA"} {campo}'
              + (f' = {json.dumps(d[campo], ensure_ascii=False)[:60]}' if presente else ''))
        if not presente and campo in ('id', 'documento', 'total'):
            fallas.append(f'campo {campo} ausente en el listado')
    total = d.get('total')
    print(f'   el portal declara {total} documentos en la carpeta '
          f'(criterio de completitud: {"disponible" if total else "NO disponible"})')

    # --- 3. el PDF público ---
    id_archivo, id_documento = d.get('documento'), d.get('id')
    if id_archivo and id_documento:
        url_pdf = (f'{api}/archivos/publico/{id_archivo}'
                   f'?id_archivo={id_archivo}&id_documento={id_documento}')
        try:
            estado, cuerpo = pedir(url_pdf)
            es_pdf = cuerpo[:5] == b'%PDF-'
            print(f'\n3. archivo público: HTTP {estado}, {len(cuerpo):,} bytes, '
                  f'{"es un PDF" if es_pdf else "NO es un PDF"}')
            if not es_pdf:
                fallas.append('la URL pública no devolvió un PDF')
        except Exception as e:
            print(f'\n3. archivo público: FALLÓ ({type(e).__name__}: {e})')
            fallas.append('la URL pública del PDF no respondió')
    else:
        fallas.append('sin identificadores para armar la URL del PDF')

    # --- veredicto ---
    print('\n' + '=' * 60)
    if fallas:
        print('VEREDICTO: este portal necesita adaptación:')
        for f in fallas:
            print(f'  - {f}')
        sys.exit(2)
    print('VEREDICTO: compatible. El recolector, el criterio de completitud y el\n'
          'enlace permanente a los PDF funcionan acá tal como están.')


if __name__ == '__main__':
    main()
