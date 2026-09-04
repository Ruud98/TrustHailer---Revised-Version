"""Production settings. Everything sensitive comes from the environment."""
import dj_database_url

from .base import *  # noqa: F403

SECRET_KEY = env("SECRET_KEY", required=True)  # noqa: F405
DEBUG = False
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS")  # noqa: F405
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")  # noqa: F405

DATABASES = {
    "default": dj_database_url.parse(
        env("DATABASE_URL", required=True),  # noqa: F405
        conn_max_age=600,
        conn_health_checks=True,
    )
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL", "redis://127.0.0.1:6379/1"),  # noqa: F405
    }
}

# ------------------------------------------------------------------- security

SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
X_FRAME_OPTIONS = "DENY"

# Admin lives somewhere non-obvious. Set this in the environment.
ADMIN_URL = env("ADMIN_URL", "manage-a8f3c1/")  # noqa: F405

# --------------------------------------------------------------------- media
# Cloudflare R2 (S3 compatible). Zero egress fees, which is the whole point:
# image bandwidth is the cost that would otherwise sink this project.

STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env("R2_BUCKET", required=True),  # noqa: F405
            "endpoint_url": env("R2_ENDPOINT", required=True),  # noqa: F405
            "access_key": env("R2_ACCESS_KEY", required=True),  # noqa: F405
            "secret_key": env("R2_SECRET_KEY", required=True),  # noqa: F405
            "default_acl": None,
            "querystring_auth": False,
            "file_overwrite": False,
        },
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage",
    },
    # Identity documents go to a SEPARATE private bucket. No public access, no
    # unsigned URLs, and nothing on the site ever renders a link to one —
    # reviewers read them through the staff-only streaming view, which is also
    # the only place a read can be logged. See apps/accounts/storages.py.
    #
    # Make this a genuinely different bucket with public access switched off at
    # the provider. Pointing R2_PRIVATE_BUCKET at the media bucket would undo
    # the whole arrangement silently.
    "kyc": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env("R2_PRIVATE_BUCKET", required=True),  # noqa: F405
            "endpoint_url": env("R2_ENDPOINT", required=True),  # noqa: F405
            "access_key": env("R2_ACCESS_KEY", required=True),  # noqa: F405
            "secret_key": env("R2_SECRET_KEY", required=True),  # noqa: F405
            "default_acl": "private",
            "querystring_auth": True,
            "querystring_expire": 300,
            "file_overwrite": False,
        },
    },
}

# The one misconfiguration that would silently undo the whole arrangement:
# pointing the private bucket at the public one. Everything would keep working,
# and identity documents would be sitting in a bucket served without
# authentication. Fail at boot instead.
if env("R2_PRIVATE_BUCKET", required=True) == env("R2_BUCKET", required=True):  # noqa: F405
    raise RuntimeError(
        "R2_PRIVATE_BUCKET must be a different bucket from R2_BUCKET. The media "
        "bucket is public by design; identity documents cannot live in it."
    )


# ------------------------------------------------------------------ services

# Transactional email. Brevo (300/day) and Resend (3,000/month) both have
# permanent free tiers that cover launch comfortably; Amazon SES is cheapest
# once you outgrow them. SendGrid retired its free plan in 2025.
#
# Whichever you pick, configure SPF, DKIM and DMARC on the sending domain and
# send from a subdomain such as mail.trusthailer.co.za. Without that, sign-in
# codes land in spam and the user simply leaves without telling you.
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("EMAIL_HOST", "")  # noqa: F405
EMAIL_PORT = int(env("EMAIL_PORT", 587))  # noqa: F405
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")  # noqa: F405
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")  # noqa: F405
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "no-reply@example.co.za")  # noqa: F405
