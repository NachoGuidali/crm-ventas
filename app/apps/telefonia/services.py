"""
Telefonía Anura.

`procesar_evento_llamada` es el único punto que interpreta eventos de llamadas: lo usan el
webhook (CallHook), el polling de respaldo de CDRs, el modo demo y el discador. Es idempotente
por callId: si Anura reintenta el envío o el polling trae una llamada ya procesada, no se duplica nada.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_tz

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.phone import normalizar_telefono

from .client import ErrorAnura, get_cliente
from .models import AgenteDiscador, CampaniaContacto, CampaniaDiscado, ConfigAnura, InternoAnura, Llamada, RutaAnura

logger = logging.getLogger('apps.telefonia')


class ErrorTelefonia(Exception):
    pass


# ═══════════════════════════════════════════════════════════════════════════
# Normalización del payload (formato a confirmar con Anura: se aceptan variantes)
# ═══════════════════════════════════════════════════════════════════════════

_FINALES = {'ANSWER': Llamada.ESTADO_ATENDIDA, 'ANSWERED': Llamada.ESTADO_ATENDIDA, 'TALK': Llamada.ESTADO_ATENDIDA,
            'COMPLETED': Llamada.ESTADO_ATENDIDA, 'NOANSWER': Llamada.ESTADO_NO_ATENDIDA,
            'BUSY': Llamada.ESTADO_OCUPADO, 'FAILED': Llamada.ESTADO_FALLIDA, 'CANCEL': Llamada.ESTADO_NO_ATENDIDA,
            'CONGESTION': Llamada.ESTADO_FALLIDA, 'CHANUNAVAIL': Llamada.ESTADO_FALLIDA}
_EVENTOS_EN_CURSO = {'START', 'TALK', 'ANSWER', 'ANSWERED', 'RINGING', 'CONNECT'}
_EVENTOS_FIN = {'END', 'HANGUP', 'FINISH', 'FINISHED', 'COMPLETED', 'CDR', 'ENDED', 'NOANSWER', 'NO_ANSWER'}


def _primero(d, *claves):
    for k in claves:
        if k in d and d[k] not in (None, ''):
            return d[k]
    return None


@dataclass
class EventoLlamada:
    call_id: str
    uuid: str
    direccion: str
    status: str
    evento: str
    calling: str
    called: str
    terminal: str
    queue_id: str
    terminales: list
    atendio: list
    custom: str
    grabacion_url: str
    momento: datetime
    bill_seconds: int | None
    grabada: bool
    raw: dict

    @property
    def es_final(self):
        if self.evento in _EVENTOS_FIN:
            return True
        if self.evento in _EVENTOS_EN_CURSO:
            return False  # el trigger de Anura manda: START / TALK nunca son el fin
        if self.status in ('NOANSWER', 'NO_ANSWER', 'BUSY', 'FAILED', 'CANCEL', 'CONGESTION', 'CHANUNAVAIL'):
            return True
        return self.bill_seconds is not None and self.status in ('ANSWER', 'ANSWERED')


def normalizar_evento(payload: dict, forzar_final=False) -> EventoLlamada:
    p = payload or {}
    raw_ts = _primero(p, 'dialTime', 'dial_time', 'startTime', 'timestamp', 'date')
    momento = timezone.now()
    if isinstance(raw_ts, (int, float)) or (isinstance(raw_ts, str) and raw_ts.isdigit()):
        v = float(raw_ts)
        momento = datetime.fromtimestamp(v / 1000 if v > 10 ** 11 else v, tz=dt_tz.utc)
    elif isinstance(raw_ts, str):
        try:
            momento = datetime.fromisoformat(raw_ts.replace('Z', '+00:00'))
            if timezone.is_naive(momento):
                momento = timezone.make_aware(momento)
        except ValueError:
            pass
    bill = _primero(p, 'billSeconds', 'billseconds', 'billsec', 'bill_seconds', 'billsecs')
    try:
        bill = int(float(bill)) if bill is not None else None
    except (TypeError, ValueError):
        bill = None
    grabada = _primero(p, 'wasRecorded', 'wasrecorded', 'recorded', 'was_recorded')
    # Identificadores del agente, en orden de confianza: quién atendió > agente de la cola > cuenta que originó
    atendio = []
    for clave in ('answerExtension', 'answerextension', 'answerTerminal', 'answerterminal', 'queueAgentExtension',
                  'queueagentextension', 'answeredBy'):
        valor = str(p.get(clave) or '').strip()
        if valor and valor not in atendio:
            atendio.append(valor)
    terminales = []
    for clave in ('answerExtension', 'answerextension', 'queueAgentExtension', 'queueagentextension',
                  'answerTerminal', 'answerterminal', 'accountExtension', 'accountextension', 'accountName',
                  'accountname', 'terminal', 'agent', 'extension', 'interno', 'answeredBy'):
        valor = str(p.get(clave) or '').strip()
        if valor and valor not in terminales:
            terminales.append(valor)
    direccion = str(_primero(p, 'direction', 'dir', 'callDirection') or 'IN').upper()
    ev = EventoLlamada(
        call_id=str(_primero(p, 'callId', 'callid', 'call_id', 'cdrId', 'cdrid', 'id') or ''),
        uuid=str(_primero(p, 'uuid', 'callUuid', 'call_uuid') or ''),
        direccion='OUT' if direccion.startswith(('OUT', 'SAL')) else 'IN',
        status=str(_primero(p, 'status', 'disposition', 'result') or '').upper().replace(' ', '').replace('_', ''),
        evento=str(_primero(p, 'event', 'hooktrigger', 'hookTrigger', 'eventType', 'type', 'hook') or '').upper(),
        calling=str(_primero(p, 'calling', 'from', 'ani', 'caller', 'src') or ''),
        called=str(_primero(p, 'called', 'to', 'dnis', 'destination', 'dst') or ''),
        terminal=terminales[0] if terminales else '',
        terminales=terminales,
        atendio=atendio,
        custom=str(_primero(p, 'custom1', 'custom', 'customs') or ''),
        grabacion_url=str(_primero(p, 'recordingUrl', 'audio_file_mp3', 'recording_url') or ''),
        queue_id=str(_primero(p, 'queueId', 'queueid', 'queue_id', 'queue') or ''),
        momento=momento, bill_seconds=bill,
        grabada=str(grabada).lower() in ('true', '1', 'yes', 'si'), raw=p,
    )
    if forzar_final and not ev.es_final:
        ev.evento = 'CDR'
    return ev


# ═══════════════════════════════════════════════════════════════════════════
# Procesamiento de eventos
# ═══════════════════════════════════════════════════════════════════════════

def procesar_evento_llamada(payload: dict, origen='webhook', forzar_final=False):
    ev = normalizar_evento(payload, forzar_final)
    if not ev.call_id and not ev.uuid:
        logger.warning('Evento de Anura sin callId ni uuid: %s', str(payload)[:300])
        return None
    config = ConfigAnura.get()
    if config.solo_eventos_propios and not _es_evento_propio(ev):
        logger.info('Evento de Anura ignorado (no es de un interno/ruta del CRM): callId=%s', ev.call_id)
        return None

    with transaction.atomic():
        llamada = _obtener_llamada(ev, origen)
        if llamada is None:
            return None
        if llamada.procesada:
            return llamada  # idempotencia: el fin de esta llamada ya se aplicó
        _actualizar_campos(llamada, ev)
        if llamada.contacto_id is None:
            _vincular_crm(llamada, ev, config)
        llamada.save()
        _registrar_actividad(llamada)
        final = ev.es_final
        if final:
            Llamada.objects.filter(pk=llamada.pk).update(procesada=True)
            llamada.procesada = True

    _asegurar_duenio(llamada, final)
    invalidar_llamada_activa(llamada.agente_id)
    if final:
        _al_finalizar(llamada, config)
    return llamada


def _es_evento_propio(ev):
    """La llamada salió del CRM, pasa por un interno asociado, por una ruta cargada, o ya la veníamos siguiendo."""
    if ev.custom.startswith('crm-'):
        return True
    if any(usuario_de_identificador(i) for i in ev.terminales):
        return True
    if (ev.queue_id and RutaAnura.objects.filter(tipo=RutaAnura.TIPO_COLA, valor=ev.queue_id).exists()) or \
            (ev.direccion == 'IN' and ev.called and RutaAnura.objects.filter(tipo=RutaAnura.TIPO_DID, valor=ev.called).exists()):
        return True
    return bool(ev.call_id and Llamada.objects.filter(call_id=ev.call_id).exists())


def _asegurar_duenio(llamada, final):
    """Oportunidad sin agente: pasa a quien atendió; si terminó sin que nadie atienda, regla del embudo."""
    from apps.crm import services as crm
    op = llamada.oportunidad
    if op is None:
        return
    op.refresh_from_db()
    if not op.activa or op.agente_id:
        return
    if llamada.agente_id and llamada.estado in (Llamada.ESTADO_EN_CURSO, Llamada.ESTADO_ATENDIDA):
        crm.reasignar(op, llamada.agente, None, nota='atendió la llamada')
    elif final:
        crm.asignar_oportunidad(op)


def _obtener_llamada(ev, origen):
    if ev.custom.startswith('crm-') and ev.custom[4:].isdigit():
        # Llamada originada desde el CRM: Click2Dial devuelve nuestro id en la variable custom1.
        propia = Llamada.objects.select_for_update().filter(pk=int(ev.custom[4:])).first()
        if propia:
            if ev.call_id and not propia.call_id and not Llamada.objects.filter(call_id=ev.call_id).exists():
                propia.call_id = ev.call_id
            return propia
    if ev.call_id:
        existente = Llamada.objects.select_for_update().filter(call_id=ev.call_id).first()
        if existente:
            return existente
    if ev.uuid:
        existente = Llamada.objects.select_for_update().filter(anura_uuid=ev.uuid).first()
        if existente:
            if ev.call_id and not existente.call_id:
                existente.call_id = ev.call_id
            return existente
    if ev.direccion == 'OUT':
        # Llamada originada por click2call/discador cuyo callId todavía no conocíamos
        numero = normalizar_telefono(ev.called)
        usuario_id = next((u for u in map(usuario_de_identificador, ev.terminales) if u), None)
        filtro = Llamada.objects.select_for_update().filter(
            direccion=Llamada.DIR_SALIENTE, call_id__isnull=True, numero=numero,
            inicio_at__gte=timezone.now() - timedelta(minutes=15),
        )
        if usuario_id:
            filtro = filtro.filter(agente_id=usuario_id)
        candidata = filtro.order_by('-inicio_at').first()
        if candidata:
            candidata.call_id = ev.call_id or None
            return candidata
    try:
        with transaction.atomic():
            return Llamada.objects.create(
                call_id=ev.call_id or None, anura_uuid=ev.uuid, direccion=ev.direccion, inicio_at=ev.momento,
                origen_registro=origen, estado=Llamada.ESTADO_SONANDO,
            )
    except IntegrityError:
        return Llamada.objects.select_for_update().filter(call_id=ev.call_id).first()


def _actualizar_campos(llamada, ev):
    externo = ev.calling if ev.direccion == 'IN' else ev.called
    if externo and not llamada.numero:
        llamada.numero_crudo = externo[:60]
        llamada.numero = normalizar_telefono(externo)
    if ev.direccion == 'IN' and ev.called and not llamada.did:
        llamada.did = ev.called[:60]
    llamada.queue_id = llamada.queue_id or ev.queue_id[:60]
    # Quien atendió manda siempre (en una cola puede sonar en varios y atender otro)
    for ident in ev.atendio:
        usuario_id = usuario_de_identificador(ident)
        if usuario_id:
            llamada.agente_id, llamada.interno = usuario_id, ident[:50]
            break
    if llamada.agente_id is None:
        for ident in ev.terminales:
            usuario_id = usuario_de_identificador(ident)
            if usuario_id:
                llamada.agente_id, llamada.interno = usuario_id, ident[:50]
                break
    if ev.terminal and not llamada.interno:
        llamada.interno = ev.terminal[:50]
    if ev.grabacion_url:
        llamada.grabada = True
    llamada.direccion = llamada.direccion or ev.direccion
    eventos = dict(llamada.payload.get('eventos', {})) if isinstance(llamada.payload, dict) else {}
    eventos[ev.evento or 'EVENTO'] = ev.raw
    llamada.payload = {**(llamada.payload if isinstance(llamada.payload, dict) else {}), **ev.raw, 'eventos': eventos}
    llamada.grabada = llamada.grabada or ev.grabada
    if ev.es_final:
        llamada.estado = _FINALES.get(ev.status, Llamada.ESTADO_ATENDIDA if (ev.bill_seconds or 0) > 0
                                      else Llamada.ESTADO_NO_ATENDIDA)
        llamada.duracion_seg = ev.bill_seconds or 0
        llamada.fin_at = timezone.now()
        if llamada.estado == Llamada.ESTADO_ATENDIDA and not llamada.atendida_at:
            llamada.atendida_at = llamada.fin_at - timedelta(seconds=llamada.duracion_seg)
    elif ev.status in ('ANSWER', 'ANSWERED') or ev.evento in ('ANSWER', 'ANSWERED', 'CONNECT', 'TALK'):
        llamada.estado = Llamada.ESTADO_EN_CURSO
        llamada.atendida_at = llamada.atendida_at or timezone.now()
    elif llamada.estado == Llamada.ESTADO_DISCANDO:
        llamada.estado = Llamada.ESTADO_SONANDO


def usuario_de_identificador(identificador):
    """Usuario del CRM al que corresponde un interno o alias informado por Anura (o None)."""
    if not identificador:
        return None
    return InternoAnura.mapa_identificadores().get(str(identificador).strip())


def _ruta_para(llamada):
    for tipo, valor in ((RutaAnura.TIPO_COLA, llamada.queue_id), (RutaAnura.TIPO_DID, llamada.did)):
        if valor:
            ruta = RutaAnura.objects.filter(tipo=tipo, valor=valor).select_related('embudo', 'agente').first()
            if ruta:
                return ruta
    return None


def _vincular_crm(llamada, ev, config):
    """Asocia la llamada al contacto y a su oportunidad; si el número es nuevo, crea la tarjeta."""
    from apps.crm import services as crm
    from apps.crm.models import Oportunidad
    if not llamada.numero:
        return
    contacto = crm.buscar_contacto(llamada.numero)
    ruta = _ruta_para(llamada) if llamada.direccion == Llamada.DIR_ENTRANTE else None
    embudo = ruta.embudo if ruta else None
    op = None
    if contacto:
        op = crm.oportunidad_activa_de(contacto, embudo) or crm.oportunidad_activa_de(contacto)

    if op is None:
        if llamada.direccion == Llamada.DIR_ENTRANTE:
            embudo = embudo or config.embudo_entrantes
            origen = Oportunidad.ORIGEN_LLAMADA_ENTRANTE
            agente = (ruta.agente if ruta and ruta.agente else None) or llamada.agente
        else:
            if not config.crear_tarjeta_salientes:
                embudo = None
            elif embudo is None and llamada.agente_id:
                embudo = llamada.agente.embudos.filter(activo=True).first() or config.embudo_entrantes
            else:
                embudo = embudo or config.embudo_entrantes
            origen = Oportunidad.ORIGEN_LLAMADA_SALIENTE
            agente = llamada.agente
        datos = {'telefono': llamada.numero, 'nombre': f'Llamada {llamada.numero}'}
        if embudo is not None:
            # Entrante sin agente conocido todavía: la tarjeta nace sin dueño; será de quien atienda,
            # o se asigna por la regla del embudo si nadie atiende (ver _asegurar_duenio).
            res = crm.ingresar_prospecto(datos, embudo, origen, fuente=f'Anura {llamada.did or llamada.queue_id}'.strip(),
                                         agente=agente, asignar=llamada.direccion != Llamada.DIR_ENTRANTE)
            contacto, op = res.contacto, (res.oportunidad if res.oportunidad and res.oportunidad.activa else None)
        elif contacto is None:
            contacto, _ = crm.upsert_contacto(datos)

    llamada.contacto = contacto
    llamada.oportunidad = op
    if llamada.agente_id is None and op is not None and op.agente_id and llamada.direccion == Llamada.DIR_SALIENTE:
        llamada.agente_id = op.agente_id


def _texto_llamada(llamada):
    from core.templatetags.crm_extras import duracion
    sentido = 'entrante' if llamada.direccion == Llamada.DIR_ENTRANTE else 'saliente'
    if llamada.viva:
        return f'Llamada {sentido} en curso' + (f' — {llamada.agente.display_name}' if llamada.agente_id else '')
    texto = f'Llamada {sentido}: {llamada.get_estado_display().lower()}'
    if llamada.duracion_seg:
        texto += f' · {duracion(llamada.duracion_seg)}'
    if llamada.agente_id:
        texto += f' · {llamada.agente.display_name}'
    return texto


def _registrar_actividad(llamada):
    from apps.crm.models import Actividad
    if not llamada.contacto_id:
        return
    texto = _texto_llamada(llamada)
    actualizadas = Actividad.objects.filter(llamada=llamada).update(
        texto=texto, oportunidad=llamada.oportunidad, usuario=llamada.agente,
    )
    if not actualizadas:
        Actividad.objects.create(
            contacto_id=llamada.contacto_id, oportunidad=llamada.oportunidad, tipo=Actividad.TIPO_LLAMADA,
            llamada=llamada, usuario=llamada.agente, texto=texto, created_at=llamada.inicio_at,
        )


def _al_finalizar(llamada, config):
    from apps.crm import services as crm
    from apps.users.services import notificar, notificar_varios, supervisores_de
    op = llamada.oportunidad
    if op is not None:
        op.refresh_from_db()
        crm.tocar(op)
        if llamada.direccion == Llamada.DIR_SALIENTE and op.activa:
            resultado = {'atendida': 'contactado', 'ocupado': 'ocupado'}.get(llamada.estado, 'sin_respuesta')
            crm.registrar_intento(op, llamada.agente, canal='llamada', resultado=resultado, crear_actividad=False)
        elif llamada.estado == Llamada.ESTADO_ATENDIDA and op.activa:
            crm.avanzar_desde_inicial(op, llamada.agente)

    if llamada.direccion == Llamada.DIR_ENTRANTE and llamada.estado in (Llamada.ESTADO_NO_ATENDIDA, Llamada.ESTADO_OCUPADO):
        nombre = llamada.contacto.nombre if llamada.contacto_id else llamada.numero
        url = op.get_absolute_url() if op else (llamada.contacto.get_absolute_url() if llamada.contacto_id else '')
        responsable = op.agente if op and op.agente_id else llamada.agente
        if responsable:
            notificar(responsable, 'llamada_perdida', f'Llamada perdida de {nombre}', llamada.numero, url)
        else:
            notificar_varios(supervisores_de(op.embudo if op else None), 'llamada_perdida',
                             f'Llamada perdida sin agente: {nombre}', llamada.numero, url)
        if config.tarea_llamada_perdida and llamada.contacto_id:
            from apps.crm.models import Tarea
            crm.crear_tarea(None, responsable, f'Devolver llamada a {nombre}', timezone.now() + timedelta(minutes=30),
                            oportunidad=op, contacto=llamada.contacto, tipo=Tarea.TIPO_LLAMADA,
                            prioridad=Tarea.PRIORIDAD_ALTA)

    if llamada.campania_contacto_id:
        _actualizar_contacto_campania(llamada)

    if llamada.grabada and config.descargar_grabaciones and not config.modo_demo and \
            (llamada.payload.get('recordingUrl') or llamada.payload.get('audio_file_mp3') or llamada.call_id):
        from .tasks import descargar_grabacion
        Llamada.objects.filter(pk=llamada.pk).update(grabacion_estado=Llamada.GRAB_PENDIENTE)
        transaction.on_commit(lambda: descargar_grabacion.apply_async(args=[llamada.pk], countdown=20))


# ═══════════════════════════════════════════════════════════════════════════
# Click2call
# ═══════════════════════════════════════════════════════════════════════════

def _clave_activa(user_id):
    return f'llamada_activa_{user_id}'


def invalidar_llamada_activa(user_id):
    if user_id:
        cache.delete(_clave_activa(user_id))


def usuario_tiene_interno(user):
    key = f'tiene_interno_{user.pk}'
    val = cache.get(key)
    if val is None:
        val = InternoAnura.objects.filter(usuario=user, activo=True).exists()
        cache.set(key, val, 300)
    return val


def llamada_activa(user):
    return (Llamada.objects.filter(agente=user, estado__in=Llamada.ESTADOS_VIVOS,
                                   inicio_at__gte=timezone.now() - timedelta(hours=3))
            .select_related('contacto', 'oportunidad').order_by('-inicio_at').first())


def estado_llamada_json(llamada):
    if llamada is None:
        return None
    referencia = llamada.atendida_at or llamada.inicio_at
    return {
        'id': llamada.pk, 'estado': llamada.estado, 'estado_display': llamada.get_estado_display(),
        'viva': llamada.viva, 'direccion': llamada.direccion, 'numero': llamada.numero,
        'nombre': llamada.contacto.nombre if llamada.contacto_id else llamada.numero,
        'url': (llamada.oportunidad.get_absolute_url() if llamada.oportunidad_id
                else llamada.contacto.get_absolute_url() if llamada.contacto_id else ''),
        'segundos': int((timezone.now() - referencia).total_seconds()) if llamada.viva else llamada.duracion_seg,
        'atendida': bool(llamada.atendida_at),
        'campania': llamada.campania_contacto.campania.nombre if llamada.campania_contacto_id else '',
        'puede_cortar': ConfigAnura.get().puede_cortar,
        'anura_url': ConfigAnura.get().webphone_url,
    }


def discar(user, numero, oportunidad=None, contacto=None, campania_contacto=None, origen='click2call'):
    config = ConfigAnura.get()
    if not config.operativa:
        raise ErrorTelefonia('La telefonía Anura no está activa: un administrador tiene que activarla en Configuración → Telefonía Anura.')
    interno = InternoAnura.objects.filter(usuario=user, activo=True).first()
    if interno is None:
        raise ErrorTelefonia('Tu usuario no tiene un interno de Anura asignado: cargalo en Usuarios → tu usuario → "Interno de Anura".')
    numero = normalizar_telefono(numero)
    if not numero:
        raise ErrorTelefonia('Número inválido.')
    if llamada_activa(user):
        raise ErrorTelefonia('Ya tenés una llamada en curso.')
    if oportunidad is not None and contacto is None:
        contacto = oportunidad.contacto
    if contacto is not None and oportunidad is None:
        from apps.crm.services import oportunidad_activa_de
        oportunidad = oportunidad_activa_de(contacto)

    llamada = Llamada.objects.create(
        direccion=Llamada.DIR_SALIENTE, estado=Llamada.ESTADO_DISCANDO, numero=numero, numero_crudo=numero,
        interno=interno.interno, agente=user, contacto=contacto, oportunidad=oportunidad,
        campania_contacto=campania_contacto, origen_registro='demo' if config.modo_demo else origen,
    )
    try:
        resp = get_cliente(config).dial(interno.interno, numero, custom=f'crm-{llamada.pk}')
    except ErrorAnura as e:
        llamada.estado, llamada.procesada = Llamada.ESTADO_FALLIDA, True
        llamada.payload = {'error': str(e)}
        llamada.save(update_fields=['estado', 'procesada', 'payload'])
        raise ErrorTelefonia(str(e)) from e
    campos = ['anura_uuid']
    llamada.anura_uuid = resp.get('uuid') or ''
    if resp.get('call_id') and not Llamada.objects.filter(call_id=resp['call_id']).exists():
        llamada.call_id = resp['call_id']
        campos.append('call_id')
    llamada.save(update_fields=campos)
    _registrar_actividad(llamada)
    invalidar_llamada_activa(user.pk)
    return llamada


def colgar(user, llamada):
    if llamada.agente_id != user.pk and not user.tiene_permiso('supervision'):
        raise ErrorTelefonia('No podés cortar una llamada de otro agente.')
    if not llamada.viva:
        return llamada
    config = ConfigAnura.get()
    if not config.puede_cortar:
        raise ErrorTelefonia('Anura no permite cortar por API: cortá desde tu teléfono o softphone.')
    try:
        get_cliente(config).hangup(llamada.interno, llamada.anura_uuid or llamada.call_id or '')
    except ErrorAnura as e:
        raise ErrorTelefonia(str(e)) from e
    return llamada


# ═══════════════════════════════════════════════════════════════════════════
# Discador progresivo
# ═══════════════════════════════════════════════════════════════════════════

def cargar_en_campania(campania, oportunidades, usuario=None):
    """Agrega oportunidades a la campaña (sin duplicar teléfonos, sin 'no contactar')."""
    existentes = set(campania.contactos.values_list('telefono', flat=True))
    nuevos = []
    omitidos = 0
    for op in oportunidades.select_related('contacto'):
        c = op.contacto
        if not c.puede_recibir_mensajes or c.telefono in existentes:
            omitidos += 1
            continue
        existentes.add(c.telefono)
        nuevos.append(CampaniaContacto(campania=campania, contacto=c, oportunidad=op, telefono=c.telefono))
    CampaniaContacto.objects.bulk_create(nuevos, batch_size=500, ignore_conflicts=True)
    from apps.crm.models import Actividad
    Actividad.objects.bulk_create([Actividad(contacto=n.contacto, oportunidad=n.oportunidad, tipo=Actividad.TIPO_SISTEMA,
                                             usuario=usuario, texto=f'Cargado al discador: campaña "{campania}"')
                                   for n in nuevos], batch_size=500)
    return len(nuevos), omitidos


def _numeros_bloqueados(config):
    bloqueados = cache.get('anura_bloqueados')
    if bloqueados is None:
        try:
            crudos = get_cliente(config).numeros_bloqueados()
            bloqueados = {normalizar_telefono(n) for n in crudos if n}
        except ErrorAnura as e:
            logger.warning('No se pudo leer la lista de bloqueados de Anura: %s', e)
            bloqueados = set()
        cache.set('anura_bloqueados', bloqueados, 600)
    return bloqueados


def agentes_libres(campania):
    ahora = timezone.now()
    espera = timedelta(seconds=campania.segundos_entre_llamadas)
    sesiones = (AgenteDiscador.objects.filter(campania=campania, en_pausa=False, agente__is_active=True,
                                              libre_desde__lte=ahora - espera)
                .select_related('agente'))
    ocupados = set(Llamada.objects.filter(estado__in=Llamada.ESTADOS_VIVOS, inicio_at__gte=ahora - timedelta(hours=3))
                   .values_list('agente_id', flat=True))
    return [s for s in sesiones if s.agente_id not in ocupados]


def tomar_siguiente(campania, agente):
    """Reserva el próximo contacto de la campaña. skip_locked: varios workers no toman el mismo."""
    ahora = timezone.now()
    with transaction.atomic():
        from django.db.models import Q
        cc = (CampaniaContacto.objects.select_for_update(skip_locked=True)
              .filter(campania=campania, estado__in=[CampaniaContacto.ESTADO_PENDIENTE, CampaniaContacto.ESTADO_REINTENTAR])
              .filter(Q(proximo_intento_at__isnull=True) | Q(proximo_intento_at__lte=ahora))
              .order_by('-prioridad', 'proximo_intento_at', 'pk').first())
        if cc is None:
            return None
        cc.estado, cc.agente, cc.ultimo_intento_at = CampaniaContacto.ESTADO_EN_CURSO, agente, ahora
        cc.intentos += 1
        cc.save(update_fields=['estado', 'agente', 'ultimo_intento_at', 'intentos'])
        return cc


def discador_tick():
    config = ConfigAnura.get()
    if not config.operativa:
        return 0
    disparadas = 0
    for campania in CampaniaDiscado.objects.filter(estado=CampaniaDiscado.ESTADO_ACTIVA):
        if not campania.en_horario():
            continue
        if not cache.add(f'discador_lock_{campania.pk}', 1, 20):
            continue
        try:
            for sesion in agentes_libres(campania):
                cc = tomar_siguiente(campania, sesion.agente)
                if cc is None:
                    _quizas_finalizar(campania)
                    break
                bloqueados = _numeros_bloqueados(config)
                contacto = cc.contacto
                if cc.telefono in bloqueados or (contacto and not contacto.puede_recibir_mensajes):
                    cc.estado, cc.detalle = CampaniaContacto.ESTADO_DESCARTADO, 'Número bloqueado / no contactar'
                    cc.save(update_fields=['estado', 'detalle'])
                    continue
                try:
                    discar(sesion.agente, cc.telefono, oportunidad=cc.oportunidad, contacto=contacto,
                           campania_contacto=cc, origen='discador')
                    disparadas += 1
                except ErrorTelefonia as e:
                    cc.estado, cc.detalle = CampaniaContacto.ESTADO_REINTENTAR, str(e)[:200]
                    cc.proximo_intento_at = timezone.now() + timedelta(minutes=5)
                    cc.intentos = max(cc.intentos - 1, 0)
                    cc.save(update_fields=['estado', 'detalle', 'proximo_intento_at', 'intentos'])
        finally:
            cache.delete(f'discador_lock_{campania.pk}')
    return disparadas


def _quizas_finalizar(campania):
    vivos = campania.contactos.filter(estado__in=[CampaniaContacto.ESTADO_PENDIENTE, CampaniaContacto.ESTADO_REINTENTAR,
                                                  CampaniaContacto.ESTADO_EN_CURSO]).exists()
    if not vivos:
        CampaniaDiscado.objects.filter(pk=campania.pk).update(estado=CampaniaDiscado.ESTADO_FINALIZADA)
        AgenteDiscador.objects.filter(campania=campania).delete()


def _actualizar_contacto_campania(llamada):
    cc = CampaniaContacto.objects.select_related('campania').filter(pk=llamada.campania_contacto_id).first()
    if cc is None:
        return
    campania = cc.campania
    if llamada.estado == Llamada.ESTADO_ATENDIDA:
        cc.estado, cc.detalle = CampaniaContacto.ESTADO_CONTACTADO, f'Atendió ({llamada.duracion_seg} s)'
        cc.proximo_intento_at = None
    elif cc.intentos >= campania.max_intentos:
        cc.estado, cc.detalle = CampaniaContacto.ESTADO_NO_CONTESTA, f'Sin respuesta tras {cc.intentos} intentos'
    else:
        cc.estado = CampaniaContacto.ESTADO_REINTENTAR
        cc.detalle = llamada.get_estado_display()
        cc.proximo_intento_at = timezone.now() + timedelta(minutes=campania.minutos_entre_intentos)
    cc.save(update_fields=['estado', 'detalle', 'proximo_intento_at'])
    AgenteDiscador.objects.filter(agente_id=llamada.agente_id).update(libre_desde=timezone.now())


def cerrar_llamadas_colgadas():
    """Llamadas que quedaron 'vivas' porque nunca llegó el evento de fin (se cierran por timeout)."""
    limite = timezone.now() - timedelta(hours=2)
    colgadas = Llamada.objects.filter(estado__in=Llamada.ESTADOS_VIVOS, inicio_at__lt=limite)
    n = 0
    for llamada in colgadas:
        if not llamada.call_id and not llamada.anura_uuid:
            llamada.anura_uuid = f'local-{llamada.pk}'
            Llamada.objects.filter(pk=llamada.pk).update(anura_uuid=llamada.anura_uuid)
        procesar_evento_llamada({'callId': llamada.call_id or '', 'uuid': llamada.anura_uuid,
                                 'status': 'FAILED', 'event': 'END', 'billSeconds': 0}, origen='timeout')
        n += 1
    # Contactos de campaña "en curso" sin llamada viva (p.ej. falló el dial sin respuesta)
    CampaniaContacto.objects.filter(estado=CampaniaContacto.ESTADO_EN_CURSO,
                                    ultimo_intento_at__lt=timezone.now() - timedelta(minutes=15)).exclude(
        llamadas__estado__in=Llamada.ESTADOS_VIVOS,
    ).update(estado=CampaniaContacto.ESTADO_REINTENTAR, proximo_intento_at=timezone.now())
    return n



# ═══════════════════════════════════════════════════════════════════════════
# Plantilla del WebHook para cargar en Anura (Configuración → Eventos)
# ═══════════════════════════════════════════════════════════════════════════

PLANTILLA_WEBHOOK = """{
  "callId": "{{ cdrid }}",
  "event": "{{ hooktrigger }}",
  "direction": "{{ direction }}",
  "status": "{{ status }}",
  "calling": "{{ calling }}",
  "callingName": "{{ callingname }}",
  "called": "{{ called }}",
  "dialTime": "{{ dialtime }}",
  "billSeconds": "{{ billseconds }}",
  "wasRecorded": "{{ wasrecorded }}",
  "answerExtension": "{{ answerextension }}",
  "answerTerminal": "{{ answerterminal }}",
  "queueAgentExtension": "{{ queueagentextension }}",
  "accountExtension": "{{ accountextension }}",
  "accountName": "{{ accountname }}",
  "queueId": "{{ queueid }}",
  "queueName": "{{ queuename }}",
  "custom1": "{{ custom1 }}",
  "recordingUrl": "{{ audio_file_mp3 }}"
}"""
