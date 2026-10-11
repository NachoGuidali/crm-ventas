"""Cifrado simétrico de secretos guardados en la base (credenciales SIP). La clave se deriva de SECRET_KEY:
si se cambia SECRET_KEY hay que volver a cargar esas contraseñas."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

PREFIJO = 'fernet:'


def _fernet():
    clave = hashlib.sha256(f'crm-cifrado:{settings.SECRET_KEY}'.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(clave))


def cifrar(texto):
    if not texto:
        return ''
    return PREFIJO + _fernet().encrypt(texto.encode()).decode()


def descifrar(valor):
    if not valor:
        return ''
    if not valor.startswith(PREFIJO):
        return valor
    try:
        return _fernet().decrypt(valor[len(PREFIJO):].encode()).decode()
    except InvalidToken:
        return ''
