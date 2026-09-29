from datetime import timedelta
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from core.testing import TestCase
from django.utils import timezone

from core.phone import normalizar_telefono
from apps.users.models import User

from . import services as crm
from .models import Actividad, Contacto, Embudo, Etapa, ImportacionLote, Oportunidad, Tarea, Tipificacion


class TelefonoTests(TestCase):
    def test_todos_los_formatos_terminan_igual(self):
        esperado = '+5491123456789'
        for f in ['+54 9 11 2345-6789', '5491123456789', '+541123456789', '011 15 2345-6789', '01123456789',
                  '1123456789', '11 15 2345 6789', '0054 9 11 2345 6789', 'whatsapp:+5491123456789',
                  '5491123456789@s.whatsapp.net', '541123456789']:
            self.assertEqual(normalizar_telefono(f), esperado, f)

    def test_interior_y_extranjero(self):
        self.assertEqual(normalizar_telefono('0221 15 123-4567'), '+5492211234567')
        self.assertEqual(normalizar_telefono('+1 415 523 8886'), '+14155238886')
        self.assertEqual(normalizar_telefono(''), '')
        self.assertEqual(normalizar_telefono('abc'), '')


class BaseCRM(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_inicial', '--sin-admin', verbosity=0)
        cls.embudo = Embudo.objects.get()
        cls.a1 = User.objects.create_user('ana', password='x', first_name='Ana')
        cls.a2 = User.objects.create_user('beto', password='x', first_name='Beto')
        cls.sup = User.objects.create_user('sup', password='x', rol=User.ROL_SUPERVISOR)
        cls.embudo.agentes.set([cls.a1, cls.a2])
        cls.etapas = list(cls.embudo.etapas.order_by('orden'))

    def ingresar(self, tel='1123456789', nombre='Juan Pérez', **kw):
        return crm.ingresar_prospecto({'telefono': tel, 'nombre': nombre, **kw.pop('datos', {})}, self.embudo,
                                      Oportunidad.ORIGEN_MANUAL, **kw)


class IngresoTests(BaseCRM):
    def test_no_duplica_contacto_ni_oportunidad(self):
        r1 = self.ingresar('011 15 2345-6789')
        r2 = self.ingresar('+5491123456789', nombre='Otro nombre')
        self.assertTrue(r1.oportunidad_nueva)
        self.assertFalse(r2.oportunidad_nueva)
        self.assertEqual(r2.motivo, 'ya_activa')
        self.assertEqual(Contacto.objects.count(), 1)
        self.assertEqual(Oportunidad.objects.count(), 1)
        self.assertTrue(Actividad.objects.filter(tipo=Actividad.TIPO_REINGRESO).exists())

    def test_completa_datos_faltantes_sin_pisar(self):
        self.ingresar(datos={'email': ''})
        self.ingresar(nombre='Otro', datos={'email': 'juan@x.com'})
        c = Contacto.objects.get()
        self.assertEqual(c.nombre, 'Juan Pérez')
        self.assertEqual(c.email, 'juan@x.com')

    def test_ya_cliente_no_abre_otra(self):
        r = self.ingresar()
        venta = Tipificacion.objects.get(nombre='Venta directa')
        crm.mover_etapa(r.oportunidad, self.embudo.etapa_ganado, self.a1, tipificacion=venta)
        r2 = self.ingresar()
        self.assertFalse(r2.oportunidad_nueva)
        self.assertEqual(r2.motivo, 'ya_cliente')

    def test_perdido_reingresa_con_oportunidad_nueva(self):
        r = self.ingresar()
        tip = Tipificacion.objects.get(nombre='Cuota fuera de presupuesto')
        crm.mover_etapa(r.oportunidad, self.embudo.etapa_perdido, self.a1, tipificacion=tip)
        r2 = self.ingresar()
        self.assertTrue(r2.oportunidad_nueva)
        self.assertEqual(Oportunidad.objects.filter(contacto=r.contacto).count(), 2)


class AsignacionTests(BaseCRM):
    def test_round_robin_alterna(self):
        agentes = [self.ingresar(tel=f'11{n:08d}').oportunidad.agente for n in range(4)]
        self.assertEqual(agentes, [self.a1, self.a2, self.a1, self.a2])
        self.assertEqual(Tarea.objects.filter(estado='pendiente').count(), 4)

    def test_saltea_no_disponibles(self):
        User.objects.filter(pk=self.a1.pk).update(disponible=False)
        agentes = {self.ingresar(tel=f'11{n:08d}').oportunidad.agente for n in range(3)}
        self.assertEqual(agentes, {self.a2})

    def test_fuera_de_horario_encola(self):
        Embudo.objects.filter(pk=self.embudo.pk).update(respetar_horario=True, dias_habiles=[])
        e = Embudo.objects.get(pk=self.embudo.pk)
        e.dias_habiles = [(timezone.localtime().weekday() + 1) % 7]
        e.save()
        r = crm.ingresar_prospecto({'telefono': '1122223333', 'nombre': 'X'}, e, Oportunidad.ORIGEN_API)
        self.assertIsNone(r.oportunidad.agente)
        self.assertTrue(r.oportunidad.pendiente_asignacion)

    def test_menor_carga(self):
        Embudo.objects.filter(pk=self.embudo.pk).update(modo_asignacion=Embudo.ASIG_MENOR_CARGA)
        self.embudo.refresh_from_db()
        for n in range(2):
            crm.ingresar_prospecto({'telefono': f'11{n:08d}', 'nombre': 'x'}, self.embudo, 'manual', agente=self.a1)
        r = self.ingresar(tel='1199999999')
        self.assertEqual(r.oportunidad.agente, self.a2)


class MovimientoTests(BaseCRM):
    def test_cierre_exige_tipificacion_correcta(self):
        op = self.ingresar().oportunidad
        with self.assertRaises(crm.ErrorNegocio):
            crm.mover_etapa(op, self.embudo.etapa_ganado, self.a1)
        with self.assertRaises(crm.ErrorNegocio):
            crm.mover_etapa(op, self.embudo.etapa_ganado, self.a1,
                            tipificacion=Tipificacion.objects.get(nombre='Sin respuesta'))
        with self.assertRaises(crm.ErrorNegocio):  # requiere nota
            crm.mover_etapa(op, self.embudo.etapa_ganado, self.a1,
                            tipificacion=Tipificacion.objects.get(nombre='Venta con condición especial'))
        crm.mover_etapa(op, self.embudo.etapa_ganado, self.a1,
                        tipificacion=Tipificacion.objects.get(nombre='Venta con condición especial'), nota='10% off')
        op.refresh_from_db()
        self.assertEqual(op.estado, Oportunidad.ESTADO_GANADA)
        self.assertFalse(op.tareas.filter(estado='pendiente').exists())

    def test_cerrada_no_se_reabre_sin_permiso(self):
        op = self.ingresar().oportunidad
        crm.mover_etapa(op, self.embudo.etapa_perdido, self.a1,
                        tipificacion=Tipificacion.objects.get(nombre='Sin respuesta'))
        with self.assertRaises(crm.ErrorNegocio):
            crm.mover_etapa(op, self.etapas[1], self.a1)
        crm.mover_etapa(op, self.etapas[1], self.sup, puede_reabrir=True)
        op.refresh_from_db()
        self.assertEqual(op.estado, Oportunidad.ESTADO_ABIERTA)
        self.assertIsNone(op.tipificacion)

    def test_no_contactar_marca_el_contacto(self):
        op = self.ingresar().oportunidad
        crm.mover_etapa(op, self.embudo.etapa_perdido, self.a1,
                        tipificacion=Tipificacion.objects.get(nombre='Solicita no ser contactado'))
        op.contacto.refresh_from_db()
        self.assertTrue(op.contacto.no_contactar)
        r = self.ingresar()
        self.assertEqual(r.motivo, 'no_contactar')

    def test_postergar_pausa_y_agenda(self):
        op = self.ingresar().oportunidad
        fecha = timezone.now() + timedelta(days=30)
        crm.postergar(op, self.a1, fecha, Tipificacion.objects.get(nombre='Pide ser recontactado más adelante'))
        op.refresh_from_db()
        self.assertEqual(op.estado, Oportunidad.ESTADO_PAUSADA)
        self.assertTrue(op.tareas.filter(tipo=Tarea.TIPO_REACTIVACION, vence_at=fecha).exists())
        Oportunidad.objects.filter(pk=op.pk).update(proximo_contacto_at=timezone.now() - timedelta(minutes=1))
        from .tasks import reactivar_pausadas
        self.assertEqual(reactivar_pausadas(), 1)
        op.refresh_from_db()
        self.assertEqual(op.estado, Oportunidad.ESTADO_ABIERTA)

    def test_intento_avanza_de_nuevo_a_en_gestion(self):
        op = self.ingresar().oportunidad
        self.assertEqual(op.etapa.nombre, 'Prospecto (Nuevo)')
        crm.registrar_intento(op, self.a1)
        op.refresh_from_db()
        self.assertEqual(op.etapa.nombre, 'En gestión')
        self.assertEqual(op.intentos_contacto, 1)

    def test_contacto_efectivo(self):
        op = self.ingresar().oportunidad
        crm.mover_etapa(op, self.etapas[2], self.a1)
        op.refresh_from_db()
        self.assertIsNotNone(op.contacto_efectivo_at)


class ImportacionTests(BaseCRM):
    def test_importa_xlsx_dedup_y_errores(self):
        from openpyxl import Workbook
        from .importacion import leer_archivo, procesar_lote, sugerir_mapeo
        wb = Workbook()
        ws = wb.active
        ws.append(['Nombre y apellido', 'Teléfono', 'Email', 'Fecha de atención'])
        ws.append(['MARIA GOMEZ', '11 15 4444-5555', 'maria@x.com', '15/08/2026'])
        ws.append(['Maria Gomez', '+5491144445555', '', ''])       # duplicado dentro del archivo
        ws.append(['Sin datos', '', '', ''])                        # error: sin contacto
        ws.append(['Pedro', '0221 15 123 4567', '', 'fecha mala'])  # error: fecha
        buf = BytesIO()
        wb.save(buf)
        archivo = SimpleUploadedFile('base.xlsx', buf.getvalue())
        columnas, filas = leer_archivo(BytesIO(buf.getvalue()), 'base.xlsx')
        mapeo = sugerir_mapeo(columnas)
        self.assertEqual(mapeo['Teléfono'], 'telefono')
        self.assertEqual(mapeo['Fecha de atención'], 'fecha_atencion')
        lote = ImportacionLote.objects.create(archivo=archivo, nombre_archivo='base.xlsx', embudo=self.embudo,
                                              mapeo=mapeo, fuente='Base septiembre')
        procesar_lote(lote.pk)
        lote.refresh_from_db()
        self.assertEqual((lote.total, lote.creados, lote.ya_existentes, lote.errores), (4, 1, 1, 2))
        c = Contacto.objects.get(telefono='+5491144445555')
        self.assertEqual(c.nombre, 'Maria Gomez')
        self.assertEqual(str(c.fecha_atencion), '2026-08-15')


class CargaMasivaTests(BaseCRM):
    def test_mil_ingresos_sin_duplicar(self):
        """1000 datos (con repetidos en distintos formatos) → sin duplicados y reparto parejo."""
        for n in range(1000):
            num = n % 800  # 200 repetidos
            tel = f'11{num:08d}' if n < 800 else f'+54 9 11 {num:08d}'
            crm.ingresar_prospecto({'telefono': tel, 'nombre': f'P{num}'}, self.embudo, 'importacion',
                                   disparar_automatizaciones=False)
        self.assertEqual(Contacto.objects.count(), 800)
        self.assertEqual(Oportunidad.objects.count(), 800)
        cargas = sorted(Oportunidad.objects.filter(agente=a).count() for a in (self.a1, self.a2))
        self.assertEqual(cargas, [400, 400])


class CamposPersonalizadosTests(BaseCRM):
    def setUp(self):
        super().setUp()
        from .models import CampoPersonalizado as CP
        self.plan = CP.objects.create(nombre='Plan de interés', tipo=CP.TIPO_LISTA, opciones=['Flex 1', 'Flex 2'],
                                      requerido=True, filtrable=True, mostrar_en_tarjeta=True)
        self.hijos = CP.objects.create(nombre='Cantidad de hijos', tipo=CP.TIPO_NUMERO, mostrar_en_lista=True)
        self.obra = CP.objects.create(nombre='Tiene obra social', tipo=CP.TIPO_SINO)
        self.admin = User.objects.create_superuser('root', 'r@x.com', 'x', rol=User.ROL_ADMIN)
        self.client.force_login(self.admin)

    def test_slug_y_alta_manual_con_tipos(self):
        self.assertEqual(self.plan.slug, 'plan_de_interes')
        base = {'nombre': 'Ana', 'telefono': '1155550000', 'embudo': self.embudo.pk}
        r = self.client.post('/oportunidades/nueva/', base)
        self.assertEqual(r.status_code, 200)  # falta el obligatorio
        self.assertFalse(Contacto.objects.exists())
        r = self.client.post('/oportunidades/nueva/', {**base, 'cp__plan_de_interes': 'Flex 2', 'cp__cantidad_de_hijos': '3',
                                                        'cp__tiene_obra_social': 'false'})
        self.assertEqual(r.status_code, 302)
        extra = Contacto.objects.get().datos_extra
        self.assertEqual((extra['plan_de_interes'], extra['cantidad_de_hijos'], extra['tiene_obra_social']), ('Flex 2', 3, False))

    def test_edicion_en_ficha_y_render(self):
        op = self.ingresar().oportunidad
        r = self.client.post(f'/contactos/{op.contacto_id}/editar/', {'nombre': 'Ana', 'telefono': op.contacto.telefono,
                                                                     'cp__plan_de_interes': 'Flex 1'})
        self.assertEqual(r.status_code, 302)
        self.assertContains(self.client.get(op.get_absolute_url()), 'Flex 1')
        self.assertContains(self.client.get('/tablero/'), 'Flex 1')
        self.assertContains(self.client.get('/oportunidades/?cp_plan_de_interes=Flex+1'), 'Ana')
        self.assertNotContains(self.client.get('/oportunidades/?cp_plan_de_interes=Flex+2'), op.get_absolute_url())

    def test_importacion_y_api(self):
        from .importacion import fila_a_datos, sugerir_mapeo
        mapeo = sugerir_mapeo(['Teléfono', 'Plan de interés', 'Cantidad de hijos'])
        self.assertEqual(mapeo['Plan de interés'], 'cp:plan_de_interes')
        cps = {c.slug: c for c in (self.plan, self.hijos)}
        datos, _ = fila_a_datos({'Teléfono': '1155551111', 'Plan de interés': 'flex 2', 'Cantidad de hijos': '2'}, mapeo, cps)
        self.assertEqual(datos['datos_extra'], {'plan_de_interes': 'Flex 2', 'cantidad_de_hijos': 2})
        with self.assertRaises(ValueError):
            fila_a_datos({'Plan de interés': 'Otro'}, mapeo, cps)
        import json
        from apps.integraciones.models import ApiKey
        _, clave = ApiKey.generar(nombre='x')
        r = self.client.post('/api/v1/leads/', json.dumps({'telefono': '1155552222', 'plan_de_interes': 'Plan X'}),
                             content_type='application/json', HTTP_X_API_KEY=clave)
        self.assertEqual(r.status_code, 400)
        r = self.client.post('/api/v1/leads/', json.dumps({'telefono': '1155552222', 'plan_de_interes': 'Flex 1',
                                                            'utm': 'fb'}), content_type='application/json', HTTP_X_API_KEY=clave)
        self.assertEqual(Contacto.objects.get(telefono='+5491155552222').datos_extra, {'plan_de_interes': 'Flex 1', 'utm': 'fb'})

    def test_variable_en_mensajes(self):
        from apps.whatsapp.models import reemplazar_variables_texto
        c = Contacto.objects.create(nombre='Ana', telefono='1155553333', datos_extra={'plan_de_interes': 'Flex 1'})
        self.assertEqual(reemplazar_variables_texto('Tu plan: {plan_de_interes}', c), 'Tu plan: Flex 1')

    def test_pantalla_config(self):
        self.assertContains(self.client.get('/config/campos/'), 'plan_de_interes')
        r = self.client.post('/config/campos/', {'nombre': 'Nivel', 'tipo': 'lista', 'opciones_texto': 'A\nB\nA', 'orden': 0,
                                                 'activo': 'on'})
        self.assertEqual(r.status_code, 302)
        from .models import CampoPersonalizado
        self.assertEqual(CampoPersonalizado.objects.get(nombre='Nivel').opciones, ['A', 'B'])
        r = self.client.post('/config/campos/', {'nombre': 'Email', 'tipo': 'texto', 'orden': 0})
        self.assertEqual(r.status_code, 200)  # nombre reservado


class CamposObligatoriosPorEtapaTests(BaseCRM):
    def setUp(self):
        super().setUp()
        from .models import CampoPersonalizado as CP
        self.plan = CP.objects.create(nombre='Plan', tipo=CP.TIPO_LISTA, opciones=['Flex 1', 'Flex 2'])
        self.propuesta = Etapa.objects.get(embudo=self.embudo, nombre='Propuesta enviada')
        self.propuesta.campos_requeridos = ['email', 'cp:plan']
        self.propuesta.save()
        self.negociacion = Etapa.objects.get(embudo=self.embudo, nombre='Negociación')
        self.op = self.ingresar().oportunidad
        self.client.force_login(self.a1)

    def test_bloquea_etapa_y_posteriores_pero_no_no_venta(self):
        for etapa in (self.propuesta, self.negociacion):
            with self.assertRaises(crm.FaltanCampos) as ctx:
                crm.mover_etapa(self.op, etapa, self.a1)
            self.assertEqual([f['clave'] for f in ctx.exception.faltan], ['email', 'cp:plan'])
        with self.assertRaises(crm.FaltanCampos):
            crm.mover_etapa(self.op, self.embudo.etapa_ganado, self.a1, tipificacion=Tipificacion.objects.get(nombre='Venta directa'))
        crm.mover_etapa(self.op, self.etapas[1], self.a1)  # etapa anterior: sin requisitos
        crm.mover_etapa(self.op, self.embudo.etapa_perdido, self.a1, tipificacion=Tipificacion.objects.get(nombre='Sin respuesta'))

    def test_flujo_web_completar_y_mover(self):
        url = f'/oportunidades/{self.op.pk}/'
        r = self.client.post(url + 'mover/', {'etapa': self.negociacion.pk})
        self.assertEqual(r.status_code, 400)
        self.assertEqual([f['nombre'] for f in r.json()['faltan']], ['Email', 'Plan'])
        self.assertEqual(r.json()['faltan'][1]['opciones'], ['Flex 1', 'Flex 2'])
        r = self.client.post(url + 'completar/', {'email': 'no-es-mail', 'cp:plan': 'Flex 1'})
        self.assertEqual(r.status_code, 400)
        r = self.client.post(url + 'completar/', {'email': 'ana@x.com', 'cp:plan': 'Flex 1'})
        self.assertEqual(r.status_code, 200, r.content)
        r = self.client.post(url + 'mover/', {'etapa': self.negociacion.pk})
        self.assertEqual(r.status_code, 200, r.content)
        self.op.contacto.refresh_from_db()
        self.assertEqual((self.op.contacto.email, self.op.contacto.datos_extra['plan']), ('ana@x.com', 'Flex 1'))

    def test_valor_exigido_en_venta_se_puede_dar_al_cerrar(self):
        g = self.embudo.etapa_ganado
        g.campos_requeridos = ['valor']
        g.save()
        self.propuesta.campos_requeridos = []
        self.propuesta.save()
        venta = Tipificacion.objects.get(nombre='Venta directa')
        with self.assertRaises(crm.FaltanCampos):
            crm.mover_etapa(self.op, g, self.a1, tipificacion=venta)
        crm.mover_etapa(self.op, g, self.a1, tipificacion=venta, valor=21900)
        self.op.refresh_from_db()
        self.assertEqual(self.op.estado, 'ganada')

    def test_avance_automatico_no_saltea_requisitos(self):
        gestion = self.etapas[1]
        gestion.campos_requeridos = ['dni']
        gestion.save()
        crm.registrar_intento(self.op, self.a1)
        self.op.refresh_from_db()
        self.assertEqual(self.op.etapa, self.etapas[0])
        self.assertEqual(self.op.intentos_contacto, 1)

    def test_configuracion_desde_etapa_y_desde_campo(self):
        admin = User.objects.create_superuser('root', 'r@x.com', 'x', rol=User.ROL_ADMIN)
        self.client.force_login(admin)
        e = self.etapas[1]
        self.client.post(f'/config/embudos/{self.embudo.pk}/etapas/{e.pk}/guardar/', {
            'nombre': e.nombre, 'color': e.color, 'tipo': e.tipo, 'requeridos_enviados': '1',
            'campos_requeridos': ['dni', 'cp:plan', 'inventado']})
        e.refresh_from_db()
        self.assertEqual(e.campos_requeridos, ['dni', 'cp:plan'])
        self.client.post(f'/config/campos/{self.plan.pk}/', {'nombre': 'Plan', 'tipo': 'lista', 'opciones_texto': 'Flex 1\nFlex 2',
                                                             'orden': 0, 'activo': 'on', 'etapas_requeridas': [self.negociacion.pk]})
        e.refresh_from_db(); self.propuesta.refresh_from_db(); self.negociacion.refresh_from_db()
        self.assertEqual((e.campos_requeridos, self.propuesta.campos_requeridos, self.negociacion.campos_requeridos),
                         (['dni'], ['email'], ['cp:plan']))
        self.assertContains(self.client.get('/config/campos/'), 'obligatorio desde Negociación')
        self.assertContains(self.client.get(f'/config/embudos/{self.embudo.pk}/'), 'Obligatorio desde acá')


class ListadoYAccionesMasivasTests(BaseCRM):
    """Caso pedido por Roisa: filtrar por etapa + rango de fechas, seleccionar todas y reasignar / cambiar estado."""

    def setUp(self):
        super().setUp()
        from datetime import datetime
        self.sup = User.objects.create_user('super', password='x', rol=User.ROL_SUPERVISOR)
        self.c3 = User.objects.create_user('caro', password='x', first_name='Caro')
        self.gestion, self.efectivo = self.etapas[1], self.etapas[2]
        self.ops = []
        for n in range(12):
            op = self.ingresar(tel=f'11{n:08d}', nombre=f'P{n}').oportunidad
            dia = timezone.make_aware(datetime(2026, 9, 1 + n, 10, 0))
            Oportunidad.objects.filter(pk=op.pk).update(etapa=self.gestion if n < 8 else self.efectivo, etapa_desde=dia)
            self.ops.append(op)
        self.client.force_login(self.sup)

    def listar(self, **params):
        r = self.client.get('/oportunidades/', params)
        self.assertEqual(r.status_code, 200)
        return r.context['page'].paginator.count

    def test_filtros_multiples_y_fecha_elegida(self):
        self.assertEqual(self.listar(embudo=self.embudo.pk, etapa=self.gestion.pk), 8)
        self.assertEqual(self.listar(embudo=self.embudo.pk, etapa=[self.gestion.pk, self.efectivo.pk]), 12)
        # Entraron a "En gestión" entre el 3 y el 6 de septiembre
        self.assertEqual(self.listar(embudo=self.embudo.pk, etapa=self.gestion.pk, fecha='etapa',
                                     desde='2026-09-03', hasta='2026-09-06'), 4)
        self.assertEqual(self.listar(embudo=self.embudo.pk, agente=[self.a1.pk, 'ninguno']), 6)
        r = self.client.get('/oportunidades/', {'embudo': self.embudo.pk, 'etapa': self.gestion.pk, 'fecha': 'etapa',
                                                'desde': '2026-09-03'})
        self.assertContains(r, 'Entró a la etapa actual: 2026-09-03')
        self.assertContains(r, 'Etapa: En gestión')

    def masiva(self, accion, filtros, **datos):
        from urllib.parse import urlencode
        return self.client.post('/oportunidades/masivas/', {
            'accion': accion, 'todos_filtrados': '1', 'filtros_qs': urlencode(filtros, doseq=True), **datos})

    def test_reasignar_todas_las_filtradas(self):
        filtros = {'embudo': self.embudo.pk, 'etapa': self.gestion.pk, 'fecha': 'etapa', 'desde': '2026-09-03',
                   'hasta': '2026-09-06'}
        r = self.masiva('reasignar', filtros, destino_agente=self.c3.pk, nota='vacaciones de Ana')
        self.assertEqual(r.status_code, 302)
        caro = Oportunidad.objects.filter(agente=self.c3)
        self.assertEqual(sorted(o.contacto.nombre for o in caro), ['P2', 'P3', 'P4', 'P5'])
        # Las tareas pendientes pasan al nuevo agente
        self.assertFalse(Tarea.objects.filter(oportunidad__in=caro, estado='pendiente').exclude(asignado_a=self.c3).exists())

    def test_repartir_en_partes_iguales(self):
        self.masiva('repartir', {'embudo': self.embudo.pk, 'etapa': self.gestion.pk},
                    destino_agentes=[self.a1.pk, self.a2.pk, self.c3.pk])
        cargas = sorted(Oportunidad.objects.filter(etapa=self.gestion, agente=a).count() for a in (self.a1, self.a2, self.c3))
        self.assertEqual(cargas, [2, 3, 3])

    def test_cerrar_y_postergar_en_lote(self):
        sin_resp = Tipificacion.objects.get(nombre='Sin respuesta')
        self.masiva('cerrar', {'embudo': self.embudo.pk, 'etapa': self.efectivo.pk}, tipificacion=sin_resp.pk)
        self.assertEqual(Oportunidad.objects.filter(estado='perdida', tipificacion=sin_resp).count(), 4)
        post = Tipificacion.objects.get(nombre='Pide ser recontactado más adelante')
        r = self.masiva('cerrar', {'embudo': self.embudo.pk, 'etapa': self.gestion.pk}, tipificacion=post.pk)
        self.assertEqual(Oportunidad.objects.filter(estado='pausada').count(), 0)  # falta la fecha → no aplica
        self.masiva('cerrar', {'embudo': self.embudo.pk, 'etapa': self.gestion.pk}, tipificacion=post.pk,
                    fecha='2026-12-01T10:00')
        self.assertEqual(Oportunidad.objects.filter(estado='pausada').count(), 8)

    def test_mover_con_datos_obligatorios_informa_los_que_no(self):
        propuesta = Etapa.objects.get(embudo=self.embudo, nombre='Propuesta enviada')
        propuesta.campos_requeridos = ['email']
        propuesta.save()
        Contacto.objects.filter(nombre__in=['P0', 'P1']).update(email='x@x.com')
        r = self.masiva('mover', {'embudo': self.embudo.pk, 'etapa': self.gestion.pk}, destino_etapa=propuesta.pk)
        self.assertEqual(Oportunidad.objects.filter(etapa=propuesta).count(), 2)
        msgs = [str(m) for m in r.wsgi_request._messages]
        self.assertTrue(any('falta completar: Email' in m for m in msgs))

    def test_agente_no_puede_reasignar_y_lote_grande_va_a_segundo_plano(self):
        self.client.force_login(self.a1)
        r = self.masiva('reasignar', {'embudo': self.embudo.pk}, destino_agente=self.a2.pk)
        self.assertEqual(r.status_code, 403)
        from unittest import mock
        from . import masivas
        self.client.force_login(self.sup)
        with mock.patch.object(masivas, 'LIMITE_SINCRONICO', 5):
            self.masiva('reasignar', {'embudo': self.embudo.pk}, destino_agente=self.c3.pk)  # 12 > 5 → tarea (eager)
        self.assertEqual(Oportunidad.objects.filter(agente=self.c3).count(), 12)
        from apps.users.models import NotificacionInterna
        self.assertTrue(NotificacionInterna.objects.filter(destinatario=self.sup, titulo__startswith='Reasignar: 12').exists())
