"""
`{% post_reactions post reactions %}` and `{% comment_reactions comment map %}`.

Tags rather than plain `{% include %}`s because the bar needs a handful of
derived values (your emoji, your label, what the trigger posts, the endpoint,
the DOM id, the picker) and computing those in the template would mean half a
dozen filters and a lookup on every card and every comment. Each takes the dict
its view built in two queries and picks its own row out of it, so rendering
stays query-free however long the thread is.
"""
from django import template

from ..reactions import EMPTY, bar_context

register = template.Library()


def _bar(context, target, lookup, variant):
    bar = bar_context(target, (lookup or {}).get(target.pk) or EMPTY, variant=variant)
    # An inclusion tag gets a fresh context, so `user` has to be carried over
    # explicitly — the template branches on it for the logged-out case.
    bar["user"] = context.get("user")
    return bar


@register.inclusion_tag("feed/_reactions.html", takes_context=True)
def post_reactions(context, post, reactions=None):
    return _bar(context, post, reactions, "post")


@register.inclusion_tag("feed/_reactions.html", takes_context=True)
def comment_reactions(context, comment, reactions=None):
    return _bar(context, comment, reactions, "comment")
