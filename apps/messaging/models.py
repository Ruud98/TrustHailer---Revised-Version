"""
Direct messages, between people who already have a reason to talk.

WHY THIS IS NOT AN OPEN INBOX
-----------------------------
The chain that produces the only thing this site sells is

    a reason to talk -> placement -> review -> reputation

An open inbox routes around the first link. Two strangers who can message
freely will settle the deal in the thread, never record a placement, and never
be reviewable. Nothing breaks loudly; the reputation data simply stops filling
up, and by the time that is obvious there is a year of it missing.

So a thread needs a reason, and `can_message` is the list of them. Every one is
a deliberate act: following back, confirming a placement, or publishing a
listing and thereby inviting answers to it.

INTEREST IS THE FIRST DOOR NOW
------------------------------
`IntroRequest` used to be it. You asked to be introduced, the other side said
yes, two numbers changed hands, and only then could you speak. The order was
backwards: people were asked to decide whether to hand over a number before
they had exchanged a word, and the commonest answer to a question asked that
way is no.

`Interest` replaces it. Tapping "I am interested" on a listing opens the thread
with a line saying which car it is about; the number is a thing either person
sends later, in the conversation, once they want to. The consent that used to
live in an approve button now lives in a Share my number button, at the point
where somebody actually knows whether they want to.

What that costs, and why it is still worth paying: an interest is one-sided, so
a listing becomes an invitation anyone may answer. The constraint below holds
it to one per person per listing, which is the same brake the old
one-pending-introduction rule provided. Existing approved introductions still
count as a reason to talk. Those records stand; they are simply no longer the
way in.

WHAT FACEBOOK ACTUALLY DOES, FOR THE RECORD
-------------------------------------------
Not an open inbox either. A stranger's first message goes to Message requests,
to be accepted or ignored — the recipient reads the message and then decides,
which is the order `Interest` now follows too.

TWO PEOPLE, NEVER MORE
----------------------
A group chat is a different product with different moderation problems, and
every rule below assumes exactly two participants.
"""
from django.db import models
from django.utils import timezone

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

    # WHO HAS HANDED OVER A NUMBER, AND IN WHICH CONVERSATION
    # ------------------------------------------------------
    # Two stamps rather than one, because sharing a number is not a mutual act
    # and treating it as one would release a number its owner never offered.
    # Each person lifts redaction for their own messages only, by pressing a
    # button that says so.
    #
    # Per thread, not per pair: "I gave this owner my number" must not come to
    # mean "my number now survives in every conversation I am in".
    a_shared_number_at = models.DateTimeField(null=True, blank=True)
    b_shared_number_at = models.DateTimeField(null=True, blank=True)

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

    def _side(self, user):
        return "a" if self.user_a_id == user.pk else "b"

    def number_shared_by(self, user):
        """Has this person handed over their number in this conversation?"""
        return getattr(self, f"{self._side(user)}_shared_number_at") is not None

    def share_number(self, user):
        """
        Record that they did, once. Pressing the button twice is not a second
        decision, and re-stamping would make the record say it was.
        """
        field = f"{self._side(user)}_shared_number_at"
        if getattr(self, field) is not None:
            return False
        setattr(self, field, timezone.now())
        self.save(update_fields=[field, "updated_at"])
        return True


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


class Interest(TimeStampedModel):
    """
    Somebody tapped "I am interested" on a listing, and a conversation began.

    WHAT IT IS FOR, IN ORDER OF IMPORTANCE
    --------------------------------------
    1. It is the reason two strangers may talk. Publishing a listing is an
       invitation; this is somebody accepting it.
    2. It is what the conversation is about. The thread head shows the listing,
       so an owner with four adverts and eleven threads can tell which is
       which, and the placement button knows which car to record.
    3. It is the brake. One per person per listing, so a keen driver cannot
       reopen the same pitch six times in an afternoon.

    The row outlives the listing being taken down and the thread going quiet,
    deliberately: it is the evidence that these two had a reason to be talking,
    and deleting it would silently revoke a permission.
    """

    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="interests"
    )
    thread = models.ForeignKey(
        Thread, on_delete=models.CASCADE, related_name="interests"
    )

    # Exactly one, enforced below. A car listing is the common case, a driver
    # answering an advert. The driver-listing side is the same move made by an
    # owner who has seen a driver they want.
    vehicle_listing = models.ForeignKey(
        "listings.VehicleListing", null=True, blank=True,
        on_delete=models.CASCADE, related_name="interests",
    )
    driver_listing = models.ForeignKey(
        "listings.DriverListing", null=True, blank=True,
        on_delete=models.CASCADE, related_name="interests",
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "vehicle_listing"], name="one_interest_per_vehicle"
            ),
            models.UniqueConstraint(
                fields=["user", "driver_listing"],
                name="one_interest_per_driver_listing",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(vehicle_listing__isnull=False, driver_listing__isnull=True)
                    | models.Q(vehicle_listing__isnull=True, driver_listing__isnull=False)
                ),
                name="interest_names_exactly_one_listing",
            ),
        ]
        indexes = [models.Index(fields=["thread", "-created_at"])]

    def __str__(self):
        return f"Interest<{self.user_id} in {self.listing}>"

    @property
    def listing(self):
        return self.vehicle_listing or self.driver_listing

    @property
    def is_about_a_car(self):
        return self.vehicle_listing_id is not None
