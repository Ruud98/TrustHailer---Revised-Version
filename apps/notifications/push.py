"""
Web push — the bell, on the lock screen.

WHY THIS IS A SECOND CHANNEL AND NOT A REPLACEMENT
---------------------------------------------------
`Notification` rows remain the record. A push is a nudge towards one: it can be
dismissed without being read, it can be refused at the operating system, the
subscription can expire silently, and on iOS it only exists at all once the app
has been added to the home screen. Anything that depends on a push having
arrived would be depending on a maybe. So the push carries the sentence that is
already stored, and the bell is still right when it did not.

WHY IT SENDS INLINE AND NOT THROUGH A QUEUE
--------------------------------------------
There is no worker process in this project yet, and adding Celery and a broker
to this sprint to post one HTTP request would be the largest piece of
infrastructure on the site existing for the smallest reason. One request to
each of a person's devices — realistically one or two — on the thread that
raised the notification is a few hundred milliseconds in the worst case, and
every failure is swallowed below.

When this does start to hurt, the shape of the fix is already here: everything
goes through `send_to_user`, so moving the body of `_deliver` onto a queue is a
change to this file and nowhere else. The signal to watch for is placement
confirmations, which fan out to several people at once.

WHY FAILURES ARE SWALLOWED
---------------------------
The caller is always in the middle of something that matters more: approving an
introduction, publishing a review, posting a message. A push service being slow
or down must never turn that into a 500 for the person who did the thing. The
two failures worth acting on — 404 and 410, meaning the subscription is dead —
are acted on by deleting the row, because a dead subscription retried forever
is the one failure mode that does not resolve itself.
"""
import json
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

# Four hours. The push service holds an undelivered message for this long while
# the phone is off or out of signal, then drops it. Long enough to survive a
# night with the phone face down, short enough that nobody is woken at 06:00 by
# a message about a car that was taken yesterday.
TTL_SECONDS = 4 * 60 * 60


def configured():
    """Whether there is a key pair to sign with at all."""
    return bool(settings.VAPID_PRIVATE_KEY and settings.VAPID_PUBLIC_KEY)


def send_to_user(user, *, title, body, url="", tag=""):
    """
    Push one notification to every device this person has subscribed.

    Returns the number of devices it reached, which is only ever used by tests
    and by the staff tooling — no caller should branch on it, because zero is a
    completely normal answer for somebody who has never granted permission.
    """
    if user is None or not getattr(user, "pk", None):
        return 0
    if not configured():
        # The common case in development and in any deploy that has not set the
        # keys. Debug rather than warning: it is a configuration choice, not a
        # fault, and at warning level it would be one line per notification.
        logger.debug("Web push skipped: no VAPID key pair configured.")
        return 0

    from .models import PushSubscription

    payload = json.dumps({
        "title": title,
        "body": body,
        "url": url or "/",
        # Without a tag every push stacks separately, so a busy thread becomes
        # a column of near-identical lines on the lock screen. Falling back to
        # the URL groups by the thing being talked about, which is the grouping
        # the recipient would have chosen.
        "tag": tag or url or "trusthailer",
    })

    sent = 0
    for subscription in PushSubscription.objects.filter(user=user):
        if _deliver(subscription, payload):
            sent += 1
    return sent


def _deliver(subscription, payload):
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        # pywebpush is in requirements.txt, but this import is deliberately not
        # at module scope: the rest of the notification system must keep
        # working on an environment where it failed to build, rather than
        # taking down every page that raises a notification.
        logger.warning("Web push skipped: pywebpush is not installed.")
        return False

    try:
        webpush(
            subscription_info=subscription.as_dict(),
            data=payload,
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": settings.VAPID_CONTACT},
            ttl=TTL_SECONDS,
        )
        return True
    except WebPushException as error:
        status = getattr(getattr(error, "response", None), "status_code", None)
        if status in (404, 410):
            # The browser threw the subscription away — uninstalled, site data
            # cleared, permission revoked. It will never work again, and this
            # is the only notice we get.
            subscription.delete()
            logger.info("Dropped dead push subscription %s.", subscription.pk)
        else:
            logger.warning("Web push failed (%s): %s", status, error)
        return False
    except Exception as error:                       # noqa: BLE001
        # Deliberately broad. See the module docstring: whatever went wrong
        # here, the thing the user actually did must still succeed.
        logger.warning("Web push failed: %s", error)
        return False
