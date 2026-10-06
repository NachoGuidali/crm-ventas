"""
Reportes comerciales: leads únicos, embudo de conversión etapa a etapa, pipeline por vendedora y actividad por
vendedora. Todo se calcula con datos que el CRM ya registra (oportunidades, historial, mensajes, llamadas).
"""
from django.db.models import Count, Max, Q, Sum


def _pct(a, b):
    return round(a * 100 / b, 1) if b else 0


def leads_unicos(embudo, ini, fin, agente=None):
    """Personas distintas que entraron en el período: nuevas (oportunidad creada) + las que reingresaron."""
    from apps.crm.models import Actividad, Oportunidad
    ops = Oportunidad.objects.filter(created_at__range=(ini, fin))
    reing = Actividad.objects.filter(tipo=Actividad.TIPO_REINGRESO, created_at__range=(ini, fin))
    if embudo is not None:
        ops = ops.filter(embudo=embudo)
        reing = reing.filter(oportunidad__embudo=embudo)
    if agente is not None:
        ops = ops.filter(agente=agente)
        reing = reing.filter(oportunidad__agente=agente)
    nuevos = set(ops.values_list('contacto_id', flat=True))
    reingresos = set(reing.values_list('contacto_id', flat=True))
    return {'unicos': len(nuevos | reingresos), 'nuevos': len(nuevos), 'reingresos': len(reingresos - nuevos),
            'eventos_reingreso': reing.count()}


def embudo_conversion(embudo, ini, fin, agente=None):
    """
    De los leads que ingresaron en el período (cohorte), cuántos llegaron a cada etapa y qué porcentaje pasó de una
    etapa a la siguiente. "Llegó" = estuvo alguna vez en esa etapa o en una posterior; una venta cuenta como haber
    pasado por todas.
    """
    from apps.crm.models import Etapa, Oportunidad
    if embudo is None:
        return []
    etapas = list(embudo.etapas.filter(tipo=Etapa.TIPO_NORMAL).order_by('orden', 'pk'))
    if not etapas:
        return []
    ops = Oportunidad.objects.filter(embudo=embudo, created_at__range=(ini, fin))
    if agente is not None:
        ops = ops.filter(agente=agente)
    filas = ops.annotate(max_hist=Max('historial__etapa_nueva__orden',
                                      filter=Q(historial__etapa_nueva__embudo=embudo,
                                               historial__etapa_nueva__tipo=Etapa.TIPO_NORMAL)))
    tope = max(e.orden for e in etapas) + 1
    alcanzado = []
    ventas = 0
    for estado, etapa_orden, etapa_tipo, max_hist in filas.values_list('estado', 'etapa__orden', 'etapa__tipo',
                                                                      'max_hist'):
        if estado == Oportunidad.ESTADO_GANADA:
            ventas += 1
            alcanzado.append(tope)
            continue
        actual = etapa_orden if etapa_tipo == Etapa.TIPO_NORMAL else 0
        alcanzado.append(max(actual or 0, max_hist or 0))
    total = len(alcanzado)
    resultado, anterior = [], total
    for e in etapas:
        n = sum(1 for a in alcanzado if a >= e.orden) if e != etapas[0] else total
        resultado.append({'nombre': e.nombre, 'color': e.color, 'n': n, 'pct_total': _pct(n, total),
                          'pct_anterior': _pct(n, anterior) if e != etapas[0] else 100.0})
        anterior = n
    resultado.append({'nombre': 'Venta', 'color': '#22C97A', 'n': ventas, 'pct_total': _pct(ventas, total),
                      'pct_anterior': _pct(ventas, anterior), 'venta': True})
    return resultado


def matriz_pipeline(embudo, agente=None):
    """Oportunidades en curso por vendedora × etapa (foto de ahora)."""
    from apps.crm.models import Etapa, Oportunidad
    from apps.users.models import User
    if embudo is None:
        return None
    etapas = list(embudo.etapas.filter(tipo=Etapa.TIPO_NORMAL).order_by('orden', 'pk'))
    ops = Oportunidad.objects.filter(embudo=embudo, estado__in=Oportunidad.ESTADOS_ACTIVOS)
    if agente is not None:
        ops = ops.filter(agente=agente)
    conteo = {}
    for ag, et, n in ops.values_list('agente', 'etapa').annotate(n=Count('pk')).values_list('agente', 'etapa', 'n'):
        conteo[(ag, et)] = n
    agentes = {u.pk: u for u in User.objects.filter(pk__in={a for a, _ in conteo if a})}
    filas = []
    for ag_id in sorted({a for a, _ in conteo}, key=lambda a: (a is None, agentes[a].display_name if a else '')):
        celdas = [conteo.get((ag_id, e.pk), 0) for e in etapas]
        filas.append({'agente': agentes.get(ag_id), 'celdas': celdas, 'total': sum(celdas)})
    totales = [sum(f['celdas'][i] for f in filas) for i in range(len(etapas))]
    return {'etapas': etapas, 'filas': filas, 'totales': totales, 'total': sum(totales),
            'max': max([c for f in filas for c in f['celdas']] or [0])}


def actividad_vendedoras(ini, fin, agentes=None):
    """
    Lo que hizo cada vendedora en el período (todos los embudos): mensajes manuales y plantillas, mensajes y emails
    automáticos a sus leads, llamadas y minutos, notas, intentos, cambios de etapa y tareas completadas.
    """
    from apps.crm.models import Actividad, Tarea
    from apps.telefonia.models import Llamada
    from apps.users.models import User
    from apps.whatsapp.models import Mensaje

    def por(qs, campo, **ann):
        return {r[campo]: r for r in qs.values(campo).annotate(**ann)}

    salientes = Mensaje.objects.filter(direccion=Mensaje.DIR_SALIENTE, timestamp__range=(ini, fin))
    manuales = por(salientes.filter(automatico=False, enviado_por__isnull=False), 'enviado_por',
                   mensajes=Count('pk'), plantillas=Count('pk', filter=Q(plantilla__isnull=False)))
    automaticos = por(salientes.filter(automatico=True, conversacion__agente__isnull=False), 'conversacion__agente',
                      n=Count('pk'))
    acts = Actividad.objects.filter(created_at__range=(ini, fin))
    propias = por(acts.filter(usuario__isnull=False), 'usuario',
                  notas=Count('pk', filter=Q(tipo=Actividad.TIPO_NOTA)),
                  intentos=Count('pk', filter=Q(tipo=Actividad.TIPO_INTENTO)),
                  etapas=Count('pk', filter=Q(tipo__in=[Actividad.TIPO_ETAPA, Actividad.TIPO_CIERRE])),
                  emails=Count('pk', filter=Q(tipo=Actividad.TIPO_EMAIL)))
    emails_auto = por(acts.filter(tipo=Actividad.TIPO_EMAIL, usuario__isnull=True, oportunidad__agente__isnull=False),
                      'oportunidad__agente', n=Count('pk'))
    llamadas = por(Llamada.objects.filter(inicio_at__range=(ini, fin), agente__isnull=False), 'agente',
                   n=Count('pk'), salientes=Count('pk', filter=Q(direccion=Llamada.DIR_SALIENTE)),
                   atendidas=Count('pk', filter=Q(estado=Llamada.ESTADO_ATENDIDA)), seg=Sum('duracion_seg'))
    tareas = por(Tarea.objects.filter(estado=Tarea.ESTADO_COMPLETADA, completada_at__range=(ini, fin),
                                      asignado_a__isnull=False), 'asignado_a', n=Count('pk'))
    ids = set(manuales) | set(automaticos) | set(propias) | set(emails_auto) | set(llamadas) | set(tareas)
    usuarios = User.objects.filter(pk__in=ids)
    if agentes is not None:
        usuarios = usuarios.filter(pk__in=[a.pk for a in agentes])
    filas = []
    for u in usuarios:
        m, p, ll = manuales.get(u.pk, {}), propias.get(u.pk, {}), llamadas.get(u.pk, {})
        seg = ll.get('seg') or 0
        filas.append({
            'u': u, 'mensajes': m.get('mensajes', 0), 'plantillas': m.get('plantillas', 0),
            'automaticos': automaticos.get(u.pk, {}).get('n', 0), 'emails': p.get('emails', 0),
            'emails_auto': emails_auto.get(u.pk, {}).get('n', 0),
            'llamadas': ll.get('n', 0), 'llamadas_salientes': ll.get('salientes', 0),
            'llamadas_atendidas': ll.get('atendidas', 0), 'minutos': round(seg / 60),
            'promedio_seg': round(seg / ll['atendidas']) if ll.get('atendidas') else 0,
            'notas': p.get('notas', 0), 'intentos': p.get('intentos', 0), 'etapas': p.get('etapas', 0),
            'tareas': tareas.get(u.pk, {}).get('n', 0),
        })
    return sorted(filas, key=lambda f: f['u'].display_name.lower())


COLUMNAS_ACTIVIDAD = [
    ('mensajes', 'WhatsApp enviados'), ('plantillas', 'de ellos, plantillas'), ('automaticos', 'WhatsApp automáticos'),
    ('emails', 'Emails'), ('emails_auto', 'Emails automáticos'), ('llamadas', 'Llamadas'),
    ('llamadas_atendidas', 'Atendidas'), ('minutos', 'Minutos'), ('promedio_seg', 'Duración prom. (s)'),
    ('intentos', 'Intentos'), ('notas', 'Notas'), ('etapas', 'Cambios de etapa'), ('tareas', 'Tareas completadas'),
]


def sla(embudo, ini, fin, agente=None):
    """De los leads asignados en el período (en embudos con SLA): cuántos tuvieron la primera gestión a tiempo."""
    from datetime import timedelta
    from django.db.models import Avg, DurationField, ExpressionWrapper, F
    from django.utils import timezone
    from apps.crm.models import Embudo, Oportunidad
    from apps.crm.services import q_sla_vencido
    embudos = [embudo] if embudo is not None else list(Embudo.objects.filter(sla_minutos__gt=0))
    embudos = [e for e in embudos if e and e.sla_minutos]
    if not embudos:
        return None
    ahora = timezone.now()
    dentro = fuera = pendientes = 0
    for e in embudos:
        limite = F('asignada_at') + timedelta(minutes=e.sla_minutos)
        ops = Oportunidad.objects.filter(embudo=e, asignada_at__range=(ini, fin))
        if agente is not None:
            ops = ops.filter(agente=agente)
        dentro += ops.filter(primer_contacto_at__lte=limite).count()
        fuera += ops.filter(primer_contacto_at__gt=limite).count()
        fuera += ops.filter(primer_contacto_at__isnull=True, asignada_at__lt=ahora - timedelta(minutes=e.sla_minutos)).count()
        pendientes += ops.filter(primer_contacto_at__isnull=True,
                                 asignada_at__gte=ahora - timedelta(minutes=e.sla_minutos)).count()
    base = Oportunidad.objects.filter(embudo__in=embudos, asignada_at__range=(ini, fin), primer_contacto_at__isnull=False,
                                      primer_contacto_at__gte=F('asignada_at'))
    if agente is not None:
        base = base.filter(agente=agente)
    prom = base.aggregate(t=Avg(ExpressionWrapper(F('primer_contacto_at') - F('asignada_at'),
                                                  output_field=DurationField())))['t']
    vencidos = Oportunidad.objects.filter(q_sla_vencido(ahora), embudo__in=embudos)
    if agente is not None:
        vencidos = vencidos.filter(agente=agente)
    return {'minutos': embudos[0].sla_minutos if len(embudos) == 1 else None, 'dentro': dentro, 'fuera': fuera,
            'pendientes': pendientes, 'pct': _pct(dentro, dentro + fuera),
            'promedio_min': round(prom.total_seconds() / 60) if prom else None, 'vencidos_ahora': vencidos.count()}


def calidad_envios(ini, fin, horas_respuesta=48, dias_avance=7):
    """
    Respuesta a los envíos (WhatsApp automáticos y plantillas; emails automáticos): entregados, leídos, respondidos
    en las N horas siguientes y tarjetas que cambiaron de etapa en los N días siguientes.
    """
    from datetime import timedelta
    from django.db.models import Exists, OuterRef
    from apps.automatizaciones.models import EmailEnviado
    from apps.crm.models import HistorialEtapa
    from apps.whatsapp.models import Mensaje
    respuesta = Mensaje.objects.filter(conversacion=OuterRef('conversacion'), direccion=Mensaje.DIR_ENTRANTE,
                                       timestamp__gt=OuterRef('timestamp'),
                                       timestamp__lte=OuterRef('timestamp') + timedelta(hours=horas_respuesta))
    avance = HistorialEtapa.objects.filter(oportunidad__contacto=OuterRef('conversacion__contacto'),
                                           etapa_anterior__isnull=False, created_at__gt=OuterRef('timestamp'),
                                           created_at__lte=OuterRef('timestamp') + timedelta(days=dias_avance))
    base = (Mensaje.objects.filter(direccion=Mensaje.DIR_SALIENTE, timestamp__range=(ini, fin))
            .filter(Q(automatico=True) | Q(plantilla__isnull=False))
            .annotate(resp=Exists(respuesta), avanzo=Exists(avance)))
    medidas = dict(n=Count('pk'), entregados=Count('pk', filter=Q(status__in=[Mensaje.STATUS_ENTREGADO, Mensaje.STATUS_LEIDO])),
                   leidos=Count('pk', filter=Q(status=Mensaje.STATUS_LEIDO)),
                   fallidos=Count('pk', filter=Q(status=Mensaje.STATUS_FALLIDO)),
                   respondidos=Count('pk', filter=Q(resp=True)), avanzaron=Count('pk', filter=Q(avanzo=True)))

    def filas(qs, campos, nombre):
        out = []
        for r in qs.values(*campos).annotate(**medidas).order_by('-n'):
            r.update(nombre=nombre(r), pct_entregados=_pct(r['entregados'], r['n']), pct_leidos=_pct(r['leidos'], r['n']),
                     pct_respondidos=_pct(r['respondidos'], r['n']), pct_avanzaron=_pct(r['avanzaron'], r['n']))
            out.append(r)
        return out

    por_automatizacion = filas(base.filter(automatico=True), ['accion', 'accion__nombre', 'accion__embudo__nombre',
                                                              'accion__etapa__nombre'],
                               lambda r: r['accion__nombre'] or 'Automático (sin automatización registrada)')
    por_plantilla = filas(base.filter(plantilla__isnull=False), ['plantilla', 'plantilla__nombre'],
                          lambda r: r['plantilla__nombre'])
    emails = []
    for r in (EmailEnviado.objects.filter(enviado_at__range=(ini, fin))
              .values('accion', 'accion__nombre').annotate(n=Count('pk'), abiertos=Count('pk', filter=Q(abierto_at__isnull=False)),
                                                           clics=Count('pk', filter=Q(clic_at__isnull=False))).order_by('-n')):
        r.update(nombre=r['accion__nombre'] or 'Email automático', pct_abiertos=_pct(r['abiertos'], r['n']),
                 pct_clics=_pct(r['clics'], r['n']))
        emails.append(r)
    return {'automatizaciones': por_automatizacion, 'plantillas': por_plantilla, 'emails': emails,
            'horas_respuesta': horas_respuesta, 'dias_avance': dias_avance}
