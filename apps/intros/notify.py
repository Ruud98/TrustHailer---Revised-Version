"""
Emails for the introduction flow.

NO PHONE NUMBER EVER GOES IN AN EMAIL
-------------------------------------
The approval mail says "they said yes, open the request" and stops there. Three
reasons, and the first is enough on its own:

* An inbox is not a safe place to put somebody else's number. Mail is forwarded,
  screenshotted, synced to a shared family tablet and left signed in on a phone
  that gets sold. The release is a decision made about one person; the email
  copy of it travels wherever the mailbox travels.
* We cannot withdraw it. A number on the site can be pulled if an account turns
  out to be a scammer; a number sitting in fifty inboxes cannot.
* It puts the record in one place. If somebody says they were never introduced,
  the request page is the answer.

Failures are logged and swallowed. An email provider having a bad afternoon
must never leave an approval half-applied — the number is released on the site
the moment the row is written, which is the part that actually matters.
"""
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


def _send(template: str, subject: str, to_user, context: dict) -> bool:
    if not to_user.email:
        return False

    context = {**context, "site_name": settings.SITE_NAME, "recipient": to_user}
    text = strip_tags(render_to_string(f"intros/email/{template}.txt", context)).strip()
    html = render_to_string(f"intros/email/{template}.html", context)

    try:
        message = EmailMultiAlternatives(
            subject=subject,
            body=text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[to_user.email],
            headers={"X-Entity-Ref-ID": "intro"},
        )
        message.attach_alternative(html, "text/html")
        return bool(message.send(fail_silently=False))
    except Exception:
        logger.exception("Intro email %s failed to user %s", template, to_user.pk)
        return False


def _url(intro):
    base = getattr(settings, "SITE_URL", "").rstrip("/")
    return f"{base}{reverse('intros:detail', args=[intro.uuid])}"


def notify_approved(intro):
    """They said yes. The numbers are on the site, not in here."""
    other = intro.to_user.get_short_name() or "They"
    return _send(
        "approved",
        f"{other} said yes — you can contact each other now",
        intro.from_user,
        {"intro": intro, "url": _url(intro)},
    )


def notify_declined(intro):
    return _send(
        "declined",
        "Your introduction request was declined",
        intro.from_user,
        {"intro": intro, "url": _url(intro)},
    )
