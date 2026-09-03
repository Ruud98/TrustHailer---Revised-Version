import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.decorators import require_participation, require_verified_phone

from .forms import BusinessListingForm, DirectoryFilterForm
from .models import BusinessListing

logger = logging.getLogger(__name__)

PER_PAGE = 20


def browse(request, category=None):
    """
    The directory. `/directory/` and `/directory/<category>/` are the same
    view — the category in the path is a shareable, guessable URL for
    "mechanics near me" rather than a separate page to keep in step with this
    one.
    """
    form = DirectoryFilterForm(request.GET or None)
    queryset = form.apply(
        BusinessListing.objects.visible().with_display_data()
    )
    if category:
        if category not in BusinessListing.Category.values:
            raise Http404
        queryset = queryset.filter(category=category)

    page = Paginator(queryset, PER_PAGE).get_page(request.GET.get("page"))
    return render(
        request,
        "directory/browse.html",
        {
            "form": form,
            "page": page,
            "total": page.paginator.count,
            "category": category,
            "categories": BusinessListing.Category.choices,
        },
    )


def detail(request, pk, slug):
    listing = get_object_or_404(
        BusinessListing.objects.with_display_data(), pk=pk
    )
    is_owner = request.user.is_authenticated and listing.owner_user_id == request.user.pk

    if not listing.is_live and not is_owner and not request.user.is_staff:
        raise Http404

    # The canonical URL includes the slug for readability, but the id is what
    # actually resolves the row — so a stale or hand-typed slug still lands on
    # the right page instead of 404ing, and simply redirects to the current one.
    if listing.slug != slug:
        return redirect(listing.get_absolute_url())

    return render(
        request, "directory/detail.html", {"listing": listing, "is_owner": is_owner}
    )


@login_required
@require_participation
@require_verified_phone
@require_http_methods(["GET", "POST"])
def create(request):
    """
    List a business. Verified-phone-gated like listing a car, not left open
    like listing yourself as a driver — the number entered here is shown in
    full to the public immediately, with no approval step in between, so this
    is the moment that deserves the same bar as a car listing rather than the
    lighter one a driver profile gets.
    """
    form = BusinessListingForm(request.POST or None, request.FILES or None, owner=request.user)
    if request.method == "POST" and form.is_valid():
        listing = form.save(commit=False)
        listing.status = BusinessListing.Status.ACTIVE
        listing.save()
        logger.info("Business %s created by user %s", listing.uuid, request.user.pk)
        messages.success(
            request,
            "Listed. It goes into moderation for a verified badge, but it is visible now.",
        )
        return redirect(listing.get_absolute_url())

    return render(request, "directory/form.html", {"form": form, "is_new": True})


@login_required
@require_participation
@require_http_methods(["GET", "POST"])
def edit(request, pk):
    listing = _owned_or_404(request, pk)
    form = BusinessListingForm(
        request.POST or None, request.FILES or None, instance=listing, owner=request.user
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Updated.")
        return redirect(listing.get_absolute_url())
    return render(request, "directory/form.html", {"form": form, "listing": listing})


@login_required
def mine(request):
    listings = BusinessListing.objects.filter(owner_user=request.user).with_display_data()
    return render(request, "directory/mine.html", {"listings": listings})


@login_required
@require_POST
def set_status(request, pk):
    listing = _owned_or_404(request, pk)
    target = request.POST.get("status")
    if target not in {s.value for s in BusinessListing.Status}:
        messages.error(request, "Unknown status.")
        return redirect(listing.get_absolute_url())
    listing.status = target
    listing.save(update_fields=["status", "updated_at"])
    labels = {
        BusinessListing.Status.ACTIVE: "Live again.",
        BusinessListing.Status.PAUSED: "Paused — nobody can see it now.",
        BusinessListing.Status.ARCHIVED: "Archived.",
    }
    messages.success(request, labels.get(target, "Updated."))
    return redirect(listing.get_absolute_url())


def _owned_or_404(request, pk):
    return get_object_or_404(BusinessListing, pk=pk, owner_user=request.user)
