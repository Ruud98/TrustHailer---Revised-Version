"""
Building reaction bars without a query per post or per comment.

THE WHOLE POINT OF THIS MODULE
------------------------------
A feed page renders 20 posts; a busy thread renders 30 comments. Each bar needs
two things the row cannot answer on its own: which kind *you* picked, and which
kinds are winning. Asked per item that is 40 or 60 queries; asked here it is
two, for any number of items.

`Post.reaction_count` stays denormalised and stays the only counter in the
database — see the note on that field for why a JSON column of per-kind counts
would be worse than this. `Comment` has no such field and does not need one:
the totals come free out of the same GROUP BY that builds the summary, which is
the only place a comment's count is ever shown.
"""
from django.db.models import Count
from django.urls import reverse

from .models import Reaction

# How many distinct emoji the summary shows before it stops. Facebook shows
# three; beyond that they stop being a glance and start being a chart.
TOP_KINDS = 3


EMPTY = {"mine": None, "top": [], "total": 0}


def for_posts(posts, user):
    """`{post_id: {"mine", "top", "total"}}` — two queries, any number of posts."""
    return _summarise("post_id", posts, user)


def for_comments(comments, user):
    """
    The same, keyed by comment id. Two queries for a whole thread.

    Call it once with every comment on the page, replies included — see the
    detail view, which flattens the tree before asking.
    """
    return _summarise("comment_id", comments, user)


def _summarise(column, items, user):
    """
    Group everyone's reactions, then mark this viewer's own.

    Items with no reactions are simply absent from the dict; every call site
    treats a missing key as `EMPTY`, so nothing has to pre-seed it.
    """
    ids = [item.pk for item in items]
    if not ids:
        return {}

    data = {}
    grouped = (
        Reaction.objects.filter(**{column + "__in": ids})
        .values(column, "kind")
        .annotate(count=Count("id"))
        .order_by(column, "-count")
    )
    for row in grouped:
        entry = data.setdefault(row[column], {"mine": None, "top": [], "total": 0})
        # Every kind counts towards the total; only the loudest few get a face.
        entry["total"] += row["count"]
        if len(entry["top"]) < TOP_KINDS:
            entry["top"].append(
                (row["kind"], Reaction.EMOJI[row["kind"]], row["count"])
            )

    if user.is_authenticated:
        mine = Reaction.objects.filter(
            user=user, **{column + "__in": ids}
        ).values_list(column, "kind")
        for item_id, kind in mine:
            data.setdefault(item_id, {"mine": None, "top": [], "total": 0})
            data[item_id]["mine"] = kind

    return data


def for_post(post, user):
    """
    The same shape, for one post.

    Used by the HTMX response after somebody reacts. Goes through `for_posts`
    rather than duplicating the queries, so the fragment that gets swapped in
    can never disagree with the one that was rendered with the page.
    """
    return for_posts([post], user).get(post.pk, dict(EMPTY))


def for_comment(comment, user):
    """The same, for one comment."""
    return for_comments([comment], user).get(comment.pk, dict(EMPTY))


def bar_context(target, data, *, variant="post"):
    """
    Everything `feed/_reactions.html` needs, from data already in hand.

    Split from the `context` helpers so the list case can reuse the two queries
    `for_posts()` / `for_comments()` already ran, instead of going back to the
    database per card — which is the whole point of this module.

    `variant` picks the shape, not the behaviour: a post gets the big trigger
    with the emoji in it, a comment gets Facebook's plain "Like" word sized to
    sit in a footer line. Both drive the same endpoint contract and the same
    picker, so there is still one definition of how reacting works.
    """
    mine = data.get("mine")
    is_comment = variant == "comment"
    return {
        "variant": variant,
        # Unique per target AND per variant, because a post and a comment can
        # share a primary key and hx-swap aims at an id.
        "dom_id": "reactions-" + variant + "-" + str(target.pk),
        "react_url": reverse(
            "feed:react_comment" if is_comment else "feed:react",
            args=[target.pk if is_comment else target.uuid],
        ),
        "mine": mine,
        "my_emoji": Reaction.EMOJI[mine] if mine else "",
        "my_label": Reaction.Kind(mine).label if mine else "",
        # What the trigger button posts. Pressing it while it already shows
        # your reaction sends the same kind back, which the view reads as
        # "take it off" — one endpoint, no separate delete.
        "trigger_kind": mine or Reaction.DEFAULT,
        "top": data.get("top") or [],
        # A post carries its own denormalised counter; a comment's total comes
        # out of the GROUP BY above, because it has nowhere else to live.
        "total": target.reaction_count if not is_comment else data.get("total", 0),
        "picker": Reaction.picker_for(target),
    }


def context(post, user):
    """The bar for one post, queries included. Used by the HTMX response."""
    return bar_context(post, for_post(post, user))


def comment_context(comment, user):
    """The bar for one comment, queries included."""
    return bar_context(comment, for_comment(comment, user), variant="comment")
