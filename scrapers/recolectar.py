"""Recolector del portal SUDOCU: metadatos y PDFs, sin descargas del navegador.

Reemplaza al camino de `Scraping.py` + `renombrar_archivos.py`. La diferencia no es de
estilo: es que aquel dependía de que el archivo que el navegador dejaba en disco fuera, por
orden de llegada, el que correspondía a la fila que se estaba leyendo. Ese supuesto se
rompió en 1.780 archivos y el error fue silencioso ---los metadatos quedaban corridos y
nada avisaba---.

Acá el vínculo es explícito. Cada documento del portal tiene dos identificadores internos
que la interfaz usa para armar la descarga, y con ellos se arma una URL estable:

    {api}/archivos/publico/{id_archivo}?id_archivo={id_archivo}&id_documento={id_documento}

El identificador viaja DOS veces, en la ruta y en la query: con la ruta sola el servidor
responde 500 con un mensaje que sugiere que el documento no tiene PDF, lo cual despista.

Con esa URL el PDF se baja por HTTP directo. No hay descargas del navegador, no hay
renombrado posterior y no hay forma de que un archivo termine emparejado con la fila
equivocada: el nombre sale de la identidad del propio acto y la fila trae su archivo.

Uso:
    python recolectar.py                      # todas las secciones de conf.SECCIONES
    python recolectar.py --seccion "RESOLUCIONES RECTOR"
    python recolectar.py --solo-metadatos     # no baja PDFs, solo releva
    python recolectar.py --paginas 2          # corta antes, para probar

Es reanudable: un PDF ya presente no se vuelve a bajar.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.request
from datetime import datetime, timedelta, timezone

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import conf
import opciones as op

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
        'Codigo': nro.get('tipo') or '',
        'Nro': nro.get('nro') or '',
        'Anio': nro.get('anio') or '',
        'Organismo': nro.get('organismo') or '',
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


def bajar(url, destino, intentos=3):
    """Baja el PDF y devuelve su sha256. Verifica que sea realmente un PDF.

    El servidor contesta 200 con JSON de error en algunos casos, así que no alcanza con
    el código de estado: se comprueba la firma del archivo.
    """
    for n in range(intentos):
        try:
            pedido = urllib.request.Request(url, headers={'User-Agent': 'rag-unlu/1.0'})
            with urllib.request.urlopen(pedido, timeout=120) as r:
                datos = r.read()
            if not datos.startswith(b'%PDF'):
                raise ValueError(f'la respuesta no es un PDF ({datos[:80]!r})')
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            with open(destino, 'wb') as f:
                f.write(datos)
            return hashlib.sha256(datos).hexdigest()
        except Exception as e:
            if n == intentos - 1:
                raise
            time.sleep(2 * (n + 1))
    return ''


def abrir_seccion(driver, seccion):
    """Portada -> VER TODOS -> carpeta de la sección. Es el camino de la interfaz.

    Cada paso espera a que aparezca lo que el siguiente necesita, en vez de dormir una
    cantidad fija. Con esperas fijas las primeras secciones salían bien y las siguientes
    fallaban: la aplicación tarda distinto según el estado en que quedó de la sección
    anterior, y el error aparecía como "no encuentro la carpeta" cuando en realidad la
    lista todavía no se había desplegado.
    """
    driver.get(conf.PORTAL_URL)

    # Se espera al botón CONCRETO, no a "algún botón". Buscarlo apenas hay botones en la
    # página encontraba la barra superior antes de que se dibujara la portada: el bucle no
    # hallaba "VER TODOS", no hacía clic, y el fallo aparecía después como si no existiera
    # la carpeta de la sección.
    def ver_todos(d):
        for b in d.find_elements(By.CSS_SELECTOR, '.md-button.ng-scope.md-ink-ripple'):
            try:
                if 'VER TODOS' in (b.text or '').upper():
                    return b
            except Exception:
                pass
        return None

    boton = WebDriverWait(driver, 60).until(ver_todos)
    driver.execute_script("arguments[0].click();", boton)

    xpath = f"//*[contains(@class, 'pointer') and contains(., '{seccion}')]"
    carpeta = WebDriverWait(driver, 60).until(
        EC.presence_of_element_located((By.XPATH, xpath)))
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", carpeta)
    # Clic por JavaScript: el clic de Selenium falla si el elemento quedó tapado por un
    # overlay de Material que todavía se está desvaneciendo.
    driver.execute_script("arguments[0].click();", carpeta)

    WebDriverWait(driver, 60).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, 'tr.md-row.ng-scope')))
    WebDriverWait(driver, 60).until(lambda d: len(leer_pagina(d)) > 0)


def leer_pagina(driver):
    """Los objetos que el portal tiene atados a las filas visibles.

    Se leen todos de una vez: pedirlos fila por fila multiplica los saltos entre Python y
    el navegador sin ninguna ventaja.
    """
    return driver.execute_script("""
        return Array.prototype.map.call(
            document.querySelectorAll('tr.md-row.ng-scope'),
            function (fila) {
                var s = angular.element(fila).scope();
                return s && s.documento ? s.documento : null;
            }).filter(Boolean);
    """)


def recolectar_seccion(driver, seccion, salida, bajar_pdfs=True, max_paginas=None):
    print(f'\n=== {seccion} ===', flush=True)
    abrir_seccion(driver, seccion)

    registros, tomados, pagina, vistos = [], set(), 1, set()
    while True:
        crudos = leer_pagina(driver)
        nuevos = 0
        for d in crudos:
            if not d.get('documento') or d['documento'] in vistos:
                continue
            vistos.add(d['documento'])
            nuevos += 1
            reg = fila_a_registro(d, seccion)
            reg['Archivo'] = nombre_archivo(reg, tomados)
            registros.append(reg)

        print(f'  página {pagina}: {nuevos} documentos (acumulado {len(registros)})', flush=True)
        if max_paginas and pagina >= max_paginas:
            break

        try:
            siguiente = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Next']")
        except Exception:
            break
        if siguiente.get_attribute('disabled'):
            break

        # Se espera a que la tabla CAMBIE, no una cantidad fija de segundos. Con una espera
        # fija la página siguiente se leía antes de que el portal la renderizara y se
        # releían las mismas filas: el recorrido terminaba en la primera página sin que
        # nada fallara a la vista.
        primero = (crudos[0].get('documento') if crudos else None)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", siguiente)
        siguiente.click()
        try:
            WebDriverWait(driver, 30).until(
                lambda d: (leer_pagina(d) or [{}])[0].get('documento') != primero)
        except Exception:
            print('  la página no cambió: se corta acá', flush=True)
            break
        pagina += 1

    if bajar_pdfs:
        destino_dir = os.path.join(conf.DIRECTORIO_DESCARGAS, _limpio(seccion))
        for i, reg in enumerate(registros, 1):
            destino = os.path.join(destino_dir, reg['Archivo'])
            if os.path.exists(destino):                      # reanudable
                with open(destino, 'rb') as f:
                    reg['sha256'] = hashlib.sha256(f.read()).hexdigest()
                continue
            try:
                reg['sha256'] = bajar(reg['URL'], destino)
            except Exception as e:
                print(f'  ERROR bajando {reg["Numero"]}: {e}', flush=True)
            if i % 50 == 0:
                print(f'  descargados {i}/{len(registros)}', flush=True)

    for i, reg in enumerate(registros, 1):
        reg['ID PDF'] = i          # se conserva por compatibilidad: es posicional dentro
                                   # de la sección, pero ya nadie depende de él para unir
    escribir(salida, registros)
    return registros


def escribir(ruta, registros):
    """Agrega al CSV, creando el encabezado si hace falta."""
    existe = os.path.exists(ruta)
    with open(ruta, 'a', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS)
        if not existe:
            w.writeheader()
        for r in registros:
            w.writerow(r)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--seccion', action='append',
                   help='sección a recolectar (repetible). Por defecto, todas las de conf.')
    p.add_argument('--salida', default=os.path.join(conf.DIRECTORIO_BASE, 'metadatos.csv'))
    p.add_argument('--solo-metadatos', action='store_true', help='no baja los PDF')
    p.add_argument('--paginas', type=int, help='cortar después de N páginas (para probar)')
    a = p.parse_args()

    secciones = a.seccion or conf.SECCIONES

    # Reanudable por sección: una corrida sobre el portal entero son horas, y si se corta
    # a la mitad no tiene sentido volver a empezar. Lo ya escrito no se toca.
    hechas = set()
    if os.path.exists(a.salida):
        with open(a.salida, encoding='utf-8-sig') as f:
            hechas = {r['Seccion'] for r in csv.DictReader(f) if r.get('Seccion')}
        if hechas:
            print(f'ya estaban: {sorted(hechas)}', flush=True)
    secciones = [s for s in secciones if s not in hechas]
    if not secciones:
        sys.exit('no queda ninguna sección por recolectar')

    def nuevo_driver():
        d = webdriver.Chrome(options=op.options(secciones[0]))
        d.implicitly_wait(12)
        return d

    total, t0, fallidas = 0, time.time(), []
    for seccion in secciones:
        # Un navegador por sección, y se cierra al terminar. Reutilizarlo entre secciones
        # parecía más eficiente pero no lo es: la aplicación deja estado detrás y a partir
        # de la segunda o tercera sección la lista de carpetas dejaba de desplegarse,
        # con un timeout que no decía nada sobre la causa real.
        for intento in (1, 2):
            driver = nuevo_driver()
            try:
                total += len(recolectar_seccion(driver, seccion, a.salida,
                                                bajar_pdfs=not a.solo_metadatos,
                                                max_paginas=a.paginas))
                break
            except Exception as e:
                print(f'  fallo {intento}/2 en {seccion}: {type(e).__name__}: {e}', flush=True)
                if intento == 2:
                    fallidas.append(seccion)
            finally:
                driver.quit()

    if fallidas:
        print(f'\nSECCIONES SIN RECOLECTAR: {fallidas}', flush=True)

    print(f'\n{total} documentos en {time.time() - t0:.0f}s -> {a.salida}', flush=True)


if __name__ == '__main__':
    main()
