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

    THE FOUR WAYS IN
    ----------------
    An interest in one of their listings, an approved introduction, a confirmed
    placement, or a follow in both directions.

    The last three took a deliberate act by the RECIPIENT — approving,
    confirming, following back. The first does not, and that is the deliberate
    change: publishing a listing IS the act. An advert nobody may answer is not
    an advert, and the old arrangement, where a driver had to ask permission
    before saying which car they meant, lost most of them at that step.

    The brake that replaces it is the one-interest-per-listing constraint, plus
    blocking, plus the fact that an owner who takes the advert down stops
    receiving new ones.
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
        _interest_between(sender, recipient)
        or _approved_intro(sender, recipient)
        or _confirmed_placement(sender, recipient)
        or _mutual_follow(sender, recipient)
    )


def why_not(sender, recipient):
    """A sentence for the interface when `can_message` says no."""
    if getattr(sender, "is_authenticated", False) and sender.pk == recipient.pk:
        return "This is you."
    return (
        "You can message each other once one of you has answered a listing, "
        "or once you follow each other."
    )


def numbers_allowed(one, other):
    """
    Whether these two already have each other's numbers by another route.

    True once an introduction between them has been approved, or a placement
    confirmed: they have the numbers at that point and stripping them would be
    theatre. Pair-level and historic — the live question is the one below.
    """
    return bool(
        _approved_intro(one, other) or _confirmed_placement(one, other)
    )


def numbers_allowed_from(thread, sender):
    """
    Whether a number typed by THIS person in THIS thread survives.

    Asked of the sender, not the pair, because a number belongs to the person
    who typed it. Somebody who has pressed Share my number has decided; the
    other person in the thread has not, and their number stays masked until
    they press it themselves.

    The old pair-level answer still counts, since by then both numbers are
    already across.
    """
    return bool(
        thread.number_shared_by(sender)
        or numbers_allowed(sender, thread.other_party(sender))
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

    if not numbers_allowed_from(thread, sender):
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



def share_number(thread, user):
    """
    Hand over your own number, in this conversation, on purpose.

    This is the consent that used to be an approve button on an introduction,
    moved to where somebody can actually make the decision: after a few
    messages, knowing who they are talking to. It sends the number as an
    ordinary message so it sits in the thread where it will be looked for, and
    it lifts redaction for this person in this thread from here on.

    Refuses when there is no number to share rather than sending the word
    "None", and says so.
    """
    if not user.phone:
        raise CannotMessage("Add your mobile number in Settings first.")

    recipient = thread.other_party(user)
    if not can_message(user, recipient):
        raise CannotMessage(why_not(user, recipient))

    thread.share_number(user)
    return send(thread, user, f"My number is {user.phone}. Give me a call.")


def express_interest(user, listing):
    """
    Answer a listing: open the thread, say which one, and tag it.

    Returns (thread, created). `created` is False when this person has already
    answered this listing — the button then just takes them back to the
    conversation rather than sending the same opening line twice, which is what
    the unique constraint would otherwise turn into an IntegrityError.
    """
    from .models import Interest, Thread

    other = _listing_person(listing)
    if other is None:
        raise CannotMessage("That listing has nobody to write to.")
    if not getattr(user, "is_authenticated", False):
        raise CannotMessage("Log in first.")
    if other.pk == user.pk:
        raise CannotMessage("This is your own listing.")
    if not user.can_participate:
        raise CannotMessage("Your account cannot send messages at the moment.")
    if not other.is_active or is_blocked_between(user, other):
        raise CannotMessage("You cannot message this person.")
    if not listing.is_live:
        raise CannotMessage("That listing is no longer open.")

    thread = Thread.between(user, other)
    field = "vehicle_listing" if _is_vehicle(listing) else "driver_listing"

    interest, created = Interest.objects.get_or_create(
        user=user, defaults={"thread": thread}, **{field: listing}
    )
    if created:
        # Sent as an ordinary message from them, not as a system note: the
        # owner is answering a person, and a thread that opens with machine
        # text invites a machine-shaped reply.
        send(thread, user, _opening_line(user, listing))
    return thread, created


def interests_in(thread):
    """Every listing this conversation has been about, newest first."""
    from .models import Interest

    return list(
        Interest.objects.filter(thread=thread).select_related(
            "user",
            "vehicle_listing__suburb__city",
            "vehicle_listing__owner",
            "driver_listing__home_suburb__city",
            "driver_listing__driver",
        )
    )


def _opening_line(user, listing):
    """
    The message the button writes.

    Deliberately short and deliberately editable-looking: it names the listing
    so the owner knows which advert this is, and then gets out of the way. A
    long generated pitch reads as a bot, and an owner who thinks they are
    talking to a bot does not reply.
    """
    name = user.get_short_name() or "Someone"
    if _is_vehicle(listing):
        where = listing.suburb.name if listing.suburb_id else None
        what = listing.title
        return (
            f"Hi, I am {name}. I am interested in your {what}"
            + (f" in {where}" if where else "")
            + ". Is it still available?"
        )
    return (
        f"Hi, I am {name}. I saw your driver listing"
        f' — "{listing.headline}" — and I have a car that might suit you.'
        " Are you still looking?"
    )


def _is_vehicle(listing):
    from apps.listings.models import VehicleListing

    return isinstance(listing, VehicleListing)


def _listing_person(listing):
    return getattr(listing, "owner", None) or getattr(listing, "driver", None)


# ------------------------------------------------------------------ the gates


def _interest_between(one, other):
    """
    Either of them answered a listing of the other's.

    Symmetric on purpose: the owner must be able to reply, and asking whose
    interest it was would make the reply the one message the gate blocked.
    """
    from .models import Interest

    return Interest.objects.filter(
        Q(thread__user_a=one, thread__user_b=other)
        | Q(thread__user_a=other, thread__user_b=one)
    ).exists()


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
