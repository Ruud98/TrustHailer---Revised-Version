"""
Reporting and blocking.

TWO DIFFERENT TOOLS FOR TWO DIFFERENT PROBLEMS
----------------------------------------------
A report says "somebody should look at this". A block says "I never want to see
this person again". They are not the same request and collapsing them into one
button serves neither: people who want to be left alone should not have to
accuse anybody, and people reporting a scam should not have to hide the
evidence from themselves to do it.

Reports go to staff. Blocks take effect immediately and privately.

WHAT DELIBERATELY DOES NOT EXIST HERE
-------------------------------------
A public "known scammers" board. In South African law truth alone is not a
complete defence to defamation — publication must also be in the public
benefit — and as the platform we can be joined to the claim. A page where users
name individuals is a standing invitation to be sued over somebody else's
sentence, and it is trivially weaponised against a competitor.

Bad actors are handled by staff-reviewed suspension, with the evidence on file
and a right of reply. User accusations live in this queue, not on the site. If
a public warning is ever needed, the platform publishes it in its own name,
having checked it. Do not build the board.
"""
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


class ReportQuerySet(models.QuerySet):
    def open(self):
        return self.filter(status=Report.Status.OPEN)


class Report(TimeStampedModel):
    """
    Somebody flagging a listing, a person or a message for staff.

    A generic foreign key rather than five nullable columns: the things worth
    reporting will keep growing — posts and comments arrive with the feed — and
    each new one should be a line in a dict, not a migration and a new branch
    in every query.
    """

    class Reason(models.TextChoices):
        SCAM = "scam", "It looks like a scam"
        FAKE = "fake", "The listing is not real"
        ABUSE = "abuse", "Abusive or threatening"
        SPAM = "spam", "Spam"
        WRONG_INFO = "wrong_info", "The details are wrong"
        OTHER = "other", "Something else"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ACTIONED = "actioned", "Actioned"
        DISMISSED = "dismissed", "Dismissed"

    reporter = models.ForeignKey(
        "accounts.User", null=True, on_delete=models.SET_NULL, related_name="reports_made"
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    target = GenericForeignKey("content_type", "object_id")

    reason = models.CharField(max_length=15, choices=Reason.choices)
    detail = models.TextField(max_length=1000, blank=True)

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    handled_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    handled_at = models.DateTimeField(null=True, blank=True)
    staff_note = models.CharField(max_length=300, blank=True)

    objects = ReportQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # One report per person per thing. Somebody who reports the same
            # listing six times is not six people, and a queue that counts them
            # as six makes the loudest complaint look like the most serious.
            models.UniqueConstraint(
                fields=["reporter", "content_type", "object_id"],
                name="one_report_per_person_per_target",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self):
        return f"{self.get_reason_display()} — {self.content_type} {self.object_id}"

    def resolve(self, *, by, actioned, note=""):
        self.status = self.Status.ACTIONED if actioned else self.Status.DISMISSED
        self.handled_by = by
        self.handled_at = timezone.now()
        self.staff_note = note or self.staff_note
        self.save(update_fields=[
            "status", "handled_by", "handled_at", "staff_note", "updated_at",
        ])


class Block(TimeStampedModel):
    """
    One person choosing not to deal with another.

    BLOCKING IS SYMMETRICAL IN EFFECT, NOT IN RECORD
    ------------------------------------------------
    The row is one-directional — A blocked B, and only A is told. The *effect*
    runs both ways: neither can request an introduction from the other, and
    neither appears in the other's browse results. A one-way effect would leave
    the person who blocked still visible to, and reachable by, the person they
    blocked, which is worse than useless — it is a false sense of safety.

    Nobody is told they have been blocked. Telling them converts a quiet exit
    into a confrontation, which is exactly what somebody using this button is
    trying to avoid.
    """

    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="blocks_made"
    )
    blocked_user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="blocked_by"
    )
    reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "blocked_user"], name="one_block_per_pair"
            ),
            # Blocking yourself is meaningless and would hide your own listings
            # from your own browse page.
            models.CheckConstraint(
                condition=~models.Q(user=models.F("blocked_user")),
                name="cannot_block_yourself",
            ),
        ]

    def save(self, *args, **kwargs):
        """
        Blocking severs the follow in both directions.

        A block that left the rows in place would keep feeding the blocked
        person's posts into the blocker's following feed, and would leave the
        blocker sitting in a follower list they asked to be out of — which is
        the opposite of what the button says it does. Done here rather than in
        the view so that a block created by staff, by a test or by a future
        second entry point behaves the same way.
        """
        new = self._state.adding
        super().save(*args, **kwargs)
        if new:
            from apps.follows.services import drop_between

            drop_between(self.user, self.blocked_user)

    def __str__(self):
        return f"{self.user_id} blocked {self.blocked_user_id}"


def blocked_user_ids(user) -> set:
    """
    Everyone this user should not see, in either direction.

    Used by the browse and search surfaces and by the introduction flow. Kept
    as one function so there is a single answer to "who is hidden from whom" —
    two implementations of that would drift, and the drift would show up as a
    blocked person reappearing somewhere.
    """
    if not user or not user.is_authenticated:
        return set()

    made = Block.objects.filter(user=user).values_list("blocked_user_id", flat=True)
    received = Block.objects.filter(blocked_user=user).values_list("user_id", flat=True)
    return set(made) | set(received)


def is_blocked_between(user, other) -> bool:
    """True if either has blocked the other."""
    if not user or not user.is_authenticated or not other:
        return False
    return Block.objects.filter(
        models.Q(user=user, blocked_user=other) | models.Q(user=other, blocked_user=user)
    ).exists()
