import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import F
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST, require_http_methods

from apps.accounts.decorators import require_participation
from apps.core.ratelimit import RateLimited, hit
from apps.notifications.models import Notification
from apps.notifications.services import notify as in_app_notify
from apps.safety.models import is_blocked_between

from .forms import CommentForm, FeedFilterForm, PostForm
from .models import Comment, Like, Post

logger = logging.getLogger(__name__)

PER_PAGE = 15


def feed(request):
    """
    The feed. Newest first, filtered by topic and city.

    NEWEST FIRST, AND NOTHING CLEVERER
    ----------------------------------
    No ranking, no engagement score, no "top posts". Two reasons. The first is
    honest: at this scale there is not enough data for a ranking model to be
    anything but noise dressed as judgement. The second matters more — half the
    value here is time-critical. A roadblock warning is worth reading for four
    hours and worthless after that, and any ranking that promotes a popular
    week-old post over a fresh one gets that exactly backwards.

    The day this needs pagination beyond "load more", the answer is still
    chronological.
    """
    form = FeedFilterForm(request.GET or None)
    queryset = form.apply(Post.objects.for_feed(request.user))

    page = Paginator(queryset, PER_PAGE).get_page(request.GET.get("page"))

    # Which posts this viewer has already liked, in one query rather than one
    # per card.
    liked = set()
    if request.user.is_authenticated:
        liked = set(
            Like.objects.filter(user=request.user, post__in=page.object_list)
            .values_list("post_id", flat=True)
        )

    context = {
        "form": form,
        "page": page,
        "liked": liked,
        "querystring": _querystring_without_page(request),
    }

    # HTMX asks for the next page and appends it. The same partial renders the
    # first page inside the full template, so there is one definition of what a
    # run of posts looks like.
    if request.headers.get("HX-Request"):
        return render(request, "feed/_posts.html", context)
    return render(request, "feed/feed.html", context)


def detail(request, uuid):
    post = get_object_or_404(
        Post.objects.select_related("author__profile", "author__verification", "city"),
        uuid=uuid,
    )
    if post.is_hidden and not request.user.is_staff:
        raise Http404
    if is_blocked_between(request.user, post.author):
        raise Http404

    comments = (
        Comment.objects.filter(post=post)
        .visible()
        .hide_blocked(request.user)
        .select_related("author__profile", "author__verification")
    )
    # One level of nesting, assembled here rather than with a recursive
    # template include: the depth is fixed at one, so a loop is the honest way
    # to say so. Each top-level comment gets its replies attached directly —
    # Django templates cannot do a dict lookup by a loop variable, and forcing
    # one with a custom filter is more machinery than a plain attribute.
    replies_by_parent = {}
    for one in comments:
        if one.parent_id:
            replies_by_parent.setdefault(one.parent_id, []).append(one)

    top_level = []
    for one in comments:
        if one.parent_id is None:
            one.child_replies = replies_by_parent.get(one.pk, [])
            top_level.append(one)

    return render(
        request,
        "feed/detail.html",
        {
            "post": post,
            "form": CommentForm(),
            "top_level": top_level,
            "liked": post.liked_by(request.user),
            "is_author": request.user.is_authenticated and post.author_id == request.user.pk,
        },
    )


@login_required
@require_participation
@require_http_methods(["GET", "POST"])
def create(request):
    """
    Write a post. No phone verification — see `PostForm` for why.

    Rate limited because a feed is only worth reading if one person cannot fill
    it. Twenty a day is far past what anybody posts honestly.
    """
    form = PostForm(request.POST or None, request.FILES or None, author=request.user)

    if request.method == "POST" and form.is_valid():
        try:
            hit(f"post:{request.user.pk}", 20, 86400,
                "That is a lot of posting for one day. Give it a rest.")
        except RateLimited as exc:
            messages.error(request, exc.message)
            return redirect("feed:feed")

        post = form.save()
        logger.info("Post %s created by user %s", post.uuid, request.user.pk)
        messages.success(request, "Posted.")
        return redirect(post.get_absolute_url())

    return render(request, "feed/form.html", {"form": form})


@login_required
@require_participation
@require_POST
def comment(request, uuid):
    post = _visible_post_or_404(request, uuid)

    parent = None
    parent_id = request.POST.get("parent")
    if parent_id:
        parent = Comment.objects.filter(pk=parent_id, post=post, is_hidden=False).first()

    form = CommentForm(
        request.POST, post=post, author=request.user, parent=parent
    )
    if form.is_valid():
        try:
            hit(f"comment:{request.user.pk}", 60, 86400,
                "That is a lot of comments for one day.")
        except RateLimited as exc:
            messages.error(request, exc.message)
            return redirect(post.get_absolute_url())

        new_comment = form.save()
        Post.objects.filter(pk=post.pk).update(comment_count=F("comment_count") + 1)

        if parent:
            in_app_notify(
                recipient=parent.author,
                kind=Notification.Kind.COMMENT_REPLY,
                message=f"{request.user.get_short_name() or 'Someone'} replied to your comment",
                url=new_comment.get_absolute_url(),
                actor=request.user,
            )
        else:
            in_app_notify(
                recipient=post.author,
                kind=Notification.Kind.POST_COMMENT,
                message=f"{request.user.get_short_name() or 'Someone'} commented on your post",
                url=new_comment.get_absolute_url(),
                actor=request.user,
            )

        return redirect(post.get_absolute_url())

    messages.error(request, form.errors.get("body", ["That did not work."])[0])
    return redirect(post.get_absolute_url())


@login_required
@require_participation
@require_POST
def like(request, uuid):
    """
    Toggle a like. Answers with the button, so HTMX can swap it in place.

    The counter is moved with an F() expression rather than read-modify-write:
    two people liking the same post in the same second is not rare on a post
    that is doing well, and that is exactly when the count is being looked at.
    """
    post = _visible_post_or_404(request, uuid)

    existing = Like.objects.filter(post=post, user=request.user).first()
    if existing:
        existing.delete()
        Post.objects.filter(pk=post.pk).update(like_count=F("like_count") - 1)
        liked = False
    else:
        try:
            Like.objects.create(post=post, user=request.user)
            Post.objects.filter(pk=post.pk).update(like_count=F("like_count") + 1)
        except IntegrityError:
            # Double tap on a slow connection. The constraint is the guard.
            pass
        liked = True

    post.refresh_from_db(fields=["like_count"])

    if request.headers.get("HX-Request"):
        return render(request, "feed/_like.html", {"post": post, "liked": liked})
    return redirect(post.get_absolute_url())


@login_required
@require_POST
def delete(request, uuid):
    """
    Delete your own post.

    A real delete, not a hide: this is somebody withdrawing their own words,
    and leaving a soft-deleted copy in the database that they cannot see is not
    honouring that. Staff hiding a post is the other case, and that one keeps
    the row — see `Post.is_hidden`.
    """
    post = get_object_or_404(Post, uuid=uuid, author=request.user)
    post.delete()
    messages.success(request, "Deleted.")
    return redirect("feed:feed")


@login_required
@require_POST
def delete_comment(request, pk):
    comment_row = get_object_or_404(Comment, pk=pk, author=request.user)
    post = comment_row.post
    comment_row.delete()
    post.recount()
    messages.success(request, "Comment deleted.")
    return redirect(post.get_absolute_url())


# ------------------------------------------------------------------ helpers


def _visible_post_or_404(request, uuid):
    post = get_object_or_404(Post, uuid=uuid, is_hidden=False)
    if is_blocked_between(request.user, post.author):
        raise Http404
    return post


def _querystring_without_page(request):
    params = request.GET.copy()
    params.pop("page", None)
    encoded = params.urlencode()
    return f"&{encoded}" if encoded else ""
