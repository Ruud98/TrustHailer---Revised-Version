"""
Generate the VAPID key pair that push notifications are signed with.

WHAT THE PAIR IS FOR
---------------------
Push services (Google's for Chrome, Mozilla's for Firefox, Apple's for Safari)
will not accept an anonymous sender. VAPID is how we say who we are: the
browser is given the public half when it subscribes, bakes it into the
subscription, and from then on the push service only accepts messages signed
with the matching private half. It is not encryption of the message — that is
separate, and uses keys the browser generates per subscription.

WHICH MEANS THE PAIR IS PERMANENT
----------------------------------
Rotate the key and every existing subscription stops working at once, silently,
with no way to tell the devices concerned — their next push is simply refused
and the only repair is each person re-granting permission. Generate it once,
put it in the environment, and treat losing it the same way you would treat
losing the database password.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Generate a VAPID key pair for web push, and print it as env vars."

    def handle(self, *args, **options):
        try:
            from py_vapid import Vapid01
        except ImportError:
            # Shipped as a dependency of pywebpush. Checked here rather than
            # at import so that `manage.py help` still works on an environment
            # where the native build failed.
            raise SystemExit(
                "py-vapid is not installed. It comes with pywebpush:\n"
                "    pip install -r requirements.txt"
            )

        vapid = Vapid01()
        vapid.generate_keys()

        # Base64url, unpadded — the form the Push API expects, and the form
        # pywebpush hands back to the service. The public key is the exact
        # string that ends up in the page.
        public = _b64(vapid.public_key.public_bytes(
            _serialization().Encoding.X962,
            _serialization().PublicFormat.UncompressedPoint,
        ))
        private = _b64(vapid.private_key.private_numbers().private_value.to_bytes(32, "big"))

        self.stdout.write("Add these to your environment. Generate once, keep forever —")
        self.stdout.write("changing them silently breaks every existing subscription.\n")
        self.stdout.write(self.style.SUCCESS(f"VAPID_PUBLIC_KEY={public}"))
        self.stdout.write(self.style.SUCCESS(f"VAPID_PRIVATE_KEY={private}"))
        self.stdout.write(
            "VAPID_CONTACT=mailto:you@yourdomain.co.za   "
            "# a real address: push services use it to reach you"
        )


def _serialization():
    from cryptography.hazmat.primitives import serialization

    return serialization


def _b64(raw):
    import base64

    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
