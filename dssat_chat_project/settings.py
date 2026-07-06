"""
Django settings for the EarthRISEAgents consolidated project.

Composed of the generic platform app (earthrise_agents_base), the DSSAT
product shell (dssat_chat_agent), and the sub-agent apps (data_agent,
dssat_agent, knowledge_agent) plus accounts — all sibling Django apps
under this single project.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# DEBUG is on only when the DEBUG env var is set to a non-falsy value.
# Absent, empty, or an explicit falsy value (0/false/f/no/off) all mean OFF,
# so a stray `DEBUG=false` cannot accidentally enable debug in production.
# Production deployments should leave DEBUG unset — that keeps DEBUG off and
# turns on the transport-security settings below.
_debug_raw = os.environ.get('DEBUG')
DEBUG = _debug_raw is not None and _debug_raw.strip().lower() not in (
    '', '0', 'false', 'f', 'no', 'n', 'off',
)

# SECRET_KEY is required in production. In DEBUG (local dev) we fall back to
# an insecure throwaway key so a bare `manage.py` run works without env setup.
SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'django-insecure-dev-key-not-for-production'
    else:
        raise ImproperlyConfigured(
            "SECRET_KEY environment variable must be set when DEBUG is off "
            "(production). Refusing to start with an insecure default key."
        )

_allowed_hosts = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1")
ALLOWED_HOSTS = [host.strip() for host in _allowed_hosts.split(",") if host.strip()]
if os.environ.get('SITE_URL'):
    CSRF_TRUSTED_ORIGINS = [
        'https://' + str(os.environ.get('SITE_URL')),
        'http://' + str(os.environ.get('SITE_URL')),
    ]

# ---------------------------------------------------------------------------
# Production transport security — enabled whenever DEBUG is off. Assumes the
# app runs behind an HTTPS-terminating proxy/ingress (SECURE_PROXY_SSL_HEADER).
# Individual toggles are env-overridable for atypical deployments.
# ---------------------------------------------------------------------------
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = os.environ.get('SECURE_SSL_REDIRECT', 'True').lower() in ('true', '1')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', '31536000'))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.gis",
    "accounts",
    # dssat_chat_agent MUST come before earthrise_agents_base so its
    # templates/shell/base.html shadows the framework's default. Every
    # sub-agent template that extends "shell/base.html" then inherits
    # the DSSAT branding automatically without importing anything from
    # this product app.
    "dssat_chat_agent",
    "earthrise_agents_base",
    "data_agent",
    "dssat_agent",
    "knowledge_agent",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "dssat_chat_project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "earthrise_agents_base.context_processors.agent_name",
                "earthrise_agents_base.context_processors.agent_visuals",
                "earthrise_agents_base.context_processors.subpath_prefix",
                "earthrise_agents_base.context_processors.nav_items",
                "earthrise_agents_base.context_processors.home_cards",
                "earthrise_agents_base.context_processors.management_items",
            ],
        },
    },
]

WSGI_APPLICATION = "dssat_chat_project.wsgi.application"
ASGI_APPLICATION = "dssat_chat_project.asgi.application"

# ---------------------------------------------------------------------------
# Database — PostGIS backend for spatial operations
# ---------------------------------------------------------------------------
if os.environ.get('DBHOST'):
    DATABASES = {
        "default": {
            "ENGINE": "django.contrib.gis.db.backends.postgis",
            "NAME": os.environ.get('DBNAME', 'dssatserv'),
            "USER": os.environ.get('DBUSER', 'postgres'),
            "PASSWORD": os.environ.get('PASSWORD', ''),
            "HOST": os.environ.get('DBHOST', 'localhost'),
            "PORT": os.environ.get('DBPORT', '5432'),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

subpath = os.environ.get('SUBPATH')
if subpath:
    STATIC_URL = subpath + "/static/"
else:
    STATIC_URL = '/static/'

# Project-level shared static dir. App static is discovered separately via
# AppDirectoriesFinder, so this is just an (optional) extension point — include
# it only when it exists, so `check`/collectstatic don't warn (W004) on a
# deployment that has no project-level static files.
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []

STATIC_ROOT = BASE_DIR / 'staticfiles'

STATICFILES_FINDERS = [
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
]

STATICFILES_STORAGE = 'whitenoise.storage.CompressedStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Celery — unified broker, queue routing
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'
CELERY_RESULT_EXPIRES = 3600
CELERY_TASK_DEFAULT_QUEUE = 'dssat_chat_project'
CELERY_WORKER_CONCURRENCY = 4
CELERY_WORKER_MAX_TASKS_PER_CHILD = 1000

CELERY_TASK_ROUTES = {
    'earthrise_agents_base.tasks.*': {'queue': 'dssat_chat_project'},
    'data_agent.tasks.*': {'queue': 'data_agent'},
    'dssat_agent.tasks.*': {'queue': 'dssat_agent'},
    'knowledge_agent.tasks.*': {'queue': 'knowledge_agent'},
}

# ---------------------------------------------------------------------------
# Schema isolation per agent
# ---------------------------------------------------------------------------
DATAAGENT_SCHEMA = os.environ.get('DATAAGENT_SCHEMA', 'dataagent')
SIMULATION_SCHEMA = os.environ.get('SIMULATION_SCHEMA', 'simulation')
KNOWLEDGE_SCHEMA = os.environ.get('KNOWLEDGE_SCHEMA', 'knowledge')

# ---------------------------------------------------------------------------
# Default backfill region for data_agent's startup time-series fetch. This is a
# domain/deployment default (lives here in the domain-root project, not in the
# generic data_agent). For the DSSAT deployment this is Alabama. Override per
# invocation with --bbox / STARTUP_FETCH_BBOX; set to None to disable the
# startup backfill entirely.
DATA_AGENT_DEFAULT_BBOX = {
    'west': -88.47, 'south': 30.14, 'east': -84.89, 'north': 35.01,
}

# Settings the knowledge_agent reads. See knowledge_agent/documentation/
# for the full schema. The default_tenant_id is the tenant that all
# chat-originated retrieval calls land in when no explicit tenant_id is
# passed. The DSSAT product shell registers its tenant(s) from
# dssat_chat_agent/data/tenants.yaml (see dssat_chat_agent/apps.py); this
# default points at that shell's tenant and is env-overridable for
# other product shells.
KNOWLEDGE_AGENT = {
    'default_tenant_id': os.environ.get('KNOWLEDGE_DEFAULT_TENANT_ID', 'dssat'),
}

# ---------------------------------------------------------------------------
# Ollama / LLM
# ---------------------------------------------------------------------------
OLLAMA_URL = os.environ.get('OLLAMA_URL', 'http://localhost:11434')
MODEL_NAME = os.environ.get('MODEL_NAME', 'llama3.1')
EMBEDDING_MODEL = os.environ.get('EMBEDDING_MODEL', 'nomic-embed-text')

# ---------------------------------------------------------------------------
# Agent client mode (embedded or remote)
# ---------------------------------------------------------------------------
AGENT_CLIENTS = {
    'data_agent': {'mode': os.environ.get('DATA_AGENT_MODE', 'embedded'),
                   'url': os.environ.get('DATA_AGENT_URL', 'http://localhost:5000')},
    'knowledge_agent': {'mode': os.environ.get('KNOWLEDGE_AGENT_MODE', 'embedded'),
                        'url': os.environ.get('KNOWLEDGE_AGENT_URL', 'http://localhost:9100')},
    'dssat_agent': {'mode': os.environ.get('DSSAT_AGENT_MODE', 'embedded'),
                    'url': os.environ.get('DSSAT_AGENT_URL', 'http://localhost:9200')},
}

# ---------------------------------------------------------------------------
# Product display name — shown in <title> tags, the nav brand, emails,
# etc. Override via CUSTOM_AGENT_NAME env var for rebranded deployments.
# ---------------------------------------------------------------------------
CUSTOM_AGENT_NAME = os.environ.get('CUSTOM_AGENT_NAME', 'ChatAgent')

# ---------------------------------------------------------------------------
# Chat welcome message (shown when a new conversation starts)
# ---------------------------------------------------------------------------
CHAT_WELCOME_MESSAGE = os.environ.get(
    'CHAT_WELCOME_MESSAGE',
    "Hello! I'm your multi-crop simulation assistant powered by DSSAT. "
    "I can help you simulate crop yields, manage soil profiles, explore weather data, "
    "and answer questions about crop modeling. What would you like to do?"
)

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
# Use URL *names* (not hardcoded paths) so Django's resolve_url() reverses
# them through the URLconf — that way the SUBPATH prefix applied in
# dssat_chat_project/urls.py is honored, instead of these settings shipping the
# browser to a path outside the prefix.
LOGIN_URL = 'accounts:login'
LOGIN_REDIRECT_URL = 'earthrise_agents_base:home'
LOGOUT_REDIRECT_URL = 'accounts:login'

# ---------------------------------------------------------------------------
# Email (console in dev, SMTP via env vars in production)
# ---------------------------------------------------------------------------
EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend'
)
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'localhost')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True').lower() in ('true', '1')
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@earthrise.local')
