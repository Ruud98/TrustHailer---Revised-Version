import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods

from apps.accounts.decorators import require_participation, require_verified_phone
from apps.core import pricing
from apps.listings.models import DriverListing, VehicleListing
from apps.notifications.models import Notification
from apps.notifications.services import notify as in_app_notify
from apps.safety.models import is_blocked_between

from . import notify
from .forms import IntroRequestForm
from .models import IntroRequest

logger = logging.getLogger(__name__)

PER_PAGE = 20


@login_required
def inbox(request):
    """
    Received and sent, as two tabs over one queryset.

    Received is the default because it is the tab with something to do in it.
    A sent request needs nothing from you but patience.
    """
    tab = "sent" if request.GET.get("tab") == "sent" else "received"
    field = "from_user" if tab == "sent" else "to_user"

    queryset = (
        IntroRequest.objects.filter(**{field: request.user})
        .with_display_data()
        .order_by("-created_at")
    )
    page = Paginator(queryset, PER_PAGE).get_page(request.GET.get("page"))

    return render(
        request,
        "intros/inbox.html",
        {
            "tab": tab,
            "page": page,
            "waiting": IntroRequest.objects.filter(to_user=request.user)
            .pending()
            .exclude(expires_at__lt=timezone.now())
            .count(),
        },
    )


@login_required
@require_participation
@require_verified_phone
@require_http_methods(["GET", "POST"])
def create(request):
    """
    Ask to be put in touch, about one specific listing.

    WHY A VERIFIED PHONE IS NEEDED TO *ASK*, NOT ONLY TO APPROVE
    ------------------------------------------------------------
    Approval releases both numbers at once. If the asker has no verified
    number, approving gives them the owner's number and gives the owner
    nothing — a one-way release, which is precisely the shape we refuse to
    build. Verifying the asker up front is what keeps the exchange even.

    This extends the rule the README states — phone verification at listing a
    car and approving an introduction — to the other half of the same moment.
    Browsing, posting and listing yourself as a driver stay open, so the funnel
    is still not walled.
    """
    listing, to_user = _target_from_query(request)

    if to_user.pk == request.user.pk:
        messages.info(request, "That is your own listing.")
        return redirect(listing.get_absolute_url())

    if not to_user.can_participate:
        messages.error(request, "That account is not available right now.")
        return redirect(listing.get_absolute_url())

    if is_blocked_between(request.user, to_user):
        # Deliberately the same wording in both directions. Telling somebody
        # they have been blocked turns a quiet exit into a confrontation, which
        # is the thing the person blocking was trying to avoid.
        raise Http404

    existing = _open_request_for(request.user, listing)
    if existing:
        messages.info(request, "You have already asked. They have not answered yet.")
        return redirect(existing.get_absolute_url())

    form = IntroRequestForm(
        request.POST or None, from_user=request.user, to_user=to_user, listing=listing
    )
    if request.method == "POST" and form.is_valid():
        try:
            intro = form.save()
        except IntegrityError:
            # Two taps on a slow connection. The constraint is the real guard;
            # this just turns it into a sentence rather than a 500.
            messages.info(request, "That request is already in.")
            return redirect(listing.get_absolute_url())

        notify.notify_requested(intro)
        in_app_notify(
            recipient=intro.to_user,
            kind=Notification.Kind.INTRO_REQUESTED,
            message=f"{intro.from_user.get_short_name() or 'Someone'} asked about "
                    f"{_listing_label(intro)}",
            url=intro.get_absolute_url(),
            actor=intro.from_user,
        )
        logger.info("Intro %s requested by %s", intro.uuid, request.user.pk)
        messages.success(
            request,
            f"Sent. They have {IntroRequest.EXPIRY_DAYS} days to answer, and you will "
            "get an email either way.",
        )
        return redirect(intro.get_absolute_url())

    return render(
        request,
        "intros/new.html",
        {
            "form": form,
            "listing": listing,
            "to_user": to_user,
            "is_car": isinstance(listing, VehicleListing),
            "price": pricing.price_for(pricing.Action.INTRO_REQUEST, user=request.user),
        },
    )


@login_required
def detail(request, uuid):
    """
    One request, from whichever side is looking.

    This is the only page on the site where an unmasked number appears, and
    only once both people have agreed to it. Staff are deliberately not given
    a way in here: an approved introduction is two people's private contact
    details, and there is no support question that needs them.
    """
    intro = get_object_or_404(IntroRequest.objects.with_display_data(), uuid=uuid)
    if not intro.involves(request.user):
        raise Http404

    is_recipient = intro.to_user_id == request.user.pk
    return render(
        request,
        "intros/detail.html",
        {
            "intro": intro,
            "is_recipient": is_recipient,
            "other": intro.other_party(request.user),
            "listing": intro.listing,
            "can_answer": is_recipient and intro.is_open,
            "can_withdraw": not is_recipient and intro.is_open,
        },
    )


@login_required
@require_participation
@require_verified_phone
@require_POST
def approve(request, uuid):
    """
    Say yes. Both numbers become visible to both people, at the same moment.
    """
    intro = _mine_or_404(request, uuid, as_recipient=True)

    if not intro.is_open:
        messages.info(request, "That request is closed.")
        return redirect(intro.get_absolute_url())

    intro.approve(by=request.user)
    notify.notify_approved(intro)
    in_app_notify(
        recipient=intro.from_user,
        kind=Notification.Kind.INTRO_APPROVED,
        message=f"{intro.to_user.get_short_name() or 'They'} said yes — "
                "you can contact each other now",
        url=intro.get_absolute_url(),
        actor=intro.to_user,
    )
    logger.info("Intro %s approved by %s", intro.uuid, request.user.pk)
    messages.success(request, "Done — you can both see each other's number now.")
    return redirect(intro.get_absolute_url())


@login_required
@require_participation
@require_POST
def decline(request, uuid):
    """
    Say no. No reason is asked for.

    A required reason box turns a no into a confrontation, and the result is
    that people answer nothing at all — which leaves the asker waiting, and is
    worse for them than a plain no. Declining is not gated on phone
    verification either: saying no releases nothing, and putting a wall in
    front of it would only push people back to silence.
    """
    intro = _mine_or_404(request, uuid, as_recipient=True)

    if not intro.is_open:
        messages.info(request, "That request is closed.")
        return redirect(intro.get_absolute_url())

    intro.decline()
    notify.notify_declined(intro)
    in_app_notify(
        recipient=intro.from_user,
        kind=Notification.Kind.INTRO_DECLINED,
        message="Your introduction request was declined",
        url=intro.get_absolute_url(),
        actor=intro.to_user,
    )
    messages.success(request, "Declined. They have been told, without a reason.")
    return redirect(intro.get_absolute_url())


@login_required
@require_POST
def withdraw(request, uuid):
    """Take back a request you sent. Nobody is emailed about this."""
    intro = _mine_or_404(request, uuid, as_recipient=False)

    if not intro.is_open:
        messages.info(request, "That request is closed.")
        return redirect(intro.get_absolute_url())

    intro.withdraw()
    messages.success(request, "Withdrawn.")
    return redirect(intro.get_absolute_url())


# ------------------------------------------------------------------ helpers


def _listing_label(intro):
    """A short phrase for the thing an introduction is about, for a notification."""
    if intro.vehicle_listing_id:
        return f"your {intro.vehicle_listing.title}"
    return "your driver listing"


def _target_from_query(request):
    """
    Resolve ?car=<uuid> or ?driver=<uuid> into a listing and the person behind
    it, refusing anything a viewer could not have reached from a live page.
    """
    car_uuid = request.GET.get("car") or request.POST.get("car")
    driver_uuid = request.GET.get("driver") or request.POST.get("driver")

    if car_uuid:
        listing = get_object_or_404(
            VehicleListing.objects.select_related("owner", "suburb__city"), uuid=car_uuid
        )
        if not listing.is_live:
            raise Http404
        if not listing.has_contactable_owner:
            # An unclaimed imported advert. There is nobody on the site to
            # introduce anyone to, and the detail page says so — this is the
            # same rule enforced against a hand-typed URL.
            raise Http404
        return listing, listing.owner

    if driver_uuid:
        listing = get_object_or_404(
            DriverListing.objects.select_related("driver", "home_suburb__city"),
            uuid=driver_uuid,
        )
        if not listing.is_live or listing.driver.profile.hide_from_search:
            raise Http404
        return listing, listing.driver

    raise Http404


def _open_request_for(user, listing):
    field = "vehicle_listing" if isinstance(listing, VehicleListing) else "driver_listing"
    return (
        IntroRequest.objects.pending()
        .filter(from_user=user, **{field: listing})
        .exclude(expires_at__lt=timezone.now())
        .first()
    )


def _mine_or_404(request, uuid, *, as_recipient):
    field = "to_user" if as_recipient else "from_user"
    return get_object_or_404(IntroRequest, uuid=uuid, **{field: request.user})
