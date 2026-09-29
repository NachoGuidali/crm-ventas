from django.urls import path

from . import views

app_name = 'automatizaciones'

urlpatterns = [
    path('', views.AccionListView.as_view(), name='lista'),
    path('nueva/', views.AccionEditarView.as_view(), name='nueva'),
    path('<int:pk>/', views.AccionEditarView.as_view(), name='editar'),
    path('ejecuciones/', views.EjecucionesView.as_view(), name='ejecuciones'),
]
