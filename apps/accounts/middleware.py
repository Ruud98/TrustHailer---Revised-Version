from django.contrib.auth import get_user_model
from django.shortcuts import redirect
from django.urls import resolve, reverse
from django.utils import timezone

# Paths a half-onboarded user may still reach.
ONBOARDING_EXEMPT = {
    "accounts:onboarding",
    "accounts:logout",
    "accounts:join",
    "accounts:verify",
    "accounts:resend",
    "geo:suburb_options",
    "geo:suburb_search",
    "healthz",
}


class OnboardingMiddleware:
    """
    Funnel users who signed up but never finished setup back into the wizard.

    Without this, a user who abandons onboarding lands on a feed with no
    location and no role, and every downstream feature has to handle that case.
    Handling it once here is much cheaper than handling it everywhere.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and not user.is_staff:
            profile = getattr(user, "profile", None)
            if profile is not None and not profile.is_onboarded:
                match = resolve(request.path_info)
                name = f"{match.namespace}:{match.url_name}" if match.namespace else match.url_name
                is_admin = request.path_info.startswith("/admin") or "admin" in (match.app_name or "")
                if name not in ONBOARDING_EXEMPT and not is_admin and not request.path_info.startswith("/static"):
                    return redirect(reverse("accounts:onboarding"))
        return self.get_response(request)


class LastSeenMiddleware:
    """
    Track activity at a coarse grain. Writing on every request would mean a
    database write per page view for no benefit, so we only write once an hour.
    """

    THROTTLE_SECONDS = 3600

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            now = timezone.now()
            last = user.last_seen
            if last is None or (now - last).total_seconds() > self.THROTTLE_SECONDS:
                # request.user is a SimpleLazyObject, so type(user) is the proxy,
                # not the model. Go through get_user_model() instead.
                get_user_model().objects.filter(pk=user.pk).update(last_seen=now)
        return response
