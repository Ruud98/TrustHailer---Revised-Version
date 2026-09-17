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

    # Optional, the way a title is optional on a Facebook group post and absent
    # from a timeline one. This feed is the former: `Topic.SCAM` and
    # `Topic.ALERT` posts name people and places, and a headline is what makes
    # one scannable in a column of twenty. "Who needs a car today?" needs no
    # headline and is not made to invent one — blank is the common case and
    # every template treats it as such.
    title = models.CharField(max_length=120, blank=True)
    body = models.TextField(max_length=3000)
    image = models.ImageField(upload_to="posts/%Y/%m/", blank=True)
    topic = models.CharField(max_length=12, choices=Topic.choices, default=Topic.GENERAL)
    city = models.ForeignKey(
        "geo.City", null=True, blank=True, on_delete=models.SET_NULL, related_name="posts"
    )

    # Denormalised. A feed page renders 20 posts; counting reactions and
    # comments per post would be 40 queries to display two numbers nobody reads
    # closely. This is the TOTAL across all seven kinds — the per-kind split is
    # not denormalised anywhere, because a JSON column of counts cannot be
    # moved with an F() expression and would reintroduce the exact lost-update
    # race this field avoids. The split is one GROUP BY per page instead.
    reaction_count = models.PositiveIntegerField(default=0)
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

    def reaction_by(self, user):
        """This viewer's reaction kind, or None. Drives the trigger button."""
        if not user.is_authenticated:
            return None
        reaction = self.reactions.filter(user=user).first()
        return reaction.kind if reaction else None

    def recount(self):
        """Recompute both counters from the rows. Used after a delete or a hide."""
        Post.objects.filter(pk=self.pk).update(
            # `post` is null on comment reactions, so this counts only the
            # ones left on the post itself. See the note on Reaction.post.
            reaction_count=Reaction.objects.filter(post=self).count(),
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


class Reaction(models.Model):
    """
    One reaction per person per post, of seven kinds.

    THIS REPLACED A SINGLE LIKE, DELIBERATELY
    -----------------------------------------
    What stood here was one like and nothing else, on the argument that a row
    of emoji turns a post into a popularity contest and hands people a way to
    pile on without writing anything they can be held to. Reactions were asked
    for anyway, on the grounds that members already know how to use them.

    ONE ROW PER PERSON PER POST IS WHAT SURVIVED OF THAT ARGUMENT
    ------------------------------------------------------------
    The unique constraint below is doing the same job the old one did, and it
    is the reason "pile on" is bounded: changing your mind from Like to Angry
    UPDATES your row, it does not add one. A post cannot accumulate more
    reactions than it has readers, and nobody can stack seven of their own.
    Do not relax this to allow multiple reactions per person.

    WHAT IS STILL TRUE OF THE ORIGINAL CONCERN
    ------------------------------------------
    `Topic.SCAM` and `Topic.ALERT` posts name people and businesses, and an
    angry face costs nothing and traces back to no transaction, unlike every
    `Review` on this site. If that turns out to be a problem in practice, the
    fix is to restrict the picker per topic in `Reaction.picker_for()` — which
    is why that is a function and not a constant — and NOT to start deleting
    rows, which loses the moderation trail.
    """

    class Kind(models.TextChoices):
        LIKE = "like", "Like"
        LOVE = "love", "Love"
        CARE = "care", "Care"
        HAHA = "haha", "Haha"
        WOW = "wow", "Wow"
        SAD = "sad", "Sad"
        ANGRY = "angry", "Angry"

    # The glyph is presentation, so it lives beside the choices rather than in
    # the database: changing how Care looks should not be a migration, and a
    # stored emoji is a stored rendering decision that ages badly.
    EMOJI = {
        Kind.LIKE: "👍",
        Kind.LOVE: "❤️",
        Kind.CARE: "🤗",
        Kind.HAHA: "😆",
        Kind.WOW: "😮",
        Kind.SAD: "😢",
        Kind.ANGRY: "😡",
    }

    # What the trigger button does when you have not reacted yet, and the kind
    # the old single-like rows were migrated to.
    DEFAULT = Kind.LIKE

    # A reaction targets exactly one thing. Mutually exclusive rather than
    # "post is always set, comment optionally too": with `post` filled in on
    # comment reactions, every existing per-post query would silently start
    # counting them, and each one would need a `comment__isnull=True` that
    # somebody eventually forgets. Null `post` means the existing queries stay
    # correct by construction, and the check constraint below stops a row that
    # points at both or neither.
    post = models.ForeignKey(
        Post, null=True, blank=True, on_delete=models.CASCADE, related_name="reactions"
    )
    comment = models.ForeignKey(
        "Comment", null=True, blank=True, on_delete=models.CASCADE,
        related_name="reactions",
    )
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="reactions"
    )
    kind = models.CharField(max_length=5, choices=Kind.choices, default=DEFAULT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # One row per person per thing, enforced separately for each target
            # because a partial unique index over a nullable column treats every
            # NULL as distinct — a single UniqueConstraint(post, user) would let
            # one person leave unlimited reactions on comments.
            models.UniqueConstraint(
                fields=["post", "user"],
                condition=models.Q(comment__isnull=True),
                name="one_reaction_per_post_per_user",
            ),
            models.UniqueConstraint(
                fields=["comment", "user"],
                condition=models.Q(comment__isnull=False),
                name="one_reaction_per_comment_per_user",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(post__isnull=False, comment__isnull=True)
                    | models.Q(post__isnull=True, comment__isnull=False)
                ),
                name="reaction_targets_exactly_one_thing",
            ),
        ]
        indexes = [
            # Each serves the GROUP BY that builds a whole page's summaries in
            # one query. Without them that is a scan per feed render.
            models.Index(fields=["post", "kind"]),
            models.Index(fields=["comment", "kind"]),
        ]

    def __str__(self):
        target = f"post {self.post_id}" if self.post_id else f"comment {self.comment_id}"
        return f"{self.user_id} reacted {self.kind} to {target}"

    @property
    def emoji(self):
        return self.EMOJI[self.kind]

    @classmethod
    def picker_for(cls, target):
        """
        The reactions offered on a post or a comment, as (value, label, emoji).

        Takes the target because the set may one day depend on it — see the
        note about SCAM and ALERT in the class docstring. It ignores it today,
        and that is fine; the call sites are already shaped for the day it
        does not.
        """
        return [(k.value, k.label, cls.EMOJI[k]) for k in cls.Kind]
