from django.conf import settings


def crm(request):
    ctx = {'CRM_NOMBRE': settings.CRM_NOMBRE, 'CRM_SUBTITULO': settings.CRM_SUBTITULO}
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return ctx
    from apps.users.services import contar_no_leidas
    from apps.crm.services import contar_tareas_hoy
    from apps.whatsapp.services import contar_no_leidos_usuario
    from apps.telefonia.services import usuario_tiene_interno
    ctx.update({
        'perms_crm': user.permisos_efectivos,
        'notif_no_leidas': contar_no_leidas(user),
        'tareas_hoy': contar_tareas_hoy(user),
        'wa_no_leidos': contar_no_leidos_usuario(user),
        'tiene_interno_anura': usuario_tiene_interno(user),
    })
    return ctx
