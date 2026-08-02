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

def admins_entorno():
    """Administradores de arranque, leídos del entorno.

    Van en el entorno y no en la base para que un sistema recién instalado tenga dueño sin
    depender de que alguien escriba SQL, y para que no exista nunca una credencial por
    defecto.

    Se lee CADA VEZ y no al importar el módulo: `api.py` importa este archivo antes de
    cargar el `.env`, así que a nivel de módulo la variable todavía no existe y la lista
    quedaba vacía. Leerla al usarla también evita tener que reiniciar para que un cambio
    en el entorno tenga efecto.
    """
    crudo = os.environ.get('RAG_ADMINS', '')
    return [c.strip().lower() for c in crudo.split(',') if c.strip()]

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

# Todo lo que ata la aplicación a una institución concreta. Estaba fijo en el build del
# front, así que otra universidad tenía que recompilar para poner su nombre: acá pasa a ser
# configuración, con los valores de la UNLu como punto de partida.
INSTITUCION_POR_OMISION = {
    'nombre': 'Universidad Nacional de Luján',
    'sigla': 'UNLu',
    'producto': 'ChatDigesto',
    'descripcion': 'Consulta de normativa institucional de acceso público',
    # Cómo llama la institución a su cuerpo normativo. La UNLu dice "Digesto"; otras dicen
    # "Boletín Oficial" o directamente "normativa". Aparece en el título de la pestaña, en
    # el campo de escritura y en la pantalla inicial.
    'denominacion': 'Digesto',
    # El aviso al pie remite acá para verificar: es la fuente que da fe, y el asistente
    # solo ayuda a encontrar. Cada universidad tiene la suya.
    'digesto_oficial': 'http://digesto.unlu.edu.ar/',
    # Portal de publicación documental de SUDOCU. Es de donde sale el corpus, así que
    # también es lo que hay que cambiar para apuntar a otra instalación.
    'portal_sudocu': 'https://portal.unlu.edu.ar/sudocu/mpd/#!/mpd/portada',
    # Texto del aviso al pie de cada respuesta. Si contiene "fuentes oficiales", esas dos
    # palabras se vuelven el enlace al digesto oficial; si no, el enlace se agrega al final.
    'aviso': 'Las respuestas pueden contener errores. Verificá siempre la información en '
             'las fuentes oficiales.',
}

LARGOS = {'nombre': 160, 'sigla': 24, 'producto': 60, 'descripcion': 200,
          'denominacion': 40, 'digesto_oficial': 300, 'portal_sudocu': 300, 'aviso': 300}

# Sugerencias de la pantalla inicial. Son la primera impresión del sistema y eran lo más
# UNLu de toda la interfaz: nombraban paritarias y programas propios. Cada institución
# escribe las suyas desde el panel.
SUGERENCIAS_POR_OMISION = [
    'Normativa sobre concursos docentes',
    'Normativa sobre becas y viajes curriculares',
    'Acuerdos de la Paritaria Particular del Sector Nodocente',
    'Reglamentos académicos',
    'Planes de Estudios y Carreras',
]
SUGERENCIAS_MAX = 8
SUGERENCIA_LARGO = 120

# El logo subido vive junto a los datos y no en el árbol del front: no se versiona, y una
# reconstrucción de la interfaz no lo borra.
DIR_MARCA = os.environ.get('RAG_MARCA', 'datos/marca')
LOGO_MAX_BYTES = 2 * 1024 * 1024
# Firmas de archivo, no la extensión del nombre: el nombre lo elige quien sube.
FIRMAS_IMAGEN = {b'\x89PNG\r\n\x1a\n': 'png', b'\xff\xd8\xff': 'jpeg', b'GIF8': 'gif'}


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
    if correo in admins_entorno():
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
    entorno = admins_entorno()
    fijos = [{'correo': c, 'alta': None, 'por': 'entorno', 'fijo': True} for c in entorno]
    return fijos + [x for x in de_bd if x['correo'] not in entorno]


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
    if correo in admins_entorno():
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


def generacion_por_omision():
    """Valores de arranque del LLM de generación.

    El entorno sigue valiendo como punto de partida ---RAG_MODELO_GEN ya existía y los
    despliegues lo usan--- pero lo guardado desde el panel manda sobre él. La CLAVE no
    entra acá: una credencial no es un ajuste, vive en el entorno y el panel a lo sumo
    informa si está o no.
    """
    return {
        'modelo': os.environ.get('RAG_MODELO_GEN', 'gpt-4o-mini'),
        # Vacío = api.openai.com. Cualquier endpoint compatible sirve: un vLLM en un
        # servidor de la Universidad, un Ollama local, otro proveedor.
        'base_url': os.environ.get('RAG_LLM_BASE', ''),
        # 0 a propósito: en normativa se busca reproducibilidad, no creatividad.
        'temperatura': 0.0,
    }


def leer_generacion():
    omision = generacion_por_omision()
    guardado = leer_ajuste('generacion', {}) or {}
    return {**omision, **{k: v for k, v in guardado.items() if k in omision}}


def guardar_generacion(valores, por):
    valores = valores or {}
    limpio = {}
    if 'modelo' in valores:
        modelo = str(valores['modelo']).strip()[:120]
        if not modelo:
            raise ValueError('modelo no puede quedar vacío')
        limpio['modelo'] = modelo
    if 'base_url' in valores:
        base = str(valores['base_url']).strip()[:300]
        if base and not base.startswith(('http://', 'https://')):
            raise ValueError('base_url: la dirección debe empezar con http:// o https://')
        limpio['base_url'] = base
    if 'temperatura' in valores:
        try:
            temperatura = float(valores['temperatura'])
        except (TypeError, ValueError):
            raise ValueError('temperatura: se espera un número')
        if not 0 <= temperatura <= 2:
            raise ValueError('temperatura: entre 0 y 2')
        limpio['temperatura'] = temperatura
    guardar_ajuste('generacion', {**(leer_ajuste('generacion', {}) or {}), **limpio}, por)
    return leer_generacion()


def leer_institucion():
    guardado = leer_ajuste('institucion', {}) or {}
    datos = {**INSTITUCION_POR_OMISION,
             **{k: v for k, v in guardado.items() if k in INSTITUCION_POR_OMISION}}
    datos['logo'] = ruta_logo() is not None
    datos['sugerencias'] = leer_ajuste('sugerencias', SUGERENCIAS_POR_OMISION)
    return datos


def guardar_institucion(valores, por):
    valores = valores or {}
    limpio = {}
    for k, largo in LARGOS.items():
        v = valores.get(k)
        if v is None:
            continue
        v = str(v).strip()[:largo]
        if k in ('digesto_oficial', 'portal_sudocu') and v and not v.startswith(('http://', 'https://')):
            raise ValueError(f'{k}: la dirección debe empezar con http:// o https://')
        if k in ('nombre', 'sigla', 'producto', 'denominacion') and not v:
            raise ValueError(f'{k} no puede quedar vacío')
        limpio[k] = v
    guardar_ajuste('institucion', limpio, por)

    # Las sugerencias van en un ajuste aparte: son una lista y no un texto, y borrarlas
    # todas es válido (la pantalla inicial queda sin chips, nada más).
    if 'sugerencias' in valores:
        crudas = valores['sugerencias']
        if not isinstance(crudas, list):
            raise ValueError('sugerencias: se espera una lista de textos')
        sugerencias = [str(s).strip()[:SUGERENCIA_LARGO] for s in crudas]
        sugerencias = [s for s in sugerencias if s][:SUGERENCIAS_MAX]
        guardar_ajuste('sugerencias', sugerencias, por)

    return leer_institucion()


def ruta_logo():
    """Ruta del logo subido, o None si no hay ninguno y corresponde usar el del build."""
    for ext in ('png', 'jpeg', 'gif'):
        ruta = os.path.join(DIR_MARCA, f'logo.{ext}')
        if os.path.exists(ruta):
            return ruta
    return None


def guardar_logo(datos: bytes, por: str):
    """Guarda el logo validando que sea una imagen de verdad.

    Se comprueba la FIRMA del archivo y no su extensión: el nombre lo elige quien sube, y
    aceptar cualquier cosa que se llame .png en un directorio que después se sirve por HTTP
    es la forma clásica de terminar publicando algo que no era una imagen.
    """
    if len(datos) > LOGO_MAX_BYTES:
        raise ValueError(f'el archivo supera {LOGO_MAX_BYTES // 1024 // 1024} MB')
    tipo = next((t for firma, t in FIRMAS_IMAGEN.items() if datos.startswith(firma)), None)
    if not tipo:
        raise ValueError('el archivo no es una imagen PNG, JPEG ni GIF')

    os.makedirs(DIR_MARCA, exist_ok=True)
    # Se borra cualquier logo anterior: si quedaran dos con distinta extensión, cuál se sirve
    # dependería del orden en que se busca.
    for ext in ('png', 'jpeg', 'gif'):
        anterior = os.path.join(DIR_MARCA, f'logo.{ext}')
        if os.path.exists(anterior):
            os.remove(anterior)
    with open(os.path.join(DIR_MARCA, f'logo.{tipo}'), 'wb') as f:
        f.write(datos)
    guardar_ajuste('logo_cambiado', int(time.time()), por)
    return tipo


def quitar_logo(por):
    borrado = False
    for ext in ('png', 'jpeg', 'gif'):
        ruta = os.path.join(DIR_MARCA, f'logo.{ext}')
        if os.path.exists(ruta):
            os.remove(ruta)
            borrado = True
    if borrado:
        guardar_ajuste('logo_cambiado', int(time.time()), por)
    return borrado


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
    g = leer_generacion()
    datos['generacion'] = {'modelo': g['modelo'],
                           'base_url': g['base_url'] or None,
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


def documentos_por_seccion(ix, limite=60):
    """Recuento por sección del portal y por tipo de acto.

    La sección se toma del campo `seccion_portal`, que el pipeline guarda con cada
    fragmento. NO se deduce del nombre del archivo: eso funcionaba mientras el corpus tenía
    una sola convención de nombres, y con la nueva ---derivada de la identidad del acto---
    dejaba a cada documento como su propia sección inventada.

    Los fragmentos anteriores a ese cambio todavía no lo tienen, así que se informan aparte
    en lugar de repartirlos con una heurística: un recuento que agrupa por conjetura miente
    con más autoridad que uno que declara lo que no sabe.

    El tipo de acto siempre está disponible ---sale de la identidad--- y responde una
    pregunta más propia del dominio: cuántas disposiciones de cada organismo hay.
    """
    import collections

    por_seccion = collections.Counter()
    frag_seccion = collections.Counter()
    por_tipo = collections.Counter()
    frag_tipo = collections.Counter()
    vistos = set()
    sin_seccion = set()

    def contar(documento, seccion, codigo):
        nuevo_doc = documento not in vistos
        if nuevo_doc:
            vistos.add(documento)
        if seccion:
            frag_seccion[seccion] += 1
            if nuevo_doc:
                por_seccion[seccion] += 1
        elif nuevo_doc:
            sin_seccion.add(documento)
        cod = (codigo or '').strip().upper() or '(sin identificar)'
        frag_tipo[cod] += 1
        if nuevo_doc:
            por_tipo[cod] += 1

    if hasattr(ix, '_bd'):
        for f in ix._bd().execute('SELECT documento, seccion_portal, document_code FROM chunk'):
            contar(f['documento'], f['seccion_portal'], f['document_code'])
    else:
        for c in ix.chunks:
            contar(c.get('documento'), c.get('seccion_portal'), c.get('document_code'))

    return {
        'secciones': [{'seccion': s, 'documentos': n, 'fragmentos': frag_seccion[s]}
                      for s, n in por_seccion.most_common(limite)],
        'tipos': [{'tipo': t, 'documentos': n, 'fragmentos': frag_tipo[t]}
                  for t, n in por_tipo.most_common(limite)],
        'sin_seccion': len(sin_seccion),
        'documentos': len(vistos),
    }


# ----------------------------------------------------------------- uso
def uso_reciente(limite=60):
    """Las conversaciones más recientes de todos los usuarios, para el panel.

    Existe porque quien administra necesita saber qué se le pregunta al sistema y si las
    respuestas sirven: es la única realimentación real que hay. El acceso es de
    administradores y queda claro en la guía; no es un canal oculto.
    """
    with historial._candado:
        filas = _bd().execute(
            'SELECT c.id, c.titulo, c.actualizada, u.correo, u.nombre, '
            '       COUNT(m.id) AS mensajes, '
            '       SUM(CASE WHEN m.util = 1 THEN 1 ELSE 0 END) AS utiles, '
            '       SUM(CASE WHEN m.util = 0 THEN 1 ELSE 0 END) AS no_utiles '
            'FROM conversacion c '
            'JOIN usuario u ON u.id = c.usuario_id '
            'LEFT JOIN mensaje m ON m.conversacion_id = c.id '
            'GROUP BY c.id ORDER BY c.actualizada DESC LIMIT ?', (limite,)).fetchall()
    return [dict(f) for f in filas]


def uso_resumen():
    with historial._candado:
        bd = _bd()
        total, utiles, no_utiles = bd.execute(
            'SELECT COUNT(*), SUM(CASE WHEN util=1 THEN 1 ELSE 0 END), '
            'SUM(CASE WHEN util=0 THEN 1 ELSE 0 END) '
            "FROM mensaje WHERE rol='assistant'").fetchone()
    return {'respuestas': total or 0, 'utiles': utiles or 0, 'no_utiles': no_utiles or 0}


def uso_conversacion(conversacion_id):
    """Una conversación completa, sin filtrar por dueño: es la vista del administrador."""
    with historial._candado:
        bd = _bd()
        conv = bd.execute(
            'SELECT c.id, c.titulo, c.creada, u.correo, u.nombre '
            'FROM conversacion c JOIN usuario u ON u.id = c.usuario_id '
            'WHERE c.id=?', (conversacion_id,)).fetchone()
        if not conv:
            return None
        mensajes = bd.execute(
            'SELECT rol, texto, momento, util FROM mensaje '
            'WHERE conversacion_id=? ORDER BY id', (conversacion_id,)).fetchall()
    return {**dict(conv), 'mensajes': [dict(m) for m in mensajes]}
