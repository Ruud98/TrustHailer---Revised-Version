import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.decorators import require_participation
from apps.notifications.models import Notification
from apps.notifications.services import notify as in_app_notify

from . import notify
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


def _mine_or_404(request, uuid, *, as_recipient):
    field = "to_user" if as_recipient else "from_user"
    return get_object_or_404(IntroRequest, uuid=uuid, **{field: request.user})
