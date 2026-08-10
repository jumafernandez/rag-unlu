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


def ruta_totales(salida):
    """El archivo donde se recuerda cuántos documentos declara cada carpeta.

    Va al lado del catálogo, porque es del mismo tipo: dato de esta instalación, no código.
    """
    return os.path.join(os.path.dirname(salida) or '.', 'totales.json')


def leer_totales(salida):
    try:
        with open(ruta_totales(salida), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def recordar_total(salida, cid, nombre, total):
    """Guarda el total que el portal declaró para una carpeta.

    El portal contesta el total casi siempre, y cuando no contesta no es que no lo tenga:
    es que devolvió un cuerpo vacío, como hace de manera intermitente. Sin memoria, esa
    respuesta vacía nos deja sin poder verificar una carpeta que sabíamos contar la semana
    pasada. Guardarlo convierte un dato que ya teníamos en un dato que no se pierde.

    Se escribe por archivo temporal y os.replace para que una corrida interrumpida no deje
    el registro a medio escribir.
    """
    if not total:
        return
    registro = leer_totales(salida)
    registro[str(cid)] = {'nombre': nombre, 'total': int(total),
                          'visto': time.strftime('%Y-%m-%d %H:%M')}
    destino = ruta_totales(salida)
    temporal = destino + '.tmp'
    with open(temporal, 'w', encoding='utf-8') as f:
        json.dump(registro, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(temporal, destino)


class OrdenInesperado(Exception):
    """El portal dejó de devolver los documentos de más nuevo a más viejo.

    No es un detalle: la recolección incremental se apoya en ese orden para poder frenar
    apenas reconoce lo que ya tiene. Si el orden cambia ---otra versión de SUDOCU, otra
    configuración, otra universidad--- frenar temprano dejaría de significar "no hay nada
    nuevo" y empezaría a significar "no miré". Por eso se verifica en cada página y, si no
    se cumple, la carpeta se recolecta completa en vez de confiar.
    """


# El total que declara el portal puede venir inflado: a veces repite una fila entre dos
# páginas y la cuenta dos veces (en Tecnología se midieron 1808 declaradas y 1807 actos
# distintos). Por eso "cerrar la cuenta" admite quedar hasta tres por debajo.
TOLERANCIA_TOTAL = 3


class PortalMudo(Exception):
    """No se pudo leer ni la primera página de la carpeta.

    El portal devuelve cuerpo vacío de manera intermitente. Si eso pasa en la primera
    página de una lectura incremental, no leímos nada, y "no encontré nada nuevo" pasaría
    a significar "no miré". Se avisa para que la carpeta se liste completa, que tiene su
    propia paciencia para esperar a que el servidor se recupere.
    """


class DemasiadoNuevo(Exception):
    """La punta del listado no converge: hay demasiado desconocido como para ser "lo nuevo".

    La lectura incremental supone que lo que cambió es un puñado de actos al principio.
    Cuando una carpeta viene de una corrida cortada ---Secretarías quedó con 255 de 5668---
    ese supuesto no vale: casi todo es desconocido, y seguir paginando la punta es hacer
    una recolección completa disfrazada, más lenta y sin las protecciones de la completa.
    Conviene decirlo temprano y listar la carpeta entera.
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


def recolectar_incremental(cid, nombre, conocidos, total_recordado=None,
                           paginas_limpias=3, tope_paginas=20, tope_vacias=6, traza=None):
    """Los documentos de una carpeta que todavía no tenemos, leyendo solo la punta.

    El portal devuelve de más nuevo a más viejo, así que lo que cambió está al principio y
    no hace falta recorrer la carpeta entera. La pregunta es cuándo parar, y la respuesta
    es una cuenta, no una corazonada: se para cuando lo que el catálogo ya tenía más lo que
    se acaba de encontrar alcanza el total que el portal declara para esa carpeta. Ahí no
    queda nada por buscar, y punto.

    Eso también resuelve solo el caso del acto cargado con retraso ---fechado en marzo,
    publicado hoy--- que entra al listado por su fecha, sepultado bajo los más nuevos. No
    hace falta adivinar cuántas páginas mirar de más: si ese acto existe, el total del
    portal lo cuenta, la resta no cierra y la lectura sigue. Una carpeta al día se
    resuelve en UNA página.

    `paginas_limpias` queda como corte de reserva para cuando no se sabe el total ---ni de
    esta corrida ni recordado de la anterior---, que es el único caso en que hay que
    conformarse con una señal indirecta.

    `total_recordado` es el total de la última vez que el portal lo declaró. Sirve desde la
    primera página: sin él habría que leer una página solo para saber cuándo parar.

    `conocidos` son los identificadores de documento que ya están en el catálogo, es decir
    la columna `id_documento` del CSV. OJO con el mapeo, porque está cruzado y es fácil
    comparar el campo equivocado: el `id` que devuelve la API se guarda como
    `id_documento`, y el campo que la API llama `documento` se guarda como `id_archivo`.
    Comparar contra el que no es hace que todo parezca nuevo y la lectura incremental
    recorra la carpeta entera sin decir por qué.

    Devuelve (documentos_nuevos, total, lo_declaro_el_portal). El tercer valor distingue
    un total que acaba de declarar el portal ---que hay que recordar--- de uno que ya venía
    recordado. Levanta OrdenInesperado si el portal deja de ordenar como se espera, para
    que quien llama recolecte la carpeta completa.
    """
    nuevos, offset, limpias, total_portal = [], 0, 0, total_recordado
    techo, vistos, del_portal = None, set(), False

    vacias, paginas = 0, 0
    while offset < tope_paginas * TOPE:
        d = pedir(cid, offset)
        docs = (d or {}).get('documents') or []
        registrar(traza, {'carpeta': cid, 'nombre': nombre, 'offset': offset,
                          'modo': 'incremental', 'devueltos': len(docs)})
        if not docs:
            # Pasado el total, una respuesta vacía ES el fin del listado y no hay nada que
            # esperar. Sin esta salida, cada carpeta chica pagaba la paciencia completa
            # ---más de un minuto de esperas--- para descubrir que ya había terminado.
            if total_portal and offset >= total_portal:
                break
            # Antes del total, una vacía puede ser el fin o la intermitencia conocida del
            # portal. Se insiste antes de creerle; si nunca entregó una página, no hay
            # lectura que valga y se avisa.
            vacias += 1
            if vacias >= tope_vacias:
                if paginas == 0:
                    raise PortalMudo(f'{tope_vacias} respuestas vacías sin entregar '
                                     f'una sola página')
                break
            time.sleep(min(5 * vacias, 45))
            continue
        vacias = 0
        paginas += 1
        techo = verificar_orden(docs, techo)
        if not del_portal:
            try:
                declarado = int(docs[0].get('total') or 0) or None
            except (TypeError, ValueError):
                declarado = None
            if declarado:
                total_portal, del_portal = declarado, True

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

        # La cuenta cierra: lo que hay más lo encontrado llega a lo que el portal declara.
        # No hay nada más que buscar en esta carpeta.
        if total_portal and len(conocidos) + len(nuevos) >= total_portal - TOLERANCIA_TOTAL:
            break

        limpias = 0 if desconocidos else limpias + 1
        if not total_portal and limpias >= paginas_limpias:
            break
        offset += TOPE
    else:
        # El while terminó por agotar el tope, no por cerrar la cuenta.
        if nuevos:
            raise DemasiadoNuevo(f'{len(nuevos)} desconocidos en {paginas} páginas sin '
                                 f'llegar a lo ya conocido')
        # Punta limpia y la cuenta igual no cierra: lo que falta no está acá adelante,
        # está en el medio del listado. No es un caso de excepción sino de aritmética, y
        # la resuelve quien llama, que sabe cuántos faltan y qué hacer con eso.

    print(f'  {nombre}: {len(nuevos)} nuevos leyendo {offset // TOPE + 1} páginas', flush=True)
    return nuevos, total_portal, del_portal


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

    # Se anexa con el encabezado que el archivo YA tiene, no con el actual: si el catálogo
    # se generó antes de que se agregara una columna, escribir con la lista nueva correría
    # los valores una posición y arruinaría el archivo en silencio.
    with open(salida, encoding='utf-8-sig') as f:
        cabecera = next(csv.reader(f), None) or COLUMNAS

    escritos = []
    with open(salida, 'a', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=cabecera, extrasaction='ignore')
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
    # Igual que al anexar: manda el encabezado que el archivo ya tiene. Un catálogo
    # generado antes de que se agregara una columna se sigue completando bien, en vez de
    # correr los valores una posición sin que nada falle.
    cabecera = COLUMNAS
    if existe:
        with open(salida, encoding='utf-8-sig') as f:
            cabecera = next(csv.reader(f), None) or COLUMNAS
    archivo_csv = open(salida, 'a', newline='', encoding='utf-8-sig')
    escritor = csv.DictWriter(archivo_csv, fieldnames=cabecera, extrasaction='ignore')
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


def restaurar_faltantes(salida, respaldo):
    """Devuelve al catálogo lo que una recolección incompleta dejó afuera.

    Rehacer una carpeta es destructivo por diseño: primero se sacan sus filas del CSV y
    después se sale a listarla de nuevo. Entre esas dos cosas puede pasar cualquiera ---el
    portal deja de contestar, el servicio se reinicia, alguien corta la corrida--- y lo que
    queda es una carpeta vacía en vez de una carpeta vieja. Ya ocurrió: Secretarías de
    Rectorado quedó en 255 de 5668, y el faltante no lo causó el corte sino el borrado
    previo.

    Devuelve las filas repuestas, para que el resumen no informe como completa una
    carpeta que se salvó reponiendo.

    Por eso el borrado deja de ser definitivo: lo que se sacó se guarda, y al terminar se
    reponen las filas cuyo documento no haya vuelto a aparecer. Reponer de más es
    imposible, porque se compara por identificador de documento; lo peor que puede pasar
    es quedarse con un acto que el portal ya no publica, que es mucho mejor que perder
    quinientos que sí.
    """
    with open(salida, encoding='utf-8-sig') as f:
        presentes = {r['id_documento'] for r in csv.DictReader(f) if r.get('id_documento')}
    faltan = [r for r in respaldo
              if r.get('id_documento') and r['id_documento'] not in presentes]
    if not faltan:
        return []
    with open(salida, 'a', newline='', encoding='utf-8-sig') as f:
        csv.DictWriter(f, fieldnames=COLUMNAS).writerows(faltan)
    return faltan


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
    p.add_argument('--reconciliar', action='store_true',
                   help='rehacer las carpetas a las que les falten actos viejos '
                        '(caro: puede tardar horas)')
    a = p.parse_args()

    # Siempre se pregunta al portal, también con --carpeta: el nombre de la sección
    # sale de acá, y el mapa estático es solo el respaldo si el pedido falla.
    carpetas = carpetas_del_portal()
    respaldo = []          # filas apartadas al rehacer carpetas, para reponer al final
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

        al_dia, rehacer, mudas, incompletas_viejas, anexados = [], [], [], [], 0
        for cid in list(ids):
            nombre = carpetas.get(cid, f'carpeta {cid}')
            conocidos = conocidos_por_seccion.get(nombre) or set()
            if not conocidos:
                continue                      # carpeta nunca recolectada: listado completo
            memoria = leer_totales(a.salida).get(str(cid)) or {}
            try:
                nuevos, total, del_portal = recolectar_incremental(
                    cid, nombre, conocidos, total_recordado=memoria.get('total'), traza=None)
            except PortalMudo as e:
                # Que el portal no entregue nada NO es un problema de nuestros datos, así
                # que rehacer la carpeta es lo peor que se puede hacer: se borra lo que hay
                # para volver a pedirle a un servidor que no está contestando, y se pasan
                # horas juntando de a dos filas. Se midió contra Secretarías de Rectorado:
                # veinte páginas seguidas vacías, y dos corridas de horas que juntaron 255
                # y 81 filas de 5.668. La carpeta se deja como está y se avisa.
                print(f'  {nombre}: {e} -> el portal no está sirviendo esta carpeta; se '
                      f'deja como está y se reintenta en la próxima corrida', flush=True)
                mudas.append(nombre)
                ids = [i for i in ids if i != cid]
                continue
            except (OrdenInesperado, DemasiadoNuevo) as e:
                print(f'  {nombre}: {e} -> se rehace entera', flush=True)
                rehacer.append(nombre)
                continue

            # El total viaja DENTRO de cada documento de la primera página, que ya se pidió
            # para leer lo nuevo. Preguntarlo aparte era una llamada de más por carpeta, y
            # en las dos que el portal atiende peor eso significaba insistir hasta un minuto
            # por un dato que la página traía gratis. Si aun así no vino, se usa el del
            # último chequeo: el portal siempre lo declara, así que no tenerlo es un fallo
            # de esta consulta, no un dato que no exista.
            if del_portal:
                recordar_total(a.salida, cid, nombre, total)
            elif total:
                print(f'  {nombre}: la página no trajo el total; se usa el del último '
                      f'chequeo ({total}, {memoria.get("visto")})', flush=True)
            tengo = len(conocidos) + len(nuevos)

            # La segunda verificación, y la que atrapa lo que la primera no puede ver: lo
            # que ya está en el catálogo más lo que se acaba de encontrar tiene que dar lo
            # que el portal declara para esa carpeta. La lectura incremental mira la punta
            # del listado, así que encuentra lo recién publicado pero es ciega a los
            # agujeros del medio ---un acto que se perdió en una corrida cortada hace
            # meses---. Esos solo aparecen como una resta que no cierra, y entonces la
            # carpeta se rehace completa. La tolerancia de 3 es la misma de siempre: el
            # portal repite filas entre páginas y las cuenta en el total.
            if total is not None and tengo < total - TOLERANCIA_TOTAL:
                # Que falten actos que NO están en la punta significa que son viejos: se
                # perdieron en alguna corrida cortada, no se publicaron esta semana.
                # Repararlos exige listar la carpeta entera, que en las grandes son horas
                # ---y contra un portal que corta las consultas pesadas a los 20 segundos,
                # a veces no termina nunca---. Eso no puede dispararse solo en cada
                # actualización semanal: quedaría reconstruyendo para siempre las mismas
                # carpetas. Se informa y se pide explícitamente con --reconciliar.
                if not a.reconciliar:
                    print(f'  {nombre}: {tengo} de {total} · le faltan {total - tengo} '
                          f'actos que no están en la punta, o sea viejos. Se deja como '
                          f'está; para repararla: --reconciliar', flush=True)
                    incompletas_viejas.append(f'{nombre} (faltan {total - tengo})')
                    ids = [i for i in ids if i != cid]
                    continue
                print(f'  {nombre}: {tengo} de {total} -> se rehace entera '
                      f'(faltan {total - tengo} del medio del listado)', flush=True)
                rehacer.append(nombre)
                continue

            # Sin total no hay verificación posible, y sin verificación no se puede
            # afirmar que la carpeta esté al día: se lista completa. Parece exagerado
            # hasta que se mira un caso real ---Secretarías de Rectorado quedó en 255 de
            # 5668 cuando una corrida se cortó a la mitad---, porque es justo en las
            # carpetas grandes donde el portal contesta vacío más seguido y donde el
            # faltante es más caro. Dar por buena una carpeta que no se pudo contar
            # significa no volver a mirarla nunca.
            if total is None:
                print(f'  {nombre}: {tengo} en el catálogo, sin total ni ahora ni en el '
                      f'registro -> se lista completa, porque no se puede verificar',
                      flush=True)
                rehacer.append(nombre)
                continue

            if nuevos:
                filas_previas.extend(anexar_nuevos(nuevos, nombre, a.salida, filas_previas))
                anexados += len(nuevos)
            al_dia.append(nombre)
            ids = [i for i in ids if i != cid]

        if anexados:
            print(f'{anexados} documentos nuevos agregados al catálogo', flush=True)
        if al_dia:
            print(f'al día: {sorted(al_dia)}', flush=True)
        if mudas:
            print(f'sin respuesta del portal, quedaron como estaban: {sorted(mudas)}',
                  flush=True)
        if incompletas_viejas:
            print(f'con faltantes viejos (correr con --reconciliar para repararlas): '
                  f'{sorted(incompletas_viejas)}', flush=True)
        if rehacer:
            respaldo = [r for r in filas_previas if r['Seccion'] in rehacer]
            conservar = [r for r in filas_previas if r['Seccion'] not in rehacer]
            with open(a.salida, 'w', newline='', encoding='utf-8-sig') as f:
                w = csv.DictWriter(f, fieldnames=COLUMNAS)
                w.writeheader()
                w.writerows(conservar)
            print(f'  ({len(respaldo)} filas apartadas de {len(rehacer)} carpeta(s); se '
                  f'reponen al final las que no vuelvan a aparecer)', flush=True)
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
            recordar_total(a.salida, cid, carpetas.get(cid, f'carpeta {cid}'), tp)
            resumen.append((carpetas.get(cid, str(cid)), len(regs), tp))
        except Exception as e:
            print(f'  ERROR en la carpeta {cid}: {type(e).__name__}: {e}', flush=True)
            resumen.append((carpetas.get(cid, str(cid)), None, None))

    repuestas = restaurar_faltantes(a.salida, respaldo) if respaldo else []
    rescatadas = {r['Seccion'] for r in repuestas}
    if repuestas:
        print(f'\nse repusieron {len(repuestas)} filas que la recolección no volvió a traer '
              f'({", ".join(sorted(rescatadas))}): esas carpetas quedaron incompletas, pero '
              f'el catálogo no perdió nada', flush=True)

    print(f'\n=== resumen: {total} documentos en {time.time() - t0:.0f}s -> {a.salida} ===')
    # Para juzgar si una carpeta quedó corta sirve tanto el total que trajo esta corrida
    # como el que se recordó de la anterior: sin eso, una carpeta que no devolvió nada se
    # informaba como "0 de ? ok", que es justo lo contrario de lo que pasó.
    recordados = {v['nombre']: v['total'] for v in leer_totales(a.salida).values()}
    incompletas = 0
    for nombre, n, tp in resumen:
        tp = tp or recordados.get(nombre)
        # Una carpeta que hubo que rescatar reponiendo filas NO está completa, aunque no se
        # sepa contra qué total compararla: decir "ok" ahí contradice el aviso de arriba.
        if nombre in rescatadas and n is not None and not (tp and n >= tp):
            print(f'  {nombre[:40]:42s} {n:>6} de {str(tp or "?"):>6}  INCOMPLETA (repuesta)')
            incompletas += 1
            continue
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
