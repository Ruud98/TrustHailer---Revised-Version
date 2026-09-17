"""
Shared settings. Never put secrets or environment-specific values here.
Import this from dev.py / prod.py.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def env(key, default=None, required=False):
    value = os.environ.get(key, default)
    if required and value in (None, ""):
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value


def env_bool(key, default=False):
    return str(os.environ.get(key, default)).lower() in ("1", "true", "yes", "on")


def env_list(key, default=""):
    raw = os.environ.get(key, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------------------------------------------------------- applications

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
]

LOCAL_APPS = [
    "apps.core",
    "apps.geo",
    "apps.accounts",
    "apps.listings",
    "apps.intros",
    "apps.safety",
    "apps.placements",
    "apps.feed",
    "apps.notifications",
    "apps.directory",
    "apps.promos",
]

INSTALLED_APPS = DJANGO_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.accounts.middleware.OnboardingMiddleware",
    "apps.accounts.middleware.LastSeenMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.site",
                "apps.notifications.context_processors.notifications",
            ],
        },
    },
]

# ---------------------------------------------------------------------- auth

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:join"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

AUTHENTICATION_BACKENDS = [
    "apps.accounts.backends.EmailBackend",
    "django.contrib.auth.backends.ModelBackend",
]

# No password validators listed: accounts authenticate by phone OTP and have
# unusable passwords. Staff accounts created via createsuperuser still get the
# admin's own hashing, and admin login is protected separately.
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

SESSION_COOKIE_AGE = 60 * 60 * 24 * 90  # 90 days: users hate re-authenticating
SESSION_SAVE_EVERY_REQUEST = True

# ------------------------------------------------------------------- i18n / tz

LANGUAGE_CODE = "en-za"
TIME_ZONE = "Africa/Johannesburg"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------- static/media

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Identity documents are kept OUTSIDE MEDIA_ROOT, deliberately. `runserver`
# serves MEDIA_ROOT at /media/ in development and the production media bucket is
# public by design (car photos, avatars — signing a URL per thumbnail would be
# absurd), so anything under it is one guessed path away from being read.
# Nothing maps a URL to this directory; reviewers read documents through the
# staff-only streaming view instead. See apps/accounts/storages.py.
KYC_ROOT = BASE_DIR / "private-media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
    "kyc": {
        "BACKEND": "apps.accounts.storages.PrivateFileSystemStorage",
        "OPTIONS": {"location": KYC_ROOT},
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------- OTP

OTP_LENGTH = 6
OTP_TTL_SECONDS = 15 * 60          # generous, because email can lag
OTP_MAX_ATTEMPTS = 5               # wrong-code attempts before the code dies
OTP_RESEND_COOLDOWN_SECONDS = 60
OTP_MAX_PER_ADDRESS_PER_DAY = 12
OTP_MAX_PER_IP_PER_HOUR = 20

# ------------------------------------------------------------------- channels
# Email carries signup and login: free, unlimited at our volumes, no per-message
# fee for a visitor who never returns.
#
# There is no SMS channel. It existed solely to verify a phone number, that
# verification is gone, and the settings, backends and sender-ID registration
# that went with it have gone too. Anything added here later should be added
# because a flow needs it, not because it was inherited.

# ------------------------------------------------------------------ pricing
# Nothing on this site costs anything: not listing, not browsing, not being
# introduced, not a feature slot. Deliberately NOT read from the environment —
# a paid platform is a product decision that should arrive as a code change
# somebody reviewed, never as an env var somebody flipped on a Friday.
#
# The pricing module stays (see apps/core/pricing.py) because it is the seam
# every chargeable action already runs through, and ripping it out would mean
# a data migration to put it back. It answers "free" for everything while this
# is False, which is the committed state.
MONETISATION_ENABLED = False

# --------------------------------------------------------------- image pipeline
# Data costs real money for our users. Every upload is re-encoded.

IMAGE_MAX_EDGE = 1400          # px, long edge of the display version
IMAGE_THUMB_EDGE = 400         # px, long edge of the thumbnail
IMAGE_QUALITY = 78             # WebP quality
IMAGE_MAX_UPLOAD_BYTES = 8 * 1024 * 1024
IMAGE_ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "HEIF", "MPO"}

# --------------------------------------------------------------------- site

SITE_NAME = "TrustHailer"
# Absolute base for links in outgoing mail. An email with a relative link in it
# is a dead end, and this is the only place that knows the public address.
SITE_URL = env("SITE_URL", "http://127.0.0.1:8000")
SITE_TAGLINE = "Cars and drivers, connected."
SUPPORT_WHATSAPP = env("SUPPORT_WHATSAPP", "+27000000000")

# -------------------------------------------------------------------- logging

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "apps": {"level": "INFO", "handlers": ["console"], "propagate": False},
    },
}
