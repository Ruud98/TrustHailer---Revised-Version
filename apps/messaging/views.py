import logging

from django.contrib import messages as flash
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, models
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods

from apps.accounts.decorators import require_participation
from apps.accounts.models import User
from apps.listings.models import DriverListing, VehicleListing
from apps.notifications.models import Notification
from apps.notifications.services import notify as in_app_notify
from apps.placements.models import Placement

from . import services
from .models import Message, Thread

logger = logging.getLogger(__name__)


@login_required
def inbox(request):
    """Every conversation, newest first."""
    threads = list(
        Thread.objects.involving(request.user)
        .with_display_data()
        .filter(last_message_at__isnull=False)
    )

    # Unread per thread and the last line, from the prefetch rather than a
    # query each. An inbox that costs two queries per row is an inbox that
    # gets slower the more it is used.
    for thread in threads:
        loaded = list(thread.messages.all())
        thread.latest = loaded[-1] if loaded else None
        thread.unread = sum(
            1 for m in loaded if m.read_at is None and m.sender_id != request.user.pk
        )
        thread.other = thread.other_party(request.user)

    return render(request, "messaging/inbox.html", {"threads": threads})


@login_required
@require_participation
@require_http_methods(["GET", "POST"])
def thread(request, pk):
    """
    One conversation.

    Opening it marks the other person's messages read — the same moment
    Facebook does, and the only moment a "read" flag can honestly be set.
    """
    conversation = get_object_or_404(
        Thread.objects.with_display_data(), pk=pk
    )
    if not conversation.includes(request.user):
        # 404 rather than 403: whether a thread exists between two other people
        # is not something a stranger should be able to establish.
        raise Http404

    other = conversation.other_party(request.user)

    if request.method == "POST":
        try:
            services.send(conversation, request.user, request.POST.get("body"))
        except services.CannotMessage as exc:
            flash.error(request, str(exc))
        return redirect("messaging:thread", pk=conversation.pk)

    services.mark_read(conversation, request.user)
    interests = services.interests_in(conversation)

    return render(
        request,
        "messaging/thread.html",
        {
            "thread": conversation,
            "other": other,
            "messages_list": list(
                conversation.messages.select_related("sender__profile")
            ),
            "can_write": services.can_message(request.user, other),
            "why_not": services.why_not(request.user, other),
            "numbers_allowed": services.numbers_allowed_from(conversation, request.user),
            "i_have_a_number": bool(request.user.phone),
            # What this conversation is about, and where it has got to.
            "interests": interests,
            **_working_together(conversation, request.user, other, interests),
        },
    )


def _working_together(conversation, user, other, interests=None):
    """
    The state of the deal, for the strip across the top of the chat.

    Four states and they are mutually exclusive: nothing recorded, waiting on
    them, waiting on me, done. The owner is the only one offered the button
    that creates the record, because the owner is the one who hands over a car
    and therefore the one who knows it happened.
    """
    from apps.placements.models import Placement

    placement = (
        Placement.objects.involving(user)
        .filter(models.Q(owner=other) | models.Q(driver=other))
        .filter(ended_on__isnull=True)
        .select_related("vehicle_listing")
        .order_by("-started_on")
        .first()
    )

    if interests is None:
        interests = services.interests_in(conversation)

    car = next(
        (i.vehicle_listing for i in interests
         if i.vehicle_listing_id and i.vehicle_listing.owner_id == user.pk),
        None,
    )

    return {
        "placement": placement,
        # Computed here rather than asked of the template, which cannot pass
        # `user` to `confirmed_by`.
        "placement_needs_me": bool(placement and not placement.confirmed_by(user)),
        # Offered only to the owner of a car this thread is actually about.
        # Without a car there is nothing to record against, and a button that
        # asks "which one?" at this moment is a button nobody presses.
        "can_start_placement": placement is None and car is not None,
        "placement_car": car,
    }


@login_required
@require_participation
@require_POST
def start(request, handle):
    """Open — or reopen — the thread with somebody, from their profile."""
    recipient = get_object_or_404(User, handle=handle, is_active=True)
    try:
        conversation = services.start(request.user, recipient)
    except services.CannotMessage as exc:
        flash.error(request, str(exc))
        return redirect(recipient.get_absolute_url())
    return redirect("messaging:thread", pk=conversation.pk)


@login_required
@require_participation
@require_POST
def interested(request, kind, uuid):
    """
    "I am interested" — open the conversation, already saying which listing.

    A driver reading an advert has one question and it is not "may I have your
    number". It is "is this still going". This gets them there in one tap, with
    the car named, which is the whole change: the old flow asked an owner to
    release a phone number to a stranger who had not yet said a word.
    """
    listing = _listing_or_404(kind, uuid)
    try:
        conversation, created = services.express_interest(request.user, listing)
    except services.CannotMessage as exc:
        flash.error(request, str(exc))
        return redirect(listing.get_absolute_url())

    if not created:
        flash.info(request, "You have already asked about this one.")
    return redirect("messaging:thread", pk=conversation.pk)


@login_required
@require_participation
@require_POST
def share_number(request, pk):
    """Hand over your own number, deliberately, in one conversation."""
    conversation = _mine_or_404(request, pk)
    try:
        services.share_number(conversation, request.user)
    except services.CannotMessage as exc:
        flash.error(request, str(exc))
    return redirect("messaging:thread", pk=conversation.pk)


@login_required
@require_participation
@require_POST
def start_placement(request, pk):
    """
    The owner says the driver has the car.

    ONE SIDE PRESSES IT, BOTH SIDES HAVE TO AGREE
    ---------------------------------------------
    This creates the record confirmed by the owner only. The driver confirms it
    themselves, from a button in this same thread. That is not ceremony: a
    `Review` exists only against a placement both people confirmed, and the
    entire value of the review data rests on one person being unable to invent
    a working relationship with a stranger. Letting the owner's tap confirm
    both sides would hand anybody with two accounts a review factory.
    """
    conversation = _mine_or_404(request, pk)
    other = conversation.other_party(request.user)

    context = _working_together(conversation, request.user, other)
    listing = context["placement_car"]
    if not context["can_start_placement"] or listing is None:
        flash.info(request, "There is nothing to record here yet.")
        return redirect("messaging:thread", pk=conversation.pk)

    placement = Placement.objects.create(
        vehicle_listing=listing,
        owner=request.user,
        driver=other,
        started_on=timezone.localdate(),
        confirmed_by_owner=True,
    )

    # The advert comes off the market. An owner who has placed somebody and
    # leaves it up wastes every driver who answers it.
    if listing.is_live:
        listing.status = VehicleListing.Status.PLACED
        listing.save(update_fields=["status", "updated_at"])

    # Said in the thread as well as in a notification, because the thread is
    # where they are both looking and a notification is a thing people miss.
    services.send(
        conversation, request.user,
        f"I have recorded that we started working together on the "
        f"{listing.title} today. Confirm it and we can both leave a review "
        f"at the end.",
    )
    in_app_notify(
        recipient=other,
        kind=Notification.Kind.PLACEMENT_CONFIRM,
        message=f"{request.user.get_short_name() or 'Someone'} says you started "
                f"working together on {listing.title} — confirm it",
        url=placement.get_absolute_url(),
        actor=request.user,
    )
    flash.success(request, "Recorded. Waiting for them to confirm.")
    return redirect("messaging:thread", pk=conversation.pk)


def _mine_or_404(request, pk):
    conversation = get_object_or_404(Thread, pk=pk)
    if not conversation.includes(request.user):
        raise Http404
    return conversation


def _listing_or_404(kind, uuid):
    model = VehicleListing if kind == "car" else DriverListing
    return get_object_or_404(model, uuid=uuid)
