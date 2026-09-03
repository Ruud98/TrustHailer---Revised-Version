from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render
from django.utils import timezone

from .models import Notification

PER_PAGE = 30


@login_required
def inbox(request):
    """
    Everything, newest first. Opening the page reads all of it.

    THE WHOLE LIST IS MARKED READ ON OPEN, NOT PER ROW
    ----------------------------------------------------
    A per-row "mark read" click is one more tap for no real benefit — nobody
    comes to this page to leave some notifications deliberately unread the way
    they might in an email inbox. The rows that were unread when the page
    loaded are still shown with the unread mark for this one render (computed
    before the update, not after), so the page itself shows what just changed
    even though the badge on the avatar has already cleared.
    """
    queryset = Notification.objects.for_user(request.user)
    page = Paginator(queryset, PER_PAGE).get_page(request.GET.get("page"))

    unread_ids = {n.pk for n in page.object_list if not n.is_read}
    Notification.objects.for_user(request.user).unread().update(
        is_read=True, read_at=timezone.now()
    )

    return render(
        request,
        "notifications/inbox.html",
        {"page": page, "unread_ids": unread_ids},
    )
