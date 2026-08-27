"""
Gates for actions where trust starts to carry weight.

Use `@require_verified_phone` in Sprint 2+ on listing creation and introduction
approval. Deliberately NOT on browsing, posting or commenting: the signup funnel
must never be blocked behind a verification step, or the platform stays empty.
"""
from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect


def require_verified_phone(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        user = request.user
        if user.is_authenticated and user.needs_phone_verification:
            messages.info(
                request,
                "Verify your mobile number first — it's how people will reach you.",
            )
            return redirect("accounts:verify_phone")
        return view(request, *args, **kwargs)

    return wrapper


def require_participation(view):
    """Blocks suspended accounts from anything that writes."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        user = request.user
        if user.is_authenticated and not user.can_participate:
            messages.error(
                request,
                "Your account is suspended. Contact support if you think this is a mistake.",
            )
            return redirect("home")
        return view(request, *args, **kwargs)

    return wrapper
