import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import F
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.decorators import require_participation, require_verified_phone
from apps.core import pricing

from .forms import ListingPhotoForm, VehicleFilterForm, VehicleListingForm
from .models import ListingPhoto, VehicleListing

logger = logging.getLogger(__name__)

PER_PAGE = 12


# ------------------------------------------------------------------- browse

def browse(request):
    """
    The car list. Filters live in the querystring so a search is shareable — an
    owner can paste "cars in Tembisa under R2500" into a WhatsApp group and it
    works for whoever taps it.
    """
    form = VehicleFilterForm(request.GET or None)
    queryset = form.apply(VehicleListing.objects.live().with_display_data())

    page = Paginator(queryset, PER_PAGE).get_page(request.GET.get("page"))
    context = {
        "form": form,
        "page": page,
        "total": page.paginator.count,
        "querystring": _querystring_without_page(request),
    }

    # HTMX swaps only the results, so filtering never reloads the page or the
    # images already on screen. On a metered connection that difference is real.
    if request.headers.get("HX-Request"):
        return render(request, "listings/_results.html", context)
    return render(request, "listings/browse.html", context)


def _querystring_without_page(request):
    params = request.GET.copy()
    params.pop("page", None)
    encoded = params.urlencode()
    return f"&{encoded}" if encoded else ""


# ------------------------------------------------------------------- detail

def detail(request, uuid):
    listing = get_object_or_404(VehicleListing.objects.with_display_data(), uuid=uuid)

    is_owner = request.user.is_authenticated and listing.owner_id == request.user.pk

    if not listing.is_live and not is_owner and not request.user.is_staff:
        raise Http404

    if not is_owner:
        # F() avoids a read-modify-write race between concurrent viewers, and
        # skips touching updated_at so a view doesn't look like an edit.
        VehicleListing.objects.filter(pk=listing.pk).update(view_count=F("view_count") + 1)

    return render(
        request,
        "listings/detail.html",
        {
            "listing": listing,
            "is_owner": is_owner,
            "photos": list(listing.photos.all()),
            "age_warnings": listing.platform_age_warnings(),
            "intro_price": pricing.price_for(pricing.Action.INTRO_REQUEST, user=request.user)
            if request.user.is_authenticated
            else None,
        },
    )


# --------------------------------------------------------------------- CRUD

@login_required
@require_participation
@require_verified_phone
@require_http_methods(["GET", "POST"])
def create(request):
    """
    Where deferred phone verification pays off: browsing needs nothing, but
    listing a car — the point at which a stranger might hand over keys — needs
    a verified number.
    """
    form = VehicleListingForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        listing = form.save(commit=False)
        listing.owner = request.user
        listing.status = VehicleListing.Status.DRAFT
        listing.save()
        form.save_m2m()
        messages.success(request, "Saved. Add photos and then publish.")
        return redirect("listings:photos", uuid=listing.uuid)

    return render(request, "listings/form.html", {"form": form, "is_new": True})


@login_required
@require_participation
@require_http_methods(["GET", "POST"])
def edit(request, uuid):
    listing = _owned_or_404(request, uuid)
    form = VehicleListingForm(request.POST or None, instance=listing)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Listing updated.")
        return redirect(listing.get_absolute_url())
    return render(request, "listings/form.html", {"form": form, "listing": listing})


@login_required
@require_participation
@require_http_methods(["GET", "POST"])
def photos(request, uuid):
    listing = _owned_or_404(request, uuid)
    form = ListingPhotoForm(request.POST or None, request.FILES or None, listing=listing)

    if request.method == "POST" and form.is_valid():
        created = form.save()
        messages.success(request, f"{len(created)} photo{'s' if len(created) > 1 else ''} added.")
        return redirect("listings:photos", uuid=listing.uuid)

    return render(
        request,
        "listings/photos.html",
        {
            "listing": listing,
            "form": form,
            "photos": list(listing.photos.all()),
            "max_photos": ListingPhoto.MAX_PER_LISTING,
        },
    )


@login_required
@require_POST
def photo_delete(request, uuid, photo_id):
    listing = _owned_or_404(request, uuid)
    photo = get_object_or_404(ListingPhoto, pk=photo_id, listing=listing)
    photo.delete()
    messages.success(request, "Photo removed.")
    return redirect("listings:photos", uuid=listing.uuid)


@login_required
@require_POST
def photo_primary(request, uuid, photo_id):
    listing = _owned_or_404(request, uuid)
    photo = get_object_or_404(ListingPhoto, pk=photo_id, listing=listing)
    photo.is_primary = True
    photo.save(update_fields=["is_primary"])
    messages.success(request, "Cover photo set.")
    return redirect("listings:photos", uuid=listing.uuid)


@login_required
@require_POST
def photo_reorder(request, uuid):
    """HTMX/JS endpoint: accepts an ordered list of photo ids."""
    listing = _owned_or_404(request, uuid)
    ids = request.POST.getlist("order[]") or request.POST.getlist("order")
    owned = {str(p.pk): p for p in listing.photos.all()}
    position = 0
    for raw_id in ids:
        photo = owned.get(str(raw_id))
        if photo:
            photo.order = position
            photo.save(update_fields=["order"])
            position += 1
    return JsonResponse({"ok": True, "count": position})


@login_required
@require_POST
def set_status(request, uuid):
    """
    Publish, pause, mark placed, archive.

    Publishing is blocked without a photo. A listing with no picture gets
    ignored, and one ignored listing teaches an owner the whole site doesn't
    work — better to stop them at the gate with a reason.
    """
    listing = _owned_or_404(request, uuid)
    target = request.POST.get("status")
    valid = {s.value for s in VehicleListing.Status}
    if target not in valid:
        messages.error(request, "Unknown status.")
        return redirect(listing.get_absolute_url())

    if target == VehicleListing.Status.ACTIVE and not listing.photos.exists():
        messages.error(request, "Add at least one photo before publishing.")
        return redirect("listings:photos", uuid=listing.uuid)

    listing.status = target
    listing.save(update_fields=["status", "published_at", "updated_at"])

    labels = {
        VehicleListing.Status.ACTIVE: "Your listing is live.",
        VehicleListing.Status.PAUSED: "Listing paused — nobody can see it now.",
        VehicleListing.Status.PLACED: "Marked as placed. Nice one.",
        VehicleListing.Status.ARCHIVED: "Listing archived.",
        VehicleListing.Status.DRAFT: "Moved back to draft.",
    }
    messages.success(request, labels.get(target, "Updated."))
    return redirect(listing.get_absolute_url())


@login_required
def my_listings(request):
    listings = (
        VehicleListing.objects.filter(owner=request.user)
        .with_display_data()
        .order_by("-created_at")
    )
    return render(request, "listings/mine.html", {"listings": listings})


@login_required
@require_POST
def boost(request, uuid):
    """
    Free while MONETISATION_ENABLED is off, but the code path is the real one:
    it asks pricing for a price and would debit a wallet if there were one.
    Turning boosts into revenue later is a config change, not a rewrite.
    """
    listing = _owned_or_404(request, uuid)
    price = pricing.price_for(pricing.Action.LISTING_BOOST, user=request.user)

    if price.is_chargeable:
        # Sprint 4 wires the wallet in here. Until then the branch is
        # unreachable, and that is deliberate — the shape is already correct.
        messages.info(request, "Paid boosts aren't switched on yet.")
        return redirect(listing.get_absolute_url())

    listing.boost_expires_at = timezone.now() + timedelta(days=7)
    listing.save(update_fields=["boost_expires_at", "updated_at"])
    messages.success(request, "Boosted to the top of your suburb for 7 days. " + price.reason)
    return redirect(listing.get_absolute_url())


def _owned_or_404(request, uuid):
    return get_object_or_404(VehicleListing, uuid=uuid, owner=request.user)
