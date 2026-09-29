from django.urls import path

from . import views

app_name = 'reportes'

urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('ranking.csv', views.ExportarRankingView.as_view(), name='ranking_csv'),
]
