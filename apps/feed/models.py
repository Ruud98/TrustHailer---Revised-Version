"""
The feed — the part that replaces the Facebook group itself.

WHY THIS IS NOT A NICE-TO-HAVE
------------------------------
Everything else here is a transaction. Somebody comes when they need a car or a
driver, and leaves when they have one. That is a site people visit twice a year,
and a site people visit twice a year is a site they forget the name of.

The group is where the rest happens: which suburb the impound van is working
this week, what Bolt actually pays after the deductions, who is running the
deposit scam this month, whether the tyre place on the corner is any good. None
of that fits in a listing, and it is the reason people open the app on a day
they are not looking for anything.

WHAT IS DELIBERATELY WORSE HERE THAN ON FACEBOOK
------------------------------------------------
No infinite algorithmic feed, no reshares, no reactions beyond a single like,
one level of comment nesting. Every one of those is a decision to make this
quieter than the thing it replaces. The group is exhausting; the complaint that
sends people looking for an alternative is noise, not a lack of features.

CONTACT DETAILS ARE STRIPPED HERE TOO
-------------------------------------
"Car available, call 082…" is the single most common post in these groups, and
allowing it here would hollow out the listings side — the structured terms, the
filters, the introduction flow, all of it routed around by a phone number in a
paragraph. It would also make every other person's number fair game to post.
The rule is the same as everywhere else on the site, and the form says so and
points at the listing form instead. This is the most arguable decision in the
sprint; see the README.
"""
import uuid

from django.db import models
from django.urls import reverse

from apps.core.models import TimeStampedModel


class PostQuerySet(models.QuerySet):
    def visible(self):
        return self.filter(is_hidden=False)

    def hide_blocked(self, user):
        """
        Drop posts by anyone this viewer has blocked, or who blocked them.

        Same helper the listing surfaces use. A block that leaves somebody's
        posts in your feed has not done what the button promised — and the feed
        is where you would see them most.
        """
        from apps.safety.models import blocked_user_ids

        hidden = blocked_user_ids(user)
        return self.exclude(author_id__in=hidden) if hidden else self

    def for_feed(self, user):
        return (
            self.visible()
            .hide_blocked(user)
            .select_related("author__profile", "author__verification", "city")
        )


class Post(TimeStampedModel):
    """
    One thing somebody wanted to say.

    TOPICS ARE A FIXED LIST, NOT FREE TAGS
    --------------------------------------
    Six topics, chosen because they are what the groups actually argue about.
    Free tags would fragment the same conversation across nine spellings of
    "petrol price" and leave the filter useless within a month. A fixed list
    also means the scam topic is one tap away, which is where somebody who has
    just been burned goes looking.

    CITY, NOT SUBURB
    ----------------
    Listings are suburb-level because that is what decides whether a deal is
    practical. A conversation is not: a warning about a roadblock in Midrand is
    worth reading in Tembisa, and suburb-scoped posts would produce a feed of
    two items. City is the right grain for talk and suburb is the right grain
    for cars, and they are different fields for that reason.
    """

    class Topic(models.TextChoices):
        GENERAL = "general", "General"
        ADVICE = "advice", "Advice"
        ALERT = "alert", "Road alert"
        SCAM = "scam", "Scam warning"
        EARNINGS = "earnings", "Earnings"
        MAINTENANCE = "maintenance", "Maintenance"

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    author = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="posts"
    )
    body = models.TextField(max_length=3000)
    image = models.ImageField(upload_to="posts/%Y/%m/", blank=True)
    topic = models.CharField(max_length=12, choices=Topic.choices, default=Topic.GENERAL)
    city = models.ForeignKey(
        "geo.City", null=True, blank=True, on_delete=models.SET_NULL, related_name="posts"
    )

    # Denormalised. A feed page renders 20 posts; counting likes and comments
    # per post would be 40 queries to display two numbers nobody reads closely.
    like_count = models.PositiveIntegerField(default=0)
    comment_count = models.PositiveIntegerField(default=0)

    is_hidden = models.BooleanField(
        default=False,
        help_text="Hidden by staff. The row stays so the moderation trail does.",
    )

    objects = PostQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["is_hidden", "-created_at"]),
            models.Index(fields=["topic", "is_hidden", "-created_at"]),
            models.Index(fields=["city", "is_hidden", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.get_topic_display()} by {self.author_id}: {self.body[:40]}"

    def get_absolute_url(self):
        return reverse("feed:detail", args=[self.uuid])

    def liked_by(self, user):
        if not user.is_authenticated:
            return False
        return self.likes.filter(user=user).exists()

    def recount(self):
        """Recompute both counters from the rows. Used after a delete or a hide."""
        Post.objects.filter(pk=self.pk).update(
            like_count=Like.objects.filter(post=self).count(),
            comment_count=Comment.objects.filter(post=self, is_hidden=False).count(),
        )


class CommentQuerySet(models.QuerySet):
    def visible(self):
        return self.filter(is_hidden=False)

    def hide_blocked(self, user):
        from apps.safety.models import blocked_user_ids

        hidden = blocked_user_ids(user)
        return self.exclude(author_id__in=hidden) if hidden else self


class Comment(TimeStampedModel):
    """
    A reply, one level deep and no deeper.

    Nesting is where group threads become unreadable on a phone: by the fourth
    level each reply is a column of six characters. One level is enough to
    answer somebody directly, and the cap is enforced here rather than left to
    the template — a `parent` that already has a parent is re-pointed at the
    top of its thread instead of being rejected, because the person replying
    does not care about our data model and should not be made to.
    """

    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="comments"
    )
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="replies"
    )
    body = models.TextField(max_length=1500)
    is_hidden = models.BooleanField(default=False)

    objects = CommentQuerySet.as_manager()

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["post", "is_hidden", "created_at"])]

    def __str__(self):
        return f"Comment by {self.author_id} on {self.post_id}"

    def get_absolute_url(self):
        return f"{self.post.get_absolute_url()}#comment-{self.pk}"

    def save(self, *args, **kwargs):
        # Flatten anything deeper than one level onto the top of its thread.
        if self.parent_id and self.parent.parent_id:
            self.parent_id = self.parent.parent_id
        super().save(*args, **kwargs)


class Like(models.Model):
    """
    One tap, and the only reaction there is.

    A row of six emoji turns every post into a small popularity contest and
    gives people a way to pile on without writing anything they can be held to.
    One like, or nothing.
    """

    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="likes")
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="likes"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["post", "user"], name="one_like_per_post_per_user")
        ]

    def __str__(self):
        return f"{self.user_id} likes {self.post_id}"
