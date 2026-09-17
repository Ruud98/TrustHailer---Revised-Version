"""
`{% promo_rail %}` — put the promo rail down the left of a page.

An inclusion tag rather than a context processor on purpose. A context
processor would run these queries on all ~40 URLs on the site to serve two of
them, and would put the rail one `{% include %}` away from appearing on pages —
like a car listing, or an introduction thread — where a flashing advert beside
somebody's phone number is exactly the wrong thing.

Opting in is two lines in the template that wants it:

    {% load promo_tags %}
    {% block rails %}{% promo_rail %}{% include "partials/_sidenav.html" %}{% endblock %}

The nav rail on the opposite side is a plain include, not a tag: it needs
`user` and `unread_notification_count` from the page context, which an
inclusion tag would have to be handed explicitly.
"""
from django import template

from .. import services

register = template.Library()


@register.inclusion_tag("promos/_rail.html", takes_context=True)
def promo_rail(context, businesses=True):
    rail = services.rail(include_businesses=businesses)
    user = context.get("user")
    return {
        "ads": rail["ads"],
        "slides": rail["slides"],
        # Nothing to show means no markup, no stylesheet hooks and no script —
        # a fresh install should not ship an empty column.
        "has_rail": bool(rail["ads"] or rail["slides"]),
        # Logged-out pages render without the top bar, so the rail starts where
        # the content does instead of 56px below a bar that is not there.
        "bare": not (user and user.is_authenticated),
    }
