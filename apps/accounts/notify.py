"""
Code delivery, over whichever channel a flow calls for.

COST MODEL
----------
Email is the default everywhere because it is free at our volumes. Several
providers have permanent free transactional tiers that comfortably cover
launch — Brevo at 300/day and Resend at 3,000/month are the usual picks, and
Amazon SES is cheapest once volume outgrows a free tier. SendGrid dropped its
free plan in 2025, so don't reach for it out of habit.

SMS is reserved for `Purpose.PHONE`: verifying a number at the point it starts
to matter (listing a car, approving an introduction). That is a small fraction
of signups, which keeps the SMS bill proportional to activity rather than to
curiosity.

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
from .sms import send_sms

logger = logging.getLogger(__name__)


class DeliveryError(Exception):
    pass


def send_code(challenge: OTPChallenge, raw_code: str) -> bool:
    """Dispatch a code over the channel its challenge specifies."""
    if challenge.channel == OTPChallenge.Channel.SMS:
        return _send_code_sms(challenge.destination, raw_code)
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


def _send_code_sms(number: str, code: str) -> bool:
    minutes = settings.OTP_TTL_SECONDS // 60
    body = (
        f"{code} is your {settings.SITE_NAME} code. "
        f"It expires in {minutes} minutes. Never share it with anyone."
    )
    return send_sms(number, body)


def sms_spend_estimate(days: int = 30) -> dict:
    """
    What SMS has cost over a window. Handy in the admin, and a useful reality
    check on whether deferred verification is doing its job.
    """
    from datetime import timedelta

    from django.utils import timezone

    since = timezone.now() - timedelta(days=days)
    count = OTPChallenge.objects.filter(
        channel=OTPChallenge.Channel.SMS, created_at__gte=since
    ).count()
    rate = getattr(settings, "SMS_UNIT_COST", 0.25)
    return {"days": days, "messages": count, "estimated_cost": round(count * rate, 2)}
