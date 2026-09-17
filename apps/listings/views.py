import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import F
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.decorators import require_participation
from apps.core import pricing
from apps.safety.models import is_blocked_between

from .forms import (
    VehicleNoteForm,
    AdvertPasteForm,
    ClaimListingForm,
    DriverFilterForm,
    DriverListingForm,
    ImportedListingForm,
    ListingPhotoForm,
    RatingProofForm,
    SaveSearchForm,
    VehicleFilterForm,
    VehicleListingForm,
)
from .importer import parse_advert
from .models import (
    VehicleNote,
    DriverListing,
    ListingClaim,
    ListingPhoto,
    Platform,
    PlatformRatingProof,
    SavedSearch,
    VehicleListing,
)

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
    queryset = form.apply(
        VehicleListing.objects.live().hide_blocked(request.user).with_display_data()
    )

    page = Paginator(queryset, PER_PAGE).get_page(request.GET.get("page"))
    context = {
        "form": form,
        "page": page,
        "total": page.paginator.count,
        "querystring": _querystring_without_page(request),
        "can_save": request.user.is_authenticated,
    }

    # HTMX swaps only the results, so filtering never reloads the page or the
    # images already on screen. On a metered connection that difference is real.
    if request.headers.get("HX-Request"):
        return render(request, "listings/_results.html", context)
    return render(request, "listings/browse.html", context)


def _my_intro(user, **listing_filter):
    """
    The viewer's most recent request about this listing, if there is one.

    Imported here rather than at module scope: `apps.intros` imports listing
    models, so a top-level import would close the loop. The detail page needs
    it so the button can say "you have already asked" instead of inviting
    somebody to ask twice and meet a constraint error.
    """
    if not user.is_authenticated:
        return None

    from apps.intros.models import IntroRequest

    return (
        IntroRequest.objects.filter(from_user=user, **listing_filter)
        .order_by("-created_at")
        .first()
    )


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

    # A block has to hold on the detail page too, or the listing is still one
    # shared link away from the person you blocked.
    if listing.owner_id and not is_owner and is_blocked_between(request.user, listing.owner):
        raise Http404

    if not is_owner:
        # F() avoids a read-modify-write race between concurrent viewers, and
        # skips touching updated_at so a view doesn't look like an edit.
        VehicleListing.objects.filter(pk=listing.pk).update(view_count=F("view_count") + 1)

    my_claim = None
    if listing.is_imported and request.user.is_authenticated:
        my_claim = ListingClaim.objects.filter(
            listing=listing, claimant=request.user
        ).first()

    return render(
        request,
        "listings/detail.html",
        {
            "listing": listing,
            "is_owner": is_owner,
            "photos": list(listing.photos.all()),
            "age_warnings": listing.platform_age_warnings(),
            "my_claim": my_claim,
            "my_intro": _my_intro(request.user, vehicle_listing=listing),
            "intro_price": pricing.price_for(pricing.Action.INTRO_REQUEST, user=request.user)
            if request.user.is_authenticated
            else None,
        },
    )


# --------------------------------------------------------------------- CRUD

@login_required
@require_participation
@require_http_methods(["GET", "POST"])
def create(request):
    """Create a new car listing."""
    form = VehicleListingForm(request.POST or None, user=request.user)
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
    form = VehicleListingForm(request.POST or None, instance=listing, user=request.user)
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

    # Imported adverts are exempt: we deliberately do not copy the photos off
    # the original post, so requiring one would make every import unpublishable
    # and every claimed listing un-republishable. See `import_advert`.
    if (
        target == VehicleListing.Status.ACTIVE
        and not listing.is_imported
        and not listing.photos.exists()
    ):
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
    """
    The fleet. Every car, and who has it.

    AN OWNER WITH SIX CARS CANNOT HOLD THIS IN THEIR HEAD
    -----------------------------------------------------
    The page used to answer "what have I listed" — title, suburb, view count,
    status. The question an owner with a fleet actually has is "who has which
    car, and when was each one last seen to", and neither of those was on it.

    Both are answered in two extra queries for the whole page rather than two
    per car: one pass over the open placements and one over the newest service
    note per car. A fleet page that costs a query per car is a fleet page that
    gets slower the more successful its owner is.
    """
    listings = list(
        VehicleListing.objects.filter(owner=request.user)
        .with_display_data()
        .order_by("-created_at")
    )
    _attach_fleet_data(listings, request.user)
    return render(
        request,
        "listings/mine.html",
        {
            "listings": listings,
            "out_count": sum(1 for car in listings if car.current_driver),
        },
    )


def _attach_fleet_data(listings, owner):
    """Hang the current driver and the last service on each car, in two queries."""
    if not listings:
        return

    from apps.placements.models import Placement

    ids = [car.pk for car in listings]

    # Confirmed and unended. An unconfirmed placement is one person's claim,
    # and showing it as "out with X" would put somebody's name against a car on
    # nothing but an assertion.
    open_placements = {
        placement.vehicle_listing_id: placement
        for placement in Placement.objects.confirmed()
        .filter(vehicle_listing_id__in=ids, ended_on__isnull=True)
        .select_related("driver__profile")
    }

    # Newest service note per car. Ordered oldest-first so the dict ends up
    # holding the most recent one for each.
    last_service = {}
    for note in VehicleNote.objects.filter(
        listing_id__in=ids, kind=VehicleNote.Kind.SERVICE
    ).order_by("happened_on"):
        last_service[note.listing_id] = note

    for car in listings:
        placement = open_placements.get(car.pk)
        car.current_placement = placement
        car.current_driver = placement.driver if placement else None
        car.last_service = last_service.get(car.pk)


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


# ===========================================================================
#  Imported adverts
# ===========================================================================


@staff_member_required
@require_http_methods(["GET", "POST"])
def import_advert(request):
    """
    Staff-only. Paste a Facebook rental advert, check what we read out of it,
    publish it with a link back to the original.

    WHY THIS EXISTS AT ALL
    ----------------------
    Cold start, and nothing else. Two-sided marketplaces die on the empty side
    first: a driver who opens the cars page, finds eleven listings and leaves
    is not coming back next week to see whether it filled up. The adverts are
    already out there in the groups. Carrying them across by hand, credited and
    linked, gives the first drivers something worth scrolling while real owners
    are still being onboarded one at a time.

    It is a ladder to be pulled up. Every import is a listing we cannot arrange
    an introduction on, which is the actual product — so the measure of success
    here is imports falling as a share of the page, not rising.

    TWO STEPS, ON PURPOSE
    ---------------------
    Paste, then correct. `parse_advert` reads the paste and pre-fills the form;
    a person then fixes every field it got wrong before anything is written.
    Nothing is created on the first step, and the raw paste is never stored —
    it is shown beside the form on the second screen and then dropped.

    WHAT WE DO NOT COPY
    -------------------
    The photos, the wording, and the phone number. Photos and wording because
    they belong to whoever wrote the post; the number because they never agreed
    to it appearing here, and because releasing contacts is what the
    introduction flow is for. What we take is the facts, and facts are exactly
    what turns into a filter.
    """
    step = request.POST.get("step")

    if step == "review":
        form = ImportedListingForm(request.POST, imported_by=request.user)
        if form.is_valid():
            listing = form.save(commit=False)
            # Straight to live. An import sitting in draft helps nobody: there
            # is no owner to come back and publish it, and the photo rule that
            # justifies the draft step on the owner side does not apply here.
            listing.status = VehicleListing.Status.ACTIVE
            listing.save()
            form.save_m2m()
            messages.success(
                request,
                f"Imported and published. It archives itself in "
                f"{VehicleListing.IMPORT_STALE_DAYS} days unless somebody claims it.",
            )
            return redirect(listing.get_absolute_url())
        return render(
            request,
            "listings/import_review.html",
            {"form": form, "raw_text": request.POST.get("raw_text", "")},
        )

    paste_form = AdvertPasteForm(request.POST or None)
    if request.method == "POST" and paste_form.is_valid():
        raw_text = paste_form.cleaned_data["raw_text"]
        guess = parse_advert(raw_text)
        platform_slugs = guess.pop("platform_slugs", [])
        if platform_slugs:
            guess["platforms"] = list(
                Platform.objects.filter(slug__in=platform_slugs, is_active=True).values_list(
                    "pk", flat=True
                )
            )
        guess["source_url"] = paste_form.cleaned_data["source_url"]

        return render(
            request,
            "listings/import_review.html",
            {
                "form": ImportedListingForm(initial=guess, imported_by=request.user),
                "raw_text": raw_text,
                "guessed": sorted(guess.keys()),
            },
        )

    return render(request, "listings/import_paste.html", {"form": paste_form})


@staff_member_required
def import_queue(request):
    """What we have carried across, and who is asking to take one over."""
    imports = (
        VehicleListing.objects.imported()
        .with_display_data()
        .select_related("imported_by")
        .order_by("-created_at")[:60]
    )
    claims = (
        ListingClaim.objects.pending()
        .select_related("listing", "claimant__verification")
        .order_by("created_at")
    )
    return render(
        request,
        "listings/import_queue.html",
        {"imports": imports, "claims": claims, "stale_days": VehicleListing.IMPORT_STALE_DAYS},
    )


@login_required
@require_participation
@require_http_methods(["GET", "POST"])
def claim(request, uuid):
    """
    Somebody saying an imported advert is theirs.

    Nothing gates this beyond being a member in good standing. What stops a
    stranger walking off with somebody else's advert is not a check at the door
    but the decision at the end: a claim is a request, and a person reads it
    against the original post before it is granted — see `ListingClaim`.
    """
    listing = get_object_or_404(VehicleListing, uuid=uuid)

    if not listing.is_claimable:
        messages.info(request, "This listing is not open to claims.")
        return redirect(listing.get_absolute_url())

    form = ClaimListingForm(
        request.POST or None, listing=listing, claimant=request.user
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        logger.info("Claim opened on listing %s by user %s", listing.uuid, request.user.pk)
        messages.success(
            request,
            "Thanks — we will check it against the original post and come back to you.",
        )
        return redirect(listing.get_absolute_url())

    return render(request, "listings/claim.html", {"form": form, "listing": listing})


# ===========================================================================
#  Drivers
# ===========================================================================


def driver_browse(request):
    """
    The driver list, filtered from the querystring like the car list.

    `searchable()` rather than `live()` — anyone who set `hide_from_search`
    must not appear here.
    """
    form = DriverFilterForm(request.GET or None)
    queryset = form.apply(
        DriverListing.objects.searchable().hide_blocked(request.user).with_display_data()
    )

    page = Paginator(queryset, PER_PAGE).get_page(request.GET.get("page"))
    context = {
        "form": form,
        "page": page,
        "total": page.paginator.count,
        "querystring": _querystring_without_page(request),
        "can_save": request.user.is_authenticated,
    }

    if request.headers.get("HX-Request"):
        return render(request, "drivers/_results.html", context)
    return render(request, "drivers/browse.html", context)


def driver_detail(request, uuid):
    listing = get_object_or_404(DriverListing.objects.with_display_data(), uuid=uuid)

    is_owner = request.user.is_authenticated and listing.driver_id == request.user.pk
    hidden = listing.driver.profile.hide_from_search

    if (not listing.is_live or hidden) and not is_owner and not request.user.is_staff:
        raise Http404

    if not is_owner and is_blocked_between(request.user, listing.driver):
        raise Http404

    if not is_owner:
        DriverListing.objects.filter(pk=listing.pk).update(view_count=F("view_count") + 1)

    return render(
        request,
        "drivers/detail.html",
        {
            "listing": listing,
            "is_owner": is_owner,
            "verified_ratings": listing.verified_ratings,
            "claimed_ratings": listing.claimed_ratings,
            "my_intro": _my_intro(request.user, driver_listing=listing),
            "intro_price": pricing.price_for(pricing.Action.INTRO_REQUEST, user=request.user)
            if request.user.is_authenticated
            else None,
        },
    )


@login_required
@require_participation
@require_http_methods(["GET", "POST"])
def driver_create(request):
    """
    No phone-verification gate here, unlike `create` for cars.

    A driver listing gives nothing away — contacts are released only when an
    introduction is approved, and that approval is gated. Putting a wall here
    would just thin out the supply owners come to browse. See the class
    docstring on `DriverListing`.

    One listing per driver. A second one would be the same person twice in the
    same list, which helps nobody and makes every count on the site wrong, so
    an existing listing sends you to the edit screen instead.
    """
    existing = DriverListing.objects.filter(driver=request.user).first()
    if existing:
        return redirect("drivers:edit", uuid=existing.uuid)

    form = DriverListingForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        listing = form.save(commit=False)
        listing.driver = request.user
        listing.save()
        form.save_m2m()

        # Drivers go live immediately. The car flow holds a listing in draft
        # because it can't be published without a photo; nothing here needs
        # that wait, and a second step is a second place to lose someone.
        messages.success(request, "You're listed. Owners in your area can see you now.")
        return redirect(listing.get_absolute_url())

    return render(request, "drivers/form.html", {"form": form, "is_new": True})


@login_required
@require_participation
@require_http_methods(["GET", "POST"])
def driver_edit(request, uuid):
    listing = _own_driver_listing_or_404(request, uuid)
    form = DriverListingForm(request.POST or None, instance=listing, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Listing updated.")
        return redirect(listing.get_absolute_url())
    return render(request, "drivers/form.html", {"form": form, "listing": listing})


@login_required
@require_POST
def driver_set_status(request, uuid):
    listing = _own_driver_listing_or_404(request, uuid)
    target = request.POST.get("status")
    if target not in {s.value for s in DriverListing.Status}:
        messages.error(request, "Unknown status.")
        return redirect(listing.get_absolute_url())

    listing.status = target
    listing.save(update_fields=["status", "published_at", "updated_at"])

    labels = {
        DriverListing.Status.ACTIVE: "You're listed again.",
        DriverListing.Status.PAUSED: "Paused — owners can't see you now.",
        DriverListing.Status.PLACED: "Marked as driving. Good luck out there.",
        DriverListing.Status.ARCHIVED: "Listing archived.",
        DriverListing.Status.DRAFT: "Moved back to draft.",
    }
    messages.success(request, labels.get(target, "Updated."))
    return redirect(listing.get_absolute_url())


# ----------------------------------------------------------- rating proofs

@login_required
@require_participation
@require_http_methods(["GET", "POST"])
def ratings(request):
    """
    Where a driver claims a platform rating and uploads the screen behind it.

    The screenshot is destroyed when a reviewer decides — it carries the
    driver's photo, legal name and trip history, which is ID-document-grade
    personal information. Say so on the page, not only in the code.
    """
    form = RatingProofForm(
        request.POST or None, request.FILES or None, driver=request.user
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(
            request,
            "Sent for checking. We'll delete the screenshot as soon as we've looked at it.",
        )
        return redirect("drivers:ratings")

    return render(
        request,
        "drivers/ratings.html",
        {
            "form": form,
            "proofs": request.user.rating_proofs.select_related("platform").all(),
        },
    )


@login_required
@require_POST
def rating_delete(request, pk):
    proof = get_object_or_404(PlatformRatingProof, pk=pk, driver=request.user)
    if proof.screenshot:
        proof.screenshot.delete(save=False)
    proof.delete()
    messages.success(request, "Rating removed.")
    return redirect("drivers:ratings")


def _own_driver_listing_or_404(request, uuid):
    return get_object_or_404(DriverListing, uuid=uuid, driver=request.user)


# ===========================================================================
#  Saved searches
# ===========================================================================


@login_required
def saved_searches(request):
    return render(
        request,
        "listings/saved_searches.html",
        {
            "searches": request.user.saved_searches.all(),
            "max_searches": SavedSearch.MAX_PER_USER,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def save_search(request):
    """
    Store the filters the user is currently looking at.

    The params are taken from the filter form, re-validated here, and never
    from `request.GET` directly. A saved search is replayed against a queryset
    weeks later; the only thing that makes that safe is that everything in it
    passed a form on the way in.
    """
    kind = request.GET.get("kind") or request.POST.get("kind")
    if kind not in {SavedSearch.Kind.CARS, SavedSearch.Kind.DRIVERS}:
        raise Http404

    source = request.GET if request.method == "GET" else request.POST
    filter_form_class = (
        DriverFilterForm if kind == SavedSearch.Kind.DRIVERS else VehicleFilterForm
    )
    filter_form = filter_form_class(source)
    params = filter_form.as_saved_params()
    params.pop("sort", None)

    form = SaveSearchForm(
        request.POST or None, user=request.user, kind=kind, params=params
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Saved. You'll find it under Saved searches.")
        return redirect("saved_searches")

    return render(
        request,
        "listings/save_search.html",
        {
            "form": form,
            "kind": kind,
            "params": params,
            "hidden_params": _hidden_params(params),
            "summary": _describe_params(filter_form, params),
        },
    )


@login_required
@require_POST
def delete_saved_search(request, pk):
    search = get_object_or_404(SavedSearch, pk=pk, user=request.user)
    search.delete()
    messages.success(request, "Saved search deleted.")
    return redirect("saved_searches")


def _hidden_params(params):
    """Flatten saved params into (name, value) pairs for hidden inputs."""
    pairs = []
    for key, value in params.items():
        for item in value if isinstance(value, list) else [value]:
            pairs.append((key, item))
    return pairs


def _describe_params(form, params):
    """
    A human sentence for the confirm screen, so nobody saves a search without
    seeing what is in it.
    """
    if not params:
        return "Everything — no filters set."

    bits = []
    for name, value in params.items():
        field = form.fields.get(name)
        if field is None:
            continue
        label = field.label or name.replace("_", " ").capitalize()
        cleaned = form.cleaned_data.get(name)
        if isinstance(cleaned, bool):
            bits.append(label)
        elif isinstance(cleaned, (list, tuple)):
            bits.append(f"{label}: " + ", ".join(str(item) for item in cleaned))
        else:
            bits.append(f"{label}: {cleaned}")
    return " · ".join(bits)


# ===========================================================================
#  The owner's log
# ===========================================================================


@login_required
@require_participation
@require_http_methods(["GET", "POST"])
def notes(request, uuid):
    """
    An owner's private log against one of their cars.

    `_owned_or_404` is the entire access control, and it is a 404 rather than a
    403 on purpose: somebody probing other people's cars should not be able to
    tell an existing log from a missing one.
    """
    listing = _owned_or_404(request, uuid)

    form = VehicleNoteForm(
        request.POST or None, listing=listing, author=request.user
    )
    if request.method == "POST" and form.is_valid():
        note = form.save()
        logger.info("Note %s added to listing %s", note.pk, listing.pk)
        messages.success(request, "Noted.")
        return redirect("listings:notes", uuid=listing.uuid)

    entries = list(
        listing.notes.select_related("placement__driver", "author")
    )

    return render(
        request,
        "listings/notes.html",
        {
            "listing": listing,
            "form": form,
            "notes": entries,
            "last_service": next(
                (n for n in entries if n.kind == VehicleNote.Kind.SERVICE), None
            ),
        },
    )


@login_required
@require_participation
@require_POST
def note_delete(request, uuid, note_id):
    listing = _owned_or_404(request, uuid)
    deleted, _ = VehicleNote.objects.filter(pk=note_id, listing=listing).delete()
    if deleted:
        messages.success(request, "Note deleted.")
    return redirect("listings:notes", uuid=listing.uuid)
