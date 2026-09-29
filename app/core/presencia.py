"""
Presencia de usuarios ("conectados"): cada pantalla del CRM late cada 4–15 s (/pulso/); si un usuario no late en
TTL segundos se lo considera desconectado. Vive en Redis, no toca la base.
"""
import logging

from django.core.cache import cache

logger = logging.getLogger('apps.users')

TTL = 240  # los navegadores espacian los timers de pestañas en segundo plano hasta ~1 min


def _clave(user_id):
    return f'presencia:{user_id}'


def marcar(user):
    """Marca al usuario como conectado. Si recién se conecta, reparte los prospectos que esperaban a alguien."""
    if cache.add(_clave(user.pk), 1, TTL):
        _al_conectarse()
        return True
    cache.touch(_clave(user.pk), TTL)
    return False


def desconectar(user):
    cache.delete(_clave(user.pk))


def conectados(ids):
    ids = list(ids)
    if not ids:
        return set()
    encontrados = cache.get_many([_clave(i) for i in ids])
    return {i for i in ids if _clave(i) in encontrados}


def esta_conectado(user):
    return bool(cache.get(_clave(user.pk)))


def _al_conectarse():
    if not cache.add('presencia:reparto', 1, 15):  # una sola corrida aunque se conecten varios a la vez
        return
    try:
        from apps.crm.tasks import asignar_pendientes
        asignar_pendientes.delay()
    except Exception:  # sin broker: lo levanta el barrido periódico
        logger.warning('No se pudo encolar el reparto de pendientes al conectarse un usuario', exc_info=True)
