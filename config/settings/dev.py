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

# ---------------------------------------------------------- stale templates
#
# The sibling of the problem above, and a nastier one because the stale thing
# is the page itself rather than its stylesheet: you edit a template, reload,
# and get the previous version back with no clue why.
#
# THE CACHED LOADER IS ON IN DEVELOPMENT, WHATEVER DEBUG SAYS
# It reads as though it is not. `django/template/engine.py` wraps the default
# loaders in `cached.Loader` unconditionally — the `if not debug` that used to
# guard it went away in Django 4.1 — so every template is compiled once per
# process and kept. What makes editing work anyway is not DEBUG but the
# AUTORELOADER: `django/template/autoreload.py` watches the template
# directories and calls `reset_loaders()` on every change.
#
# WHICH MEANS `runserver --noreload` NEVER PICKS UP A TEMPLATE EDIT
# No autoreloader, no `file_changed` signal, no `reset_loaders()`, and the
# compiled template lives as long as the process. That is not a hypothetical:
# the preview runner this project is developed against launches exactly that
# command, and the symptom is remarkably good at looking like something else —
# CSS updates fine, because stylesheets are read off disk per request and never
# touch the template cache, so the obvious conclusion is that the template edit
# did not save.
#
# Loading the two loaders directly drops the cache, so a template is read from
# disk each time it is rendered. That is the cost of a stat and a parse per
# render, on a development server, in exchange for the edit-reload loop
# behaving the way everybody already believes it does. `APP_DIRS` has to go off
# because Django refuses both at once — the loader list below includes the app
# directories loader, so the same files are still found in the same order.
TEMPLATES[0]["APP_DIRS"] = False  # noqa: F405
TEMPLATES[0]["OPTIONS"]["loaders"] = [  # noqa: F405
    "django.template.loaders.filesystem.Loader",
    "django.template.loaders.app_directories.Loader",
]
