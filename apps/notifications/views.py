import json

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Notification, PushSubscription

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


# ---------------------------------------------------------- push subscriptions
#
# Two endpoints, both called by static/js/pwa.js and by nothing else. They take
# JSON rather than a form because what they receive is the browser's own
# `PushSubscription` object, serialised by the browser — reshaping that into
# form fields client-side only to reassemble it here would be work done twice
# for the chance to get it wrong once.
#
# CSRF protection applies as normal: the fetch sends the token, the same way
# every hx-post on the site does. These are state-changing endpoints on a
# session cookie, and a push subscription silently registered from another
# origin is somebody else's lock screen.


@require_POST
@login_required
def push_subscribe(request):
    """
    Record a subscription the browser has just minted.

    Called on every page load once permission has been granted, not only the
    first time, because a browser can rotate a subscription on its own and the
    old one simply stops working. Idempotent for that reason: the endpoint is
    the key, and re-registering an endpoint we already have updates the keys
    and the owner rather than failing or duplicating.
    """
    data = _json_body(request)
    if data is None:
        return HttpResponseBadRequest("Expected JSON.")

    endpoint = (data.get("endpoint") or "").strip()
    keys = data.get("keys") or {}
    p256dh = (keys.get("p256dh") or "").strip()
    auth = (keys.get("auth") or "").strip()
    if not (endpoint and p256dh and auth):
        return HttpResponseBadRequest("Incomplete subscription.")

    PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            "user": request.user,
            "p256dh": p256dh,
            "auth": auth,
            # Truncated to the column rather than rejected: this is a label for
            # a human, and losing the tail of a user agent string costs nothing
            # while a 400 here would cost the subscription.
            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:200],
        },
    )
    return JsonResponse({"ok": True})


@require_POST
@login_required
def push_unsubscribe(request):
    """
    Forget a subscription, when somebody turns notifications off.

    Scoped to the requesting user: an endpoint is not a secret — it travels to
    the push service and back — so deleting by endpoint alone would let anyone
    holding one switch off somebody else's notifications. Silent when there is
    nothing to delete, because "already gone" is the outcome that was asked for.
    """
    data = _json_body(request)
    if data is None:
        return HttpResponseBadRequest("Expected JSON.")

    endpoint = (data.get("endpoint") or "").strip()
    if endpoint:
        PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()
    return JsonResponse({"ok": True})


def _json_body(request):
    try:
        return json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
