from django.http import JsonResponse
from django.shortcuts import render


def home(request):
    """Landing page when logged out, feed placeholder when logged in."""
    if request.user.is_authenticated:
        return render(request, "pages/feed_placeholder.html")
    return render(request, "pages/landing.html")


def healthz(request):
    """Liveness probe. Keep it cheap — no database call."""
    return JsonResponse({"status": "ok"})
