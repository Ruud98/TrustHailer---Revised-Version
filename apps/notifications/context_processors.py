"""
Puts the unread count on every page, cheaply.

One indexed COUNT query per authenticated request. That is the cost of the
badge existing at all — a page that has to remember to ask for it would
eventually have a page that forgot, and a stale badge is worse than a slightly
slow one. `unread(); .count()` hits `Notification`'s
`(recipient, is_read, -created_at)` index, so it stays cheap as the table
grows.
"""
from django.conf import settings

from .models import Notification


def notifications(request):
    if not request.user.is_authenticated:
        return {}
    return {
        "unread_notification_count": (
            Notification.objects.for_user(request.user).unread().count()
        ),
        # The public half of the push key pair, which base.html hands to
        # pwa.js. Public by definition — it ships inside the page, and its
        # whole purpose is for the browser to encrypt a subscription to it.
        # Empty when push is not configured, which is how the script knows
        # not to ask for permission it cannot use.
        "VAPID_PUBLIC_KEY": settings.VAPID_PUBLIC_KEY,
    }
