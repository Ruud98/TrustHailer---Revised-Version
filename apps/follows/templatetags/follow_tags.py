"""
`{% follow_button person %}` — the follow control, wherever a person appears.

The context is built by `button_context` rather than inline in the tag, because
the view that answers the HTMX post has to produce exactly the same shape. Two
builders would be two chances for the swapped-in button to disagree with the
one that was on the page.
"""
from django import template

from .. import services

register = template.Library()


def button_context(viewer, target):
    """Everything `follows/_button.html` needs, for one viewer and one person."""
    signed_in = getattr(viewer, "is_authenticated", False)
    is_me = signed_in and viewer.pk == target.pk
    return {
        "viewer": viewer,
        "person": target,
        "is_me": is_me,
        "following": services.is_following(viewer, target),
        # Shown as "Follow back" when they already follow you. Instagram's
        # wording, and it is worth the extra query: it turns a cold action into
        # an obvious reciprocation.
        # Guarded on `signed_in`, not just `not is_me`: this asks whether the
        # TARGET follows the VIEWER, so an AnonymousUser lands on the right of
        # the query and the ORM cannot coerce it to an id.
        "follows_you": (
            signed_in and not is_me and services.is_following(target, viewer)
        ),
    }


@register.inclusion_tag("follows/_button.html", takes_context=True)
def follow_button(context, person):
    return button_context(context.get("user"), person)
