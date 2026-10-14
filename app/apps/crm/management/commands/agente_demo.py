"""
Prepara la prueba con una vendedora: usuario "Agente Demo" con ~10 prospectos propios en distintas situaciones,
regla para que los leads de la landing de prueba le lleguen a ella y una campaña de discador.

Las vendedoras solo ven sus propios prospectos, así que los datos demo del resto del CRM no le aparecen.
Se puede volver a correr para dejar la prueba como nueva (borra y recrea solo lo que crea este comando).

    python manage.py agente_demo --telefonos "11 2345 6789, 11 3456 7890"
    python manage.py agente_demo --borrar
"""
import secrets
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

FUENTE = 'Prueba Agente Demo'
ORIGEN_LANDING = 'Landing Prueba'
USUARIO = 'agente.demo'

# nombre, etapa (nombre o None = inicial), qué tiene de particular
PROSPECTOS = [
    ('Lucía Fernández', None, 'nuevo'),
    ('Martín Gómez', None, 'nuevo'),
    ('Sofía Ramírez', None, 'prioridad'),
    ('Diego Torres', 'En gestión', 'tarea_vencida'),
    ('Carla Benítez', 'En gestión', 'reingreso'),
    ('Javier Sosa', 'Contacto efectivo', 'nota'),
    ('Valentina Ruiz', 'Contacto efectivo', 'tarea_hoy'),
    ('Nicolás Herrera', 'Propuesta enviada', ''),
    ('Florencia Díaz', 'Negociación', 'tarea_hoy'),
    ('Pablo Medina', 'Propuesta enviada', 'sin_actividad'),
]


class Command(BaseCommand):
    help = 'Crea el usuario "Agente Demo" con ~10 prospectos de prueba (para probar el CRM con una vendedora).'

    def add_arguments(self, parser):
        parser.add_argument('--telefonos', default='',
                            help='Celulares reales (separados por coma) para los primeros prospectos: así las '
                                 'llamadas y WhatsApp llegan a alguien. El resto usa números ficticios.')
        parser.add_argument('--password', default='', help='Contraseña (por defecto se genera una).')
        parser.add_argument('--interno', default='', help='Interno de Anura a asignarle (opcional).')
        parser.add_argument('--borrar', action='store_true', help='Solo borra los datos de la prueba.')

    def handle(self, *args, **o):
        from apps.users.models import User
        with transaction.atomic():
            self._borrar()
            if o['borrar']:
                User.objects.filter(username=USUARIO).update(is_active=False)
                self.stdout.write(self.style.SUCCESS('Datos de la prueba borrados y usuario desactivado.'))
                return
            self._crear(o)

    def _borrar(self):
        from apps.crm.models import Contacto, Oportunidad
        from apps.telefonia.models import CampaniaDiscado
        from django.db.models import Q
        ops = Oportunidad.objects.filter(Q(fuente=FUENTE) | Q(origen_pauta=ORIGEN_LANDING))
        contactos = set(ops.values_list('contacto_id', flat=True))
        ops.delete()
        # Contactos que quedaron sin oportunidades (los que tienen otras, por ejemplo números reales, no se tocan)
        Contacto.objects.filter(pk__in=contactos, oportunidades__isnull=True).delete()
        CampaniaDiscado.objects.filter(nombre=FUENTE).delete()

    def _crear(self, o):
        from apps.crm import services as crm
        from apps.crm.models import Embudo, Etapa, Oportunidad, ReglaAsignacion, Tarea
        from apps.telefonia.models import CampaniaDiscado, CampaniaContacto, InternoAnura
        from apps.users.models import User
        from core.phone import normalizar_telefono

        embudo = Embudo.objects.filter(activo=True).order_by('orden', 'pk').first()
        if embudo is None:
            self.stderr.write('No hay embudos activos.')
            return
        password = o['password'] or f'Demo-{secrets.token_hex(3)}'
        user, _ = User.objects.get_or_create(username=USUARIO)
        user.first_name, user.last_name, user.email = 'Agente', 'Demo', ''
        user.rol, user.rol_custom, user.is_active, user.disponible = User.ROL_AGENTE, None, True, True
        user.debe_cambiar_password = False
        user.set_password(password)
        user.save()
        embudo.agentes.add(user)
        if o['interno']:
            InternoAnura.objects.update_or_create(usuario=user, defaults={'interno': o['interno'], 'activo': True})

        # Los leads de la landing de prueba (?origen=Landing Prueba) le llegan solo a ella
        regla, _ = ReglaAsignacion.objects.update_or_create(
            embudo=embudo, nombre=f'{FUENTE}: landing', defaults={
                'orden': 0, 'activa': True, 'canales': [], 'textos_origen': [ORIGEN_LANDING],
                'accion': ReglaAsignacion.ACCION_AGENTES, 'asignar_entre': Embudo.ENTRE_TODOS,
                'si_no_hay': ReglaAsignacion.SI_NO_HAY_TODOS})
        regla.agentes.set([user])

        reales = [t for t in (normalizar_telefono(x) for x in o['telefonos'].split(',')) if t]
        ahora = timezone.now()
        creadas = []
        for i, (nombre, etapa_nombre, extra) in enumerate(PROSPECTOS):
            tel = reales[i] if i < len(reales) else f'+549115000{i:04d}'
            res = crm.ingresar_prospecto({'nombre': nombre, 'telefono': tel}, embudo, Oportunidad.ORIGEN_WEB,
                                         fuente=FUENTE, agente=user, disparar_automatizaciones=False)
            op = res.oportunidad
            if op is None:
                continue
            if not res.oportunidad_nueva:  # número real que ya tenía una oportunidad abierta: pasa a la prueba
                crm.reasignar(op, user, None, nota='prueba con vendedora')
                Oportunidad.objects.filter(pk=op.pk).update(fuente=FUENTE)
            etapa = Etapa.objects.filter(embudo=embudo, nombre=etapa_nombre).first() if etapa_nombre else None
            if etapa is not None:
                hace = ahora - timedelta(days=2 + i % 3)
                Oportunidad.objects.filter(pk=op.pk).update(etapa=etapa, etapa_desde=hace, intentos_contacto=1,
                                                            primer_contacto_at=hace, ultima_actividad_at=hace)
            op.refresh_from_db()
            if extra == 'prioridad':
                crm.marcar_prioridad(op, 'Volvió a dejar sus datos en la web')
            elif extra == 'reingreso':
                crm._registrar_reingreso(op, op.contacto, 'Reingresó por Formulario web (Landing Meta). No se duplicó: '
                                                          'ya tenía una oportunidad en curso.', None)
            elif extra == 'tarea_vencida':
                crm.crear_tarea(user, user, f'Llamar a {nombre}: quedó en pensarlo', ahora - timedelta(hours=20),
                                oportunidad=op, tipo=Tarea.TIPO_LLAMADA)
            elif extra == 'tarea_hoy':
                crm.crear_tarea(user, user, f'Seguimiento de la propuesta a {nombre}', ahora + timedelta(hours=3),
                                oportunidad=op, tipo=Tarea.TIPO_SEGUIMIENTO)
            elif extra == 'nota':
                crm.agregar_nota(op, user, 'Interesado en el plan familiar (2 adultos y 1 hijo). Pidió precio por WhatsApp.')
            elif extra == 'sin_actividad':
                Oportunidad.objects.filter(pk=op.pk).update(ultima_actividad_at=ahora - timedelta(days=5))
            creadas.append(op)

        # Discador: solo con los números reales (así las llamadas le llegan a alguien)
        campania = CampaniaDiscado.objects.create(
            nombre=FUENTE, embudo=embudo, estado=CampaniaDiscado.ESTADO_BORRADOR, max_intentos=2,
            minutos_entre_intentos=5, hora_desde='00:00', hora_hasta='23:59', segundos_entre_llamadas=10)
        campania.agentes.set([user])
        con_tel_real = [op for op in creadas if op.contacto.telefono in reales]
        if con_tel_real:
            from apps.telefonia.services import cargar_en_campania
            cargar_en_campania(campania, Oportunidad.objects.filter(pk__in=[o.pk for o in con_tel_real]))

        self.stdout.write(self.style.SUCCESS(
            f'\nListo. Usuario: {USUARIO}  ·  Contraseña: {password}\n'
            f'Prospectos de prueba: {len(creadas)} (embudo {embudo}){" · con números reales: " + str(len(con_tel_real)) if reales else ""}\n'
            f'Landing para la prueba: /demo/formulario/?origen={ORIGEN_LANDING.replace(" ", "%20")}  (requiere DEMO_LANDING=1)\n'
            f'Campaña de discador "{FUENTE}" en borrador: activala desde Discador cuando llegue el momento.\n'
            f'Para borrar todo al terminar: python manage.py agente_demo --borrar'))
