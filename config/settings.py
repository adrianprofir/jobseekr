"""
Django settings for jobseekr.

Every deployment-specific value comes from environment variables so the same
code runs in local development, CI and the Docker Compose stack.
See `.env.example` for the full list.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)

# A public demo: "Try the demo" (/try/) gives each visitor a private sandbox
# account with sample data, deleted after SANDBOX_LIFETIME_HOURS. It changes the
# defaults of CAPTURE_MODE and DOCUMENT_UPLOADS_ENABLED below. See README.md.
DEMO_MODE = env_bool("DEMO_MODE", False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is off.")
    SECRET_KEY = "insecure-development-key-only-used-with-debug"

# localhost is always allowed so the container healthcheck can reach the app.
ALLOWED_HOSTS = [*env_list("DJANGO_ALLOWED_HOSTS"), "localhost", "127.0.0.1"]
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "tracker",
    "demo",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Signs a visitor out of an expired demo sandbox before the login check.
    "demo.middleware.SandboxMiddleware",
    # Every page requires a login unless the view opts out explicitly.
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "tracker.context_processors.follow_ups",
                "tracker.context_processors.main_site",
                "tracker.context_processors.features",
                "demo.context_processors.demo",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "jobtracker"),
        "USER": env("POSTGRES_USER", "jobtracker"),
        "PASSWORD": env("POSTGRES_PASSWORD", ""),
        "HOST": env("POSTGRES_HOST", "localhost"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# In the demo, a visitor without a session lands on "Try the demo" instead.
LOGIN_URL = "demo-try" if DEMO_MODE else "login"
LOGIN_REDIRECT_URL = "application-list"
LOGOUT_REDIRECT_URL = "login"

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Uploaded CVs and cover letters. They live outside STATIC_ROOT and there is
# deliberately no MEDIA_URL route: files are only served by the login-protected
# document download view.
PRIVATE_MEDIA_ROOT = Path(env("PRIVATE_MEDIA_ROOT", BASE_DIR / "private-media"))
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": PRIVATE_MEDIA_ROOT, "base_url": None},
    },
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    },
}

DOCUMENT_MAX_UPLOAD_MB = int(env("DOCUMENT_MAX_UPLOAD_MB", "15"))
# Uploading CVs and cover letters. Off in the demo by default, so strangers'
# CVs never land on the server; documents already stored stay downloadable.
DOCUMENT_UPLOADS_ENABLED = env_bool("DOCUMENT_UPLOADS_ENABLED", not DEMO_MODE)
if not DOCUMENT_UPLOADS_ENABLED:
    # Files in a request are skipped unread instead of written to a temporary file.
    FILE_UPLOAD_HANDLERS = ["tracker.uploads.SkipFilesUploadHandler"]
# The largest request body apart from uploaded files. A demo form never needs much.
DATA_UPLOAD_MAX_MEMORY_SIZE = (512 if DEMO_MODE else 5 * 1024) * 1024

# Link capture: "live" fetches the pasted page from the internet; "samples"
# fetches nothing and only answers the bundled sample postings
# (tracker/capture/samples.py). The demo defaults to samples.
CAPTURE_MODE = env("CAPTURE_MODE", "samples" if DEMO_MODE else "live")
if CAPTURE_MODE not in {"live", "samples"}:
    raise ImproperlyConfigured("CAPTURE_MODE must be 'live' or 'samples'.")

# Sources offered as suggestions in the application form, next to every source
# already used. Comma-separated; set it empty to offer only the ones in use.
SOURCE_SUGGESTIONS = env_list(
    "SOURCE_SUGGESTIONS", "LinkedIn,Company website,Referral,Recruiter,Jobindex"
)

# Days without logged activity before an application waiting on the employer
# is flagged on the overview. See ApplicationQuerySet.gone_quiet.
STALE_AFTER_DAYS = int(env("STALE_AFTER_DAYS", "14"))

# Outgoing e-mail, used only for the optional weekly summary
# (`manage.py send_weekly_summary`). Leave EMAIL_HOST empty to turn it off.
WEEKLY_SUMMARY_EMAIL_ENABLED = bool(env("EMAIL_HOST"))
_email_use_ssl = env_bool("EMAIL_USE_SSL", False)
MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "OPTIONS": {
            "host": env("EMAIL_HOST") or "localhost",
            # Without EMAIL_PORT: 465 with SSL, 587 with STARTTLS, else 25.
            "port": int(env("EMAIL_PORT")) if env("EMAIL_PORT") else None,
            "username": env("EMAIL_HOST_USER", ""),
            "password": env("EMAIL_HOST_PASSWORD", ""),
            "use_ssl": _email_use_ssl,
            "use_tls": env_bool("EMAIL_USE_TLS", not _email_use_ssl),
            "timeout": 30,
        },
    },
}
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL") or env("EMAIL_HOST_USER") or "jobseekr@localhost"
# The address you open the tracker on, e.g. http://localhost:8000, so the
# summary e-mail can link back to it. Optional.
SITE_URL = env("SITE_URL", "").rstrip("/")

# The demo (DEMO_MODE). A sandbox is deleted SANDBOX_LIFETIME_HOURS after it was
# made. At most SANDBOX_MAX_ACTIVE exist at once, and one visitor address may
# start SANDBOX_RATE_LIMIT in SANDBOX_RATE_WINDOW seconds. The address comes from
# CLIENT_IP_HEADER when set (CF-Connecting-IP behind Cloudflare), else from the
# connection; only set it when a proxy you control always sets that header.
SANDBOX_LIFETIME_HOURS = int(env("SANDBOX_LIFETIME_HOURS", "24"))
SANDBOX_MAX_ACTIVE = int(env("SANDBOX_MAX_ACTIVE", "100"))
SANDBOX_RATE_LIMIT = int(env("SANDBOX_RATE_LIMIT", "3"))
SANDBOX_RATE_WINDOW = int(env("SANDBOX_RATE_WINDOW", "3600"))
CLIENT_IP_HEADER = env("CLIENT_IP_HEADER", "")
# What one demo account may hold. Text limits are in tracker/forms.py.
SANDBOX_MAX_APPLICATIONS = int(env("SANDBOX_MAX_APPLICATIONS", "200"))
SANDBOX_MAX_COMPANIES = int(env("SANDBOX_MAX_COMPANIES", "100"))
# How long the server's logs keep visitor addresses, as stated on the Try page.
# Set it to match the log retention of your deployment.
DEMO_LOG_RETENTION_DAYS = int(env("DEMO_LOG_RETENTION_DAYS", "14"))
# A link back to the site the app is shown on, in a strip on top of every page.
# Empty (the default) shows no strip.
MAIN_SITE_URL = env("MAIN_SITE_URL", "").strip()

if DEMO_MODE:
    if env("EMAIL_HOST"):
        raise ImproperlyConfigured(
            "DEMO_MODE is on and EMAIL_HOST is set. A public demo must never send e-mail: "
            "unset EMAIL_HOST."
        )
    if CAPTURE_MODE != "samples":
        raise ImproperlyConfigured(
            "DEMO_MODE needs CAPTURE_MODE=samples, so visitors cannot make the server "
            "fetch other sites."
        )
    if DOCUMENT_UPLOADS_ENABLED:
        raise ImproperlyConfigured(
            "DEMO_MODE needs DOCUMENT_UPLOADS_ENABLED=false, so visitors cannot upload "
            "files to the server."
        )

# Sensible hardening for a plain-HTTP deployment on a trusted network. Everything
# that needs HTTPS is opt-in, because the app is served over HTTP by default.
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
# A demo session only needs to outlive its sandbox long enough to say it expired.
SESSION_COOKIE_AGE = 60 * 60 * (SANDBOX_LIFETIME_HOURS + 24 if DEMO_MODE else 24 * 14)
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", False)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE

# Behind a TLS-terminating reverse proxy: trust its X-Forwarded-Proto header (only
# enable this when the proxy always sets it), redirect plain HTTP, and send HSTS.
# The container health check calls /healthz over plain HTTP, so it is exempt
# from the redirect.
if env_bool("TRUST_X_FORWARDED_PROTO", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", False)
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.db.backends": {"level": "WARNING"},
    },
}
