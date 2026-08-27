"""Settings used by the test suite."""
from .dev import *  # noqa: F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
SMS_BACKEND = "apps.accounts.sms.MemorySMSBackend"
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
DATABASES["default"]["NAME"] = ":memory:"  # noqa: F405
