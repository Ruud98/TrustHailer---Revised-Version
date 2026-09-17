"""
In-app notifications — the bell.

WHY THE MESSAGE IS WRITTEN ONCE AND STORED, NOT A GENERIC FOREIGN KEY
-----------------------------------------------------------------------
Every other "this event refers to that record" relationship in this codebase
(`Report.target`, for one) uses a `GenericForeignKey` and renders text from the
live object at read time. Notifications do the opposite on purpose: the
sentence is composed once, at the moment the event happens, and stored as
plain text.

Two reasons. First, a notification has to keep making sense after the thing it
describes has changed or gone: "Thabo asked about your Corolla" should still
read correctly a year later even if Thabo has since deleted his account or the
introduction expired — rendering it live from a `GenericForeignKey` would leave
either a broken link or a sentence with a hole in it. Second, it is one query
instead of one-plus-N: a notification list is exactly the kind of view that
gets opened often and skimmed fast, and resolving a generic relation per row to
build a sentence is the wrong place to spend that budget.

`url` is stored alongside the message for the same reason — where the link
should go is a fact about the moment the notification was created, not a
property of whatever the target object currently is.

WHY THIS IS NOT A SIXTH ITEM IN THE BOTTOM NAV
-----------------------------------------------
The interface principles this project has followed since Sprint 0 fix the
bottom nav at five items and put everything else in the avatar menu. A bell
icon in the top bar would also break the stated rule of exactly three things
there: logo, search, avatar. So the unread count is a badge ON the avatar
button — see `templates/partials/_topbar.html` — and "Notifications" is a line
in the dropdown, the same way Verification and Placements already are.
"""
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


class NotificationQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(recipient=user)

    def unread(self):
        return self.filter(is_read=False)


class Notification(TimeStampedModel):
    """
    One event, already turned into a sentence somebody can read at a glance.

    THE LIST OF KINDS IS DELIBERATELY SHORT
    ----------------------------------------
    Every kind here is something that changes what the recipient should do
    next: answer a request, confirm a placement, read a review, reply to a
    reply. A like is not on this list — the feed is already a deliberate step
    down from the thing it replaces (see apps/feed/models.py), and a
    notification for every like would undo that in the one place people check
    most often.
    """

    class Kind(models.TextChoices):
        INTRO_REQUESTED = "intro_requested", "Introduction requested"
        INTRO_APPROVED = "intro_approved", "Introduction approved"
        INTRO_DECLINED = "intro_declined", "Introduction declined"
        PLACEMENT_CONFIRM = "placement_confirm", "Placement needs confirming"
        PLACEMENT_CONFIRMED = "placement_confirmed", "Placement confirmed"
        REVIEW_PUBLISHED = "review_published", "Review published"
        POST_COMMENT = "post_comment", "New comment"
        COMMENT_REPLY = "comment_reply", "New reply"
        CLAIM_APPROVED = "claim_approved", "Claim approved"
        CLAIM_REJECTED = "claim_rejected", "Claim rejected"
        BUSINESS_VERIFIED = "business_verified", "Business verified"
        NEW_FOLLOWER = "new_follower", "New follower"
        NEW_MESSAGE = "new_message", "New message"

    recipient = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="notifications"
    )
    # Who did the thing, when it makes sense to show an avatar next to the
    # sentence. Null for staff-triggered events (a claim decision, a business
    # verification) — those are the platform speaking, not a person, and
    # naming which staff member acted is not useful to a recipient.
    actor = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    message = models.CharField(max_length=200)
    url = models.CharField(max_length=300, blank=True)

    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    objects = NotificationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "is_read", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.kind} → {self.recipient_id}: {self.message}"

    def mark_read(self):
        if self.is_read:
            return
        self.is_read = True
        self.read_at = timezone.now()
        self.save(update_fields=["is_read", "read_at", "updated_at"])
