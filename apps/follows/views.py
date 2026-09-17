from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.decorators import require_participation
from apps.accounts.models import User
from apps.safety.models import is_blocked_between

from . import services
from .models import Follow

PER_PAGE = 30


@login_required
@require_participation
@require_POST
def toggle(request, handle):
    """
    Follow or unfollow, depending on where you already stand.

    One endpoint rather than two, for the same reason the reaction bar has one:
    the button's job is "make the opposite of this true", and splitting it into
    /follow/ and /unfollow/ means the client has to know which one applies and
    can be wrong about it after somebody else's tab has acted.
    """
    target = get_object_or_404(User, handle=handle, is_active=True)

    if services.is_following(request.user, target):
        services.unfollow(request.user, target)
    else:
        try:
            services.follow(request.user, target)
        except services.CannotFollow as exc:
            if request.headers.get("HX-Request"):
                # Re-render the button as it actually stands, rather than
                # leaving it showing a state the server just refused.
                return _button(request, target, error=str(exc))
            messages.error(request, str(exc))
            return redirect(target.get_absolute_url())

    if request.headers.get("HX-Request"):
        return _button(request, target)
    return redirect(target.get_absolute_url())


def followers(request, handle):
    """Everybody following this member."""
    return _people(request, handle, "followers")


def following(request, handle):
    """Everybody this member follows."""
    return _people(request, handle, "following")


def _people(request, handle, which):
    user = _visible_or_404(request, handle)

    if which == "followers":
        rows = Follow.objects.filter(following=user).select_related(
            "follower__profile", "follower__verification"
        )
        people = [row.follower for row in rows]
    else:
        rows = Follow.objects.filter(follower=user).select_related(
            "following__profile", "following__verification"
        )
        people = [row.following for row in rows]

    # A blocked account is not merely hidden from browse — it should not be
    # reachable through somebody else's follower list either.
    from apps.safety.models import blocked_user_ids

    hidden = set(blocked_user_ids(request.user))
    people = [p for p in people if p.pk not in hidden]

    page = Paginator(people, PER_PAGE).get_page(request.GET.get("page"))
    followed = set(Follow.objects.followed_ids(request.user))

    return render(
        request,
        "follows/people.html",
        {
            "profile_user": user,
            "which": which,
            "page": page,
            "followed": followed,
            "is_me": request.user.is_authenticated and request.user.pk == user.pk,
        },
    )


def _visible_or_404(request, handle):
    user = get_object_or_404(User, handle=handle, is_active=True)
    if request.user != user and is_blocked_between(request.user, user):
        raise Http404
    if (
        user.profile.hide_from_search
        and request.user != user
        and not request.user.is_staff
    ):
        raise Http404
    return user


def _button(request, target, error=""):
    from .templatetags.follow_tags import button_context

    context = button_context(request.user, target)
    context["error"] = error
    return render(request, "follows/_button.html", context)
