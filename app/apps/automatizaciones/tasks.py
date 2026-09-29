from celery import shared_task


@shared_task(name='automatizaciones.ejecutar_accion')
def ejecutar_accion(ejecucion_id):
    from .services import ejecutar
    return ejecutar(ejecucion_id)


@shared_task(name='automatizaciones.revisar_inactividad')
def revisar_inactividad():
    from .services import revisar_inactividad as revisar
    return revisar()


@shared_task(name='automatizaciones.barrer_programadas')
def barrer_programadas():
    from .services import barrer_programadas as barrer
    return barrer()
