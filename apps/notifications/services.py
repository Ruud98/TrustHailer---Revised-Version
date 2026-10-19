"""
The one function everything else calls to raise a notification.

Deliberately thin. Every call site already knows the sentence it wants to show
— "Thabo asked about your Corolla", "Your claim was approved" — because it is
sitting right next to the code that just made that sentence true. Composing
the message here instead, generically, from a `kind` and a pile of objects,
would just move that knowledge sideways into a big if/elif nobody wants to
maintain. See `Notification` for why the message is plain text rather than
built from a stored relation.
"""
from django.conf import settings

from . import push
from .models import Notification


def notify(recipient, kind, message, *, url="", actor=None):
    """
    Raise one notification, or do nothing if there is nobody to tell.

    Silently no-ops rather than raising when `recipient` is missing or is the
    same person as `actor` — every call site would otherwise need its own
    "unless this is somebody notifying themselves" guard, and that guard is
    the same one line everywhere. An unclaimed imported listing has no owner
    to notify, and that is a normal state, not a bug to propagate.
    """
    if recipient is None or not getattr(recipient, "pk", None):
        return None
    if actor is not None and actor.pk == recipient.pk:
        return None
    notification = Notification.objects.create(
        recipient=recipient, actor=actor, kind=kind, message=message, url=url
    )

    # The push goes out from here rather than from each call site for the same
    # reason the guards above are here: every caller would otherwise repeat it,
    # and the one that forgot would be a notification that silently never
    # reached a phone. The row is already written, so a failure below costs the
    # lock-screen nudge and nothing else — see push.py on why none of this can
    # raise. The title is the site, not the kind: a lock screen has room for
    # one line and the sentence is more use than its category.
    push.send_to_user(
        recipient,
        title=settings.SITE_NAME,
        body=message,
        url=url,
        tag=kind,
    )
    return notification
