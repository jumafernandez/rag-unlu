"""
Inicio de sesión con Google e identidad del usuario.

Se usa OAuth de Google con cuentas personales (la UNLu no tiene Google Workspace, así que
no hay identidad institucional a la que engancharse). El usuario queda identificado por el
`sub` que devuelve Google: un identificador estable y propio de cada cuenta, que no cambia
aunque la persona cambie su dirección de correo.

El backend NO ve contraseñas en ningún momento: recibe un token firmado por Google, lo
valida contra las claves públicas de Google, y de ahí saca la identidad.

Configuración (.env):
    GOOGLE_CLIENT_ID=...          id de cliente OAuth (público, va también en el front)
    RAG_SESION_SECRETO=...        secreto para firmar las sesiones propias
    RAG_SESION_HORAS=720          duración de la sesión (30 días por defecto)
    RAG_DOMINIOS=                 si se completa, solo entran correos de esos dominios
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

HORAS = int(os.environ.get('RAG_SESION_HORAS', '720'))
# Vacío = cualquier cuenta de Google. Se deja configurable por si más adelante la
# Universidad tiene dominio propio y se quiere restringir.
DOMINIOS = [d.strip().lower() for d in os.environ.get('RAG_DOMINIOS', '').split(',') if d.strip()]

# Sin secreto configurado se genera uno al azar por proceso: las sesiones no sobreviven a un
# reinicio, pero nunca queda una clave por defecto que alguien olvide cambiar.
_SECRETO = os.environ.get('RAG_SESION_SECRETO') or secrets.token_hex(32)


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).decode().rstrip('=')


def _de_b64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + '=' * (-len(texto) % 4))


def emitir_sesion(usuario_id: str) -> str:
    """Token de sesión propio, firmado, que identifica al usuario."""
    cuerpo = {'uid': usuario_id, 'exp': int(time.time()) + HORAS * 3600}
    carga = _b64(json.dumps(cuerpo, separators=(',', ':')).encode())
    firma = _b64(hmac.new(_SECRETO.encode(), carga.encode(), hashlib.sha256).digest())
    return f'{carga}.{firma}'


def leer_sesion(token: str):
    """Devuelve el usuario_id si el token es válido y no venció; si no, None."""
    if not token or '.' not in token:
        return None
    carga, _, firma = token.partition('.')
    esperada = _b64(hmac.new(_SECRETO.encode(), carga.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(firma, esperada):
        return None
    try:
        cuerpo = json.loads(_de_b64(carga))
    except Exception:
        return None
    if int(cuerpo.get('exp', 0)) <= time.time():
        return None
    return cuerpo.get('uid')


def dominio_autorizado(correo: str) -> bool:
    if not DOMINIOS:
        return True
    if not correo or '@' not in correo:
        return False
    return correo.rsplit('@', 1)[1].lower() in DOMINIOS


def verificar_credencial_google(credencial: str) -> dict:
    """Valida el id_token de Google y devuelve {sub, email, name}.

    La validación la hace la librería de Google contra sus claves públicas: comprueba la
    firma, el emisor, el destinatario (nuestro client_id) y el vencimiento. Un token
    inventado o de otra aplicación no pasa.
    """
    from google.auth.transport import requests as peticiones_google
    from google.oauth2 import id_token

    cliente = os.environ.get('GOOGLE_CLIENT_ID')
    if not cliente:
        raise RuntimeError('falta GOOGLE_CLIENT_ID en el entorno')

    datos = id_token.verify_oauth2_token(credencial, peticiones_google.Request(), cliente)
    if not datos.get('email_verified'):
        raise ValueError('el correo de la cuenta no está verificado')
    return {
        'sub': datos['sub'],                       # identificador estable de la cuenta
        'email': datos.get('email', ''),
        'nombre': datos.get('name') or datos.get('given_name') or '',
    }
