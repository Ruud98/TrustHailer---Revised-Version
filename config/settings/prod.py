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

# -------------------------------------------------------------------- cache
# Redis, or an in-process dictionary you had to ask for by name. Never a
# quiet third thing.
#
# This is NOT a convenience fallback. `apps/core/ratelimit.py` is cache-backed
# and sits on the login path — every OTP send and every resend goes through it
# — so a CACHES entry pointing at a Redis that is not running does not degrade
# anything gracefully. It raises a connection error inside the sign-in flow,
# and the site is unusable for everybody, with a traceback that talks about
# Redis rather than about logging in.
#
# Defaulting to localhost made that the behaviour of any deploy that had not
# set REDIS_URL, which on a container host is the normal state of affairs.
#
# WEAKENING IT IS A CHOICE, AND HAS TO BE MADE OUT LOUD
# The in-process fallback below is not something to arrive at by forgetting a
# variable. LocMemCache is per-process: with more than one gunicorn worker each
# holds its own counters, so a limit of twenty an hour becomes twenty an hour
# PER WORKER and nothing is shared between instances. Worse on a host that
# sleeps when idle — the process dies, and every counter resets with it, which
# on a free tier happens several times a day. Rate limiting in that state is
# close to decorative.
#
# What that actually exposes is narrower than it sounds, and worth knowing so
# the trade can be judged rather than guessed at. Brute-forcing a code is NOT
# on the list: an OTP dies after five wrong attempts and that counter lives in
# the database. What is on the list is abuse of the send endpoint — burning the
# daily email allowance, and getting a young sending domain flagged as a
# spammer. For a site where signing in IS an email, damaged deliverability is
# close to fatal and slow to repair.
#
# So: Redis, or an explicit flag saying you know. Never silence.
#
# This mirrors the R2 bucket assertion further down, for the same reason — a
# misconfiguration that leaves everything apparently working while a control
# that was supposed to be there is not. Fail at boot instead.
_redis_url = env("REDIS_URL", "")  # noqa: F405

if _redis_url:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": _redis_url,
        }
    }
elif env_bool("ALLOW_WEAK_RATE_LIMIT"):  # noqa: F405
    # Deliberate, and visible in whatever dashboard set it.
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "trusthailer-prod",
        }
    }
else:
    raise RuntimeError(
        "No REDIS_URL is set, so rate limiting would fall back to an "
        "in-process cache: per-worker counters, reset on every restart. "
        "apps/core/ratelimit.py sits on the login path, so this weakens the "
        "only thing standing between a stranger and your email reputation. "
        "Either set REDIS_URL (Upstash has a free tier), or set "
        "ALLOW_WEAK_RATE_LIMIT=1 to accept the above — which is reasonable for "
        "a closed beta on one small instance, and for nothing else."
    )

# --------------------------------------------------------------- static files
# Directly behind SecurityMiddleware, which is where WhiteNoise documents it
# belongs: the security redirects and HSTS still apply to a request for a
# stylesheet, while nothing further down the chain — sessions, auth, the
# onboarding funnel, the unread-count query — runs for a file that needs none
# of it.
MIDDLEWARE = (  # noqa: F405
    MIDDLEWARE[:1]  # noqa: F405
    + ["whitenoise.middleware.WhiteNoiseMiddleware"]
    + MIDDLEWARE[1:]  # noqa: F405
)

# A year, immutable. Safe only because the filenames carry a content hash, so a
# changed file is a different URL and nothing can be pinned to a stale copy.
WHITENOISE_MAX_AGE = 60 * 60 * 24 * 365

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
    # WhiteNoise's subclass of ManifestStaticFilesStorage: same content-hashed
    # filenames, plus a gzip and brotli copy of every compressible file written
    # at collectstatic time and served in preference to the original.
    #
    # The plain ManifestStaticFilesStorage that was here hashed the filenames
    # and wrote them to disk, and then NOTHING SERVED THAT DISK. gunicorn does
    # not serve static files, runserver's handler is development-only, and
    # there is no nginx in front. Every stylesheet, script and icon was a 404
    # the moment this was deployed anywhere — which also meant no service
    # worker, since `cache.addAll` rejects as a unit and one missing file in
    # the precache list means no worker installs at all.
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
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

# ----------------------------------------------------------------- app shell
# Every service-worker cache is named after this, and a worker deletes every
# cache not named for the current value when it activates. An env var somebody
# has to remember to bump on each deploy is one that will not get bumped, and
# the failure is invisible: the app keeps working while serving last month's
# stylesheet. So RELEASE wins if it is set, and otherwise the host's own commit
# SHA is used — Render exposes RENDER_GIT_COMMIT, and the same pattern extends
# to whatever else ends up hosting this.
PWA_VERSION = (
    env("RELEASE", "")  # noqa: F405
    or env("RENDER_GIT_COMMIT", "")  # noqa: F405
    or env("SOURCE_VERSION", "")  # noqa: F405  (Heroku and its lookalikes)
    or "prod"
)
