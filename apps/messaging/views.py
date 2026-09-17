import logging

from django.contrib import messages as flash
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST, require_http_methods

from apps.accounts.decorators import require_participation
from apps.accounts.models import User

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
            "numbers_allowed": services.numbers_allowed(request.user, other),
        },
    )


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
