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
        _abrir_sesion(user)
        _al_conectarse()
        return True
    cache.touch(_clave(user.pk), TTL)
    _registrar_pulso(user)
    return False


def desconectar(user):
    cache.delete(_clave(user.pk))
    from django.utils import timezone
    from apps.users.models import SesionConexion
    sid = cache.get(f'presencia:sesion:{user.pk}')
    if sid:
        SesionConexion.objects.filter(pk=sid, fin__isnull=True).update(fin=timezone.now(), ultimo=timezone.now())
        cache.delete(f'presencia:sesion:{user.pk}')


def _abrir_sesion(user):
    from django.utils import timezone
    from apps.users.models import SesionConexion
    ahora = timezone.now()
    sesion = SesionConexion.objects.create(usuario=user, inicio=ahora, ultimo=ahora)
    cache.set(f'presencia:sesion:{user.pk}', sesion.pk, 60 * 60 * 24)


def _registrar_pulso(user):
    """Actualiza el "último pulso" de la sesión como mucho una vez por minuto (no escribe en cada pulso)."""
    if not cache.add(f'presencia:pulso_db:{user.pk}', 1, 60):
        return
    from django.utils import timezone
    from apps.users.models import SesionConexion
    sid = cache.get(f'presencia:sesion:{user.pk}')
    if sid and SesionConexion.objects.filter(pk=sid, fin__isnull=True).update(ultimo=timezone.now()):
        return
    _abrir_sesion(user)  # se perdió la referencia (reinicio de Redis): abre otra


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
