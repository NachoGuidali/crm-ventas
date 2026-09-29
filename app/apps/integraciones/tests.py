import json

from django.core.management import call_command

from core.testing import TestCase
from apps.crm.models import Contacto, Embudo, Oportunidad

from .models import ApiKey


class ApiLeadsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_inicial', '--sin-admin', verbosity=0)
        cls.key, cls.clave = ApiKey.generar(nombre='Landing', embudo=Embudo.objects.get(), fuente='Landing DF')

    def post(self, data, clave=None):
        return self.client.post('/api/v1/leads/', json.dumps(data), content_type='application/json',
                                HTTP_X_API_KEY=clave or self.clave)

    def test_alta_y_duplicado(self):
        r = self.post({'nombre': 'Ana', 'telefono': '11 5555-0000', 'fecha_atencion': '15/09/2026', 'utm': 'google'})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(r.json()['oportunidad_nueva'])
        c = Contacto.objects.get()
        self.assertEqual(c.datos_extra['utm'], 'google')
        self.assertEqual(Oportunidad.objects.get().fuente, 'Landing DF')
        r = self.post({'nombre': 'Ana', 'telefono': '+5491155550000'})
        self.assertEqual((r.status_code, r.json()['motivo']), (200, 'ya_activa'))

    def test_clave_invalida(self):
        self.assertEqual(self.post({'telefono': '1'}, clave='mala').status_code, 401)
        self.key.activa = False
        self.key.save()
        self.assertEqual(self.post({'telefono': '1155550000'}).status_code, 401)

    def test_buscar(self):
        self.post({'nombre': 'Ana', 'telefono': '1155550000'})
        r = self.client.get('/api/v1/leads/buscar/', {'telefono': '011 15 5555 0000'}, HTTP_X_API_KEY=self.clave)
        self.assertEqual(r.json()['contacto']['nombre'], 'Ana')
