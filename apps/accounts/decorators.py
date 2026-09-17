"""
Gates for actions where trust starts to carry weight.

There was a phone-verification gate here, on introductions, the directory and
advert claims. It is gone: the cost of it fell on every honest member at the
moment they were trying to do something useful, and the checks that actually
carry weight — ID, licence, PrDP — sit further up the ladder and are untouched.

What remains is `@require_participation`, which is about standing rather than
identity: a suspended account writes nothing.
"""
from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect


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
