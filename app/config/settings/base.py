import os
from pathlib import Path

from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def env(key, default=''):
    return os.environ.get(key, default)


def env_bool(key, default=False):
    return env(key, str(default)).strip().lower() in ('1', 'true', 'yes', 'si')


SECRET_KEY = env('DJANGO_SECRET_KEY', 'dev-insecure-change-me')
DEBUG = env_bool('DJANGO_DEBUG', True)
ALLOWED_HOSTS = env('ALLOWED_HOSTS', 'localhost 127.0.0.1').split()
CSRF_TRUSTED_ORIGINS = [o.strip() for o in env('CSRF_TRUSTED_ORIGINS', '').split(',') if o.strip()]

# Nombre visible del sistema (logo, títulos, emails)
CRM_NOMBRE = env('CRM_NOMBRE', 'CRM Ventas')
CRM_SUBTITULO = env('CRM_SUBTITULO', 'SupReg Solutions')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'django.contrib.postgres',
    'django_celery_beat',
    'core',
    'apps.users',
    'apps.crm',
    'apps.whatsapp',
    'apps.telefonia',
    'apps.automatizaciones',
    'apps.integraciones',
    'apps.pautas',
    'apps.reportes',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'apps.users.middleware.ForzarCambioPasswordMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.crm',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': env('POSTGRES_DB', 'crm_ventas'),
        'USER': env('POSTGRES_USER', 'crm_ventas'),
        'PASSWORD': env('POSTGRES_PASSWORD', 'crm_ventas'),
        'HOST': env('POSTGRES_HOST', 'db'),
        'PORT': env('POSTGRES_PORT', '5432'),
        # Conexiones persistentes: evita abrir una conexión por request con muchos usuarios.
        'CONN_MAX_AGE': int(env('DB_CONN_MAX_AGE', '60')),
        'CONN_HEALTH_CHECKS': True,
    }
}

AUTH_USER_MODEL = 'users.User'
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
AUTHENTICATION_BACKENDS = ['apps.users.backends.UsernameOrEmailBackend']

LOGIN_URL = '/usuarios/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/usuarios/login/'

# Links de recupero de contraseña válidos por 2 horas
PASSWORD_RESET_TIMEOUT = int(env('PASSWORD_RESET_TIMEOUT', str(60 * 60 * 2)))
# Bloqueo de login tras N intentos fallidos (por usuario + IP)
LOGIN_MAX_INTENTOS = int(env('LOGIN_MAX_INTENTOS', '5'))
LOGIN_BLOQUEO_MINUTOS = int(env('LOGIN_BLOQUEO_MINUTOS', '15'))

LANGUAGE_CODE = 'es-ar'
TIME_ZONE = 'America/Argentina/Buenos_Aires'
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# URL pública del sitio: links en emails, webhooks y media enviada desde tareas Celery
SITE_URL = env('SITE_URL', 'http://localhost:8000').rstrip('/')

# ── Redis / Celery ─────────────────────────────────────────────────────────
REDIS_URL = env('REDIS_URL', 'redis://redis:6379/0')

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': REDIS_URL,
        'KEY_PREFIX': 'crmv',
    }
}
SESSION_ENGINE = 'django.contrib.sessions.backends.cached_db'
SESSION_COOKIE_AGE = 60 * 60 * 12

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = None
CELERY_TASK_IGNORE_RESULT = True
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = 60 * 30
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'
# Colas separadas: los envíos masivos/importaciones no traban los webhooks entrantes.
CELERY_TASK_ROUTES = {
    'whatsapp.procesar_mensaje_entrante': {'queue': 'entrantes'},
    'telefonia.procesar_evento_llamada': {'queue': 'entrantes'},
    'crm.procesar_importacion': {'queue': 'masivos'},
    'crm.accion_masiva': {'queue': 'masivos'},
    'whatsapp.enviar_mensaje': {'queue': 'salientes'},
    'automatizaciones.ejecutar_accion': {'queue': 'salientes'},
    'automatizaciones.difusiones_tick': {'queue': 'masivos'},
}
CELERY_TASK_DEFAULT_QUEUE = 'default'
CELERY_BEAT_SCHEDULE = {
    'asignar-pendientes': {'task': 'crm.asignar_pendientes', 'schedule': 120},
    'revisar-sla': {'task': 'crm.revisar_sla', 'schedule': 120},
    'recalcular-puntajes': {'task': 'crm.recalcular_puntajes', 'schedule': crontab(minute=40)},
    'reactivar-pausadas': {'task': 'crm.reactivar_pausadas', 'schedule': 300},
    'vencer-tareas': {'task': 'crm.notificar_tareas_vencidas', 'schedule': 600},
    'automatizaciones-programadas': {'task': 'automatizaciones.barrer_programadas', 'schedule': 60},
    'difusiones': {'task': 'automatizaciones.difusiones_tick', 'schedule': 60},
    'automatizaciones-sin-actividad': {'task': 'automatizaciones.revisar_sin_actividad', 'schedule': 300},
    'recordatorio-inactividad': {'task': 'automatizaciones.revisar_inactividad', 'schedule': crontab(minute=15)},
    'discador-tick': {'task': 'telefonia.discador_tick', 'schedule': 10},
    'anura-polling-cdrs': {'task': 'telefonia.polling_cdrs', 'schedule': 300},
    'anura-limpiar-llamadas-colgadas': {'task': 'telefonia.cerrar_llamadas_colgadas', 'schedule': 120},
    'whatsapp-estado-lineas': {'task': 'whatsapp.actualizar_estado_lineas', 'schedule': 300},
    'whatsapp-sync-plantillas': {'task': 'whatsapp.sincronizar_plantillas', 'schedule': crontab(minute=0)},
    'purgar-logs': {'task': 'core.purgar_logs', 'schedule': crontab(hour=3, minute=30)},
}

# ── Email (recupero de contraseña y mensajes automáticos) ─────────────────
EMAIL_BACKEND = env('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = env('EMAIL_HOST', '')
EMAIL_PORT = int(env('EMAIL_PORT', '587'))
EMAIL_HOST_USER = env('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = env_bool('EMAIL_USE_TLS', True)
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', 'CRM Ventas <no-responder@localhost>')

# ── WhatsApp: valores default para líneas Evolution (cada línea puede pisarlos) ─
EVOLUTION_API_URL = env('EVOLUTION_API_URL', 'http://evolution-api:8080')
EVOLUTION_API_KEY = env('EVOLUTION_API_KEY', '')

# API key para sistemas externos (n8n, landings); además existen claves por integración en DB.
CRM_API_KEY = env('CRM_API_KEY', '')

LOG_RETENTION_DAYS = int(env('LOG_RETENTION_DAYS', '90'))

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {'verbose': {'format': '{levelname} {asctime} {name} {message}', 'style': '{'}},
    'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'verbose'}},
    'root': {'handlers': ['console'], 'level': 'INFO'},
    'loggers': {
        'django': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'apps': {'handlers': ['console'], 'level': env('APPS_LOG_LEVEL', 'INFO'), 'propagate': False},
    },
}
