import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST, require_http_methods

from apps.accounts.decorators import require_participation
from apps.accounts.models import User
from apps.listings.models import VehicleListing
from apps.notifications.models import Notification
from apps.notifications.services import notify as in_app_notify

from .forms import EndPlacementForm, PlacementForm, ReviewForm
from .models import Placement, Review, publish_pair_if_ready

logger = logging.getLogger(__name__)


@login_required
def mine(request):
    """Every deal this person has been part of, on either side."""
    placements = (
        Placement.objects.involving(request.user).with_display_data()
    )
    return render(
        request,
        "placements/mine.html",
        {
            "placements": placements,
            "waiting": [
                placement for placement in placements
                if not placement.is_confirmed and not placement.confirmed_by(request.user)
            ],
        },
    )


@login_required
@require_participation
@require_http_methods(["GET", "POST"])
def create(request, uuid):
    """
    Record a placement against one of your cars, or against a car you drive.

    Reachable from the car page's "mark driver placed" action. The dropdown is
    limited to approved introductions on this listing — see `PlacementForm`,
    where that restriction is the whole security model.
    """
    listing = get_object_or_404(VehicleListing, uuid=uuid)

    form = PlacementForm(request.POST or None, user=request.user, listing=listing)
    if not form.fields["intro"].queryset.exists():
        messages.info(
            request,
            "You can only record a placement with somebody you were introduced to here. "
            "Approve an introduction first.",
        )
        return redirect(listing.get_absolute_url())

    if request.method == "POST" and form.is_valid():
        try:
            placement = form.save()
        except IntegrityError:
            messages.info(request, "That placement is already recorded.")
            return redirect(listing.get_absolute_url())

        # The listing comes off the market. An owner who has placed somebody
        # and leaves the ad up wastes every driver who answers it.
        if listing.owner_id == request.user.pk and listing.is_live:
            listing.status = VehicleListing.Status.PLACED
            listing.save(update_fields=["status", "updated_at"])

        other = placement.other_party(request.user)
        in_app_notify(
            recipient=other,
            kind=Notification.Kind.PLACEMENT_CONFIRM,
            message=f"{request.user.get_short_name() or 'Someone'} recorded a placement "
                    f"on {placement.vehicle_listing.title} — confirm it to unlock reviews",
            url=placement.get_absolute_url(),
            actor=request.user,
        )

        logger.info("Placement %s recorded by user %s", placement.uuid, request.user.pk)
        messages.success(
            request,
            "Recorded. Once they confirm it, you can both review each other.",
        )
        return redirect(placement.get_absolute_url())

    return render(request, "placements/new.html", {"form": form, "listing": listing})


@login_required
def detail(request, uuid):
    placement = _mine_or_404(request, uuid)
    my_review = placement.review_by(request.user)
    their_review = next(
        (r for r in placement.reviews.all() if r.author_id != request.user.pk), None
    )

    return render(
        request,
        "placements/detail.html",
        {
            "placement": placement,
            "other": placement.other_party(request.user),
            "is_owner": placement.is_owner(request.user),
            "needs_my_confirmation": not placement.confirmed_by(request.user),
            "can_review": placement.can_review(request.user),
            "my_review": my_review,
            # Only ever handed to the template once it is published. The blind
            # is enforced here, not by remembering to hide it in the markup.
            "their_review": their_review if their_review and their_review.is_published else None,
            "waiting_on_them": bool(my_review and not my_review.is_published),
        },
    )


@login_required
@require_participation
@require_POST
def confirm(request, uuid):
    """
    Agree that this happened.

    One person asserting a placement is an assertion; two is a fact, and it is
    the only thing between the review system and somebody inventing a working
    relationship to review a stranger.
    """
    placement = _mine_or_404(request, uuid)
    if placement.confirmed_by(request.user):
        messages.info(request, "You have already confirmed that one.")
        return redirect(placement.get_absolute_url())

    placement.confirm(request.user)

    if placement.is_confirmed:
        # Only the OTHER party is told — the one who confirmed already knows,
        # since they are the one looking at this response right now.
        in_app_notify(
            recipient=placement.other_party(request.user),
            kind=Notification.Kind.PLACEMENT_CONFIRMED,
            message=f"{request.user.get_short_name() or 'They'} confirmed the placement on "
                    f"{placement.vehicle_listing.title} — you can both review each other now",
            url=placement.get_absolute_url(),
            actor=request.user,
        )

    messages.success(
        request,
        "Confirmed. You can both write a review now."
        if placement.is_confirmed
        else "Confirmed. Waiting on them.",
    )
    return redirect(placement.get_absolute_url())


@login_required
@require_participation
@require_http_methods(["GET", "POST"])
def end(request, uuid):
    """Close it off. Either side can, and the reason is never published."""
    placement = _mine_or_404(request, uuid)
    if not placement.is_open:
        messages.info(request, "That one is already closed.")
        return redirect(placement.get_absolute_url())

    form = EndPlacementForm(request.POST or None, instance=placement, placement=placement)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(
            request,
            "Closed. If you have not reviewed each other yet, you still can.",
        )
        return redirect(placement.get_absolute_url())

    return render(request, "placements/end.html", {"form": form, "placement": placement})


@login_required
@require_participation
@require_http_methods(["GET", "POST"])
def review(request, uuid):
    """
    Write your side.

    Publication is handled here as well as by the nightly job: somebody who has
    just written the second review expects to see the first one immediately,
    and making them wait until 2am for a result the rules say is already due
    reads as broken.
    """
    placement = _mine_or_404(request, uuid)

    if not placement.is_confirmed:
        messages.info(request, "Both of you have to confirm the placement first.")
        return redirect(placement.get_absolute_url())
    if placement.review_by(request.user):
        messages.info(request, "You have already reviewed this one.")
        return redirect(placement.get_absolute_url())

    form = ReviewForm(request.POST or None, placement=placement, author=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except IntegrityError:
            messages.info(request, "You have already reviewed this one.")
            return redirect(placement.get_absolute_url())

        published = publish_pair_if_ready(placement)
        if published:
            messages.success(request, "Both reviews are up — you can see theirs now.")
        else:
            messages.success(
                request,
                f"Saved. It goes up when they write theirs, or in "
                f"{Review.BLIND_DAYS} days, whichever comes first.",
            )
        return redirect(placement.get_absolute_url())

    return render(
        request,
        "placements/review.html",
        {
            "form": form,
            "placement": placement,
            "other": placement.other_party(request.user),
            "reviewing_the_driver": placement.is_owner(request.user),
        },
    )


def reviews_for(request, handle):
    """
    Somebody's published reviews. Public, like the profile.

    Only published ones, and never the reviews this person wrote — a page of
    somebody's own opinions of other people is a different thing, and it turns
    a reputation page into a place to go looking for arguments.
    """
    user = get_object_or_404(User, handle=handle, is_active=True)

    from apps.safety.models import is_blocked_between

    if request.user != user and is_blocked_between(request.user, user):
        raise Http404
    if user.profile.hide_from_search and request.user != user and not request.user.is_staff:
        raise Http404

    reviews = (
        Review.objects.published()
        .filter(subject=user)
        .select_related("author__profile", "placement__vehicle_listing")
    )
    return render(
        request,
        "placements/reviews.html",
        {"profile_user": user, "profile": user.profile, "reviews": reviews},
    )


def _mine_or_404(request, uuid):
    placement = get_object_or_404(
        Placement.objects.with_display_data(), uuid=uuid
    )
    if request.user.pk not in (placement.owner_id, placement.driver_id):
        # Staff included. A placement is two people's business arrangement and
        # carries two unpublished reviews; there is no support question that
        # needs to read one before it publishes.
        raise Http404
    return placement
