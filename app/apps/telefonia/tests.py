import json
from unittest import mock

from django.core.management import call_command
from django.test import Client

from core.testing import TestCase
from apps.crm.models import Actividad, Contacto, Embudo, Oportunidad, Tarea
from apps.crm.services import ingresar_prospecto
from apps.users.models import NotificacionInterna, User

from .models import AgenteDiscador, CampaniaContacto, CampaniaDiscado, ConfigAnura, InternoAnura, Llamada, RutaAnura
from .services import discador_tick, procesar_evento_llamada


class AnuraBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_inicial', '--sin-admin', verbosity=0)
        cls.embudo = Embudo.objects.get()
        cls.agente = User.objects.create_user('ana', password='x', first_name='Ana')
        cls.embudo.agentes.set([cls.agente])
        InternoAnura.objects.create(usuario=cls.agente, interno='201')
        ConfigAnura.objects.filter(pk=1).update(activo=True, modo_demo=True)

    def webhook(self, payload):
        config = ConfigAnura.objects.get(pk=1)
        return self.client.post(f'/api/integrations/anura/webhook?token={config.webhook_token}', json.dumps(payload),
                                content_type='application/json')


class EntrantesTests(AnuraBase):
    def test_llamada_perdida_de_numero_nuevo_crea_tarjeta_tarea_y_aviso(self):
        r = self.webhook({'callId': '393608182', 'direction': 'IN', 'status': 'NOANSWER', 'calling': '01155554444',
                          'called': '9009', 'dialTime': 1565967840545, 'billSeconds': 0, 'wasRecorded': False})
        self.assertEqual(r.status_code, 200)
        c = Contacto.objects.get(telefono='+5491155554444')
        op = c.oportunidades.get()
        self.assertEqual((op.origen, op.agente), (Oportunidad.ORIGEN_LLAMADA_ENTRANTE, self.agente))
        self.assertTrue(Tarea.objects.filter(oportunidad=op, titulo__startswith='Devolver llamada').exists())
        self.assertTrue(NotificacionInterna.objects.filter(destinatario=self.agente, tipo='llamada_perdida').exists())
        self.assertEqual(Llamada.objects.get().estado, Llamada.ESTADO_NO_ATENDIDA)

    def test_idempotencia_por_call_id(self):
        ev = {'callId': 'X1', 'direction': 'IN', 'status': 'ANSWER', 'calling': '1155554444', 'billSeconds': 30}
        for _ in range(3):
            self.webhook(ev)
        self.assertEqual(Llamada.objects.count(), 1)
        self.assertEqual(Actividad.objects.filter(tipo='llamada').count(), 1)

    def test_eventos_inicio_y_fin_de_la_misma_llamada(self):
        self.webhook({'callId': 'X2', 'direction': 'IN', 'event': 'START', 'calling': '1155554444'})
        ll = Llamada.objects.get()
        self.assertTrue(ll.viva)
        self.webhook({'callId': 'X2', 'direction': 'IN', 'event': 'ANSWER', 'status': 'ANSWER', 'calling': '1155554444',
                      'terminal': '201'})
        self.webhook({'callId': 'X2', 'direction': 'IN', 'event': 'END', 'status': 'ANSWER', 'billSeconds': 95,
                      'calling': '1155554444'})
        ll.refresh_from_db()
        self.assertEqual((ll.estado, ll.duracion_seg, ll.agente), ('atendida', 95, self.agente))

    def test_contacto_existente_se_enlaza_sin_duplicar(self):
        op = ingresar_prospecto({'telefono': '+54 9 11 5555-4444', 'nombre': 'Juan'}, self.embudo, 'manual').oportunidad
        self.webhook({'callId': 'X3', 'direction': 'IN', 'status': 'ANSWER', 'calling': '541155554444', 'billSeconds': 10})
        self.assertEqual(Oportunidad.objects.count(), 1)
        self.assertEqual(Llamada.objects.get().oportunidad, op)

    def test_ruta_por_did(self):
        otro = Embudo.objects.create(nombre='Otro')
        otro.etapas.create(nombre='Nuevo', orden=1)
        RutaAnura.objects.create(tipo='did', valor='4800', embudo=otro, agente=self.agente)
        self.webhook({'callId': 'X4', 'direction': 'IN', 'status': 'NOANSWER', 'calling': '1166667777', 'called': '4800'})
        self.assertEqual(Oportunidad.objects.get().embudo, otro)

    def test_token_invalido(self):
        r = self.client.post('/api/integrations/anura/webhook?token=malo', '{}', content_type='application/json')
        self.assertEqual(r.status_code, 403)

    def test_saliente_desde_softphone_a_numero_nuevo_crea_tarjeta(self):
        procesar_evento_llamada({'callId': 'S1', 'direction': 'OUT', 'status': 'ANSWER', 'called': '1177778888',
                                 'terminal': '201', 'billSeconds': 40})
        op = Oportunidad.objects.get()
        self.assertEqual((op.origen, op.agente), (Oportunidad.ORIGEN_LLAMADA_SALIENTE, self.agente))
        self.assertEqual(op.intentos_contacto, 1)


class Click2CallTests(AnuraBase):
    def test_dial_y_webhook_se_unifican(self):
        op = ingresar_prospecto({'telefono': '1155554444', 'nombre': 'Juan'}, self.embudo, 'manual').oportunidad
        cl = Client()
        cl.force_login(self.agente)
        with mock.patch('apps.telefonia.client.ClienteAnuraDemo.dial', return_value={'call_id': '', 'uuid': 'u-1', 'raw': {}}):
            r = cl.post('/api/telephony/dial', json.dumps({'oportunidadId': op.pk}), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['llamada']['estado'], 'discando')
        r = cl.post('/api/telephony/dial', json.dumps({'oportunidadId': op.pk}), content_type='application/json')
        self.assertEqual(r.status_code, 400)  # ya tiene una llamada en curso
        # Llega el CallHook con el callId real: se asocia a la misma llamada (sin duplicar)
        procesar_evento_llamada({'callId': '777', 'direction': 'OUT', 'status': 'ANSWER', 'called': '1155554444',
                                 'terminal': '201', 'billSeconds': 120})
        self.assertEqual(Llamada.objects.count(), 1)
        ll = Llamada.objects.get()
        self.assertEqual((ll.call_id, ll.estado, ll.oportunidad), ('777', 'atendida', op))
        op.refresh_from_db()
        self.assertEqual(op.intentos_contacto, 1)
        self.assertEqual(op.etapa.nombre, 'En gestión')
        self.assertIsNone(cl.get('/api/telephony/estado/').json()['llamada'])

    def test_sin_interno_no_puede_llamar(self):
        otro = User.objects.create_user('beto', password='x')
        cl = Client()
        cl.force_login(otro)
        r = cl.post('/api/telephony/dial', json.dumps({'phoneNumber': '1155554444'}), content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('interno', r.json()['error'])


class DiscadorTests(AnuraBase):
    def test_disca_al_agente_libre_y_reintenta(self):
        ops = [ingresar_prospecto({'telefono': f'11{n:08d}', 'nombre': f'P{n}'}, self.embudo, 'manual').oportunidad
               for n in range(3)]
        camp = CampaniaDiscado.objects.create(nombre='C', embudo=self.embudo, estado='activa', dias=list(range(7)),
                                              hora_desde='00:00', hora_hasta='23:59', segundos_entre_llamadas=0,
                                              max_intentos=2, minutos_entre_intentos=0)
        camp.agentes.set([self.agente])
        from .services import cargar_en_campania
        cargar_en_campania(camp, Oportunidad.objects.filter(pk__in=[o.pk for o in ops]))
        AgenteDiscador.objects.create(agente=self.agente, campania=camp)
        with mock.patch('apps.telefonia.client.ClienteAnuraDemo.dial', return_value={'call_id': 'D1', 'uuid': 'D1', 'raw': {}}):
            self.assertEqual(discador_tick(), 1)
            self.assertEqual(discador_tick(), 0)  # el agente está ocupado
        cc = CampaniaContacto.objects.get(estado='en_curso')
        procesar_evento_llamada({'callId': 'D1', 'direction': 'OUT', 'status': 'NOANSWER', 'billSeconds': 0})
        cc.refresh_from_db()
        self.assertEqual((cc.estado, cc.intentos), ('reintentar', 1))


class AliasTests(AnuraBase):
    def test_evento_con_alias_se_asigna_al_usuario(self):
        InternoAnura.objects.filter(usuario=self.agente).update(alias='ras_201, 5401')
        InternoAnura.objects.get(usuario=self.agente).save()  # invalida el mapa cacheado
        procesar_evento_llamada({'callId': 'AL1', 'direction': 'IN', 'status': 'ANSWER', 'calling': '1133332222',
                                 'terminal': 'ras_201', 'billSeconds': 20})
        self.assertEqual(Llamada.objects.get(call_id='AL1').agente, self.agente)
        procesar_evento_llamada({'callId': 'AL2', 'direction': 'OUT', 'status': 'ANSWER', 'called': '1133334444',
                                 'terminal': '5401', 'billSeconds': 20})
        self.assertEqual(Oportunidad.objects.get(contacto__telefono='+5491133334444').agente, self.agente)

    def test_alias_repetido_rechazado(self):
        otro = User.objects.create_user('beto', password='x')
        admin = User.objects.create_superuser('root', 'r@x.com', 'x', rol=User.ROL_ADMIN)
        InternoAnura.objects.filter(usuario=self.agente).update(alias='ras_201')
        self.client.force_login(admin)
        self.client.post(f'/usuarios/{otro.pk}/', {'username': 'beto', 'email': 'b@x.com', 'rol': 'agente', 'is_active': 'on',
                                                   'interno_anura': '202', 'alias_anura': 'ras_201'})
        self.assertFalse(InternoAnura.objects.filter(usuario=otro).exists())
        self.client.post(f'/usuarios/{otro.pk}/', {'username': 'beto', 'email': 'b@x.com', 'rol': 'agente', 'is_active': 'on',
                                                   'interno_anura': '202', 'alias_anura': 'ras_202,  ras_202 , sip202'})
        self.assertEqual(InternoAnura.objects.get(usuario=otro).alias, 'ras_202, sip202')


def _plantilla(**valores):
    """Renderiza la plantilla que se carga en Anura, como lo haría Anura con sus variables."""
    import re
    from .services import PLANTILLA_WEBHOOK
    return json.loads(re.sub(r'\{\{ (\w+) \}\}', lambda m: str(valores.get(m.group(1), '')), PLANTILLA_WEBHOOK))


class AnuraRealTests(AnuraBase):
    def setUp(self):
        super().setUp()
        ConfigAnura.objects.filter(pk=1).update(modo_demo=False, click2dial_token='tok123', descargar_grabaciones=True)
        from django.core.cache import cache
        cache.clear()

    def test_click2dial_pedido_exacto_y_unificacion_por_custom(self):
        op = ingresar_prospecto({'telefono': '1155554444', 'nombre': 'Juan'}, self.embudo, 'manual').oportunidad
        cl = Client()
        cl.force_login(self.agente)
        resp = mock.Mock(status_code=200, text='{}')
        resp.json.return_value = {}
        with mock.patch('apps.telefonia.client.requests.post', return_value=resp) as post:
            r = cl.post('/api/telephony/dial', json.dumps({'oportunidadId': op.pk}), content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        args, kwargs = post.call_args
        self.assertEqual(args[0], 'https://api.anura.com.ar/adapter/default/click2call')
        self.assertEqual(kwargs['headers'], {'Authorization': 'Bearer tok123'})
        llamada = Llamada.objects.get()
        self.assertEqual(kwargs['data'], [('called', '5491155554444'), ('extension', '201'), ('customs', f'crm-{llamada.pk}')])
        # Eventos de Anura con la plantilla: TALK no cierra, END sí; custom1 unifica aunque el callId sea nuevo
        base = dict(cdrid='98765', direction='OUT', calling='201', called='5491155554444', accountextension='201',
                    custom1=f'crm-{llamada.pk}')
        self.webhook(_plantilla(**base, hooktrigger='START'))
        self.webhook(_plantilla(**base, hooktrigger='TALK', status='ANSWER', answerextension='201'))
        llamada.refresh_from_db()
        self.assertEqual((llamada.estado, llamada.call_id, llamada.procesada), ('en_curso', '98765', False))
        with mock.patch('apps.telefonia.client.requests.get', return_value=mock.Mock(status_code=200, content=b'MP3')), \
                self.captureOnCommitCallbacks(execute=True):
            self.webhook(_plantilla(**base, hooktrigger='END', status='ANSWER', billseconds='42', wasrecorded='true',
                                    audio_file_mp3='https://grab.anura/x.mp3'))
        llamada.refresh_from_db()
        self.assertEqual(Llamada.objects.count(), 1)
        self.assertEqual((llamada.estado, llamada.duracion_seg, llamada.grabacion_estado), ('atendida', 42, 'ok'))
        self.assertTrue(llamada.grabacion.name.endswith('.mp3'))

    def test_entrante_por_cola_asigna_al_que_atendio(self):
        otro = User.objects.create_user('beto', password='x')
        InternoAnura.objects.create(usuario=otro, interno='202', alias='beto_sip')
        ev = dict(cdrid='555', direction='IN', calling='01144443333', called='48001234', queueid='7',
                  accountextension='900')
        self.webhook(_plantilla(**ev, hooktrigger='START'))
        self.webhook(_plantilla(**ev, hooktrigger='TALK', status='ANSWER', answerterminal='beto_sip'))
        self.webhook(_plantilla(**ev, hooktrigger='END', status='ANSWER', billseconds='80', answerextension='202'))
        ll = Llamada.objects.get()
        self.assertEqual((ll.agente, ll.estado, ll.numero), (otro, 'atendida', '+5491144443333'))
        self.assertEqual(ll.oportunidad.agente, otro)
        self.assertEqual(set(ll.payload['eventos']), {'START', 'TALK', 'END'})

    def test_errores_de_click2dial_traducidos(self):
        from .client import ClienteAnura, ErrorAnura
        config = ConfigAnura.objects.get(pk=1)
        for status, cuerpo, esperado in [(400, {'message': 'Originate Terminal is not registered'}, 'no está conectado'),
                                         (400, {'message': 'No Originate terminal for extension'}, 'terminal principal'),
                                         (401, {'message': 'SecretId 9 not found'}, 'token')]:
            resp = mock.Mock(status_code=status, text=json.dumps(cuerpo))
            resp.json.return_value = cuerpo
            with mock.patch('apps.telefonia.client.requests.post', return_value=resp):
                with self.assertRaisesMessage(ErrorAnura, esperado):
                    ClienteAnura(config).dial('201', '+5491155554444')

    def test_webhook_con_bearer_y_cortar_no_disponible(self):
        token = ConfigAnura.objects.get(pk=1).webhook_token
        r = self.client.post('/api/integrations/anura/webhook', json.dumps({'callId': 'B1', 'direction': 'IN',
                             'event': 'END', 'status': 'NOANSWER', 'calling': '1122223333'}),
                             content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(r.status_code, 200)
        ll = Llamada.objects.create(direccion='OUT', estado='en_curso', agente=self.agente, numero='+5491100000000')
        cl = Client()
        cl.force_login(self.agente)
        r = cl.put(f'/api/telephony/hangup/{ll.pk}')
        self.assertEqual(r.status_code, 400)
        self.assertIn('cortá desde tu teléfono', r.json()['error'])


class IrAAnuraTests(AnuraBase):
    def test_estado_incluye_direccion_de_anura_y_boton_en_pantalla(self):
        ConfigAnura.objects.filter(pk=1).update(webphone_url='https://panel.anura.com.ar')
        from django.core.cache import cache
        cache.clear()
        ll = Llamada.objects.create(direccion='OUT', estado='en_curso', agente=self.agente, numero='+5491100000000')
        cl = Client()
        cl.force_login(self.agente)
        self.assertEqual(cl.get('/api/telephony/estado/').json()['llamada']['anura_url'], 'https://panel.anura.com.ar')
        self.assertContains(cl.get('/mi-dia/'), 'ir-anura')


class SoloEventosPropiosTests(AnuraBase):
    """Cuenta de Anura compartida: solo entran las llamadas de internos asociados, rutas cargadas o "Llamar" del CRM."""

    def setUp(self):
        ConfigAnura.objects.filter(pk=1).update(solo_eventos_propios=True)

    def test_llamada_de_otro_interno_se_ignora(self):
        self.webhook({'callId': 'AJ1', 'direction': 'IN', 'event': 'END', 'status': 'ANSWER', 'billSeconds': 40,
                      'calling': '1155550000', 'called': '47000000', 'answerExtension': '999'})
        self.assertFalse(Llamada.objects.exists())
        self.assertFalse(Contacto.objects.exists())

    def test_llamada_de_interno_asociado_entra_completa(self):
        self.webhook({'callId': 'P1', 'direction': 'IN', 'event': 'START', 'calling': '1155551111',
                      'accountExtension': '201'})
        # el fin puede venir sin identificadores: se sigue porque ya la veníamos siguiendo
        self.webhook({'callId': 'P1', 'direction': 'IN', 'event': 'END', 'status': 'ANSWER', 'billSeconds': 20,
                      'calling': '1155551111'})
        ll = Llamada.objects.get()
        self.assertEqual((ll.estado, ll.agente), ('atendida', self.agente))

    def test_ruta_cargada_entra_aunque_nadie_atienda(self):
        RutaAnura.objects.create(tipo=RutaAnura.TIPO_DID, valor='47001111', embudo=self.embudo)
        self.webhook({'callId': 'R1', 'direction': 'IN', 'status': 'NOANSWER', 'calling': '1155552222',
                      'called': '47001111', 'billSeconds': 0})
        self.assertTrue(Contacto.objects.filter(telefono='+5491155552222').exists())


class LlamadaSinAvisoTests(AnuraBase):
    """Click2Dial aceptado pero Anura nunca manda eventos: el aviso no puede quedar trabado en 'Discando'."""

    def setUp(self):
        super().setUp()
        ConfigAnura.objects.filter(pk=1).update(modo_demo=False, click2dial_token='tok', solo_eventos_propios=True)
        from django.core.cache import cache
        cache.clear()
        op = ingresar_prospecto({'telefono': '1155557777', 'nombre': 'Rita'}, self.embudo, 'manual').oportunidad
        self.cl = Client()
        self.cl.force_login(self.agente)
        resp = mock.Mock(status_code=200, text='{"id": 55}')
        resp.json.return_value = {'id': 55}
        with mock.patch('apps.telefonia.client.requests.post', return_value=resp):
            self.cl.post('/api/telephony/dial', json.dumps({'oportunidadId': op.pk}), content_type='application/json')
        self.llamada = Llamada.objects.get()

    def test_guarda_respuesta_y_el_agente_puede_descartar(self):
        self.assertEqual(self.llamada.payload['click2dial'], {'id': 55})
        d = self.cl.get('/api/telephony/estado/').json()['llamada']
        self.assertTrue(d['sin_aviso'])
        r = self.cl.put(f'/api/telephony/hangup/{self.llamada.pk}?descartar=1')
        self.assertEqual(r.status_code, 200, r.content)
        self.llamada.refresh_from_db()
        self.assertEqual(self.llamada.estado, Llamada.ESTADO_FALLIDA)
        self.assertIsNone(self.cl.get('/api/telephony/estado/').json()['llamada'])

    def test_se_cierra_sola_a_los_3_minutos(self):
        from datetime import timedelta
        from django.utils import timezone
        from .services import cerrar_llamadas_colgadas
        Llamada.objects.filter(pk=self.llamada.pk).update(inicio_at=timezone.now() - timedelta(minutes=4))
        cerrar_llamadas_colgadas()
        self.llamada.refresh_from_db()
        self.assertFalse(self.llamada.viva)
        op = self.llamada.oportunidad
        op.refresh_from_db()
        self.assertEqual((op.intentos_contacto, op.etapa.orden), (0, 1))  # no cuenta como intento ni avanza
        self.assertTrue(op.actividades.filter(texto__contains='sin confirmación de Anura').exists())
