"""
Who may write to whom, and whether their numbers survive the trip.

Both questions are answered here and nowhere else. The view asks, the profile
button asks, and the tests ask; a rule enforced at one call site is a rule the
next call site skips.
"""
from django.db.models import Q
from django.utils import timezone

from apps.core.redact import redact_contacts
from apps.safety.models import is_blocked_between

from .models import Message, Thread


class CannotMessage(Exception):
    """Raised when a thread is refused. The message is safe to show."""


def can_message(sender, recipient):
    """
    True when these two already have a reason to be talking.

    THE THREE WAYS IN, AND WHAT THEY HAVE IN COMMON
    -----------------------------------------------
    An approved introduction, a confirmed placement, or a follow in both
    directions. Every one of them took a deliberate act by the RECIPIENT —
    approving, confirming, following back. None can be manufactured by the
    sender alone, which is the only property that makes this a gate rather
    than a formality.
    """
    if not getattr(sender, "is_authenticated", False):
        return False
    if sender.pk == recipient.pk:
        return False
    if not sender.can_participate or not recipient.is_active:
        return False
    if is_blocked_between(sender, recipient):
        return False
    return bool(
        _approved_intro(sender, recipient)
        or _confirmed_placement(sender, recipient)
        or _mutual_follow(sender, recipient)
    )


def why_not(sender, recipient):
    """A sentence for the interface when `can_message` says no."""
    if getattr(sender, "is_authenticated", False) and sender.pk == recipient.pk:
        return "This is you."
    return (
        "You can message each other once you have been introduced, "
        "or once you follow each other."
    )


def numbers_allowed(one, other):
    """
    Whether a phone number survives being sent in this thread.

    True once an introduction between them has been approved: they already
    have each other's numbers at that point, and stripping them would be
    theatre. False otherwise, so a DM cannot become the shortcut past the gate
    that releasing a number is supposed to be.

    A confirmed placement counts too — it can only exist off an approved
    introduction, and where one was recorded without a linked intro the two
    people have demonstrably dealt with each other.
    """
    return bool(
        _approved_intro(one, other) or _confirmed_placement(one, other)
    )


def start(sender, recipient):
    """The thread for these two, refusing if they have no reason to have one."""
    if not can_message(sender, recipient):
        raise CannotMessage(why_not(sender, recipient))
    return Thread.between(sender, recipient)


def send(thread, sender, body):
    """
    Add a message, redacting it first where redaction applies.

    Re-checks permission rather than trusting the thread's existence: a block
    or an unfollow may have happened since it was created, and a thread is not
    a licence that outlives the reason it was granted.
    """
    recipient = thread.other_party(sender)
    if not can_message(sender, recipient):
        raise CannotMessage(why_not(sender, recipient))

    body = (body or "").strip()
    if not body:
        raise CannotMessage("Write something first.")

    if not numbers_allowed(sender, recipient):
        body = redact_contacts(body)

    message = Message.objects.create(thread=thread, sender=sender, body=body)
    Thread.objects.filter(pk=thread.pk).update(last_message_at=message.created_at)
    _notify(recipient, sender, thread)
    return message


def mark_read(thread, reader):
    """Mark the other person's messages in this thread as read. Returns how many."""
    return Message.objects.filter(
        thread=thread, read_at__isnull=True
    ).exclude(sender=reader).update(read_at=timezone.now())


def unread_count(user):
    """
    Unread messages across every thread, for the badge.

    One indexed query per authenticated request, the same bargain the
    notification badge already makes: a count that has to be asked for is a
    count some page eventually forgets to ask for.
    """
    if not getattr(user, "is_authenticated", False):
        return 0
    return (
        Message.objects.filter(
            Q(thread__user_a=user) | Q(thread__user_b=user),
            read_at__isnull=True,
        )
        .exclude(sender=user)
        .count()
    )


# ------------------------------------------------------------------ the gates


def _approved_intro(one, other):
    from apps.intros.models import IntroRequest

    return IntroRequest.objects.filter(
        Q(from_user=one, to_user=other) | Q(from_user=other, to_user=one),
        status=IntroRequest.Status.APPROVED,
    ).exists()


def _confirmed_placement(one, other):
    from apps.placements.models import Placement

    return (
        Placement.objects.confirmed()
        .filter(Q(owner=one, driver=other) | Q(owner=other, driver=one))
        .exists()
    )


def _mutual_follow(one, other):
    from apps.follows.models import Follow

    both = Follow.objects.filter(
        Q(follower=one, following=other) | Q(follower=other, following=one)
    ).count()
    return both == 2


def _notify(recipient, sender, thread):
    from apps.notifications.models import Notification
    from apps.notifications.services import notify

    notify(
        recipient=recipient,
        kind=Notification.Kind.NEW_MESSAGE,
        message=f"{sender.get_short_name() or 'Someone'} sent you a message",
        url=f"/messages/{thread.pk}/",
        actor=sender,
    )
