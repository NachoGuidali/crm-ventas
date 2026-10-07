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
    TIPO_EMBUDO = 'embudo'
    TIPO_ETAPA = 'etapa'
    TIPO_SMS = 'sms'
    DISP_ENTRADA = 'entrada'
    DISP_SIN_RESPUESTA = 'sin_respuesta'
    DISP_RESPUESTA = 'respuesta'
    DISP_SIN_ACTIVIDAD = 'sin_actividad'
    DISP_DESPUES_DE = 'despues_de'
    DISPARADORES = [
        (DISP_ENTRADA, 'Al entrar a la etapa'),
        (DISP_SIN_RESPUESTA, 'Si el cliente no responde (en el tiempo de demora desde que entró a la etapa)'),
        (DISP_RESPUESTA, 'Cuando el cliente responde (WhatsApp o llamada atendida) estando en la etapa'),
        (DISP_SIN_ACTIVIDAD, 'Si no hay ninguna actividad durante el tiempo de demora'),
        (DISP_DESPUES_DE, 'Después de que se ejecutó otra automatización (secuencia)'),
    ]
    TIPO_CHOICES = [
        (TIPO_WHATSAPP, 'Enviar WhatsApp al prospecto'),
        (TIPO_EMAIL, 'Enviar email al prospecto'),
        (TIPO_SMS, 'Enviar SMS al prospecto'),
        (TIPO_TAREA, 'Crear tarea para el agente'),
        (TIPO_NOTIF_AGENTE, 'Notificar al agente'),
        (TIPO_NOTIF_SUPERVISORES, 'Notificar a supervisión'),
        (TIPO_EMBUDO, 'Pasar a otro embudo'),
        (TIPO_ETAPA, 'Mover a otra etapa / cerrar'),
    ]
    MODO_CREAR = 'crear'
    MODO_MOVER = 'mover'
    MODO_VOLVER = 'volver'
    MODO_CHOICES = [
        (MODO_CREAR, 'Crear una oportunidad nueva en otro embudo (la actual queda como está)'),
        (MODO_MOVER, 'Mover esta misma tarjeta a otro embudo'),
        (MODO_VOLVER, 'Volver al embudo del que vino'),
    ]
    VOLVER_MISMA = 'misma'
    VOLVER_SIGUIENTE = 'siguiente'
    VOLVER_CHOICES = [(VOLVER_MISMA, 'A la etapa donde estaba'), (VOLVER_SIGUIENTE, 'A la etapa siguiente')]
    ASIGNAR_CHOICES = [('mismo', 'Al mismo agente'), ('embudo', 'Según la regla del embudo de destino'),
                       ('usuario', 'A un usuario fijo')]
    ICONOS = {TIPO_WHATSAPP: 'whatsapp', TIPO_EMAIL: 'envelope', TIPO_TAREA: 'calendar-plus',
              TIPO_NOTIF_AGENTE: 'bell', TIPO_NOTIF_SUPERVISORES: 'megaphone', TIPO_EMBUDO: 'signpost-split', TIPO_ETAPA: 'arrow-right-circle', TIPO_SMS: 'phone'}

    embudo = models.ForeignKey('crm.Embudo', on_delete=models.CASCADE, related_name='acciones')
    etapa = models.ForeignKey('crm.Etapa', on_delete=models.CASCADE, related_name='acciones',
                              verbose_name='Cuando entra a la etapa')
    nombre = models.CharField(max_length=120)
    disparador = models.CharField(max_length=15, choices=DISPARADORES, default=DISP_ENTRADA, verbose_name='Cuándo')
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
    accion_previa = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='siguientes',
                                      verbose_name='Después de la automatización')
    # Email
    plantilla_email = models.ForeignKey('PlantillaEmail', null=True, blank=True, on_delete=models.SET_NULL,
                                        related_name='+', verbose_name='Plantilla de email')
    email_asunto = models.CharField(max_length=200, blank=True)
    # Tarea
    tarea_titulo = models.CharField(max_length=200, blank=True)
    tarea_vence_horas = models.PositiveSmallIntegerField(default=24)

    # Mover a otra etapa / cerrar (mismo embudo)
    mover_a = models.ForeignKey('crm.Etapa', null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
                                verbose_name='Mover a la etapa')
    tipificacion = models.ForeignKey('crm.Tipificacion', null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name='+', verbose_name='Tipificación (si cierra)')
    # Pasar a otro embudo
    modo_embudo = models.CharField(max_length=10, choices=MODO_CHOICES, default=MODO_CREAR, verbose_name='Cómo')
    embudo_destino = models.ForeignKey('crm.Embudo', null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name='+', verbose_name='Embudo de destino')
    etapa_destino = models.ForeignKey('crm.Etapa', null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
                                      verbose_name='Etapa de destino', help_text='Vacío = la primera etapa.')
    volver_a = models.CharField(max_length=10, choices=VOLVER_CHOICES, default=VOLVER_MISMA,
                                verbose_name='Al volver, ir')
    asignar_destino = models.CharField(max_length=10, choices=ASIGNAR_CHOICES, default='mismo',
                                       verbose_name='Asignar')
    usuario_destino = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL,
                                        related_name='+', verbose_name='Usuario')

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

    def resumen_embudo(self):
        if self.tipo == self.TIPO_ETAPA:
            return f'Pasa a {self.mover_a}' + (f' ({self.tipificacion})' if self.tipificacion_id else '')
        if self.tipo != self.TIPO_EMBUDO:
            return ''
        if self.modo_embudo == self.MODO_VOLVER:
            return f'Vuelve al embudo de origen ({self.get_volver_a_display().lower()})'
        destino = f'{self.embudo_destino or "?"}' + (f' · {self.etapa_destino}' if self.etapa_destino_id else '')
        return ('Nueva oportunidad en ' if self.modo_embudo == self.MODO_CREAR else 'Pasa a ') + destino

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


class EmailEnviado(models.Model):
    """Email automático enviado, con seguimiento de aperturas (píxel) y clics (links redirigidos)."""
    accion = models.ForeignKey(AccionEtapa, null=True, blank=True, on_delete=models.SET_NULL, related_name='emails')
    oportunidad = models.ForeignKey('crm.Oportunidad', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    contacto = models.ForeignKey('crm.Contacto', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    enviado_por = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    difusion = models.ForeignKey('Difusion', null=True, blank=True, on_delete=models.SET_NULL, related_name='emails')
    plantilla = models.ForeignKey('PlantillaEmail', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    baja_at = models.DateTimeField(null=True, blank=True)
    para = models.EmailField()
    asunto = models.CharField(max_length=200)
    token = models.CharField(max_length=40, unique=True)
    enviado_at = models.DateTimeField(auto_now_add=True, db_index=True)
    abierto_at = models.DateTimeField(null=True, blank=True)
    aperturas = models.PositiveIntegerField(default=0)
    clic_at = models.DateTimeField(null=True, blank=True)
    clics = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['-enviado_at']



class PlantillaEmail(models.Model):
    """Email reutilizable: en la ficha, en automatizaciones y en difusiones. Variables: {nombre} {primer_nombre}…"""
    nombre = models.CharField(max_length=120, unique=True)
    asunto = models.CharField(max_length=200)
    cuerpo = models.TextField(help_text='Texto del email. Los links se miden solos. Variables: {nombre} {primer_nombre} '
                                        '{agente} {embudo} {etapa} y los campos personalizados.')
    activa = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class ConfigEmail(models.Model):
    """Servidor de correo (SMTP) configurable desde la pantalla. Si está inactivo se usa el del archivo .env."""
    activo = models.BooleanField(default=False, verbose_name='Usar esta configuración')
    host = models.CharField(max_length=200, blank=True, verbose_name='Servidor SMTP')
    puerto = models.PositiveIntegerField(default=587)
    usuario = models.CharField(max_length=200, blank=True)
    password = models.CharField(max_length=300, blank=True, verbose_name='Contraseña')
    seguridad = models.CharField(max_length=5, default='tls', choices=[('tls', 'STARTTLS (587)'), ('ssl', 'SSL (465)'),
                                                                      ('', 'Ninguna')])
    remitente = models.CharField(max_length=200, blank=True, verbose_name='Remitente',
                                 help_text='Ej: "Roisa Ventas <ventas@roisa.com.ar>". Tiene que estar autorizado en el servidor.')
    por_minuto = models.PositiveSmallIntegerField(default=30, verbose_name='Emails por minuto en difusiones',
                                                  help_text='Gmail: no más de ~500 por día. Brevo / SES / Mailgun: más.')

    class Meta:
        verbose_name = 'Configuración de email'

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Difusion(models.Model):
    """Envío masivo a un grupo de leads (elegidos con los filtros de la lista), por email o WhatsApp."""
    CANAL_EMAIL = 'email'
    CANAL_WHATSAPP = 'whatsapp'
    CANALES = [(CANAL_EMAIL, 'Email'), (CANAL_WHATSAPP, 'WhatsApp')]
    BORRADOR, PROGRAMADA, ENVIANDO, FINALIZADA, CANCELADA = 'borrador', 'programada', 'enviando', 'finalizada', 'cancelada'
    ESTADOS = [(BORRADOR, 'Borrador'), (PROGRAMADA, 'Programada'), (ENVIANDO, 'Enviando'), (FINALIZADA, 'Finalizada'),
               (CANCELADA, 'Cancelada')]

    nombre = models.CharField(max_length=150)
    canal = models.CharField(max_length=10, choices=CANALES, default=CANAL_EMAIL)
    plantilla_email = models.ForeignKey(PlantillaEmail, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    plantilla_wa = models.ForeignKey('whatsapp.Plantilla', null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name='+', verbose_name='Plantilla de WhatsApp')
    texto_wa = models.TextField(blank=True, verbose_name='Texto (solo líneas no oficiales, sin plantilla)')
    linea = models.ForeignKey('whatsapp.LineaWhatsApp', null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
                              help_text='Vacío = la del chat existente o la del embudo de cada lead.')
    estado = models.CharField(max_length=12, choices=ESTADOS, default=BORRADOR, db_index=True)
    programada_para = models.DateTimeField(null=True, blank=True)
    descripcion_filtro = models.CharField(max_length=500, blank=True)
    creada_por = models.ForeignKey('users.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    iniciada_at = models.DateTimeField(null=True, blank=True)
    finalizada_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.nombre


class DifusionDestinatario(models.Model):
    PENDIENTE, ENVIADO, OMITIDO, ERROR = 'pendiente', 'enviado', 'omitido', 'error'
    ESTADOS = [(PENDIENTE, 'Pendiente'), (ENVIADO, 'Enviado'), (OMITIDO, 'Omitido'), (ERROR, 'Error')]
    difusion = models.ForeignKey(Difusion, on_delete=models.CASCADE, related_name='destinatarios')
    contacto = models.ForeignKey('crm.Contacto', on_delete=models.CASCADE, related_name='+')
    oportunidad = models.ForeignKey('crm.Oportunidad', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    estado = models.CharField(max_length=10, choices=ESTADOS, default=PENDIENTE, db_index=True)
    detalle = models.CharField(max_length=300, blank=True)
    mensaje = models.ForeignKey('whatsapp.Mensaje', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    email = models.ForeignKey(EmailEnviado, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    enviado_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['difusion', 'contacto'], name='difusion_contacto_unico')]
        indexes = [models.Index(fields=['difusion', 'estado'])]
