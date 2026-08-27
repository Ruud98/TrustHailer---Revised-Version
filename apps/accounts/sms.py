"""
SMS IS NO LONGER ON THE SIGNUP PATH. Email carries account creation and login,
because email is free and SMS is roughly R0.25 a message — an SMS-at-signup
design bills you for every visitor who never returns.

What is left here runs only for phone verification, and only once
PHONE_VERIFICATION_CHANNEL is switched from "manual" to "sms". Until then no
SMS is sent at all: staff confirm numbers over WhatsApp from the admin queue.

Set SMS_BACKEND in settings to a dotted path. Swapping providers should never
require touching a view.
"""
import logging

from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)


class SMSError(Exception):
    pass


class BaseSMSBackend:
    def send(self, to: str, body: str) -> bool:
        raise NotImplementedError


class ConsoleSMSBackend(BaseSMSBackend):
    """Development. Prints the message so you can read the OTP in your terminal."""

    def send(self, to: str, body: str) -> bool:
        print("\n" + "=" * 56)
        print(f"  SMS -> {to}")
        print(f"  {body}")
        print("=" * 56 + "\n")
        return True


class MemorySMSBackend(BaseSMSBackend):
    """Tests. Messages accumulate in `MemorySMSBackend.outbox`."""

    outbox: list = []

    def send(self, to: str, body: str) -> bool:
        MemorySMSBackend.outbox.append({"to": to, "body": body})
        return True

    @classmethod
    def clear(cls):
        cls.outbox = []

    @classmethod
    def last(cls):
        return cls.outbox[-1] if cls.outbox else None


class ClickatellSMSBackend(BaseSMSBackend):
    """
    Production stub for Clickatell's one-way REST API.

    Left unimplemented on purpose: fill this in against the provider's current
    docs when you have credentials, rather than trusting a snippet. Keep the
    `send()` signature and everything else keeps working.
    """

    def send(self, to: str, body: str) -> bool:
        api_key = getattr(settings, "CLICKATELL_API_KEY", "")
        if not api_key:
            raise SMSError("CLICKATELL_API_KEY is not configured.")
        raise NotImplementedError(
            "Implement ClickatellSMSBackend.send() against the provider's current API. "
            "POST the message, treat any non-2xx as a failure, log the provider "
            "message id, and return True only on confirmed acceptance."
        )


_cached_backend = None


def get_backend():
    global _cached_backend
    path = settings.SMS_BACKEND
    if _cached_backend is None or _cached_backend.__class__.__module__ + "." + \
            _cached_backend.__class__.__name__ != path:
        _cached_backend = import_string(path)()
    return _cached_backend


def send_sms(to: str, body: str) -> bool:
    """
    Send one message. Never raises on provider failure — logs and returns False,
    so a flaky provider produces a retry prompt rather than a 500 page.
    """
    try:
        return get_backend().send(to, body)
    except NotImplementedError:
        raise
    except Exception:
        logger.exception("SMS delivery failed to %s", to)
        return False


def send_otp(to: str, code: str) -> bool:
    body = (
        f"{code} is your {settings.SITE_NAME} code. "
        f"It expires in {settings.OTP_TTL_SECONDS // 60} minutes. "
        f"Never share it with anyone."
    )
    return send_sms(to, body)
