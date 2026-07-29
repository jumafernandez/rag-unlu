"""Recolector por API, sin navegador.

El portal expone el listado de cada carpeta pública en un endpoint que se puede consultar
directamente:

    {api}/mpd/guest/documentos?dir=DESC&idContenedor={id}&limit=15&offset={n}&orden=nombre_plural

Devuelve los mismos objetos que la interfaz muestra en la tabla, con sus identificadores.
No hace falta Selenium, ni Chrome, ni esperar renderizados.

Por qué existe además de recolectar.py: hay carpetas que la interfaz NO logra mostrar
---devuelve "No se encontraron documentos"--- pero que por API sí responden. SECRETARIAS
DE RECTORADO (4.644 actos en nuestro corpus) es una de ellas. Sin esta vía, esos documentos
quedarían sin enlace al PDF oficial por una falla de la pantalla, no de los datos.

Los ids de carpeta salen del objeto que la portada tiene atado a cada una:

    angular.element(elemento).scope()  ->  { id: 30, ... }

Uso:
    python recolectar_api.py --carpeta 30 --salida meta_secretarias.csv
    python recolectar_api.py --todas --salida meta_api.csv
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request

import conf
from catalogo_comun import API, COLUMNAS, fila_a_registro, nombre_archivo

# Carpetas públicas del portal de la UNLu, leídas del scope de la portada. Para otra
# universidad se releen de la suya: son ids internos de esa instalación.
CARPETAS = {
    6: 'RESOLUCIONES RECTOR',
    9: 'RESOLUCIONES ASAMBLEA UNIVERSITARIA',
    10: 'DEPARTAMENTO DE CIENCIAS BÁSICAS',
    12: 'RESOLUCIONES H. CONSEJO SUPERIOR',
    13: 'RESOLUCIONES PRESIDENTE H. CONSEJO SUPERIOR',
    26: 'DEPARTAMENTO DE TECNOLOGIA',
    27: 'DEPARTAMENTO DE CIENCIAS SOCIALES',
    28: 'DEPARTAMENTO DE EDUCACION',
    29: 'DIRECCIONES ADMINISTRATIVAS',
    30: 'SECRETARIAS DE RECTORADO',
    31: 'ORDENES DE COMPRA',
}

# El servidor ignora valores mayores: pedir 100 devuelve cero documentos, no cien.
TOPE = 15


def registrar(traza, dato):
    """Una línea por pedido al portal. Es lo que permite explicar un faltante.

    Sin esto la recolección informa un total y, si termina corta, no hay manera de saber
    qué se pidió, qué contestó el servidor y en qué tramo se perdieron los documentos.
    Un recolector que no se puede auditar no sirve para armar un corpus normativo.
    """
    if traza:
        traza.write(json.dumps(dato, ensure_ascii=False) + '\n')
        traza.flush()


def pedir(cid, offset, intentos=3):
    url = (f'{API}/mpd/guest/documentos?dir=DESC&idContenedor={cid}'
           f'&limit={TOPE}&limitOptions=10&limitOptions=15&offset={offset}&orden=nombre_plural')
    for n in range(intentos):
        try:
            pedido = urllib.request.Request(url, headers={'User-Agent': 'rag-unlu/1.0'})
            with urllib.request.urlopen(pedido, timeout=180) as r:
                cuerpo = r.read().decode('utf-8', 'replace').strip()
            if not cuerpo:
                return None
            return json.loads(cuerpo)
        except Exception:
            if n == intentos - 1:
                raise
            time.sleep(3 * (n + 1))
    return None


def recolectar(cid, nombre, salida, maximo=None, tope_vacias=6, traza=None):
    print(f'\n=== [{cid}] {nombre} ===', flush=True)
    registros, tomados, vistos, offset = [], set(), set(), 0
    vacias, total_portal = 0, None
    while True:
        t_ini = time.time()
        d = pedir(cid, offset)
        docs = (d or {}).get('documents') or []
        registrar(traza, {'carpeta': cid, 'nombre': nombre, 'offset': offset,
                          'devueltos': len(docs), 'segundos': round(time.time() - t_ini, 2),
                          'ids': [x.get('documento') for x in docs]})
        if not docs:
            # Una respuesta vacía NO significa que se acabó el listado. El servidor
            # responde en falso de manera intermitente: la misma consulta que devuelve
            # quince documentos, repetida un minuto después, devuelve cero. Es la misma
            # falla que hace que la pantalla del portal muestre "No se encontraron
            # documentos" en carpetas que sí tienen contenido. Se reintenta el mismo
            # tramo varias veces antes de darlo por terminado.
            vacias += 1
            registrar(traza, {'carpeta': cid, 'offset': offset, 'evento': 'vacia',
                              'consecutivas': vacias})
            if vacias >= tope_vacias:
                print(f'  fin en offset {offset} ({tope_vacias} respuestas vacías seguidas)',
                      flush=True)
                break
            # Espera creciente y acotada: el servidor se recupera solo, pero tarda.
            time.sleep(min(5 * vacias, 45))
            continue
        vacias = 0
        # El portal declara, en cada documento, cuántos tiene la carpeta. Es el único dato
        # con el que se puede AFIRMAR que una recolección quedó completa en vez de suponerlo.
        if total_portal is None:
            try:
                total_portal = int(docs[0].get('total') or 0) or None
            except (TypeError, ValueError):
                total_portal = None
        nuevos = 0
        for x in docs:
            if not x.get('documento') or x['documento'] in vistos:
                continue
            vistos.add(x['documento'])
            nuevos += 1
            reg = fila_a_registro(x, nombre)
            reg['Archivo'] = nombre_archivo(reg, tomados)
            registros.append(reg)
        offset += TOPE
        if len(registros) % 150 < TOPE:
            print(f'  {len(registros)} documentos (offset {offset})', flush=True)
        # Una página entera repetida sí indica que el listado dejó de avanzar.
        if not nuevos:
            registrar(traza, {'carpeta': cid, 'offset': offset, 'evento': 'sin_nuevos',
                              'acumulado': len(registros)})
            print(f'  fin en offset {offset} (sin documentos nuevos)', flush=True)
            break
        # Si ya se juntó lo que el portal declara, se termina sin esperar más. Antes había
        # que confirmar el final agotando la paciencia contra respuestas vacías, y eso
        # costaba varios minutos por carpeta sin aportar nada.
        if total_portal and len(registros) >= total_portal:
            print(f'  completa: {len(registros)} de {total_portal}', flush=True)
            break
        if maximo and len(registros) >= maximo:
            break

    for i, reg in enumerate(registros, 1):
        reg['ID PDF'] = i
    if registros:
        existe = os.path.exists(salida)
        with open(salida, 'a', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=COLUMNAS)
            if not existe:
                w.writeheader()
            w.writerows(registros)
    if total_portal:
        faltan = total_portal - len(registros)
        estado = 'COMPLETA' if faltan == 0 else f'INCOMPLETA: faltan {faltan}'
        print(f'  -> {len(registros)} de {total_portal} que declara el portal  [{estado}]', flush=True)
    else:
        print(f'  -> {len(registros)} documentos (el portal no declaró total)', flush=True)
    return registros, total_portal


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--carpeta', type=int, action='append', help='id de carpeta (repetible)')
    p.add_argument('--todas', action='store_true')
    p.add_argument('--salida', required=True)
    p.add_argument('--maximo', type=int, help='cortar después de N documentos (para probar)')
    p.add_argument('--traza', default=None,
                   help='JSONL con una línea por pedido: offset, cuántos volvieron y sus ids')
    p.add_argument('--paciencia', type=int, default=6,
                   help='respuestas vacías seguidas antes de dar por terminada una carpeta')
    a = p.parse_args()

    ids = a.carpeta or (sorted(CARPETAS) if a.todas else None)
    if not ids:
        sys.exit('indicá --carpeta ID o --todas')

    # Reanudable por carpeta: lo ya recolectado en este CSV no se repite.
    if os.path.exists(a.salida):
        with open(a.salida, encoding='utf-8-sig') as f:
            hechas = {r['Seccion'] for r in csv.DictReader(f)}
        antes = len(ids)
        ids = [i for i in ids if CARPETAS.get(i) not in hechas]
        if len(ids) < antes:
            print(f'ya estaban: {sorted(hechas)}', flush=True)
        if not ids:
            sys.exit('no queda ninguna carpeta por recolectar en este CSV')

    traza = open(a.traza, 'a', encoding='utf-8') if a.traza else None
    t0, total, resumen = time.time(), 0, []
    for cid in ids:
        try:
            regs, tp = recolectar(cid, CARPETAS.get(cid, f'carpeta {cid}'),
                                  a.salida, a.maximo, a.paciencia, traza)
            total += len(regs)
            resumen.append((CARPETAS.get(cid, str(cid)), len(regs), tp))
        except Exception as e:
            print(f'  ERROR en la carpeta {cid}: {type(e).__name__}: {e}', flush=True)
            resumen.append((CARPETAS.get(cid, str(cid)), None, None))

    print(f'\n=== resumen: {total} documentos en {time.time() - t0:.0f}s -> {a.salida} ===')
    incompletas = 0
    for nombre, n, tp in resumen:
        if n is None:
            print(f'  {nombre[:40]:42s} ERROR'); incompletas += 1
        elif tp and n < tp:
            print(f'  {nombre[:40]:42s} {n:>6} de {tp:>6}  INCOMPLETA'); incompletas += 1
        else:
            print(f'  {nombre[:40]:42s} {n:>6} de {str(tp or "?"):>6}  ok')
    if traza:
        traza.close()
        print(f'traza: {a.traza}')
    if incompletas:
        print(f'\n{incompletas} carpeta(s) sin completar: volver a correr solo esas '
              f'con --carpeta ID (lo ya recolectado no se repite).')


if __name__ == '__main__':
    main()
