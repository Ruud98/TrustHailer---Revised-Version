"""
Puts the unread count on every page, cheaply.

One indexed COUNT query per authenticated request. That is the cost of the
badge existing at all — a page that has to remember to ask for it would
eventually have a page that forgot, and a stale badge is worse than a slightly
slow one. `unread(); .count()` hits `Notification`'s
`(recipient, is_read, -created_at)` index, so it stays cheap as the table
grows.
"""
from .models import Notification


def notifications(request):
    if not request.user.is_authenticated:
        return {}
    return {
        "unread_notification_count": (
            Notification.objects.for_user(request.user).unread().count()
        )
    }
