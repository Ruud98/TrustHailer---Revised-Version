"""
Following — one person choosing to hear from another.

WHY THIS IS ASYMMETRIC, AND WHY THAT MATTERS HERE
-------------------------------------------------
Instagram's shape, not Facebook's: I follow you, you owe me nothing, and there
is no request to accept. That is the right fit for a marketplace where most of
the interesting people are strangers. An owner with four cars does not want to
approve two hundred friend requests to be worth following, and a rider who
wants to keep an eye on a good mechanic should not have to be approved by them.

The cost of asymmetry is that following is an act done *to* somebody, so the
block rules below are not optional garnish — they are the whole reason this is
safe to ship.

FOLLOWING IS NOT AN INTRODUCTION
-------------------------------
Nothing here releases a phone number, and nothing here should ever start to.
`IntroRequest` is the only path to somebody's contact details, it is approved
one at a time, and every review on this site traces back to one. A follow is
strictly "show me their posts": if a follow ever becomes a way to reach
somebody directly, it becomes a way to bypass the thing that makes reviews
mean anything.

COUNTS ARE NOT DENORMALISED, DELIBERATELY
------------------------------------------
`Profile.rating_avg` and `Post.reaction_count` are denormalised because they
appear on every card in a list, where counting per card is a query per card.
Follower counts appear on one profile at a time — two COUNTs on a page that
already runs several. A stored counter would buy nothing and would need
keeping in sync through follow, unfollow, block and account deletion, which is
four places to get it wrong for no measurable gain. Revisit it the day a
follower count appears on a browse card, and not before.
"""
from django.db import models

from apps.core.models import TimeStampedModel


class FollowQuerySet(models.QuerySet):
    def followed_ids(self, user):
        """The ids this user follows. One query, used to build the feed."""
        if not user.is_authenticated:
            return []
        return list(self.filter(follower=user).values_list("following_id", flat=True))


class Follow(TimeStampedModel):
    """
    `follower` follows `following`. One row, one direction.

    A mutual follow is two rows, which is what makes "follows you" a fact this
    model can answer rather than something the interface has to imply.
    """

    follower = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="following_set"
    )
    following = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="follower_set"
    )

    objects = FollowQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["follower", "following"], name="one_follow_per_pair"
            ),
            # Following yourself would put your own posts in your following
            # feed twice and make every count read one too high.
            models.CheckConstraint(
                condition=~models.Q(follower=models.F("following")),
                name="cannot_follow_yourself",
            ),
        ]
        indexes = [
            # "Who do I follow" builds the following feed on every load of it;
            # "who follows them" builds the count and the list on a profile.
            models.Index(fields=["follower", "-created_at"]),
            models.Index(fields=["following", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.follower_id} follows {self.following_id}"
