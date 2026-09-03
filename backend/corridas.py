"""Registro y ejecución de corridas: las operaciones del pipeline lanzadas desde el panel.

Una CORRIDA es una ejecución de una operación del pipeline ---recolectar, descargar,
vectorizar, indexar, o la actualización completa--- con su registro: quién la lanzó, con
qué parámetros, cuándo empezó y terminó, cómo salió y dónde quedó su log. El registro es
la memoria operativa del sistema: sin él, "¿cuándo se actualizó el corpus por última vez y
cómo salió?" se contesta buscando en la terminal de alguien.

Diseño:

- Las operaciones son los MISMOS scripts que se corren a mano. El panel no tiene una
  segunda implementación de nada: arma la línea de comandos y la ejecuta. Todo lo que se
  puede hacer desde el panel se puede hacer desde una terminal, y viceversa.

- Una sola corrida a la vez. Las operaciones comparten archivos (catálogo, PDFs, índice);
  dos corridas simultáneas se pisan. El candado es la fila con estado 'en_curso'.

- El log va a un archivo por corrida, no a la base: es un flujo que crece y se lee por
  la cola, exactamente lo que un archivo hace bien y una base hace mal.

- Si el proceso de la API muere, las corridas 'en_curso' quedan huérfanas. Al arrancar se
  marcan como 'interrumpida': mentir "en curso" para siempre es peor que reconocer el
  corte.
"""
import json
import os
import signal
import subprocess
import sys
import re
import threading
import time

from . import historial

ESQUEMA = """
CREATE TABLE IF NOT EXISTS corrida (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    operacion  TEXT NOT NULL,
    parametros TEXT,               -- JSON con los argumentos con que se lanzó
    inicio     INTEGER NOT NULL,
    fin        INTEGER,
    estado     TEXT NOT NULL,      -- en_curso | ok | error | cancelada | interrumpida
    codigo     INTEGER,            -- código de salida del proceso
    log        TEXT,               -- ruta del archivo de log
    por        TEXT,               -- correo del admin que la lanzó, o 'programada'
    resumen    TEXT                -- qué hizo, en una línea: ver resumir()
);
"""


def dir_logs():
    return os.environ.get('RAG_CORRIDAS', 'datos/corridas')


def _bd():
    c = historial._bd()
    c.executescript(ESQUEMA)
    # Las instalaciones que ya venían corriendo no tienen la columna: se agrega sin tocar
    # lo que hay, y se rellena hacia atrás con los logs que sigan en disco. Sin el relleno
    # la columna nace vacía justo para las corridas que uno quiere mirar ---las
    # actualizaciones de las últimas semanas--- y habría que esperar a que pasen otras para
    # que sirva de algo. Una corrida cuyo log ya no está queda sin resumen, que es la
    # verdad: el dato se perdió con el archivo.
    if 'resumen' not in {f[1] for f in c.execute('PRAGMA table_info(corrida)')}:
        c.execute('ALTER TABLE corrida ADD COLUMN resumen TEXT')
        for fila in c.execute("SELECT id, log FROM corrida WHERE log IS NOT NULL "
                              "AND estado <> 'en_curso'").fetchall():
            texto = resumir(fila['log'])
            if texto:
                c.execute('UPDATE corrida SET resumen=? WHERE id=?', (texto, fila['id']))
        c.commit()
    return c


def al_arrancar():
    """Resuelve las corridas que quedaron 'en_curso' de un proceso anterior de la API.

    Dos casos. Si el proceso de la operación MURIÓ con la API, la corrida se marca
    'interrumpida': mentir "en curso" para siempre es peor que reconocer el corte. Pero si
    sigue vivo ---las operaciones corren en su propia sesión justamente para sobrevivir a
    un reinicio--- la corrida se RE-ADOPTA: queda en curso, el candado de exclusión se
    mantiene, y un hilo espera a que termine. Lo único que se pierde con el reinicio es el
    código de salida, y eso se dice en vez de inventarse: el estado final es 'terminada'.
    """
    with historial._candado:
        filas = _bd().execute(
            "SELECT id, parametros FROM corrida WHERE estado='en_curso'").fetchall()
    for fila in filas:
        try:
            pid = json.loads(fila['parametros'] or '{}').get('pid')
        except ValueError:
            pid = None
        if pid and _vivo(pid):
            hilo = threading.Thread(target=_esperar_ajeno, args=(fila['id'], pid),
                                    daemon=True, name=f"readopta-{fila['id']}")
            hilo.start()
        else:
            _terminar(fila['id'], 'interrumpida', None)


def _vivo(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _esperar_ajeno(cid, pid):
    """Espera un proceso que no es hijo nuestro: se sondea hasta que desaparece.

    No hay `wait()` posible ---el proceso quedó huérfano con el reinicio--- así que el
    código de salida no se puede conocer. El log de la corrida cuenta cómo terminó.
    """
    while _vivo(pid):
        time.sleep(5)
    estado = leer(cid, colas_log=0)
    if estado and estado['estado'] == 'en_curso':
        _terminar(cid, 'terminada', None)


def en_curso():
    with historial._candado:
        fila = _bd().execute(
            "SELECT id, operacion, inicio FROM corrida WHERE estado='en_curso'").fetchone()
    return dict(fila) if fila else None


def listar(limite=50):
    with historial._candado:
        filas = _bd().execute(
            'SELECT id, operacion, parametros, inicio, fin, estado, codigo, por, resumen '
            'FROM corrida ORDER BY id DESC LIMIT ?', (limite,)).fetchall()
    corridas = []
    for f in filas:
        c = dict(f)
        try:
            c['parametros'] = json.loads(c['parametros'] or '{}')
        except ValueError:
            c['parametros'] = {}
        corridas.append(c)
    return corridas


def leer(cid, colas_log=200):
    with historial._candado:
        fila = _bd().execute('SELECT * FROM corrida WHERE id=?', (cid,)).fetchone()
    if not fila:
        return None
    c = dict(fila)
    try:
        c['parametros'] = json.loads(c['parametros'] or '{}')
    except ValueError:
        c['parametros'] = {}
    c['log_cola'] = _cola_del_log(c.get('log'), colas_log)
    return c


def _cola_del_log(ruta, lineas):
    """Últimas líneas del log, para el panel. Se lee desde el final para no cargar
    logs de horas en memoria."""
    if not ruta or not os.path.exists(ruta):
        return []
    try:
        with open(ruta, 'rb') as f:
            f.seek(0, os.SEEK_END)
            tam = f.tell()
            f.seek(max(0, tam - 256 * 1024))
            texto = f.read().decode('utf-8', errors='replace')
        colas = texto.splitlines()
        if tam > 256 * 1024 and colas:
            colas = colas[1:]  # la primera puede venir cortada
        return colas[-lineas:]
    except OSError:
        return []


# ------------------------------------------------------------------ ejecución
_candado_lanzar = threading.Lock()


def lanzar(operacion, comando, parametros, por, cwd=None, entorno=None):
    """Registra la corrida y ejecuta `comando` en segundo plano.

    Devuelve el id de la corrida, o levanta ValueError si ya hay una en curso: las
    operaciones comparten archivos y no se permiten dos a la vez.
    """
    with _candado_lanzar:
        activa = en_curso()
        if activa:
            raise ValueError(f"ya hay una corrida en curso: {activa['operacion']} "
                             f"(#{activa['id']})")

        os.makedirs(dir_logs(), exist_ok=True)
        ahora = int(time.time())
        with historial._candado:
            bd = _bd()
            cur = bd.execute(
                'INSERT INTO corrida (operacion, parametros, inicio, estado, por) '
                "VALUES (?,?,?,'en_curso',?)",
                (operacion, json.dumps(parametros, ensure_ascii=False), ahora, por))
            cid = cur.lastrowid
            ruta_log = os.path.join(dir_logs(), f'{cid:05d}-{operacion}.log')
            bd.execute('UPDATE corrida SET log=? WHERE id=?', (ruta_log, cid))
            bd.commit()

    hilo = threading.Thread(target=_correr, args=(cid, comando, ruta_log, cwd, entorno),
                            daemon=True, name=f'corrida-{cid}')
    hilo.start()
    return cid


def _correr(cid, comando, ruta_log, cwd, entorno=None):
    with open(ruta_log, 'w', encoding='utf-8') as log:
        log.write(f"$ {' '.join(comando)}\n\n")
        log.flush()
        try:
            # Grupo de procesos propio: cancelar mata también a los hijos que el script
            # haya lanzado, no solo al intérprete.
            ambiente = {**os.environ, **(entorno or {})}
            proceso = subprocess.Popen(comando, stdout=log, stderr=subprocess.STDOUT,
                                       cwd=cwd, env=ambiente, start_new_session=True)
        except OSError as e:
            log.write(f'\nno se pudo lanzar: {e}\n')
            _terminar(cid, 'error', None)
            return

    _registrar_pid(cid, proceso.pid)
    codigo = proceso.wait()
    estado_actual = leer(cid, colas_log=0)
    if estado_actual and estado_actual['estado'] == 'cancelada':
        return  # cancelar() ya cerró el registro
    _terminar(cid, 'ok' if codigo == 0 else 'error', codigo)


def _registrar_pid(cid, pid):
    with historial._candado:
        bd = _bd()
        bd.execute("UPDATE corrida SET parametros=json_set(COALESCE(parametros,'{}'), "
                   "'$.pid', ?) WHERE id=?", (pid, cid))
        bd.commit()


# Lo que cada paso ya imprime al terminar. No se agrega instrumentación nueva: estas líneas
# existen desde siempre y son las que uno lee en el log; lo único que faltaba era subirlas a
# la lista, porque para saber si la actualización de anoche trajo algo había que abrir la
# corrida y buscarlas a mano.
MARCAS = [
    (re.compile(r'^(\d+) documentos nuevos agregados al catálogo', re.M),
     lambda m: f'{int(m.group(1))} al catálogo'),
    (re.compile(r'^bajados (\d+) · ya estaban (\d+) · con error (\d+)', re.M),
     lambda m: f'{int(m.group(1))} bajados' + (f', {int(m.group(3))} con error'
                                               if int(m.group(3)) else '')),
    (re.compile(r'^=== (?:rescatados|con contenido) (\d+) ', re.M),
     lambda m: f'{int(m.group(1))} rescatados'),
    (re.compile(r'fusionado en \d+s: ([\d.]+) fragmentos', re.M),
     lambda m: f'{m.group(1)} fragmentos en el índice'),
]


def resumir(ruta_log):
    """Qué hizo una corrida, en una línea, sacado de su propio log.

    Se lee al terminar y se guarda: el archivo de log puede rotar o borrarse, y el número
    de documentos que trajo una actualización es justamente lo que uno quiere mirar meses
    después. Devuelve '' cuando no hay nada que contar ---una verificación, una corrida que
    murió antes de hacer nada--- y eso también es información: la lista no inventa.
    """
    try:
        with open(ruta_log, encoding='utf-8', errors='replace') as f:
            texto = f.read()
    except OSError:
        return ''
    partes = []
    for patron, formato in MARCAS:
        encontrados = list(patron.finditer(texto))
        if encontrados:
            # El último: una actualización completa corre varios pasos y puede repetir la
            # marca; el que vale es el del final.
            partes.append(formato(encontrados[-1]))
    return ' · '.join(partes)


def _terminar(cid, estado, codigo):
    with historial._candado:
        bd = _bd()
        fila = bd.execute('SELECT log FROM corrida WHERE id=?', (cid,)).fetchone()
        bd.execute('UPDATE corrida SET estado=?, codigo=?, fin=?, resumen=? WHERE id=?',
                   (estado, codigo, int(time.time()),
                    resumir(fila['log']) if fila and fila['log'] else '', cid))
        bd.commit()


def cancelar(cid, por):
    corrida = leer(cid, colas_log=0)
    if not corrida or corrida['estado'] != 'en_curso':
        return False
    pid = corrida['parametros'].get('pid')
    _terminar(cid, 'cancelada', None)
    if pid:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
    with open(corrida['log'], 'a', encoding='utf-8') as log:
        log.write(f'\n[cancelada por {por}]\n')
    return True
