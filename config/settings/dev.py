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

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "trusthailer-dev",
    }
}

INTERNAL_IPS = ["127.0.0.1"]

# ------------------------------------------------------- stale static files
#
# runserver sends no Cache-Control on /static/, so Chrome invents its own
# freshness window and you spend an afternoon debugging last version's
# stylesheet. Two pieces are needed, and neither works without the other:
#
#   1. The middleware, which sets no-store on anything under STATIC_URL or
#      MEDIA_URL.
#   2. apps.core ahead of django.contrib.staticfiles, so that apps.core's
#      `runserver` override wins the command lookup. Django resolves a
#      duplicate command name to the FIRST app in INSTALLED_APPS that defines
#      it. Without this line the override is dead code, staticfiles' own
#      runserver answers /static/ in front of the middleware chain, and the
#      middleware never sees a stylesheet request.
#
# apps.core carries no templates and no static directory, so moving it to the
# front changes command resolution and nothing else.
#
# Development only. Production hashes static filenames via
# ManifestStaticFilesStorage and caches them hard, which is what members on
# metered connections want.
MIDDLEWARE = ["apps.core.middleware.NoStoreStaticMiddleware"] + MIDDLEWARE  # noqa: F405
INSTALLED_APPS = ["apps.core"] + [  # noqa: F405
    app for app in INSTALLED_APPS if app != "apps.core"  # noqa: F405
]
