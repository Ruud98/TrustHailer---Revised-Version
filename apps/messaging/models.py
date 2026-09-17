"""
Direct messages, between people who already have a reason to talk.

WHY THIS IS NOT AN OPEN INBOX
-----------------------------
`IntroRequest` is described in its own module as the double opt-in that
releases two phone numbers, and it is load-bearing far beyond that: a
`Placement` can only be recorded against an approved introduction, and a
`Review` can only exist against a confirmed placement. The chain is

    approved intro -> placement -> review -> the reputation that is the product

An open inbox routes around the first link. Two strangers who can message
freely will settle the deal in the thread, never file an introduction, never
record a placement, and never be reviewable. Nothing breaks loudly; the
reputation data simply stops filling up, and by the time that is obvious there
is a year of it missing.

So a thread needs one of three existing relationships — see `can_message`. All
three are things both people already agreed to. None of them can be created by
the sender alone, which is the property that matters.

WHAT FACEBOOK ACTUALLY DOES, FOR THE RECORD
-------------------------------------------
Not an open inbox either. A stranger's first message goes to Message requests,
to be accepted or ignored. That is structurally what `IntroRequest` already is
here, specialised for a market where the thing being requested is a phone
number rather than attention.

TWO PEOPLE, NEVER MORE
----------------------
A group chat is a different product with different moderation problems, and
every rule below assumes exactly two participants.
"""
from django.db import models

from apps.core.models import TimeStampedModel


class ThreadQuerySet(models.QuerySet):
    def involving(self, user):
        return self.filter(models.Q(user_a=user) | models.Q(user_b=user))

    def with_display_data(self):
        return self.select_related(
            "user_a__profile", "user_b__profile"
        ).prefetch_related("messages")


class Thread(TimeStampedModel):
    """
    One conversation between exactly two people.

    THE PAIR IS STORED IN A FIXED ORDER
    -----------------------------------
    `user_a` always holds the lower primary key. Without that convention the
    same two people produce two different rows depending on who wrote first,
    the unique constraint cannot see them as the same pair, and the inbox shows
    a conversation twice. `between()` is the only sanctioned way to build one,
    and the check constraint stops anything else.
    """

    user_a = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="threads_as_a"
    )
    user_b = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="threads_as_b"
    )

    # Denormalised, unlike the follower counts, and for the opposite reason:
    # the inbox sorts every thread by it on every load. Ordering by a
    # subquery-of-latest-message across a list is exactly the per-row cost the
    # rest of this codebase denormalises to avoid.
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = ThreadQuerySet.as_manager()

    class Meta:
        ordering = ["-last_message_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user_a", "user_b"], name="one_thread_per_pair"
            ),
            # Enforces both "no talking to yourself" and the ordering
            # convention in a single check the database cannot be talked out of.
            models.CheckConstraint(
                condition=models.Q(user_a__lt=models.F("user_b")),
                name="thread_pair_is_ordered_and_distinct",
            ),
        ]

    def __str__(self):
        return f"Thread<{self.user_a_id}, {self.user_b_id}>"

    @classmethod
    def between(cls, one, other):
        """Fetch or create the single thread for this pair, in canonical order."""
        low, high = sorted([one, other], key=lambda u: u.pk)
        thread, _ = cls.objects.get_or_create(user_a=low, user_b=high)
        return thread

    def other_party(self, user):
        return self.user_b if self.user_a_id == user.pk else self.user_a

    def includes(self, user):
        return user.is_authenticated and user.pk in (self.user_a_id, self.user_b_id)


class Message(TimeStampedModel):
    """
    One message. Stored as it will be shown.

    REDACTION HAPPENS ON THE WAY IN, NOT ON THE WAY OUT
    ---------------------------------------------------
    `body` is already redacted when it is saved, where redaction applies at
    all. Redacting on render would mean the raw number sits in the database
    and every future template, export, admin screen and email is one missed
    call away from leaking it. Stored clean, it is clean everywhere.

    Which threads redact is `services.numbers_allowed`: once an introduction
    between these two has been approved they already have each other's
    numbers, and stripping them there is theatre. Before that, a DM must not
    become the shortcut around the gate.
    """

    thread = models.ForeignKey(
        Thread, on_delete=models.CASCADE, related_name="messages"
    )
    sender = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="messages_sent"
    )
    body = models.TextField(max_length=2000)

    # Set when the OTHER person opens the thread. Null means unread by them.
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["thread", "created_at"]),
            # Serves the unread badge: "messages in my threads, not mine, unread".
            models.Index(fields=["sender", "read_at"]),
        ]

    def __str__(self):
        return f"Message<{self.sender_id} in {self.thread_id}>"
