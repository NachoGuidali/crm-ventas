from django.db import models


class AccionEtapa(models.Model):
    """
    'Cuando una oportunidad entra a la etapa X → hacer Y' (opcionalmente con demora).
    Cubre los mensajes automáticos del circuito: bienvenida, seguimiento, confirmación y despedida.
    """
    TIPO_WHATSAPP = 'whatsapp'
    TIPO_EMAIL = 'email'
    TIPO_TAREA = 'tarea'
    TIPO_NOTIF_AGENTE = 'notif_agente'
    TIPO_NOTIF_SUPERVISORES = 'notif_supervisores'
    TIPO_CHOICES = [
        (TIPO_WHATSAPP, 'Enviar WhatsApp al prospecto'),
        (TIPO_EMAIL, 'Enviar email al prospecto'),
        (TIPO_TAREA, 'Crear tarea para el agente'),
        (TIPO_NOTIF_AGENTE, 'Notificar al agente'),
        (TIPO_NOTIF_SUPERVISORES, 'Notificar a supervisión'),
    ]
    ICONOS = {TIPO_WHATSAPP: 'whatsapp', TIPO_EMAIL: 'envelope', TIPO_TAREA: 'calendar-plus',
              TIPO_NOTIF_AGENTE: 'bell', TIPO_NOTIF_SUPERVISORES: 'megaphone'}

    embudo = models.ForeignKey('crm.Embudo', on_delete=models.CASCADE, related_name='acciones')
    etapa = models.ForeignKey('crm.Etapa', on_delete=models.CASCADE, related_name='acciones',
                              verbose_name='Cuando entra a la etapa')
    nombre = models.CharField(max_length=120)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES, default=TIPO_WHATSAPP)
    activa = models.BooleanField(default=True)
    demora_minutos = models.PositiveIntegerField(default=0, verbose_name='Demora (minutos)',
                                                 help_text='0 = inmediato. Ej: 1440 = al día siguiente.')
    solo_si_sigue_en_etapa = models.BooleanField(
        default=True, verbose_name='Solo si sigue en esa etapa al momento de ejecutar',
        help_text='Evita mandar un seguimiento si el prospecto ya avanzó o se cerró.',
    )
    solo_en_horario = models.BooleanField(
        default=True, verbose_name='Ejecutar solo en el horario de atención del embudo',
        help_text='Si cae fuera de horario se posterga hasta la próxima apertura (no se escribe de madrugada).',
    )

    # WhatsApp
    plantilla = models.ForeignKey('whatsapp.Plantilla', null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name='+', help_text='Obligatoria para líneas oficiales (Meta/Twilio).')
    texto = models.TextField(blank=True, help_text='Mensaje libre (líneas Evolution / email). '
                                                   'Variables: {nombre} {primer_nombre} {agente} {embudo} {etapa}')
    linea = models.ForeignKey('whatsapp.LineaWhatsApp', null=True, blank=True, on_delete=models.SET_NULL,
                              related_name='+', help_text='Vacío = la del chat existente o la del embudo.')
    # Email
    email_asunto = models.CharField(max_length=200, blank=True)
    # Tarea
    tarea_titulo = models.CharField(max_length=200, blank=True)
    tarea_vence_horas = models.PositiveSmallIntegerField(default=24)

    orden = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['embudo', 'etapa__orden', 'orden', 'pk']
        verbose_name = 'Automatización'
        verbose_name_plural = 'Automatizaciones'

    def __str__(self):
        return self.nombre

    @property
    def icono(self):
        return self.ICONOS.get(self.tipo, 'lightning')

    def demora_display(self):
        m = self.demora_minutos
        if not m:
            return 'Inmediato'
        if m % 1440 == 0:
            return f'{m // 1440} día{"s" if m // 1440 > 1 else ""} después'
        if m % 60 == 0:
            return f'{m // 60} h después'
        return f'{m} min después'


class EjecucionAccion(models.Model):
    ESTADO_PROGRAMADA = 'programada'
    ESTADO_EJECUTADA = 'ejecutada'
    ESTADO_OMITIDA = 'omitida'
    ESTADO_ERROR = 'error'
    ESTADO_CHOICES = [(ESTADO_PROGRAMADA, 'Programada'), (ESTADO_EJECUTADA, 'Ejecutada'),
                      (ESTADO_OMITIDA, 'Omitida'), (ESTADO_ERROR, 'Error')]

    accion = models.ForeignKey(AccionEtapa, on_delete=models.CASCADE, related_name='ejecuciones')
    oportunidad = models.ForeignKey('crm.Oportunidad', on_delete=models.CASCADE, related_name='ejecuciones')
    historial = models.ForeignKey('crm.HistorialEtapa', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    estado = models.CharField(max_length=12, choices=ESTADO_CHOICES, default=ESTADO_PROGRAMADA, db_index=True)
    programada_para = models.DateTimeField()
    ejecutada_at = models.DateTimeField(null=True, blank=True)
    detalle = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            # Idempotencia: una acción se programa una sola vez por cada entrada a la etapa.
            models.UniqueConstraint(fields=['accion', 'historial'], name='ejecucion_unica_por_entrada'),
        ]
        indexes = [models.Index(fields=['estado', 'programada_para'])]
