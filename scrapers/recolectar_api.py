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
# Respaldo por si el endpoint de carpetas no responde. La fuente primaria es el propio
# portal: `carpetas_del_portal()` las descubre en un pedido, con esto como plan B.
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


def carpetas_del_portal():
    """Las carpetas públicas, preguntadas al portal en vez de codificadas a mano.

    El endpoint es el mismo que usa la portada para dibujarse. Esto es lo que vuelve al
    recolector portable de verdad: en otra universidad las carpetas son otras y tienen
    otros ids, y nadie tiene que ir a hurgar el scope de Angular para averiguarlos.
    Si el pedido falla se usa el mapa estático de arriba: peor es no recolectar.
    """
    url = f'{API}/mpd/contenedores/?id_area=0'
    try:
        pedido = urllib.request.Request(url, headers={'User-Agent': 'rag-unlu/1.0'})
        with urllib.request.urlopen(pedido, timeout=60) as r:
            carpetas = json.load(r).get('folders', [])
        publicas = {c['id']: c.get('contenedor', f"carpeta {c['id']}")
                    for c in carpetas if c.get('publico') and not c.get('eliminado')}
        if publicas:
            return publicas
    except Exception as e:
        print(f'(no se pudo pedir la lista de carpetas: {e}; se usa el mapa estático)',
              flush=True)
    return dict(CARPETAS)


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


def total_declarado(cid, insistencias=6):
    """El total que el portal declara para una carpeta. None si no se logra saberlo.

    Se insiste porque el portal devuelve cuerpo vacío de manera intermitente, y no parejo:
    las carpetas más grandes ---Secretarías, Direcciones Administrativas--- fallan mucho
    más seguido que el resto. Preguntar una sola vez hacía que justo esas dos, las que más
    importa vigilar, quedaran sin total y por lo tanto sin verificación.

    Que devuelva None no es lo mismo que "está completa": quien llama tiene que tratarlo
    como "no pude verificar" y decirlo.
    """
    for n in range(insistencias):
        try:
            d = pedir(cid, 0, intentos=2)
            docs = (d or {}).get('documents') or []
            if docs:
                return int(docs[0].get('total') or 0) or None
        except Exception:
            pass
        if n < insistencias - 1:
            time.sleep(min(4 * (n + 1), 20))
    return None


class OrdenInesperado(Exception):
    """El portal dejó de devolver los documentos de más nuevo a más viejo.

    No es un detalle: la recolección incremental se apoya en ese orden para poder frenar
    apenas reconoce lo que ya tiene. Si el orden cambia ---otra versión de SUDOCU, otra
    configuración, otra universidad--- frenar temprano dejaría de significar "no hay nada
    nuevo" y empezaría a significar "no miré". Por eso se verifica en cada página y, si no
    se cumple, la carpeta se recolecta completa en vez de confiar.
    """


def verificar_orden(docs, techo):
    """Comprueba que lo más nuevo esté al principio del listado.

    La verificación NO es que cada fila sea más vieja que la anterior: se midió contra el
    portal de la UNLu y no se cumple ---en Consejo Superior aparece un acto del 12 de
    diciembre después de uno del 11---. El listado viene ordenado por algo que correlaciona
    con la fecha pero admite inversiones locales.

    Lo que sí tiene que valer, y es lo único que la lectura incremental necesita, es que
    ninguna página posterior traiga un documento MÁS NUEVO que el más nuevo de la primera.
    Si eso pasa, lo reciente no está al principio y frenar temprano dejaría afuera actos.

    `techo` es la fecha máxima de la primera página; None mientras no se estableció.
    """
    fechas = [(x.get('fecha') or '')[:10] for x in docs]
    fechas = [f for f in fechas if f]
    if not fechas:
        return techo
    if techo is None:
        return max(fechas)
    if max(fechas) > techo:
        raise OrdenInesperado(
            f'apareció un documento del {max(fechas)}, más nuevo que el tope de la '
            f'primera página ({techo}): el listado dejó de venir ordenado por recencia')
    return techo


def recolectar_incremental(cid, nombre, conocidos, paginas_limpias=3, tope_paginas=200,
                           traza=None):
    """Los documentos de una carpeta que todavía no tenemos, leyendo solo la punta.

    El portal devuelve de más nuevo a más viejo, así que lo que cambió está al principio y
    no hace falta recorrer la carpeta entera: se lee hasta que varias páginas seguidas no
    traen nada desconocido. En un corpus de doscientos mil actos, una actualización semanal
    lee tres páginas por carpeta en vez de doscientas mil filas.

    `paginas_limpias` no es 1 a propósito. Un acto cargado con retraso ---fechado en marzo,
    publicado hoy--- entra en el listado por su fecha, o sea sepultado bajo los más nuevos.
    Frenar con el primer conocido lo dejaría afuera para siempre. Con tres páginas de
    margen se recuperan los rezagados sin pagar el costo del listado completo; los que
    caigan más atrás los levanta la reconciliación completa.

    `conocidos` son los identificadores de documento que ya están en el catálogo, es decir
    la columna `id_documento` del CSV. OJO con el mapeo, porque está cruzado y es fácil
    comparar el campo equivocado: el `id` que devuelve la API se guarda como
    `id_documento`, y el campo que la API llama `documento` se guarda como `id_archivo`.
    Comparar contra el que no es hace que todo parezca nuevo y la lectura incremental
    recorra la carpeta entera sin decir por qué.

    Devuelve (documentos_nuevos, total_declarado). Levanta OrdenInesperado si el portal
    deja de ordenar como se espera, para que quien llama recolecte la carpeta completa.
    """
    nuevos, offset, limpias, total_portal = [], 0, 0, None
    techo, vistos = None, set()

    while offset < tope_paginas * TOPE:
        d = pedir(cid, offset)
        docs = (d or {}).get('documents') or []
        registrar(traza, {'carpeta': cid, 'nombre': nombre, 'offset': offset,
                          'modo': 'incremental', 'devueltos': len(docs)})
        if not docs:
            break
        techo = verificar_orden(docs, techo)
        if total_portal is None:
            try:
                total_portal = int(docs[0].get('total') or 0) or None
            except (TypeError, ValueError):
                total_portal = None

        desconocidos = 0
        for x in docs:
            ident = x.get('id')          # se guarda como id_documento en el catálogo
            if not ident or ident in vistos:
                continue
            vistos.add(ident)
            if ident in conocidos:
                continue
            desconocidos += 1
            nuevos.append(x)

        limpias = 0 if desconocidos else limpias + 1
        if limpias >= paginas_limpias:
            break
        offset += TOPE

    print(f'  {nombre}: {len(nuevos)} nuevos leyendo {offset // TOPE + 1} páginas', flush=True)
    return nuevos, total_portal


def anexar_nuevos(docs, nombre, salida, filas_previas):
    """Agrega al final del catálogo los documentos nuevos de una carpeta.

    El nombre de archivo se deriva de la identidad del acto, así que hay que sembrar
    `tomados` con TODO lo que ya está en el CSV ---no solo con la carpeta que se está
    actualizando---: dos organismos distintos pueden llegar al mismo código, número y año,
    y ahí el desempate por identificador de archivo es lo único que evita que un acto nuevo
    se descargue encima de uno viejo.
    """
    tomados = {r['Archivo'] for r in filas_previas if r.get('Archivo')}
    ordinales = [int(r['ID PDF']) for r in filas_previas
                 if r.get('Seccion') == nombre and str(r.get('ID PDF', '')).isdigit()]
    siguiente = max(ordinales, default=0)

    escritos = []
    with open(salida, 'a', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS)
        # Se anexan de más viejo a más nuevo para que el orden del CSV siga siendo el del
        # listado: el portal los entrega al revés.
        for x in reversed(docs):
            reg = fila_a_registro(x, nombre)
            reg['Archivo'] = nombre_archivo(reg, tomados)
            siguiente += 1
            reg['ID PDF'] = siguiente
            w.writerow(reg)
            escritos.append(reg)
    return escritos


def recolectar(cid, nombre, salida, maximo=None, tope_vacias=6, traza=None):
    print(f'\n=== [{cid}] {nombre} ===', flush=True)
    registros, tomados, vistos, offset = [], set(), set(), 0
    vacias, total_portal = 0, None

    # El CSV se escribe PÁGINA a página, no al final de la carpeta: un corte a mitad de
    # una carpeta grande no pierde nada, y reanudar es rehacer solo la carpeta corta.
    existe = os.path.exists(salida)
    archivo_csv = open(salida, 'a', newline='', encoding='utf-8-sig')
    escritor = csv.DictWriter(archivo_csv, fieldnames=COLUMNAS)
    if not existe:
        escritor.writeheader()
    while True:
        t_ini = time.time()
        d = pedir(cid, offset)
        docs = (d or {}).get('documents') or []
        registrar(traza, {'carpeta': cid, 'nombre': nombre, 'offset': offset,
                          'devueltos': len(docs), 'segundos': round(time.time() - t_ini, 2),
                          'ids': [x.get('documento') for x in docs]})
        if not docs:
            # Pasado el total declarado, una respuesta vacía ES el fin del listado, no
            # intermitencia. Sin este corte, cada carpeta cuyo total viene inflado
            # ---el portal repite filas entre páginas y las cuenta--- terminaba
            # quemando toda la paciencia persiguiendo un documento que no existe:
            # en la carpeta 26 se midieron 1808 filas con 1807 ids distintos.
            if total_portal and offset >= total_portal:
                print(f'  fin del listado en offset {offset} (total declarado '
                      f'{total_portal}; el portal repite filas entre páginas)', flush=True)
                break
            # Antes del total, una respuesta vacía NO significa que se acabó el listado.
            # El servidor responde en falso de manera intermitente: la misma consulta que
            # devuelve quince documentos, repetida un minuto después, devuelve cero. Es la
            # misma falla que hace que la pantalla del portal muestre "No se encontraron
            # documentos" en carpetas que sí tienen contenido. Se reintenta el mismo tramo
            # varias veces antes de darlo por terminado.
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
            reg['ID PDF'] = len(registros) + 1
            registros.append(reg)
            escritor.writerow(reg)
        archivo_csv.flush()
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

    archivo_csv.close()
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
    p.add_argument('--completo', action='store_true',
                   help='listar cada carpeta entera en vez de leer solo lo nuevo')
    a = p.parse_args()

    # Siempre se pregunta al portal, también con --carpeta: el nombre de la sección
    # sale de acá, y el mapa estático es solo el respaldo si el pedido falla.
    carpetas = carpetas_del_portal()
    ids = a.carpeta or (sorted(carpetas) if a.todas else None)
    if not ids:
        sys.exit('indicá --carpeta ID o --todas')

    # Reanudable por carpeta, mirando el TOTAL: una carpeta solo se saltea si lo que hay
    # en el CSV alcanza lo que el portal declara (con tolerancia de 3, porque el total
    # viene inflado por filas repetidas entre páginas: se midieron diferencias de 1 a 3).
    # Una carpeta corta ---un corte a mitad de listado--- se REHACE: sus filas se sacan
    # del CSV y se lista de nuevo. Antes bastaba una fila para saltear la carpeta entera,
    # y una interrupción dejaba agujeros permanentes.
    if os.path.exists(a.salida) and not a.completo:
        with open(a.salida, encoding='utf-8-sig') as f:
            filas_previas = list(csv.DictReader(f))
        conocidos_por_seccion = {}
        for r in filas_previas:
            if r.get('id_documento'):
                conocidos_por_seccion.setdefault(r['Seccion'], set()).add(r['id_documento'])

        al_dia, rehacer, anexados = [], [], 0
        for cid in list(ids):
            nombre = carpetas.get(cid, f'carpeta {cid}')
            conocidos = conocidos_por_seccion.get(nombre) or set()
            if not conocidos:
                continue                      # carpeta nunca recolectada: listado completo
            total = total_declarado(cid)
            try:
                nuevos, total_pag = recolectar_incremental(cid, nombre, conocidos, traza=None)
            except OrdenInesperado as e:
                print(f'  {nombre}: {e} -> se rehace entera', flush=True)
                rehacer.append(nombre)
                continue
            total = total or total_pag
            tengo = len(conocidos) + len(nuevos)

            # La segunda verificación, y la que atrapa lo que la primera no puede ver: lo
            # que ya está en el catálogo más lo que se acaba de encontrar tiene que dar lo
            # que el portal declara para esa carpeta. La lectura incremental mira la punta
            # del listado, así que encuentra lo recién publicado pero es ciega a los
            # agujeros del medio ---un acto que se perdió en una corrida cortada hace
            # meses---. Esos solo aparecen como una resta que no cierra, y entonces la
            # carpeta se rehace completa. La tolerancia de 3 es la misma de siempre: el
            # portal repite filas entre páginas y las cuenta en el total.
            if total is not None and tengo < total - 3:
                print(f'  {nombre}: {tengo} de {total} tras leer lo nuevo -> se rehace '
                      f'entera (faltan {total - tengo} del medio del listado)', flush=True)
                rehacer.append(nombre)
                continue

            if nuevos:
                filas_previas.extend(anexar_nuevos(nuevos, nombre, a.salida, filas_previas))
                anexados += len(nuevos)
            if total is None:
                print(f'  {nombre}: {tengo} en el catálogo, {len(nuevos)} nuevos; el portal '
                      f'no declaró total: NO SE PUDO VERIFICAR que esté completa', flush=True)
            al_dia.append(nombre)
            ids = [i for i in ids if i != cid]

        if anexados:
            print(f'{anexados} documentos nuevos agregados al catálogo', flush=True)
        if al_dia:
            print(f'al día: {sorted(al_dia)}', flush=True)
        if rehacer:
            conservar = [r for r in filas_previas if r['Seccion'] not in rehacer]
            with open(a.salida, 'w', newline='', encoding='utf-8-sig') as f:
                w = csv.DictWriter(f, fieldnames=COLUMNAS)
                w.writeheader()
                w.writerows(conservar)
        if not ids:
            # Que no quede nada por hacer es un final feliz, no un error: salir con
            # código distinto de cero hacía que el panel lo mostrara como "Falló".
            print('no queda ninguna carpeta por recolectar: el catálogo está completo',
                  flush=True)
            return

    traza = open(a.traza, 'a', encoding='utf-8') if a.traza else None
    t0, total, resumen = time.time(), 0, []
    for cid in ids:
        try:
            regs, tp = recolectar(cid, carpetas.get(cid, f'carpeta {cid}'),
                                  a.salida, a.maximo, a.paciencia, traza)
            total += len(regs)
            resumen.append((carpetas.get(cid, str(cid)), len(regs), tp))
        except Exception as e:
            print(f'  ERROR en la carpeta {cid}: {type(e).__name__}: {e}', flush=True)
            resumen.append((carpetas.get(cid, str(cid)), None, None))

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
