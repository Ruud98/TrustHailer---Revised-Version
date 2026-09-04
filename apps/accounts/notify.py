"""
Code delivery.

COST MODEL
----------
Email is the only channel, and it is free at our volumes. Several providers
have permanent free transactional tiers that comfortably cover launch — Brevo
at 300/day and Resend at 3,000/month are the usual picks, and Amazon SES is
cheapest once volume outgrows a free tier. SendGrid dropped its free plan in
2025, so don't reach for it out of habit.

There was an SMS channel here, used only to verify a phone number. Phone
verification is gone, so the SMS bill is now zero and the code that produced it
has gone with it. If a future flow genuinely needs SMS, add it back knowingly
rather than inheriting it.

DELIVERABILITY IS THE REAL COST OF FREE
---------------------------------------
An SMS arrives. An email lands in spam unless SPF, DKIM and DMARC are set up on
the sending domain. A signup code in a spam folder is a user lost silently —
they don't complain, they just leave. Configure all three before launch and
send from a real subdomain (mail.trusthailer.co.za), never a free mailbox.
"""
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from .models import OTPChallenge

logger = logging.getLogger(__name__)


class DeliveryError(Exception):
    pass


def send_code(challenge: OTPChallenge, raw_code: str) -> bool:
    return _send_code_email(challenge.destination, raw_code, challenge.purpose)


def _send_code_email(address: str, code: str, purpose: str) -> bool:
    minutes = settings.OTP_TTL_SECONDS // 60
    subject = f"{code} is your {settings.SITE_NAME} code"

    context = {
        "code": code,
        "minutes": minutes,
        "site_name": settings.SITE_NAME,
        "purpose": purpose,
    }
    html = render_to_string("accounts/email/otp_code.html", context)
    text = strip_tags(render_to_string("accounts/email/otp_code.txt", context)).strip()

    try:
        message = EmailMultiAlternatives(
            subject=subject,
            body=text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[address],
            # Codes are transactional. Mail clients and providers treat a
            # missing List-Unsubscribe on transactional mail as normal; adding
            # one here would wrongly mark it as bulk.
            headers={"X-Entity-Ref-ID": "otp"},
        )
        message.attach_alternative(html, "text/html")
        sent = message.send(fail_silently=False)
        return bool(sent)
    except Exception:
        logger.exception("Email delivery failed to %s", address)
        return False
