"""Local development settings. Never used in production."""
from .base import *  # noqa: F403

SECRET_KEY = env("SECRET_KEY", "dev-only-insecure-key-do-not-use-in-production")  # noqa: F405
DEBUG = True
ALLOWED_HOSTS = ["*"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
    }
}
# Swap to Postgres locally by setting DATABASE_URL and uncommenting:
# import dj_database_url
# if env("DATABASE_URL"):
#     DATABASES["default"] = dj_database_url.parse(env("DATABASE_URL"), conn_max_age=600)

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
SMS_BACKEND = "apps.accounts.sms.ConsoleSMSBackend"

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "trusthailer-dev",
    }
}

INTERNAL_IPS = ["127.0.0.1"]
