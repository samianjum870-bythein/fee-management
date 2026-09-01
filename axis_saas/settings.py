import environ
import os
from pathlib import Path

env = environ.Env(DEBUG=(bool, False))
BASE_DIR = Path(__file__).resolve().parent.parent
environ.Env.read_env(os.path.join(BASE_DIR, '.env'))

def get_csrf_trusted_origins():
    import os
    origins = os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',')
    origins = [o.strip() for o in origins if o.strip()]
    # Add Railway production domain if available
    railway_domain = os.environ.get('RAILWAY_PUBLIC_DOMAIN', '')
    if railway_domain and railway_domain not in origins:
        origins.append(f"https://{railway_domain}")
    # Also add the base domain pattern? No wildcard. So we keep as is.
    if not origins:
        # Fallback for development
        origins = ['http://localhost:8000']
    return origins


SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-fallback-for-build-only')
# Auto-detect local development (no DATABASE_URL means local)
if not os.environ.get('DATABASE_URL'):
    DEBUG = True
else:
    DEBUG = env('DEBUG', default=False)

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '').split(',') if os.environ.get('ALLOWED_HOSTS') else ['*']
# Auto-add local development hosts when DEBUG is True
if DEBUG:
    ALLOWED_HOSTS += ['127.0.0.1', 'localhost']

railway_domain = os.environ.get('RAILWAY_PUBLIC_DOMAIN', '').strip()
if railway_domain:
    default_webauthn_origin = f'https://{railway_domain}'
    default_rp_id = railway_domain
else:
    # Avoid forcing localhost for production requests in environments where the
    # WebAuthn variables are not explicitly set. The request host is a safer
    # fallback, and the request-level helper will still override to localhost
    # only for local development when the host is actually local.
    default_webauthn_origin = ''
    default_rp_id = ''

WEBAUTHN_RP_ID = os.environ.get('WEBAUTHN_RP_ID', default_rp_id)
WEBAUTHN_ORIGIN = os.environ.get('WEBAUTHN_ORIGIN', default_webauthn_origin)
WEBAUTHN_RP_NAME = os.environ.get('WEBAUTHN_RP_NAME', 'AXIS School Portal')
WEBAUTHN_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get('WEBAUTHN_ALLOWED_ORIGINS', WEBAUTHN_ORIGIN).split(',')
    if origin.strip()
]
if WEBAUTHN_ORIGIN not in WEBAUTHN_ALLOWED_ORIGINS:
    WEBAUTHN_ALLOWED_ORIGINS.insert(0, WEBAUTHN_ORIGIN)

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'axis_saas.middleware.url_tenant_middleware.URLPathTenantMiddleware',
    'axis_saas.middleware.staff_tenant_middleware.StaffTenantMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'axis_saas.public_urls'
PUBLIC_SCHEMA_URLCONF = 'axis_saas.public_urls'
TENANT_URLCONF = 'axis_saas.tenant_urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'axis_saas.context_processors.tenant_processor',   # ✅ ADD THIS LINE
            ],
        },
    },
]

WSGI_APPLICATION = 'axis_saas.wsgi.application'

# Database – force django_tenants backend
if os.environ.get('DATABASE_URL'):
    import dj_database_url
    database_url = os.environ['DATABASE_URL']
    if 'sslmode=disable' in database_url.lower():
        DATABASES = {
            'default': dj_database_url.parse(database_url, conn_max_age=600)
        }




    else:
        DATABASES = {
            'default': dj_database_url.config(conn_max_age=600, ssl_require=True)
        }
    DATABASES['default']['ENGINE'] = 'django_tenants.postgresql_backend'
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django_tenants.postgresql_backend',
            'NAME': 'dummy',
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Karachi'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
MEDIA_ROOT = BASE_DIR / 'media'
MEDIA_URL = '/media/'

STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

LOGIN_URL = 'tenant_login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'tenant_login'

# Multi-tenant
SHARED_APPS = [
    'django_tenants',
    'axis_saas',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

TENANT_APPS = [
    'axis_saas',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

INSTALLED_APPS = SHARED_APPS + [app for app in TENANT_APPS if app not in SHARED_APPS]

TENANT_MODEL = 'axis_saas.SchoolClient'
TENANT_DOMAIN_MODEL = 'axis_saas.SchoolDomain'
TENANT_SUBFOLDER_PREFIX = 'portal'

# ✅ Fix for "No tenant for hostname" error – use public schema on root URL
PUBLIC_SCHEMA_NAME = 'public'
TENANT_LIMIT_SET_CALLS = True

DATABASE_ROUTERS = (
    'django_tenants.routers.TenantSyncRouter',
)

# Security
SESSION_COOKIE_DOMAIN = None
CSRF_COOKIE_DOMAIN = None
SESSION_ENGINE = 'axis_saas.session_backend'
SESSION_SAVE_EVERY_REQUEST = False
CSRF_TRUSTED_ORIGINS = get_csrf_trusted_origins()
SESSION_COOKIE_PATH = '/'
SESSION_FILE_PATH = '/tmp/django_sessions/'

# ---------- REDIS CACHE ----------
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': os.environ.get('REDIS_URL', 'redis://127.0.0.1:6379/1'),
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        'CONNECTION_POOL_CLASS': 'redis.BlockingConnectionPool',
            'CONNECTION_POOL_CLASS_KWARGS': {
                'max_connections': 50,
                'timeout': 20,
            },
            'MAX_CONNECTIONS': 1000,
            'PICKLE_VERSION': -1,
        },
        'KEY_PREFIX': 'axis'
    }
}

