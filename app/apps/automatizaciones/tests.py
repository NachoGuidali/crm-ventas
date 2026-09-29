from datetime import timedelta
from unittest import mock

from django.core.management import call_command
from django.utils import timezone

from core.testing import TestCase
from apps.crm import services as crm
from apps.crm.models import Embudo, Etapa
from apps.users.models import User
from apps.whatsapp.models import LineaWhatsApp, Mensaje

from .models import AccionEtapa, EjecucionAccion
from .services import ejecutar, proxima_apertura, revisar_inactividad


class AutomatizacionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_inicial', '--sin-admin', verbosity=0)
        cls.embudo = Embudo.objects.get()
        cls.agente = User.objects.create_user('ana', password='x', first_name='Ana')
        cls.embudo.agentes.set([cls.agente])
        cls.linea = LineaWhatsApp.objects.create(nombre='Demo', proveedor='demo', min_segundos_entre_envios=0)
        Embudo.objects.filter(pk=cls.embudo.pk).update(linea_whatsapp=cls.linea)
        AccionEtapa.objects.update(activa=True, solo_en_horario=False)

    def ingresar(self, tel='1155556666'):
        with self.captureOnCommitCallbacks(execute=True):
            return crm.ingresar_prospecto({'telefono': tel, 'nombre': 'María López'}, Embudo.objects.get(), 'importacion')

    @mock.patch('apps.whatsapp.proveedores.demo.ProveedorDemo._quizas_responder')
    def test_bienvenida_al_ingresar(self, _):
        with self.captureOnCommitCallbacks(execute=True):
            res = self.ingresar()
        ej = EjecucionAccion.objects.get(oportunidad=res.oportunidad)
        self.assertEqual(ej.estado, 'ejecutada', ej.detalle)
        msg = Mensaje.objects.get()
        self.assertIn('María', msg.contenido)
        self.assertIn('Ana', msg.contenido)
        self.assertTrue(msg.automatico)

    @mock.patch('apps.whatsapp.proveedores.demo.ProveedorDemo._quizas_responder')
    def test_no_contactar_y_pausa_bloquean(self, _):
        res = crm.ingresar_prospecto({'telefono': '1100001111', 'nombre': 'X'}, self.embudo, 'manual',
                                     disparar_automatizaciones=False)
        op = res.oportunidad
        res.contacto.no_contactar = True
        res.contacto.save()
        accion = AccionEtapa.objects.get(etapa=self.embudo.etapa_inicial)
        ej = EjecucionAccion.objects.create(accion=accion, oportunidad=op, programada_para=timezone.now())
        ejecutar(ej.pk)
        ej.refresh_from_db()
        self.assertEqual(ej.estado, 'omitida')
        self.assertEqual(Mensaje.objects.count(), 0)

    def test_demora_y_si_ya_avanzo_se_omite(self):
        etapa = Etapa.objects.get(nombre='Propuesta enviada')
        AccionEtapa.objects.filter(etapa=etapa).update(demora_minutos=60 * 24)
        op = self.ingresar().oportunidad
        with self.captureOnCommitCallbacks(execute=True):
            crm.mover_etapa(op, etapa, self.agente)
        ej = EjecucionAccion.objects.get(accion__etapa=etapa)
        self.assertEqual(ej.estado, 'programada')
        self.assertGreater(ej.programada_para, timezone.now() + timedelta(hours=23))
        crm.mover_etapa(op, Etapa.objects.get(nombre='Negociación'), self.agente)
        EjecucionAccion.objects.filter(pk=ej.pk).update(programada_para=timezone.now())
        ejecutar(ej.pk)
        ej.refresh_from_db()
        self.assertEqual(ej.estado, 'omitida')

    def test_idempotente(self):
        op = self.ingresar().oportunidad
        from .services import programar_acciones_de_etapa
        hist = op.historial.first()
        self.assertEqual(programar_acciones_de_etapa(op.pk, hist.pk), 0)

    def test_proxima_apertura(self):
        e = Embudo(respetar_horario=True, horario_desde='09:00', horario_hasta='18:00', dias_habiles=[0, 1, 2, 3, 4])
        sabado = timezone.make_aware(timezone.datetime(2026, 9, 26, 22, 0))
        lunes = proxima_apertura(e, sabado)
        self.assertEqual((timezone.localtime(lunes).weekday(), timezone.localtime(lunes).hour), (0, 9))

    def test_recordatorio_por_inactividad_una_sola_vez(self):
        op = crm.ingresar_prospecto({'telefono': '1122223333', 'nombre': 'Y'}, self.embudo, 'manual',
                                    disparar_automatizaciones=False).oportunidad
        op.tareas.all().delete()
        type(op).objects.filter(pk=op.pk).update(ultima_actividad_at=timezone.now() - timedelta(days=5),
                                                  etapa_desde=timezone.now() - timedelta(days=9))
        r1, r2 = revisar_inactividad(), revisar_inactividad()
        self.assertEqual((r1['recordatorios'], r1['estancados']), (1, 1))
        self.assertEqual((r2['recordatorios'], r2['estancados']), (0, 0))
        self.assertEqual(op.tareas.count(), 1)
