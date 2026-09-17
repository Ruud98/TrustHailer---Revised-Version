"""
The rules around following, in one place because three call sites need them.

Everything that creates or destroys a `Follow` goes through here: the view, the
block flow, and the tests. A rule enforced in the view only is a rule that the
next entry point quietly skips.
"""
import logging

from django.db import IntegrityError
from django.db.models import Q

from apps.safety.models import is_blocked_between

from .models import Follow

logger = logging.getLogger(__name__)


class CannotFollow(Exception):
    """Raised when a follow is refused. The message is safe to show."""


def follow(follower, following):
    """
    Start following somebody. Returns True if this call created the row.

    Refuses in exactly the cases where the row would be a lie: yourself, a
    suspended account, somebody hiding from search, or anybody there is a block
    with in either direction.
    """
    _check(follower, following)
    try:
        _, created = Follow.objects.get_or_create(
            follower=follower, following=following
        )
    except IntegrityError:
        # Double tap on a slow connection. The constraint is the guard.
        return False

    if created:
        logger.info("User %s followed %s", follower.pk, following.pk)
        _notify(follower, following)
    return created


def unfollow(follower, following):
    """
    Stop following. Returns True if a row was actually removed.

    Deliberately has none of `follow`'s checks: if a block or a suspension has
    happened since, stopping is still allowed. A rule that can trap somebody
    into following an account they want rid of is worse than no rule.
    """
    deleted, _ = Follow.objects.filter(
        follower=follower, following=following
    ).delete()
    return bool(deleted)


def drop_between(one, other):
    """
    Remove any follow in either direction.

    Called when somebody blocks somebody. A block that left the follow in place
    would keep feeding the blocked person's posts into the blocker's following
    feed, which is the opposite of what the button says it does.
    """
    return Follow.objects.filter(
        Q(follower=one, following=other) | Q(follower=other, following=one)
    ).delete()[0]


def is_following(follower, following):
    """
    Both sides are guarded, not just the left.

    `follows_you` asks this the other way round — does the person being looked
    at follow the viewer — which puts an AnonymousUser on the right of the
    query, where the ORM cannot coerce it to an id and raises. Checking one
    argument was enough until the second call site existed.
    """
    for side in (follower, following):
        if not getattr(side, "is_authenticated", False):
            return False
    return Follow.objects.filter(follower=follower, following=following).exists()


def counts(user):
    """`(followers, following)` for a profile page. Two COUNTs, no denormalising."""
    return (
        Follow.objects.filter(following=user).count(),
        Follow.objects.filter(follower=user).count(),
    )


def _check(follower, following):
    if not getattr(follower, "is_authenticated", False):
        raise CannotFollow("Sign in to follow people.")
    if follower.pk == following.pk:
        raise CannotFollow("You cannot follow yourself.")
    if not follower.can_participate:
        raise CannotFollow("Your account is suspended.")
    if not following.is_active:
        raise CannotFollow("That account is not available.")
    # Both directions. Somebody who blocked you should not find you in their
    # follower list, and you should not be able to put yourself there.
    if is_blocked_between(follower, following):
        raise CannotFollow("That account is not available.")
    # Hiding from search is a request not to be found and followed by
    # strangers, not merely to be left out of one listing page.
    profile = getattr(following, "profile", None)
    if profile is not None and profile.hide_from_search:
        raise CannotFollow("That account is not available.")


def _notify(follower, following):
    """
    Tell them, once, in the app only.

    No email. A follow costs the follower nothing and means nothing has to
    happen next, so it does not earn a message in somebody's inbox — the
    introduction and review notifications do, because those need an answer.
    """
    from apps.notifications.models import Notification
    from apps.notifications.services import notify

    notify(
        recipient=following,
        kind=Notification.Kind.NEW_FOLLOWER,
        message=f"{follower.get_short_name() or 'Someone'} started following you",
        url=follower.get_absolute_url(),
        actor=follower,
    )
