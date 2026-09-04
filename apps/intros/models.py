"""
Introductions — the double opt-in that releases two phone numbers.

WHAT THIS IS
------------
Everything before this point is a catalogue. This is the part that makes it a
marketplace: somebody sees a car or a driver, asks to be put in touch, the
other side agrees, and only then do the numbers change hands. Contacts are
masked everywhere else on the site precisely so that this moment means
something.

WHY BOTH SIDES HAVE TO AGREE
----------------------------
A directory that hands over a number on one person's say-so is a lead-gen list,
and the people in it did not sign up to be one. The double opt-in is what makes
the number worth masking in the first place, and it is what a driver is
actually agreeing to when they publish a listing: not "anyone may phone me",
but "I will decide who does".

Both numbers release at the same instant, to both parties. Never one side
first — an exchange where only one person is reachable is not an introduction,
it is a lead, and it makes the site useful to exactly the sort of person we do
not want on it.

WHAT HAPPENED TO THE CREDITS
----------------------------
The spec had this sprint as "intros and credits": a wallet, a ledger, credit
packs, a payment provider and an idempotent webhook. The platform is free, so
none of that would be reachable code — a payments integration nobody can spend
money through is a liability that rots between the day it is written and the
day it might be wanted.

What survives is the seam. `credits_charged` is written on approval from
`apps.core.pricing`, which answers zero for everything, so the moment there is
ever a price the number lands in a field that already exists and every approval
in history can be read back consistently. See the pricing module for why the
zero is written down rather than skipped.
"""
import uuid
from datetime import timedelta

from django.db import models
from django.urls import reverse
from django.utils import timezone

from apps.core import pricing
from apps.core.models import TimeStampedModel


class IntroRequestQuerySet(models.QuerySet):
    def pending(self):
        return self.filter(status=IntroRequest.Status.PENDING)

    def stale(self):
        """Pending, and past the day it should have been answered."""
        return self.pending().filter(expires_at__lt=timezone.now())

    def with_display_data(self):
        return self.select_related(
            "from_user__profile", "from_user__verification",
            "to_user__profile", "to_user__verification",
            "vehicle_listing__suburb__city", "driver_listing__home_suburb__city",
        )


class IntroRequest(TimeStampedModel):
    """
    One person asking to be put in touch with another about one listing.

    THE SEVEN-DAY EXPIRY IS FOR THE ASKER, NOT THE ANSWERER
    -------------------------------------------------------
    A request that sits open forever tells a driver nothing. Owners go quiet —
    they place someone, they lose the phone, they change their mind and cannot
    face saying so. After a week the honest reading is no, and saying so lets
    the driver stop waiting and ask somebody else. It also stops the inbox
    filling with requests nobody will ever answer, which is what teaches people
    to stop opening it.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting for a reply"
        APPROVED = "approved", "Approved"
        DECLINED = "declined", "Declined"
        EXPIRED = "expired", "Expired"
        WITHDRAWN = "withdrawn", "Withdrawn"

    EXPIRY_DAYS = 7

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    from_user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="intros_sent"
    )
    to_user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="intros_received"
    )

    # Exactly one of these, enforced by a check constraint below. A request is
    # always about a specific listing: "can I have your number" with nothing
    # attached is the thing we are replacing.
    vehicle_listing = models.ForeignKey(
        "listings.VehicleListing", null=True, blank=True,
        on_delete=models.CASCADE, related_name="intro_requests",
    )
    driver_listing = models.ForeignKey(
        "listings.DriverListing", null=True, blank=True,
        on_delete=models.CASCADE, related_name="intro_requests",
    )

    message = models.TextField(max_length=400)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)

    # Always zero while the platform is free. Written anyway — see the module
    # docstring, and `apps.core.pricing`.
    credits_charged = models.PositiveSmallIntegerField(default=0)

    contacts_released_at = models.DateTimeField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()

    objects = IntroRequestQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # One open request per person per listing. Without this, a keen
            # driver sends the same owner four requests in an afternoon and the
            # owner stops opening the inbox. Scoped to pending on purpose: a
            # declined request may reasonably be sent again months later, when
            # the car is free or the driver has more experience.
            models.UniqueConstraint(
                fields=["from_user", "vehicle_listing"],
                condition=models.Q(status="pending"),
                name="one_pending_intro_per_vehicle",
            ),
            models.UniqueConstraint(
                fields=["from_user", "driver_listing"],
                condition=models.Q(status="pending"),
                name="one_pending_intro_per_driver",
            ),
            # A request is about exactly one listing, never both and never
            # neither. "Neither" would be a contact request with no context,
            # which is the thing this whole flow exists to replace.
            models.CheckConstraint(
                condition=(
                    models.Q(vehicle_listing__isnull=False, driver_listing__isnull=True)
                    | models.Q(vehicle_listing__isnull=True, driver_listing__isnull=False)
                ),
                name="intro_names_exactly_one_listing",
            ),
        ]
        indexes = [
            models.Index(fields=["to_user", "status", "-created_at"]),
            models.Index(fields=["from_user", "status", "-created_at"]),
            models.Index(fields=["status", "expires_at"]),
        ]

    def __str__(self):
        return f"Intro {self.uuid} ({self.status})"

    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(days=self.EXPIRY_DAYS)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("intros:detail", args=[self.uuid])

    # ------------------------------------------------------------- reading

    @property
    def listing(self):
        return self.vehicle_listing or self.driver_listing

    @property
    def is_about_a_car(self):
        return self.vehicle_listing_id is not None

    @property
    def is_expired(self):
        """
        Past its date and still unanswered.

        Read as a property as well as swept by `expire_intros`, so a request
        that times out overnight reads correctly on the page even if the cron
        has not run. The command exists to make the stored status match; this
        property is what stops the page lying in the meantime.
        """
        return self.status == self.Status.PENDING and self.expires_at <= timezone.now()

    @property
    def effective_status(self):
        return self.Status.EXPIRED if self.is_expired else self.status

    @property
    def is_open(self):
        return self.status == self.Status.PENDING and not self.is_expired

    @property
    def contacts_released(self):
        return self.status == self.Status.APPROVED and self.contacts_released_at is not None

    def other_party(self, user):
        return self.to_user if user.pk == self.from_user_id else self.from_user

    def involves(self, user):
        return user.pk in (self.from_user_id, self.to_user_id)

    # ------------------------------------------------------------ writing

    def approve(self, *, by=None):
        """
        Say yes, and release both numbers in the same breath.

        `credits_charged` is asked of the pricing module rather than assumed to
        be zero, so the day there is a price this line does not need finding.
        The payer is whoever approves — the hiring side of the deal — and
        drivers return zero from `price_for` regardless.
        """
        price = pricing.price_for(pricing.Action.INTRO_APPROVE, user=by or self.to_user)
        now = timezone.now()
        self.status = self.Status.APPROVED
        self.credits_charged = price.credits
        self.contacts_released_at = now
        self.responded_at = now
        self.save(update_fields=[
            "status", "credits_charged", "contacts_released_at", "responded_at", "updated_at",
        ])

    def decline(self):
        self.status = self.Status.DECLINED
        self.responded_at = timezone.now()
        self.save(update_fields=["status", "responded_at", "updated_at"])

    def withdraw(self):
        self.status = self.Status.WITHDRAWN
        self.responded_at = timezone.now()
        self.save(update_fields=["status", "responded_at", "updated_at"])
