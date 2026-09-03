import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST, require_http_methods

from apps.accounts.models import User
from apps.core.ratelimit import RateLimited, hit
from apps.listings.models import DriverListing, VehicleListing

from .forms import BlockForm, ReportForm
from .models import Block, Report

logger = logging.getLogger(__name__)


def safety(request):
    """
    The guidance page. Static, public, and linked from everywhere a decision
    gets made.

    Public on purpose: somebody deciding whether to trust this site at all
    should be able to read how it handles a scam before signing up, and a
    driver being pressured for a deposit right now should be able to reach it
    from a WhatsApp link without logging in.
    """
    return render(request, "pages/safety.html")


# ------------------------------------------------------------------ reports


@login_required
@require_http_methods(["GET", "POST"])
def report(request):
    """
    Flag a listing or a person for staff.

    Rate limited, because a report queue is a weapon if it is free to fill. Ten
    a day is far more than any honest user needs and low enough that somebody
    burying a competitor under complaints runs out before staff do.
    """
    target, label = _target_from_query(request)

    if _owner_of(target) == request.user:
        # Reporting your own listing, or yourself. Not malicious, just a wrong
        # tap — but a queue with self-reports in it is a queue staff learn to
        # skim, and that is how a real one gets missed.
        messages.info(request, "That is yours. If you want it gone, delete it.")
        return redirect(_back_to(target))

    existing = Report.objects.filter(
        reporter=request.user,
        content_type=ContentType.objects.get_for_model(target),
        object_id=target.pk,
    ).first()
    if existing:
        messages.info(request, "You have already reported this. We are looking at it.")
        return redirect(_back_to(target))

    form = ReportForm(request.POST or None, reporter=request.user, target=target)
    if request.method == "POST" and form.is_valid():
        try:
            hit(f"report:{request.user.pk}", 10, 86400,
                "That is a lot of reports for one day. Get in touch with us directly.")
        except RateLimited as exc:
            messages.error(request, exc.message)
            return redirect(_back_to(target))

        try:
            report_row = form.save()
        except IntegrityError:
            messages.info(request, "You have already reported this.")
            return redirect(_back_to(target))

        logger.info(
            "Report %s opened by user %s on %s %s",
            report_row.pk, request.user.pk, report_row.content_type, report_row.object_id,
        )
        messages.success(
            request,
            "Thanks — somebody will look at it. We will not tell them who reported it.",
        )
        return redirect(_back_to(target))

    return render(request, "safety/report.html", {"form": form, "label": label})


# ------------------------------------------------------------------- blocks


@login_required
@require_http_methods(["GET", "POST"])
def block(request, handle):
    """
    Stop dealing with somebody.

    Takes effect at once and nobody is told. See the `Block` docstring for why
    the effect runs both ways while the record does not.
    """
    other = get_object_or_404(User, handle=handle, is_active=True)
    if other.pk == request.user.pk:
        raise Http404

    form = BlockForm(request.POST or None, user=request.user, blocked_user=other)
    if request.method == "POST" and form.is_valid():
        form.save()
        logger.info("User %s blocked user %s", request.user.pk, other.pk)
        messages.success(
            request,
            f"Blocked. You will not see {other.get_short_name() or 'them'} again, and "
            "they cannot reach you here.",
        )
        return redirect("home")

    return render(request, "safety/block.html", {"form": form, "other": other})


@login_required
@require_POST
def unblock(request, handle):
    other = get_object_or_404(User, handle=handle)
    Block.objects.filter(user=request.user, blocked_user=other).delete()
    messages.success(request, "Unblocked.")
    return redirect("safety:blocked")


@login_required
def blocked(request):
    return render(
        request,
        "safety/blocked.html",
        {
            "blocks": Block.objects.filter(user=request.user).select_related(
                "blocked_user__profile"
            ),
        },
    )


# ------------------------------------------------------------------ helpers


def _target_from_query(request):
    """Resolve ?car= / ?driver= / ?business= / ?user= into the thing being reported."""
    source = request.GET or request.POST

    if source.get("car"):
        listing = get_object_or_404(VehicleListing, uuid=source["car"])
        return listing, f"the listing for {listing.title}"

    if source.get("driver"):
        listing = get_object_or_404(DriverListing, uuid=source["driver"])
        return listing, f"the driver listing “{listing.headline}”"

    if source.get("business"):
        # A business is reported as itself, not through its owner account —
        # staff can carry a business in with no linked account at all (see
        # BusinessListing.owner_user), and even when there is one, it is the
        # listing's claims that are in question, not the person's identity.
        from apps.directory.models import BusinessListing

        listing = get_object_or_404(BusinessListing, pk=source["business"])
        return listing, f"the business listing “{listing.name}”"

    if source.get("user"):
        user = get_object_or_404(User, handle=source["user"], is_active=True)
        return user, user.full_name or "this member"

    raise Http404


def _owner_of(target):
    """The person a reportable thing belongs to, whatever kind of thing it is."""
    if isinstance(target, User):
        return target
    return (
        getattr(target, "owner", None)
        or getattr(target, "driver", None)
        or getattr(target, "owner_user", None)
    )


def _back_to(target):
    """Send people back to what they were looking at, not to the home page."""
    return target.get_absolute_url()
