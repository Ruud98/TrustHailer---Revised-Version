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
    return Notification.objects.create(
        recipient=recipient, actor=actor, kind=kind, message=message, url=url
    )
