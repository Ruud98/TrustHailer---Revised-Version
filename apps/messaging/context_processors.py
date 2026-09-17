"""
The unread badge, on every page.

One indexed COUNT per authenticated request — the same bargain the notification
badge already makes, and for the same reason: a count each page has to remember
to ask for is a count some page eventually forgets.
"""
from . import services


def unread_messages(request):
    if not request.user.is_authenticated:
        return {}
    return {"unread_message_count": services.unread_count(request.user)}
