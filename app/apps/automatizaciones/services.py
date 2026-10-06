import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.db.models import Exists, F, OuterRef
from django.utils import timezone
from django.utils.html import escape, linebreaks

import secrets

from .models import AccionEtapa, EjecucionAccion, EmailEnviado

logger = logging.getLogger('apps.automatizaciones')


def enviar_email(contacto, op, asunto, cuerpo, usuario=None, accion=None):
    """Envía un email (manual o automático) con seguimiento de apertura y clics, y lo registra en la ficha."""
    from apps.crm.models import Actividad
    envio = EmailEnviado.objects.create(accion=accion, oportunidad=op, contacto=contacto, para=contacto.email,
                                        asunto=asunto[:200], token=secrets.token_urlsafe(24), enviado_por=usuario)
    mail = EmailMultiAlternatives(asunto, cuerpo, settings.DEFAULT_FROM_EMAIL, [contacto.email],
                                  reply_to=[usuario.email] if usuario is not None and usuario.email else None)
    mail.attach_alternative(html_con_seguimiento(cuerpo, envio.token), 'text/html')
    mail.send(fail_silently=False)
    Actividad.objects.create(contacto=contacto, oportunidad=op, tipo=Actividad.TIPO_EMAIL, usuario=usuario,
                             texto=f'Email{" automático" if usuario is None else ""}: {asunto}\n{cuerpo[:500]}')
    return envio


def html_con_seguimiento(cuerpo, token):
    """Cuerpo del email en HTML con los links redirigidos (clics) y un píxel invisible (aperturas)."""
    import re
    from django.core import signing
    from django.utils.html import urlize
    base = settings.SITE_URL

    def redirigir(m):
        firmado = signing.dumps(m.group(1), salt='email-clic')
        return f'href="{base}/e/c/{token}/?u={firmado}"'
    html = re.sub(r'href="(https?://[^"]+)"', redirigir, urlize(linebreaks(escape(cuerpo))))
    return html + f'<img src="{base}/e/o/{token}.gif" width="1" height="1" alt="" style="display:block;border:0">'


def proxima_apertura(embudo, momento):
    """Si `momento` cae fuera del horario de atención del embudo, devuelve la próxima apertura."""
    if not embudo.respetar_horario or embudo.en_horario(momento):
        return momento
    local = timezone.localtime(momento)
    dias = embudo.dias_habiles or [0, 1, 2, 3, 4]
    desde = embudo.horario_desde
    if isinstance(desde, str):
        from datetime import time
        desde = time(*map(int, desde.split(':')[:2]))
    for delta in range(0, 8):
        dia = (local + timedelta(days=delta)).date()
        candidato = timezone.make_aware(datetime.combine(dia, desde), timezone.get_current_timezone())
        if dia.weekday() in dias and candidato > local:
            return candidato
    return momento


def programar_acciones_de_etapa(oportunidad_id, historial_id):
    """Se llama al entrar a una etapa (on_commit). Programa las acciones configuradas para esa etapa."""
    from apps.crm.models import HistorialEtapa
    historial = HistorialEtapa.objects.select_related('oportunidad__embudo', 'etapa_nueva').filter(pk=historial_id).first()
    if historial is None or historial.etapa_nueva_id is None:
        return 0
    op = historial.oportunidad
    acciones = AccionEtapa.objects.filter(etapa_id=historial.etapa_nueva_id, activa=True, disparador__in=[
        AccionEtapa.DISP_ENTRADA, AccionEtapa.DISP_SIN_RESPUESTA]).order_by('orden', 'pk')
    ahora = timezone.now()
    programadas = 0
    for accion in acciones:
        cuando = ahora + timedelta(minutes=accion.demora_minutos)
        if accion.solo_en_horario:
            cuando = proxima_apertura(op.embudo, cuando)
        try:
            with transaction.atomic():
                ejec = EjecucionAccion.objects.create(accion=accion, oportunidad=op, historial=historial,
                                                      programada_para=cuando)
        except IntegrityError:
            continue
        programadas += 1
        _encolar(ejec, cuando, ahora)
    return programadas


def _historial_actual(op):
    from apps.crm.models import HistorialEtapa
    return HistorialEtapa.objects.filter(oportunidad=op, etapa_nueva_id=op.etapa_id).order_by('-created_at', '-pk').first()


def _programar_ya(accion, op, historial):
    """Crea (una sola vez por entrada a la etapa) y encola una ejecución inmediata."""
    if historial is None:
        return None
    ahora = timezone.now()
    cuando = proxima_apertura(op.embudo, ahora) if accion.solo_en_horario else ahora
    try:
        with transaction.atomic():
            ejec = EjecucionAccion.objects.create(accion=accion, oportunidad=op, historial=historial, programada_para=cuando)
    except IntegrityError:
        return None
    _encolar(ejec, cuando, ahora)
    return ejec


def cliente_respondio(op):
    """El cliente respondió (mensaje entrante o llamada atendida): dispara las automatizaciones "Cuando responde"."""
    if op is None or not op.activa:
        return 0
    acciones = list(AccionEtapa.objects.filter(etapa_id=op.etapa_id, activa=True, disparador=AccionEtapa.DISP_RESPUESTA))
    if not acciones:
        return 0
    historial = _historial_actual(op)
    return sum(1 for a in acciones if _programar_ya(a, op, historial))


def revisar_sin_actividad():
    """Cada 5 min: automatizaciones "Si no hay actividad durante X" sobre las oportunidades en esa etapa."""
    from apps.crm.models import Oportunidad
    ahora, n = timezone.now(), 0
    for accion in AccionEtapa.objects.filter(activa=True, disparador=AccionEtapa.DISP_SIN_ACTIVIDAD).select_related('embudo'):
        limite = ahora - timedelta(minutes=max(accion.demora_minutos, 1))
        ops = (Oportunidad.objects.filter(etapa_id=accion.etapa_id, estado=Oportunidad.ESTADO_ABIERTA,
                                          ultima_actividad_at__lt=limite, etapa_desde__lt=limite)
               .exclude(Exists(EjecucionAccion.objects.filter(accion=accion, oportunidad=OuterRef('pk'),
                                                             created_at__gte=OuterRef('etapa_desde'))))
               .select_related('embudo')[:300])
        for op in ops:
            if _programar_ya(accion, op, _historial_actual(op)):
                n += 1
    return n


def _respondio_desde(op, desde):
    from apps.telefonia.models import Llamada
    from apps.whatsapp.models import Mensaje
    return (Mensaje.objects.filter(conversacion__contacto_id=op.contacto_id, direccion=Mensaje.DIR_ENTRANTE,
                                   timestamp__gte=desde).exists()
            or Llamada.objects.filter(contacto_id=op.contacto_id, direccion=Llamada.DIR_ENTRANTE,
                                      estado=Llamada.ESTADO_ATENDIDA, inicio_at__gte=desde).exists())


def _encolar(ejec, cuando, ahora):
    from .tasks import ejecutar_accion
    try:
        if cuando <= ahora + timedelta(seconds=5):
            ejecutar_accion.delay(ejec.pk)
        elif cuando <= ahora + timedelta(minutes=15):
            ejecutar_accion.apply_async(args=[ejec.pk], eta=cuando)
        # Demoras largas (horas/días): no se dejan en el broker; las toma `barrer_programadas` a su hora.
    except Exception as e:
        # Si el broker no está disponible queda "programada" y la recoge el barrido periódico.
        logger.error('No se pudo encolar la ejecución %s: %s', ejec.pk, e)


def ejecutar(ejecucion_id):
    with transaction.atomic():
        ejec = (EjecucionAccion.objects.select_for_update(of=('self',))
                .select_related('accion', 'historial', 'oportunidad__contacto', 'oportunidad__embudo', 'oportunidad__etapa',
                                'oportunidad__agente').filter(pk=ejecucion_id).first())
        if ejec is None or ejec.estado != EjecucionAccion.ESTADO_PROGRAMADA:
            return None
        if ejec.programada_para > timezone.now() + timedelta(seconds=30):
            return None  # se adelantó (reintento de la cola): la ejecuta el barrido a su hora
        ejec.estado = EjecucionAccion.ESTADO_EJECUTADA  # reservada: otro worker no la repite
        ejec.ejecutada_at = timezone.now()
        ejec.save(update_fields=['estado', 'ejecutada_at'])

    accion, op = ejec.accion, ejec.oportunidad
    try:
        desde = None
        if accion.disparador == AccionEtapa.DISP_SIN_RESPUESTA and ejec.historial_id:
            desde = ejec.historial.created_at
        elif accion.disparador == AccionEtapa.DISP_SIN_ACTIVIDAD:
            desde = ejec.created_at
        estado, detalle = _correr(accion, op, desde)
    except Exception as e:
        logger.exception('Error ejecutando automatización %s sobre oportunidad #%s', accion, op.pk)
        estado, detalle = EjecucionAccion.ESTADO_ERROR, str(e)[:500]
    EjecucionAccion.objects.filter(pk=ejec.pk).update(estado=estado, detalle=detalle[:500])
    return estado


def _correr(accion, op, ejec_desde=None):
    from apps.crm import services as crm
    from apps.crm.models import Actividad, Oportunidad, Tarea
    from apps.users.services import notificar, notificar_varios, supervisores_de
    from apps.whatsapp.models import reemplazar_variables_texto

    E = EjecucionAccion
    op.refresh_from_db()
    if not accion.activa:
        return E.ESTADO_OMITIDA, 'La automatización se desactivó.'
    if accion.solo_si_sigue_en_etapa and op.etapa_id != accion.etapa_id:
        return E.ESTADO_OMITIDA, 'El prospecto ya no está en esa etapa.'
    if accion.disparador == AccionEtapa.DISP_SIN_RESPUESTA and ejec_desde is not None and _respondio_desde(op, ejec_desde):
        return E.ESTADO_OMITIDA, 'El cliente respondió.'
    if accion.disparador == AccionEtapa.DISP_SIN_ACTIVIDAD and ejec_desde is not None \
            and op.ultima_actividad_at and op.ultima_actividad_at > ejec_desde:
        return E.ESTADO_OMITIDA, 'Hubo actividad antes de ejecutar.'
    es_mensaje = accion.tipo in (AccionEtapa.TIPO_WHATSAPP, AccionEtapa.TIPO_EMAIL)
    if es_mensaje and op.estado == Oportunidad.ESTADO_PAUSADA:
        return E.ESTADO_OMITIDA, 'El prospecto está pausado.'

    contacto = op.contacto
    if accion.tipo == AccionEtapa.TIPO_WHATSAPP:
        from apps.whatsapp.services import enviar_automatico
        msg, detalle = enviar_automatico(op, plantilla=accion.plantilla, texto=accion.texto, linea=accion.linea,
                                         accion=accion)
        return (E.ESTADO_EJECUTADA if msg else E.ESTADO_OMITIDA), detalle

    if accion.tipo == AccionEtapa.TIPO_EMAIL:
        if not contacto.email or contacto.no_contactar:
            return E.ESTADO_OMITIDA, 'Sin email o contacto marcado como "no contactar".'
        cuerpo = reemplazar_variables_texto(accion.texto, contacto, op)
        asunto = reemplazar_variables_texto(accion.email_asunto or op.embudo.nombre, contacto, op)
        enviar_email(contacto, op, asunto, cuerpo, accion=accion)
        return E.ESTADO_EJECUTADA, f'Email enviado a {contacto.email}'

    titulo = reemplazar_variables_texto(accion.tarea_titulo or accion.nombre, contacto, op)
    if accion.tipo == AccionEtapa.TIPO_TAREA:
        if op.agente is None:
            return E.ESTADO_OMITIDA, 'La oportunidad no tiene agente asignado.'
        tarea = crm.crear_tarea(None, op.agente, titulo, timezone.now() + timedelta(hours=accion.tarea_vence_horas),
                                oportunidad=op, tipo=Tarea.TIPO_SEGUIMIENTO)
        Tarea.objects.filter(pk=tarea.pk).update(automatica=True)
        return E.ESTADO_EJECUTADA, f'Tarea creada para {op.agente.display_name}'

    if accion.tipo == AccionEtapa.TIPO_NOTIF_AGENTE:
        if op.agente is None:
            return E.ESTADO_OMITIDA, 'La oportunidad no tiene agente asignado.'
        notificar(op.agente, 'sistema', titulo, f'{contacto.nombre} · {op.etapa}', op.get_absolute_url())
        return E.ESTADO_EJECUTADA, f'Notificado {op.agente.display_name}'

    if accion.tipo == AccionEtapa.TIPO_NOTIF_SUPERVISORES:
        destinatarios = supervisores_de(op.embudo)
        notificar_varios(destinatarios, 'sistema', titulo, f'{contacto.nombre} · {op.etapa}', op.get_absolute_url())
        return E.ESTADO_EJECUTADA, f'Notificados {len(destinatarios)} supervisores'

    if accion.tipo == AccionEtapa.TIPO_EMBUDO:
        try:
            if accion.modo_embudo == AccionEtapa.MODO_VOLVER:
                destino = crm.volver_a_embudo_previo(op, a_la_siguiente=accion.volver_a == AccionEtapa.VOLVER_SIGUIENTE,
                                                     asignar=accion.asignar_destino,
                                                     usuario_destino=accion.usuario_destino)
            else:
                if accion.embudo_destino is None:
                    return E.ESTADO_OMITIDA, 'Falta el embudo de destino.'
                destino = crm.pasar_a_embudo(op, accion.embudo_destino, accion.etapa_destino, modo=accion.modo_embudo,
                                             asignar=accion.asignar_destino, usuario_destino=accion.usuario_destino)
        except crm.ErrorNegocio as e:
            return E.ESTADO_OMITIDA, str(e)
        return E.ESTADO_EJECUTADA, f'{"Creada #" + str(destino.pk) + " en" if destino.pk != op.pk else "Pasó a"} {destino.embudo} · {destino.etapa}'

    if accion.tipo == AccionEtapa.TIPO_SMS:
        from apps.integraciones import sms
        if not contacto.puede_recibir_mensajes:
            return E.ESTADO_OMITIDA, 'El contacto no acepta mensajes o no tiene teléfono válido.'
        try:
            sms.enviar(contacto, reemplazar_variables_texto(accion.texto, contacto, op), oportunidad=op)
        except sms.ErrorSMS as e:
            return E.ESTADO_OMITIDA, str(e)
        return E.ESTADO_EJECUTADA, f'SMS enviado a {contacto.telefono}'

    if accion.tipo == AccionEtapa.TIPO_ETAPA:
        destino = accion.mover_a
        if destino is None or destino.embudo_id != op.embudo_id:
            return E.ESTADO_OMITIDA, 'La etapa de destino no es de este embudo.'
        if not op.activa:
            return E.ESTADO_OMITIDA, 'La oportunidad ya está cerrada.'
        try:
            if destino.es_cierre and accion.tipificacion and accion.tipificacion.es_postergacion:
                return E.ESTADO_OMITIDA, 'Para postergar usá una tipificación que no sea de postergación.'
            hist = crm.mover_etapa(op, destino, None, tipificacion=accion.tipificacion if destino.es_cierre else None,
                                   nota=f'Automatización: {accion.nombre}', automatico=True)
        except crm.ErrorNegocio as e:
            return E.ESTADO_OMITIDA, str(e)
        if hist is None:
            return E.ESTADO_OMITIDA, 'No se movió (ya estaba ahí o faltan datos obligatorios).'
        return E.ESTADO_EJECUTADA, f'Pasó a {destino}'

    return E.ESTADO_OMITIDA, 'Tipo de acción desconocido.'


def barrer_programadas():
    """Ejecuta las acciones con demora larga cuando llega su hora (y las que no salieron por la cola)."""
    from .tasks import ejecutar_accion
    vencidas = list(EjecucionAccion.objects.filter(
        estado=EjecucionAccion.ESTADO_PROGRAMADA, programada_para__lte=timezone.now() - timedelta(seconds=30),
    ).order_by('programada_para').values_list('pk', flat=True)[:500])
    for pk in vencidas:
        ejecutar_accion.delay(pk)
    return len(vencidas)


def revisar_inactividad():
    """
    - Recordatorio al agente cuando un prospecto lleva N días sin actividad (crea tarea).
    - Aviso a supervisión cuando un prospecto queda estancado N días en la misma etapa
      (agrupado: un solo aviso por supervisor y embudo, no uno por prospecto).
    """
    from django.db.models import F, Q
    from apps.crm import services as crm
    from apps.crm.models import Embudo, Etapa, Oportunidad, Tarea
    from apps.users.services import notificar, notificar_varios, supervisores_de

    ahora = timezone.now()
    recordatorios = estancados_total = 0
    for embudo in Embudo.objects.filter(activo=True):
        if embudo.dias_inactividad_recordatorio:
            limite = ahora - timedelta(days=embudo.dias_inactividad_recordatorio)
            qs = (Oportunidad.objects.filter(embudo=embudo, estado=Oportunidad.ESTADO_ABIERTA, agente__isnull=False,
                                             ultima_actividad_at__lt=limite)
                  .filter(Q(recordatorio_enviado_at__isnull=True) | Q(recordatorio_enviado_at__lt=F('ultima_actividad_at')))
                  .select_related('contacto', 'agente')[:500])
            for op in qs:
                if not op.tareas.filter(estado=Tarea.ESTADO_PENDIENTE).exists():
                    crm.crear_tarea(None, op.agente, f'Seguimiento: {op.contacto.nombre} sin actividad hace '
                                                     f'{op.dias_sin_actividad} días', ahora + timedelta(hours=4),
                                    oportunidad=op, tipo=Tarea.TIPO_SEGUIMIENTO)
                    notificar(op.agente, 'tarea', f'{op.contacto.nombre} lleva {op.dias_sin_actividad} días sin gestión',
                              str(op.embudo), op.get_absolute_url())
                Oportunidad.objects.filter(pk=op.pk).update(recordatorio_enviado_at=ahora)
                recordatorios += 1

        if embudo.dias_estancado_alerta:
            limite = ahora - timedelta(days=embudo.dias_estancado_alerta)
            estancados = (Oportunidad.objects.filter(embudo=embudo, estado=Oportunidad.ESTADO_ABIERTA,
                                                     etapa__tipo=Etapa.TIPO_NORMAL, etapa_desde__lt=limite)
                          .filter(Q(alerta_estancado_at__isnull=True) | Q(alerta_estancado_at__lt=F('etapa_desde'))))
            ids = list(estancados.values_list('pk', flat=True)[:2000])
            if ids:
                Oportunidad.objects.filter(pk__in=ids).update(alerta_estancado_at=ahora)
                notificar_varios(
                    supervisores_de(embudo), 'estancado',
                    f'{len(ids)} prospecto{"s" if len(ids) > 1 else ""} estancado{"s" if len(ids) > 1 else ""} en {embudo}',
                    f'Llevan más de {embudo.dias_estancado_alerta} días en la misma etapa.',
                    f'/oportunidades/?embudo={embudo.pk}&estancados=1',
                )
                estancados_total += len(ids)
    return {'recordatorios': recordatorios, 'estancados': estancados_total}
