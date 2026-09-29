from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path

from core.views import media_protegida

from apps.integraciones.urls import api_urlpatterns as api_integraciones
from apps.reportes import views as reportes_views
from apps.telefonia.urls import api_urlpatterns as api_telefonia

urlpatterns = [
    path('', reportes_views.inicio, name='inicio'),
    path('pulso/', reportes_views.PulsoView.as_view(), name='pulso'),
    path('admin/', admin.site.urls),
    path('usuarios/', include('apps.users.urls')),
    path('reportes/', include('apps.reportes.urls')),
    path('whatsapp/', include('apps.whatsapp.urls')),
    path('telefonia/', include('apps.telefonia.urls')),
    path('automatizaciones/', include('apps.automatizaciones.urls')),
    path('integraciones/', include('apps.integraciones.urls')),
    path('pautas/', include('apps.pautas.urls')),
    path('', include('apps.crm.urls')),
] + api_telefonia + api_integraciones

urlpatterns.insert(0, re_path(r'^media/(?P<ruta>.+)$', media_protegida, name='media'))

handler403 = 'core.views.error_403'
handler404 = 'core.views.error_404'
handler500 = 'core.views.error_500'
