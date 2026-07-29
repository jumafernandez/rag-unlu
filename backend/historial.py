"""
Conversaciones guardadas por usuario.

SQLite: un archivo, sin servidor que administrar, backup = copiar el archivo, y auditable
con cualquier visor. Si el uso crece, migrar a PostgreSQL es directo porque el modelo no
cambia.

Además del texto de cada respuesta se guardan las FUENTES que la sustentaron. Sin eso, una
conversación vieja es una afirmación sin respaldo: con esto se puede reconstruir en qué
normativa se apoyó el sistema cuando respondió.

Configuración (.env):
    RAG_BD=datos/chatdigesto.sqlite
"""

import json
import os
import sqlite3
import threading
import time

def ruta():
    """Ruta de la base. Se lee al usarla y no al importar el módulo: `api.py` importa esto
    antes de cargar el `.env`, así que a nivel de módulo `RAG_BD` todavía no existe."""
    return os.environ.get('RAG_BD', 'datos/chatdigesto.sqlite')

_candado = threading.Lock()
_conexion = None

ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuario (
    id       TEXT PRIMARY KEY,          -- 'sub' de Google: estable aunque cambie el correo
    correo   TEXT,
    nombre   TEXT,
    alta     INTEGER NOT NULL,
    visto    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS conversacion (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id  TEXT NOT NULL REFERENCES usuario(id),
    titulo      TEXT,
    creada      INTEGER NOT NULL,
    actualizada INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS mensaje (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversacion_id INTEGER NOT NULL REFERENCES conversacion(id) ON DELETE CASCADE,
    rol             TEXT NOT NULL,      -- 'user' | 'assistant'
    texto           TEXT NOT NULL,
    fuentes         TEXT,               -- JSON con las fuentes citadas, si es respuesta
    momento         INTEGER NOT NULL,
    -- Valoración de la respuesta: 1 sirvió, 0 no sirvió, NULL sin valorar.
    -- Junto con la consulta y las fuentes que se guardan al lado, deja juicios de
    -- relevancia sobre consultas reales, que es el insumo más caro de conseguir para
    -- medir si la recuperación funciona.
    util            INTEGER
);

CREATE INDEX IF NOT EXISTS idx_conv_usuario ON conversacion(usuario_id, actualizada DESC);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON mensaje(conversacion_id, id);
"""


def _bd():
    global _conexion
    if _conexion is None:
        carpeta = os.path.dirname(ruta())
        if carpeta:
            os.makedirs(carpeta, exist_ok=True)
        _conexion = sqlite3.connect(ruta(), check_same_thread=False)
        _conexion.row_factory = sqlite3.Row
        _conexion.execute('PRAGMA foreign_keys = ON')
        _conexion.executescript(ESQUEMA)
        _conexion.commit()
    return _conexion


def registrar_usuario(sub, correo, nombre):
    ahora = int(time.time())
    with _candado:
        bd = _bd()
        bd.execute(
            'INSERT INTO usuario (id, correo, nombre, alta, visto) VALUES (?,?,?,?,?)'
            ' ON CONFLICT(id) DO UPDATE SET correo=excluded.correo,'
            ' nombre=excluded.nombre, visto=excluded.visto',
            (sub, correo, nombre, ahora, ahora))
        bd.commit()
    return sub


def crear_conversacion(usuario_id, titulo):
    ahora = int(time.time())
    with _candado:
        bd = _bd()
        cur = bd.execute(
            'INSERT INTO conversacion (usuario_id, titulo, creada, actualizada) VALUES (?,?,?,?)',
            (usuario_id, (titulo or 'Consulta')[:120], ahora, ahora))
        bd.commit()
        return cur.lastrowid


def agregar_mensaje(conversacion_id, usuario_id, rol, texto, fuentes=None):
    """Agrega un mensaje. Verifica que la conversación sea del usuario."""
    ahora = int(time.time())
    with _candado:
        bd = _bd()
        duenio = bd.execute('SELECT usuario_id FROM conversacion WHERE id=?',
                            (conversacion_id,)).fetchone()
        if not duenio or duenio['usuario_id'] != usuario_id:
            return None
        cur = bd.execute(
            'INSERT INTO mensaje (conversacion_id, rol, texto, fuentes, momento) VALUES (?,?,?,?,?)',
            (conversacion_id, rol, texto,
             json.dumps(fuentes, ensure_ascii=False) if fuentes else None, ahora))
        bd.execute('UPDATE conversacion SET actualizada=? WHERE id=?', (ahora, conversacion_id))
        bd.commit()
        return cur.lastrowid


def listar_conversaciones(usuario_id, limite=50):
    with _candado:
        filas = _bd().execute(
            'SELECT id, titulo, creada, actualizada FROM conversacion'
            ' WHERE usuario_id=? ORDER BY actualizada DESC LIMIT ?',
            (usuario_id, limite)).fetchall()
    return [dict(f) for f in filas]


def leer_conversacion(conversacion_id, usuario_id):
    with _candado:
        bd = _bd()
        conv = bd.execute('SELECT id, titulo, usuario_id FROM conversacion WHERE id=?',
                          (conversacion_id,)).fetchone()
        if not conv or conv['usuario_id'] != usuario_id:
            return None
        msgs = bd.execute(
            'SELECT id, rol, texto, fuentes, momento, util FROM mensaje'
            ' WHERE conversacion_id=? ORDER BY id', (conversacion_id,)).fetchall()
    return {
        'id': conv['id'],
        'titulo': conv['titulo'],
        'mensajes': [
            {'id': m['id'], 'rol': m['rol'], 'texto': m['texto'], 'momento': m['momento'],
             'util': m['util'],
             'fuentes': json.loads(m['fuentes']) if m['fuentes'] else None}
            for m in msgs
        ],
    }


def renombrar_conversacion(conversacion_id, usuario_id, titulo):
    """El título arranca siendo la primera pregunta recortada; el usuario puede cambiarlo."""
    titulo = (titulo or '').strip()[:120]
    if not titulo:
        return False
    with _candado:
        bd = _bd()
        conv = bd.execute('SELECT usuario_id FROM conversacion WHERE id=?',
                          (conversacion_id,)).fetchone()
        if not conv or conv['usuario_id'] != usuario_id:
            return False
        bd.execute('UPDATE conversacion SET titulo=? WHERE id=?', (titulo, conversacion_id))
        bd.commit()
        return True


def valorar_mensaje(mensaje_id, usuario_id, util):
    """Marca una respuesta como útil (True) o no (False). None quita la valoración."""
    with _candado:
        bd = _bd()
        fila = bd.execute(
            'SELECT c.usuario_id AS duenio FROM mensaje m'
            ' JOIN conversacion c ON c.id = m.conversacion_id WHERE m.id=?',
            (mensaje_id,)).fetchone()
        if not fila or fila['duenio'] != usuario_id:
            return False
        bd.execute('UPDATE mensaje SET util=? WHERE id=?',
                   (None if util is None else int(bool(util)), mensaje_id))
        bd.commit()
        return True


def borrar_conversacion(conversacion_id, usuario_id):
    with _candado:
        bd = _bd()
        conv = bd.execute('SELECT usuario_id FROM conversacion WHERE id=?',
                          (conversacion_id,)).fetchone()
        if not conv or conv['usuario_id'] != usuario_id:
            return False
        bd.execute('DELETE FROM mensaje WHERE conversacion_id=?', (conversacion_id,))
        bd.execute('DELETE FROM conversacion WHERE id=?', (conversacion_id,))
        bd.commit()
        return True
