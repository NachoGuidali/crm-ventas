from datetime import timedelta

from django.core.management import call_command
from django.utils import timezone

from core.testing import TestCase
from apps.crm import services as crm
from apps.crm.models import Embudo, Oportunidad, Tipificacion
from apps.users.models import User

from . import analisis


class ReportesComercialesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_inicial', '--sin-admin', verbosity=0)
        cls.embudo = Embudo.objects.get()
        cls.ana = User.objects.create_user('ana', password='x', first_name='Ana')
        cls.beto = User.objects.create_user('beto', password='x', first_name='Beto')
        cls.jefa = User.objects.create_user('jefa', password='x', rol=User.ROL_ADMIN)
        cls.embudo.agentes.set([cls.ana, cls.beto])

    def rango(self):
        return timezone.now() - timedelta(days=1), timezone.now() + timedelta(minutes=1)

    def lead(self, tel):
        return crm.ingresar_prospecto({'telefono': tel, 'nombre': 'X'}, self.embudo, 'web').oportunidad

    def test_leads_unicos_y_contador_de_ingresos(self):
        a = self.lead('1160000001')
        self.lead('1160000002')
        self.lead('1160000001')  # reingreso
        self.lead('1160000001')
        a.refresh_from_db()
        self.assertEqual(a.ingresos, 3)
        u = analisis.leads_unicos(self.embudo, *self.rango())
        self.assertEqual((u['unicos'], u['nuevos'], u['reingresos'], u['eventos_reingreso']), (2, 2, 0, 2))
        self.client.force_login(self.jefa)
        r = self.client.get('/oportunidades/', {'ingresos_min': 2})
        self.assertEqual(r.context['page'].paginator.count, 1)
        self.assertContains(self.client.get(a.get_absolute_url()), '3 veces')

    def test_conversion_por_etapa_y_pipeline(self):
        etapas = list(self.embudo.etapas.filter(tipo='normal').order_by('orden'))
        ops = [self.lead(f'11600001{i:02d}') for i in range(4)]
        crm.mover_etapa(ops[0], etapas[2], self.jefa)
        crm.mover_etapa(ops[0], etapas[1], self.jefa)  # volvió atrás: igual "llegó" a la 3
        crm.mover_etapa(ops[1], etapas[1], self.jefa)
        tip = Tipificacion.objects.filter(resultado=Tipificacion.RESULTADO_VENTA).first()
        crm.mover_etapa(ops[2], self.embudo.etapa_ganado, self.jefa, tipificacion=tip, valor=100)
        conv = analisis.embudo_conversion(self.embudo, *self.rango())
        self.assertEqual([c['n'] for c in conv[:3]], [4, 3, 2])
        self.assertEqual((conv[-1]['nombre'], conv[-1]['n']), ('Venta', 1))
        self.assertEqual(conv[1]['pct_anterior'], 75.0)
        pipe = analisis.matriz_pipeline(self.embudo)
        self.assertEqual(pipe['total'], 3)
        self.assertEqual(sum(f['total'] for f in pipe['filas']), 3)

    def test_actividad_por_vendedora(self):
        from apps.telefonia.models import Llamada
        from apps.whatsapp.models import Conversacion, LineaWhatsApp, Mensaje, Plantilla
        op = self.lead('1160000300')
        linea = LineaWhatsApp.objects.create(nombre='L', proveedor='demo')
        conv = Conversacion.objects.create(linea=linea, telefono=op.contacto.telefono, contacto=op.contacto, agente=self.ana)
        p = Plantilla.objects.first()
        Mensaje.objects.create(conversacion=conv, direccion='out', contenido='hola', enviado_por=self.ana)
        Mensaje.objects.create(conversacion=conv, direccion='out', contenido='plantilla', enviado_por=self.ana, plantilla=p)
        Mensaje.objects.create(conversacion=conv, direccion='out', contenido='auto', automatico=True)
        Llamada.objects.create(direccion='OUT', estado='atendida', agente=self.ana, duracion_seg=120)
        crm.agregar_nota(op, self.ana, 'nota')
        fila = next(f for f in analisis.actividad_vendedoras(*self.rango()) if f['u'] == self.ana)
        self.assertEqual((fila['mensajes'], fila['plantillas'], fila['automaticos'], fila['llamadas'], fila['minutos'],
                          fila['promedio_seg'], fila['notas']), (2, 1, 1, 1, 2, 120, 1))
        self.client.force_login(self.jefa)
        r = self.client.get('/reportes/', {'embudo': self.embudo.pk})
        self.assertContains(r, 'Actividad por vendedora')
        self.assertContains(r, 'Conversión por etapa')
        r = self.client.get('/reportes/actividad.csv')
        self.assertIn('Ana', r.content.decode())

    def test_mis_numeros_para_la_vendedora(self):
        self.client.force_login(self.ana)
        r = self.client.get('/reportes/mis-numeros/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['agente'], self.ana)
        r = self.client.get('/reportes/mis-numeros/', {'agente': self.beto.pk})  # sin permiso: ve lo suyo
        self.assertEqual(r.context['agente'], self.ana)
        self.client.force_login(self.jefa)
        self.assertEqual(self.client.get('/reportes/mis-numeros/', {'agente': self.beto.pk}).context['agente'], self.beto)
