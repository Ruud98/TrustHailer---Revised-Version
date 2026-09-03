"""Settings used by the test suite."""
import tempfile

from .dev import *  # noqa: F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
SMS_BACKEND = "apps.accounts.sms.MemorySMSBackend"
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
DATABASES["default"]["NAME"] = ":memory:"  # noqa: F405

# Uploads go to a throwaway directory. Several tests save real images — car
# photos, avatars, rating screenshots — and pointed at the dev MEDIA_ROOT they
# leave a pile of orphaned WebP behind on every run. It also means a test that
# asserts a file was deleted is reading a directory nothing else wrote to.
MEDIA_ROOT = tempfile.mkdtemp(prefix="trusthailer-test-media-")

# Identity documents go somewhere throwaway too, and somewhere separate from
# MEDIA_ROOT — a test that asserts a document is not reachable under /media/
# has to be reading a directory that genuinely is not MEDIA_ROOT.
KYC_ROOT = tempfile.mkdtemp(prefix="trusthailer-test-kyc-")
STORAGES = {  # noqa: F405
    **STORAGES,  # noqa: F405
    "kyc": {
        "BACKEND": "apps.accounts.storages.PrivateFileSystemStorage",
        "OPTIONS": {"location": KYC_ROOT},
    },
}
