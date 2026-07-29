"""Panel de administración: estado del sistema y ajustes.

Qué es y qué no. Acá vive lo que el panel MUESTRA y lo que GUARDA. Lo que el panel
*ejecuta* ---recolectar, descargar, vectorizar, indexar--- no vive acá: son operaciones del
pipeline, con sus scripts, y el panel las va a lanzar y seguir a través del registro de
corridas. Esa separación es deliberada: si la lógica de una operación queda adentro del
panel, en un mes hay cosas que solo se pueden hacer desde el panel y el sistema deja de
poder explicarse desde una terminal.

Quién entra. Los administradores salen de dos lugares: `RAG_ADMINS` en el entorno ---el
arranque, para que exista el primero sin que nadie tenga que insertar una fila a mano--- y
la tabla `admin`, para los que se dan de alta después desde el panel. Los usuarios comunes
no se administran: entran con Google y listo.
"""
import json
import os
import shutil
import sqlite3
import time

from . import historial

# Administradores de arranque. Van en el entorno y no en la base para que un sistema recién
# instalado tenga dueño sin depender de que alguien escriba SQL, y para que no exista nunca
# una credencial por defecto.
ADMINS_ENTORNO = [c.strip().lower() for c in os.environ.get('RAG_ADMINS', '').split(',') if c.strip()]

ESQUEMA = """
CREATE TABLE IF NOT EXISTS admin (
    correo TEXT PRIMARY KEY,
    alta   INTEGER,
    por    TEXT              -- quién lo dio de alta, para poder reconstruir el porqué
);
CREATE TABLE IF NOT EXISTS ajuste (
    clave    TEXT PRIMARY KEY,
    valor    TEXT,
    cambiado INTEGER,
    por      TEXT
);
"""

# Colores que definen la identidad visual. Son los cuatro roles de los que dependen todos
# los demás en la hoja de estilos: con estos cuatro, otra institución tiene su marca.
TEMA_POR_OMISION = {
    'marca': '#2f6b2f',
    'marca-oscura': '#24521f',
    'fondo-marca': '#eef4eb',
    'realce': '#deecd8',
}


def _bd():
    c = historial._bd()
    c.executescript(ESQUEMA)
    return c


def correo_de(sub: str):
    """Correo del usuario a partir del identificador de sesión.

    La sesión guarda el `sub` de Google ---estable aunque la persona cambie de correo--- y
    la lista de administradores se escribe con correos, que es lo que una persona sabe. Esta
    función traduce entre las dos cosas.
    """
    if not sub:
        return None
    with historial._candado:
        fila = _bd().execute('SELECT correo FROM usuario WHERE id=?', (sub,)).fetchone()
    return fila['correo'] if fila else None


def es_admin_por_sub(sub: str) -> bool:
    return es_admin(correo_de(sub))


def es_admin(correo: str) -> bool:
    if not correo:
        return False
    correo = correo.strip().lower()
    if correo in ADMINS_ENTORNO:
        return True
    with historial._candado:
        fila = _bd().execute('SELECT 1 FROM admin WHERE correo=?', (correo,)).fetchone()
    return bool(fila)


def listar_admins():
    with historial._candado:
        filas = _bd().execute('SELECT correo, alta, por FROM admin ORDER BY correo').fetchall()
    de_bd = [{'correo': f['correo'], 'alta': f['alta'], 'por': f['por'], 'fijo': False}
             for f in filas]
    # Los del entorno se muestran como fijos: no se pueden quitar desde el panel, porque si
    # se pudiera, un administrador podría dejar al sistema sin ninguno.
    fijos = [{'correo': c, 'alta': None, 'por': 'entorno', 'fijo': True} for c in ADMINS_ENTORNO]
    return fijos + [x for x in de_bd if x['correo'] not in ADMINS_ENTORNO]


def agregar_admin(correo: str, por: str):
    correo = (correo or '').strip().lower()
    if '@' not in correo:
        raise ValueError('correo inválido')
    with historial._candado:
        bd = _bd()
        bd.execute('INSERT OR IGNORE INTO admin (correo, alta, por) VALUES (?,?,?)',
                   (correo, int(time.time()), por))
        bd.commit()
    return correo


def quitar_admin(correo: str) -> bool:
    correo = (correo or '').strip().lower()
    if correo in ADMINS_ENTORNO:
        return False
    with historial._candado:
        bd = _bd()
        cur = bd.execute('DELETE FROM admin WHERE correo=?', (correo,))
        bd.commit()
    return cur.rowcount > 0


# ----------------------------------------------------------------- ajustes
def leer_ajuste(clave, por_omision=None):
    with historial._candado:
        fila = _bd().execute('SELECT valor FROM ajuste WHERE clave=?', (clave,)).fetchone()
    if not fila:
        return por_omision
    try:
        return json.loads(fila['valor'])
    except (ValueError, TypeError):
        return por_omision


def guardar_ajuste(clave, valor, por):
    with historial._candado:
        bd = _bd()
        bd.execute('INSERT INTO ajuste (clave, valor, cambiado, por) VALUES (?,?,?,?) '
                   'ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor, '
                   'cambiado=excluded.cambiado, por=excluded.por',
                   (clave, json.dumps(valor, ensure_ascii=False), int(time.time()), por))
        bd.commit()
    return valor


def leer_tema():
    guardado = leer_ajuste('tema', {}) or {}
    return {**TEMA_POR_OMISION, **{k: v for k, v in guardado.items() if k in TEMA_POR_OMISION}}


def guardar_tema(colores, por):
    import re
    limpio = {}
    for k in TEMA_POR_OMISION:
        v = (colores or {}).get(k)
        if v is None:
            continue
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', str(v)):
            raise ValueError(f'color inválido en {k}: {v!r}')
        limpio[k] = str(v).lower()
    guardar_ajuste('tema', limpio, por)
    return leer_tema()


# ----------------------------------------------------------------- estado
def estado(ix=None, ruta_indice='indice'):
    """Lo que el panel muestra como monitor. Solo lectura, sin efectos."""
    datos = {'momento': int(time.time())}

    # --- corpus e índice ---
    if ix is not None:
        try:
            docs, hasta = ix.documentos_y_fechas()
            datos['corpus'] = {'documentos': docs, 'fragmentos': len(ix),
                              'normativa_hasta': hasta,
                              'almacen': type(ix).__name__}
        except Exception as e:
            datos['corpus'] = {'error': f'{type(e).__name__}: {e}'}

    # --- artefactos en disco, con su fecha: es lo que permite saber si el índice que se
    #     está sirviendo corresponde a la última reconstrucción o quedó viejo ---
    artefactos = {}
    for nombre in ('chunks.jsonl', 'densos.npy', 'chunks.sqlite', 'vectores.faiss'):
        ruta = os.path.join(ruta_indice, nombre)
        if os.path.exists(ruta):
            artefactos[nombre] = {'bytes': os.path.getsize(ruta),
                                  'modificado': int(os.path.getmtime(ruta))}
    datos['artefactos'] = artefactos

    # --- disco ---
    try:
        u = shutil.disk_usage('.')
        datos['disco'] = {'total': u.total, 'libre': u.free}
    except OSError:
        pass

    # --- generación ---
    datos['generacion'] = {'modelo': os.environ.get('RAG_MODELO_GEN', 'gpt-4o-mini'),
                           'clave_configurada': bool(os.environ.get('OPENAI_API_KEY'))}

    # --- uso ---
    try:
        with historial._candado:
            bd = _bd()
            datos['uso'] = {
                'usuarios': bd.execute('SELECT COUNT(*) FROM usuario').fetchone()[0],
                'conversaciones': bd.execute('SELECT COUNT(*) FROM conversacion').fetchone()[0],
                'mensajes': bd.execute('SELECT COUNT(*) FROM mensaje').fetchone()[0],
            }
    except sqlite3.Error as e:
        datos['uso'] = {'error': str(e)}

    return datos


def documentos_por_seccion(ix, limite=40):
    """Recuento por sección, para la pantalla de documentos.

    Sale del propio índice y no del catálogo a propósito: lo que interesa mostrar es qué
    está efectivamente indexado ---lo que el asistente puede responder--- y no lo que
    alguna vez se recolectó.
    """
    import collections
    import re
    cuenta = collections.Counter()
    frags = collections.Counter()
    vistos = set()
    if hasattr(ix, '_bd'):
        filas = ix._bd().execute('SELECT documento, document_code FROM chunk')
        for f in filas:
            seccion = re.sub(r'_\d+$', '', f['documento'] or '') or '(sin sección)'
            frags[seccion] += 1
            if f['documento'] not in vistos:
                vistos.add(f['documento'])
                cuenta[seccion] += 1
    else:
        for c in ix.chunks:
            seccion = re.sub(r'_\d+$', '', c.get('documento') or '') or '(sin sección)'
            frags[seccion] += 1
            if c.get('documento') not in vistos:
                vistos.add(c.get('documento'))
                cuenta[seccion] += 1
    return [{'seccion': s, 'documentos': n, 'fragmentos': frags[s]}
            for s, n in cuenta.most_common(limite)]
