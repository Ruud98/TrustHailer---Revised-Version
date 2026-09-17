import uuid
from datetime import timedelta
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.http import urlencode

from apps.core.models import TimeStampedModel
from apps.geo.models import CURRENCY_FOR_COUNTRY, Country


class Platform(models.Model):
    """
    Uber, Bolt, inDrive, and whatever comes next.

    Vehicle age limits live here as DATA, not as constants in code. Each
    platform sets its own rule, the rules differ by city, and they change
    without notice. Hardcoding "no older than 8 years" would quietly mislead
    owners the day it changes. Leave `max_vehicle_age_years` null until you have
    confirmed the current rule from the platform itself — a null means we simply
    don't warn, which is the honest default.
    """

    name = models.CharField(max_length=40)
    slug = models.SlugField(unique=True)
    country = models.CharField(max_length=2, choices=Country.choices, default=Country.ZA)
    max_vehicle_age_years = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Leave blank unless you've confirmed the platform's current rule.",
    )
    is_active = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name

    def age_limit_exceeded_by(self, year: int) -> bool:
        if not self.max_vehicle_age_years or not year:
            return False
        return (timezone.localdate().year - year) > self.max_vehicle_age_years


class Arrangement(models.TextChoices):
    WEEKLY = "weekly", "Fixed weekly rental"
    DAILY = "daily", "Daily rental"
    SHARE = "share", "Earnings share"
    RENT2OWN = "rent2own", "Rent to own"


class PaidBy(models.TextChoices):
    OWNER = "owner", "Owner pays"
    DRIVER = "driver", "Driver pays"
    SHARED = "shared", "Shared"
    NA = "na", "Not applicable"


class Transmission(models.TextChoices):
    MANUAL = "manual", "Manual"
    AUTO = "auto", "Automatic"


class FuelType(models.TextChoices):
    PETROL = "petrol", "Petrol"
    DIESEL = "diesel", "Diesel"
    HYBRID = "hybrid", "Hybrid"
    ELECTRIC = "electric", "Electric"


class VehicleListingQuerySet(models.QuerySet):
    def live(self):
        return self.filter(status=VehicleListing.Status.ACTIVE)

    def visible_to(self, user):
        """Active listings, plus the viewer's own drafts and paused listings."""
        if user.is_authenticated:
            return self.filter(
                models.Q(status=VehicleListing.Status.ACTIVE) | models.Q(owner=user)
            )
        return self.live()

    def hide_blocked(self, user):
        """
        Drop listings belonging to anyone this viewer has blocked, or who has
        blocked them.

        Applied on every browse surface rather than only on the profile page: a
        block that leaves somebody's car sitting in your search results has not
        done the thing the button promised. Imported listings have no owner and
        are never hidden by this.

        Imported inside the method because `apps.safety` imports these models —
        a module-level import would close the loop.
        """
        from apps.safety.models import blocked_user_ids

        hidden = blocked_user_ids(user)
        return self.exclude(owner_id__in=hidden) if hidden else self

    def imported(self):
        return self.filter(source=VehicleListing.Source.FACEBOOK)

    def unclaimed(self):
        return self.filter(owner__isnull=True)

    def ranked(self):
        """
        Boosted first, then members' own listings, then newest.

        `boost_expires_at` is null for the overwhelming majority of rows, so
        nulls_last is doing the real work on the first key — without it
        Postgres sorts nulls first on a descending order and every unboosted
        listing outranks every boosted one.

        The middle key is the import policy made visible. A listing somebody
        posted here themselves is worth more than one we copied off Facebook:
        it is current, the person behind it is on the site, and an introduction
        can actually be arranged. Imported adverts fill the page while the site
        is young, and they should sink under real listings the moment there are
        any. Ordering on `source` directly would do the opposite — "facebook"
        sorts before "user" — so the rank is explicit.
        """
        return self.annotate(
            source_rank=models.Case(
                models.When(source=VehicleListing.Source.FACEBOOK, then=1),
                default=0,
                output_field=models.IntegerField(),
            )
        ).order_by(
            models.F("boost_expires_at").desc(nulls_last=True),
            "source_rank",
            "-created_at",
        )

    def with_display_data(self):
        return self.select_related(
            "owner__profile", "owner__verification", "suburb__city"
        ).prefetch_related("platforms", "photos")


class VehicleListing(TimeStampedModel):
    """
    A car offered for rent to an e-hailing driver.

    THE `*_paid_by` FIELDS ARE THE POINT
    ------------------------------------
    They look like over-engineering. They are the substance of every deal in
    this market and the source of most disputes: who buys fuel, who fixes the
    clutch, whose name the insurance is in. In a Facebook group all of that is
    buried in free text, so nobody can filter on it and half the conversations
    are people re-establishing the same five facts. Structuring them is what
    makes "maintenance covered" a filter — and that filter is a real reason to
    leave the group.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Live"
        PAUSED = "paused", "Paused"
        PLACED = "placed", "Driver placed"
        ARCHIVED = "archived", "Archived"

    class Source(models.TextChoices):
        USER = "user", "Posted by the owner"
        FACEBOOK = "facebook", "Copied from Facebook"

    # An imported advert is a snapshot of something posted somewhere else, and
    # it goes stale fast — the car is usually taken within a fortnight and
    # nobody comes back to tell us. `expire_imports` archives them at this age.
    # Better an empty shelf than a shelf of cars that are already gone: one
    # dead lead is enough to teach a driver the site is a waste of airtime.
    IMPORT_STALE_DAYS = 21

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    owner = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="vehicle_listings",
        null=True,
        blank=True,
        help_text="Null on an imported advert nobody has claimed yet.",
    )

    # --- provenance
    #
    # Everything below is null or blank on an ordinary listing and carries the
    # whole story on an imported one. The invariant, asserted in `clean()`:
    # a listing has an owner, or it has a source URL, and an imported listing
    # keeps its source URL for good — a claim gives it an owner without
    # rewriting where it came from.
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.USER)
    source_url = models.URLField(
        blank=True,
        max_length=500,
        help_text="Link to the original post. Required on an import — it is the attribution.",
    )
    source_author_name = models.CharField(
        max_length=80,
        blank=True,
        help_text="Who posted the original, as shown on the post. For credit, nothing else.",
    )
    source_posted_at = models.DateTimeField(
        null=True, blank=True, help_text="When the original went up, if the post shows it."
    )
    imported_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="listings_imported",
    )
    claimed_at = models.DateTimeField(null=True, blank=True)

    # --- the car
    make = models.CharField(max_length=40)
    model = models.CharField(max_length=60)
    year = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1990), MaxValueValidator(2100)]
    )
    transmission = models.CharField(max_length=10, choices=Transmission.choices)
    fuel_type = models.CharField(max_length=10, choices=FuelType.choices, default=FuelType.PETROL)
    colour = models.CharField(max_length=30, blank=True)
    platforms = models.ManyToManyField(Platform, related_name="listings", blank=True)

    # --- commercial terms
    arrangement = models.CharField(max_length=10, choices=Arrangement.choices)
    currency = models.CharField(max_length=3, default="ZAR")
    weekly_rate = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    daily_rate = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    deposit_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    earnings_share_pct = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MaxValueValidator(100)],
        help_text="Driver's share of gross earnings.",
    )
    rent_to_own_months = models.PositiveSmallIntegerField(null=True, blank=True)

    # --- who pays what
    fuel_paid_by = models.CharField(max_length=8, choices=PaidBy.choices, default=PaidBy.DRIVER)
    maintenance_paid_by = models.CharField(
        max_length=8, choices=PaidBy.choices, default=PaidBy.OWNER
    )
    insurance_paid_by = models.CharField(
        max_length=8, choices=PaidBy.choices, default=PaidBy.OWNER
    )
    licensing_paid_by = models.CharField(
        max_length=8, choices=PaidBy.choices, default=PaidBy.OWNER
    )
    tracker_paid_by = models.CharField(max_length=8, choices=PaidBy.choices, default=PaidBy.OWNER)

    # --- condition and requirements
    has_tracker = models.BooleanField(default=False)
    has_insurance = models.BooleanField(default=False)
    weekly_km_limit = models.PositiveIntegerField(
        null=True, blank=True, help_text="Leave blank for unlimited."
    )
    min_experience_years = models.PositiveSmallIntegerField(default=0)
    requires_prdp = models.BooleanField(default=True)

    # --- placement
    suburb = models.ForeignKey("geo.Suburb", on_delete=models.PROTECT, related_name="listings")
    available_from = models.DateField(null=True, blank=True)
    description = models.TextField(max_length=2000, blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    boost_expires_at = models.DateTimeField(null=True, blank=True)
    view_count = models.PositiveIntegerField(default=0)
    published_at = models.DateTimeField(null=True, blank=True)

    objects = VehicleListingQuerySet.as_manager()

    # ------------------------------------------------------ servicing
    #
    # All four are optional and all four are the owner's. A car with no
    # interval set simply has no reminders, and nothing below fires.
    #
    # The odometer is logged by the OWNER, not the driver. That was a
    # deliberate choice and it has a cost worth writing down: the reading is
    # only as fresh as the last time the owner updated it, so a reminder fires
    # late if they leave it. `odometer_at` exists so the interface can say how
    # stale the figure is rather than presenting a month-old number as today's.
    service_interval_km = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="How often this car needs a service, in kilometres.",
    )
    service_warn_km = models.PositiveIntegerField(
        default=1000,
        help_text="How far ahead to warn. 1000 on a 15000 interval warns at "
                  "14000 and again at 15000.",
    )
    odometer_km = models.PositiveIntegerField(null=True, blank=True)
    odometer_at = models.DateTimeField(null=True, blank=True)
    # Who last confirmed it. The driver is the only person who sees this car
    # daily, so their reading is the current one and the owner's is a estimate
    # of a estimate. Recorded so the page can say whose figure it is showing.
    odometer_by = models.ForeignKey(
        "accounts.User", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="odometer_readings",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "suburb"]),
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["owner", "status"]),
            models.Index(fields=["status", "arrangement", "weekly_rate"]),
            models.Index(fields=["source", "status"]),
        ]
        constraints = [
            # A listing has an owner, or it says where it came from. Never
            # neither: a row with no owner and no source URL is a car nobody
            # can be asked about, nobody can claim and nobody can trace, on a
            # site whose whole product is knowing who you are dealing with.
            #
            # This is a database constraint rather than a `clean()` because the
            # owner is attached after the form validates on the ordinary create
            # path — a model-level check would fire on every owner listing on
            # its way in and be wrong every time.
            models.CheckConstraint(
                condition=models.Q(owner__isnull=False) | ~models.Q(source_url=""),
                name="listing_has_an_owner_or_a_source",
            ),
        ]

    def __str__(self):
        return f"{self.year} {self.make} {self.model} — {self.suburb.name}"

    def save(self, *args, **kwargs):
        # Currency follows the country the car sits in. Zimbabwean rentals are
        # quoted in USD in practice, so deriving it beats asking.
        if self.suburb_id and not self.pk:
            self.currency = CURRENCY_FOR_COUNTRY.get(self.suburb.city.province.country, "ZAR")
        if self.status == self.Status.ACTIVE and not self.published_at:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("listings:detail", args=[self.uuid])

    # --------------------------------------------------------- provenance

    @property
    def is_imported(self):
        return self.source == self.Source.FACEBOOK

    @property
    def is_claimed(self):
        """An import that has found its real owner."""
        return self.is_imported and self.owner_id is not None

    @property
    def is_claimable(self):
        """
        Live, imported, and nobody has been confirmed as the owner yet.

        A claimed import is not claimable a second time. The remedy for a wrong
        approval is staff reversing it, not a race between two strangers.
        """
        return self.is_imported and self.owner_id is None and self.is_live

    @property
    def has_contactable_owner(self):
        """
        Whether there is a member behind this listing to introduce anyone to.

        False for an unclaimed import — which is why the detail page offers a
        link to the original post there instead of an introduction button. We
        hold no contact details for these adverts at all: the description is
        scrubbed on the way in, and the phone number was never copied.
        """
        return self.owner_id is not None

    @property
    def stale_after(self):
        if not self.is_imported or not self.published_at:
            return None
        return self.published_at + timedelta(days=self.IMPORT_STALE_DAYS)

    # ------------------------------------------------------------- display

    @property
    def title(self):
        return f"{self.year} {self.make} {self.model}"

    @property
    def primary_photo(self):
        photos = list(self.photos.all())
        if not photos:
            return None
        return next((p for p in photos if p.is_primary), photos[0])

    @property
    def headline_price(self):
        """The number a driver actually compares. One per arrangement."""
        if self.arrangement == Arrangement.WEEKLY and self.weekly_rate is not None:
            return self.weekly_rate, "per week"
        if self.arrangement == Arrangement.DAILY and self.daily_rate is not None:
            return self.daily_rate, "per day"
        if self.arrangement == Arrangement.SHARE and self.earnings_share_pct is not None:
            return self.earnings_share_pct, "% to driver"
        if self.arrangement == Arrangement.RENT2OWN and self.weekly_rate is not None:
            return self.weekly_rate, "per week to own"
        return None, ""

    @property
    def is_boosted(self):
        return bool(self.boost_expires_at and self.boost_expires_at > timezone.now())

    @property
    def is_live(self):
        return self.status == self.Status.ACTIVE

    @property
    def age_years(self):
        return timezone.localdate().year - self.year

    # ------------------------------------------------------ servicing

    @property
    def last_service_km(self):
        """
        The odometer at the most recent logged service, or None.

        Derived from the log rather than stored, so it cannot disagree with the
        entry it came from — and correcting a mistyped service reading corrects
        the schedule at the same time.
        """
        note = (
            self.notes.filter(kind="service", odometer_km__isnull=False)
            .order_by("-happened_on", "-created_at")
            .first()
        )
        return note.odometer_km if note else None

    @property
    def next_service_km(self):
        """
        Where the next service falls, or None if we cannot honestly say.

        Needs both an interval and a service to count from. Without a logged
        service there is no baseline, and inventing one — from today's reading,
        say — would quietly tell somebody their car is fine when nobody knows.
        """
        if not self.service_interval_km:
            return None
        last = self.last_service_km
        if last is None:
            return None
        return last + self.service_interval_km

    # How long two readings must be apart before the gap between them is worth
    # calling a rate, and how far forward that rate may be carried.
    MIN_RATE_WINDOW_DAYS = 21
    MAX_PROJECTION_DAYS = 120

    @property
    def km_per_week(self):
        """
        How fast this car covers ground, or None if nothing says.

        Measured first, declared second. Two dated readings give the real
        figure for THIS car with THIS driver, which beats any number somebody
        typed into a form months ago. `weekly_km_limit` is the fallback because
        an owner who set one has told us what they expect, and an expectation
        is better than nothing.

        Returns None rather than a default when neither exists: a made-up rate
        would produce a confident estimate out of no information at all, which
        is worse than admitting there is no estimate.
        """
        if self.odometer_km is not None and self.odometer_at is not None:
            earlier = (
                self.notes.filter(kind="service", odometer_km__isnull=False)
                .order_by("-happened_on")
                .first()
            )
            if earlier and earlier.odometer_km < self.odometer_km:
                days = (self.odometer_at.date() - earlier.happened_on).days
                # Three weeks, not one. A ten-day sample extrapolated across a
                # month amplifies whatever happened in those ten days: one busy
                # fortnight becomes a permanent 4000 km/week and the car reads
                # as overdue when it is not. A short window is not a small
                # measurement, it is a bad one.
                if days >= self.MIN_RATE_WINDOW_DAYS:
                    return round((self.odometer_km - earlier.odometer_km) / days * 7)
        return self.weekly_km_limit or None

    @property
    def estimated_odometer_km(self):
        """
        Where the car probably is today, as opposed to where it was.

        THE WHOLE REASON THIS EXISTS
        ----------------------------
        The confirmed reading is only as fresh as the last person to type it
        in. Comparing a six-week-old number against the service threshold means
        the reminder fires when somebody NEXT updates the reading — which is
        the moment they were already looking at the car, so it tells them
        nothing they had not just worked out.

        Projecting forward makes a stale reading useful instead of misleading.
        It is an estimate and the interface must say so; `odometer_is_estimated`
        is what it asks.
        """
        if self.odometer_km is None:
            return None
        rate = self.km_per_week
        if not rate or self.odometer_at is None:
            return self.odometer_km
        days = max((timezone.now().date() - self.odometer_at.date()).days, 0)
        # Stop projecting eventually. Past this the figure is arithmetic rather
        # than an estimate — and by then the monthly nudge has gone unanswered
        # half a dozen times, which is its own signal. Better to under-state
        # and let the reminder be late than to declare a car overdue on six
        # months of compounding guesswork.
        days = min(days, self.MAX_PROJECTION_DAYS)
        return self.odometer_km + round(rate / 7 * days)

    @property
    def odometer_is_estimated(self):
        """True when the figure being shown is projected rather than confirmed."""
        estimate = self.estimated_odometer_km
        return estimate is not None and estimate != self.odometer_km

    @property
    def odometer_age_days(self):
        if self.odometer_at is None:
            return None
        return max((timezone.now().date() - self.odometer_at.date()).days, 0)

    @property
    def km_to_service(self):
        """
        Negative once it is overdue. None when anything is missing.

        Measured against the ESTIMATE, not the last confirmed reading. That is
        the fix for the whole problem: a reminder that waits for somebody to
        update a number arrives after they already knew.
        """
        due = self.next_service_km
        reading = self.estimated_odometer_km
        if due is None or reading is None:
            return None
        return due - reading

    @property
    def service_state(self):
        """`"overdue"`, `"due"`, `"ok"` or None. What the pills and the cron read."""
        remaining = self.km_to_service
        if remaining is None:
            return None
        if remaining <= 0:
            return "overdue"
        if remaining <= (self.service_warn_km or 0):
            return "due"
        return "ok"

    def platform_age_warnings(self):
        """
        Platforms this car may be too old for, based on rules the owner has
        confirmed. Empty when no rule is recorded — we don't guess.
        """
        return [p for p in self.platforms.all() if p.age_limit_exceeded_by(self.year)]

    @property
    def driver_covered_costs(self):
        """What the driver is NOT paying for. The selling point, in one list."""
        labels = {
            "fuel_paid_by": "Fuel",
            "maintenance_paid_by": "Maintenance",
            "insurance_paid_by": "Insurance",
            "licensing_paid_by": "Licensing",
            "tracker_paid_by": "Tracker",
        }
        return [
            label
            for field, label in labels.items()
            if getattr(self, field) in (PaidBy.OWNER, PaidBy.SHARED)
        ]


def listing_photo_path(instance, filename):
    return f"cars/{instance.listing.uuid}/{uuid.uuid4().hex}.webp"


class ListingPhoto(models.Model):
    listing = models.ForeignKey(
        VehicleListing, on_delete=models.CASCADE, related_name="photos"
    )
    image = models.ImageField(upload_to=listing_photo_path)
    thumbnail = models.ImageField(upload_to=listing_photo_path, blank=True)
    is_primary = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    MAX_PER_LISTING = 8

    class Meta:
        ordering = ["order", "id"]
        indexes = [models.Index(fields=["listing", "order"])]

    def __str__(self):
        return f"Photo {self.order} for {self.listing_id}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_primary:
            # Exactly one primary per listing, enforced here rather than by a
            # constraint: a partial unique index would reject the intermediate
            # state while promoting a different photo.
            ListingPhoto.objects.filter(listing=self.listing).exclude(pk=self.pk).update(
                is_primary=False
            )

    def delete(self, *args, **kwargs):
        listing = self.listing
        was_primary = self.is_primary
        for field in (self.image, self.thumbnail):
            if field:
                field.delete(save=False)
        super().delete(*args, **kwargs)
        if was_primary:
            replacement = ListingPhoto.objects.filter(listing=listing).first()
            if replacement:
                replacement.is_primary = True
                replacement.save(update_fields=["is_primary"])


class ListingClaimQuerySet(models.QuerySet):
    def pending(self):
        return self.filter(status=ListingClaim.Status.PENDING)


class ListingClaim(TimeStampedModel):
    """
    "That's my car" — a member asking to take over an imported advert.

    WHY A HUMAN DECIDES THIS
    ------------------------
    Handing over a listing hands over a phone number release, a review history
    and the right to speak for a car. An automatic claim button is an open door
    to taking control of somebody else's advert and collecting deposits under
    their car's photo — which is the exact scam this site exists to design out.
    So a claim is a request, staff check it against the original post, and the
    approval is what moves ownership.

    WHY IT NEEDS A VERIFIED PHONE
    -----------------------------
    Approval turns the claimant into the owner of a live car listing, which is
    the same bar `listings:create` sets. Deferring verification for browsing is
    the funnel decision; deferring it here would just be a hole around the
    other one.

    ONE PENDING CLAIM PER PERSON PER LISTING. Approving one rejects the rest —
    a listing has one owner, and leaving losing claims open would put a queue
    of people waiting on a decision that has already been made.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    listing = models.ForeignKey(
        VehicleListing, on_delete=models.CASCADE, related_name="claims"
    )
    claimant = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="listing_claims"
    )
    message = models.TextField(
        max_length=600,
        blank=True,
        help_text="Anything that helps us match you to the original post.",
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="claims_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reject_reason = models.CharField(max_length=200, blank=True)

    objects = ListingClaimQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["listing", "claimant"], name="uniq_claim_per_listing_per_user"
            )
        ]
        indexes = [models.Index(fields=["status", "created_at"])]

    def __str__(self):
        return f"Claim on {self.listing_id} by {self.claimant_id} ({self.status})"

    @property
    def is_pending(self):
        return self.status == self.Status.PENDING

    def approve(self, *, by=None):
        """
        Hand the listing over, and close every other claim on it.

        The provenance stays exactly as it was. The listing still says it came
        from Facebook and still links to the original post — a claim answers
        who is responsible for it now, not where it came from, and quietly
        rewriting the history would make the link on the page a lie.
        """
        listing = self.listing
        listing.owner = self.claimant
        listing.claimed_at = timezone.now()
        listing.save(update_fields=["owner", "claimed_at", "updated_at"])

        self.status = self.Status.APPROVED
        self.reviewed_by = by
        self.reviewed_at = timezone.now()
        self.reject_reason = ""
        self.save(update_fields=[
            "status", "reviewed_by", "reviewed_at", "reject_reason", "updated_at",
        ])

        from apps.notifications.models import Notification
        from apps.notifications.services import notify

        notify(
            recipient=self.claimant,
            kind=Notification.Kind.CLAIM_APPROVED,
            message=f"Your claim on the {listing.title} was approved — it's yours now",
            url=listing.get_absolute_url(),
        )

        ListingClaim.objects.filter(listing=listing, status=self.Status.PENDING).exclude(
            pk=self.pk
        ).update(
            status=self.Status.REJECTED,
            reviewed_by=by,
            reviewed_at=timezone.now(),
            reject_reason="Another claim on this listing was approved.",
        )
        return listing

    def reject(self, *, by=None, reason=""):
        self.status = self.Status.REJECTED
        self.reviewed_by = by
        self.reviewed_at = timezone.now()
        self.reject_reason = reason
        self.save(update_fields=[
            "status", "reviewed_by", "reviewed_at", "reject_reason", "updated_at",
        ])

        from apps.notifications.models import Notification
        from apps.notifications.services import notify

        notify(
            recipient=self.claimant,
            kind=Notification.Kind.CLAIM_REJECTED,
            message=f"We could not match your claim on the {self.listing.title} to the "
                    "original post",
            url=self.listing.get_absolute_url(),
        )


# ===========================================================================
#  Drivers
# ===========================================================================


class LicenceCode(models.TextChoices):
    """
    South African licence codes. Zimbabwe issues class numbers rather than
    these, which is a phase-2 problem — until then a Zimbabwean driver picks
    the nearest equivalent, and `licence_code` is never used as a hard gate.
    """

    B = "B", "Code B — light vehicle"
    EB = "EB", "Code EB — light vehicle and trailer"
    C1 = "C1", "Code C1 — heavy vehicle"
    C = "C", "Code C — heavy vehicle"
    EC1 = "EC1", "Code EC1 — heavy vehicle and trailer"
    EC = "EC", "Code EC — articulated"


class DriverListingQuerySet(models.QuerySet):
    def live(self):
        return self.filter(status=DriverListing.Status.ACTIVE)

    def searchable(self):
        """
        Live listings, minus anyone who asked to be left out of search.

        `Profile.hide_from_search` lives on the accounts side and has to be
        honoured here too. A driver listing is a person advertising themselves,
        and a person who switched themselves off must actually disappear —
        otherwise the setting is a lie, and under POPIA it is an objection we
        recorded and then ignored.
        """
        return self.live().filter(driver__profile__hide_from_search=False)

    def hide_blocked(self, user):
        """The driver-side twin of `VehicleListingQuerySet.hide_blocked`."""
        from apps.safety.models import blocked_user_ids

        hidden = blocked_user_ids(user)
        return self.exclude(driver_id__in=hidden) if hidden else self

    def ranked(self):
        """
        Verified first, then experience, then newest.

        THIS IS A PRODUCT DECISION, NOT A TIE-BREAK
        -------------------------------------------
        Trust is what we sell, so the default order has to reward it. A driver
        who verifies their phone moves up the list owners actually read, which
        makes verification a pull rather than a wall in front of the listing
        form. Cars rank boosted-first because owners can pay; drivers never pay
        for anything (see `apps.core.pricing`), so the only currency on this
        side of the market is trust.

        `trust_rank` deliberately approximates `Verification.level` instead of
        reproducing it. The real property applies licence and PrDP expiry rules
        that belong in Python, and smuggling date arithmetic into a CASE
        expression would leave two definitions of verification to drift apart.
        Ordering only needs the coarse shape — the badge on the card still
        renders from the real property.
        """
        return self.annotate(
            trust_rank=models.Case(
                models.When(driver__verification__id_verified_at__isnull=False, then=2),
                models.When(driver__verification__email_verified_at__isnull=False, then=1),
                default=0,
                output_field=models.IntegerField(),
            )
        ).order_by("-trust_rank", "-years_experience", "-created_at")

    def with_display_data(self):
        return self.select_related(
            "driver__profile", "driver__verification", "home_suburb__city"
        ).prefetch_related(
            "platforms_experience",
            "work_suburbs__city",
            "driver__rating_proofs__platform",
        )


class DriverListing(TimeStampedModel):
    """
    A driver advertising themselves to car owners.

    THE MIRROR OF A VEHICLE LISTING, WITH ONE DIFFERENCE
    ----------------------------------------------------
    A car listing is an asset on offer. A driver listing is a person on offer,
    and that changes what the page may show. Contact details stay masked here
    exactly as they are on `/u/<handle>/`; work areas are suburbs, never a home
    address; and `Profile.hide_from_search` removes the listing from every
    browse surface (see `DriverListingQuerySet.searchable`).

    WHY NOTHING HERE IS GATED ON VERIFICATION
    -----------------------------------------
    Publishing a driver profile hands over nothing: contacts are not released
    until an introduction is approved. A wall in front of this form would only
    thin out the supply owners come here to browse. The incentive is applied as
    a pull instead — verified drivers rank above unverified ones in `ranked()`,
    which is visible on the first screen.
    """

    class Status(models.TextChoices):
        # The same five values as VehicleListing.Status, so the status pill and
        # the manage screens read identically on both sides of the market.
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Live"
        PAUSED = "paused", "Paused"
        PLACED = "placed", "Driving a car"
        ARCHIVED = "archived", "Archived"

    MAX_WORK_SUBURBS = 8

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    driver = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="driver_listings"
    )

    headline = models.CharField(
        max_length=120,
        help_text="The one line an owner reads first.",
    )
    years_experience = models.PositiveSmallIntegerField(
        default=0, validators=[MaxValueValidator(60)]
    )
    licence_code = models.CharField(
        max_length=5, choices=LicenceCode.choices, default=LicenceCode.B
    )
    has_prdp = models.BooleanField(default=False)
    platforms_experience = models.ManyToManyField(
        Platform, related_name="driver_listings", blank=True
    )

    preferred_arrangement = models.CharField(
        max_length=10, choices=Arrangement.choices, blank=True
    )
    max_weekly_rate = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="The most you're willing to pay a week. Leave blank if it depends.",
    )
    currency = models.CharField(max_length=3, default="ZAR")

    home_suburb = models.ForeignKey(
        "geo.Suburb", on_delete=models.PROTECT, related_name="+"
    )
    work_suburbs = models.ManyToManyField(
        "geo.Suburb", related_name="driver_listings", blank=True
    )
    available_from = models.DateField(null=True, blank=True)
    about = models.TextField(max_length=1500, blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    view_count = models.PositiveIntegerField(default=0)
    published_at = models.DateTimeField(null=True, blank=True)

    objects = DriverListingQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "home_suburb"]),
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["driver", "status"]),
        ]

    def __str__(self):
        return f"{self.driver.get_short_name() or 'Driver'} — {self.home_suburb.name}"

    def save(self, *args, **kwargs):
        if self.home_suburb_id and not self.pk:
            self.currency = CURRENCY_FOR_COUNTRY.get(
                self.home_suburb.city.province.country, "ZAR"
            )
        if self.status == self.Status.ACTIVE and not self.published_at:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("drivers:detail", args=[self.uuid])

    # ------------------------------------------------------------- display

    @property
    def title(self):
        return self.driver.full_name or "Driver"

    @property
    def is_live(self):
        return self.status == self.Status.ACTIVE

    @property
    def experience_display(self):
        if not self.years_experience:
            return "New to e-hailing"
        plural = "s" if self.years_experience > 1 else ""
        return f"{self.years_experience} year{plural} driving"

    @property
    def work_area_display(self):
        """Home suburb first — it is the one an owner weighs most."""
        names = [self.home_suburb.name]
        names += [s.name for s in self.work_suburbs.all() if s.pk != self.home_suburb_id]
        return " · ".join(names[:4])

    @property
    def verified_ratings(self):
        """
        Only ratings staff have actually checked against a screenshot.

        Cards and search results show nothing else. An unverified 4.98 sitting
        next to a verified 4.72 in the same typeface teaches owners that the
        badge means nothing — and the badge is the product.
        """
        return [p for p in self.driver.rating_proofs.all() if p.verified]

    @property
    def claimed_ratings(self):
        """Self-reported figures. Shown on the detail page, labelled as such."""
        return [p for p in self.driver.rating_proofs.all() if not p.verified]

    @property
    def best_verified_rating(self):
        ratings = self.verified_ratings
        return max(ratings, key=lambda proof: proof.rating) if ratings else None


def rating_proof_path(instance, filename):
    return f"ratings/{instance.driver_id}/{uuid.uuid4().hex}.webp"


class PlatformRatingProof(TimeStampedModel):
    """
    A driver's platform rating, with a screenshot as the evidence for it.

    WHY THE SCREENSHOT IS DELETED THE MOMENT IT IS REVIEWED
    -------------------------------------------------------
    A screenshot of the Uber or Bolt driver app carries the driver's photo,
    their legal name, their trip history and often their earnings. That is the
    same class of personal information as an ID scan, so it gets the same
    treatment `VerificationDocument` gets: the reviewer's decision writes the
    outcome to this row and deletes the file in the same action, and
    `purge_kyc` sweeps up whatever was abandoned in the queue.

    What survives is a number and the fact that somebody checked it. That is
    all the product needs; holding the image any longer is a liability with no
    upside.

    One row per driver per platform. A driver who re-uploads after a rejection
    overwrites the earlier attempt instead of stacking screenshots.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Verified"
        REJECTED = "rejected", "Rejected"

    RETENTION_DAYS = 30

    driver = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="rating_proofs"
    )
    platform = models.ForeignKey(
        Platform, on_delete=models.CASCADE, related_name="rating_proofs"
    )
    rating = models.DecimalField(
        max_digits=3, decimal_places=2,
        validators=[MinValueValidator(Decimal("1.00")), MaxValueValidator(Decimal("5.00"))],
    )
    trips = models.PositiveIntegerField(
        null=True, blank=True, help_text="Completed trips, if the screen shows them."
    )
    screenshot = models.ImageField(upload_to=rating_proof_path, blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="ratings_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reject_reason = models.CharField(max_length=200, blank=True)
    purge_after = models.DateField()

    class Meta:
        ordering = ["-rating"]
        constraints = [
            models.UniqueConstraint(
                fields=["driver", "platform"], name="uniq_rating_proof_per_platform"
            )
        ]
        indexes = [models.Index(fields=["status", "created_at"])]

    def __str__(self):
        return f"{self.platform.name} {self.rating} for {self.driver.handle} ({self.status})"

    def save(self, *args, **kwargs):
        if not self.purge_after:
            self.purge_after = timezone.localdate() + timedelta(days=self.RETENTION_DAYS)
        super().save(*args, **kwargs)

    @property
    def verified(self) -> bool:
        return self.status == self.Status.APPROVED

    @property
    def is_pending(self) -> bool:
        return self.status == self.Status.PENDING

    def purge_screenshot(self):
        """Delete the stored image, keep the row."""
        if self.screenshot:
            self.screenshot.delete(save=False)
            self.screenshot = ""
            self.save(update_fields=["screenshot", "updated_at"])

    def review(self, *, approved: bool, by=None, reason=""):
        """Record the decision and destroy the evidence in one step."""
        self.status = self.Status.APPROVED if approved else self.Status.REJECTED
        self.reviewed_by = by
        self.reviewed_at = timezone.now()
        self.reject_reason = "" if approved else reason
        if self.screenshot:
            self.screenshot.delete(save=False)
            self.screenshot = ""
        self.save(update_fields=[
            "status", "reviewed_by", "reviewed_at", "reject_reason",
            "screenshot", "updated_at",
        ])


class SavedSearch(TimeStampedModel):
    """
    A filter set somebody wants to come back to.

    Sprint 3 stores these and nothing more — no alerting yet. That split is
    deliberate: the moment this model starts sending mail it needs a digest
    job, an unsubscribe link and a bounce policy, and none of that should hold
    up the milestone this sprint exists for. `frequency` is collected now so
    the digest job has an audience the day it is written.

    `params` is written from a VALIDATED filter form, never from raw
    `request.GET`. Anything unrecognised is dropped on the way in, so replaying
    a saved search months later cannot smuggle a stale or crafted querystring
    into a queryset.
    """

    class Kind(models.TextChoices):
        CARS = "cars", "Cars"
        DRIVERS = "drivers", "Drivers"

    class Frequency(models.TextChoices):
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        NEVER = "never", "Don't alert me"

    MAX_PER_USER = 12

    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="saved_searches"
    )
    label = models.CharField(max_length=80)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    params = models.JSONField(default=dict, blank=True)
    frequency = models.CharField(
        max_length=10, choices=Frequency.choices, default=Frequency.DAILY
    )
    last_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "label"], name="uniq_saved_search_label")
        ]

    def __str__(self):
        return f"{self.label} ({self.kind})"

    @property
    def querystring(self) -> str:
        pairs = []
        for key, value in sorted(self.params.items()):
            for item in value if isinstance(value, list) else [value]:
                pairs.append((key, item))
        return urlencode(pairs)

    def get_absolute_url(self):
        base = reverse(
            "drivers:browse" if self.kind == self.Kind.DRIVERS else "listings:browse"
        )
        query = self.querystring
        return f"{base}?{query}" if query else base


class VehicleNote(TimeStampedModel):
    """
    An owner's private log against one of their cars.

    WHY THIS HANGS OFF THE CAR AND NOT THE PLACEMENT
    ------------------------------------------------
    It was asked for as "notes on the active placement", and the examples given
    were servicing and faults — which are facts about the car, not about the
    arrangement. A service record attached to a placement would disappear from
    view the day that driver left, and the next driver would take over a car
    that looked like it had no history at all. The whole value of a log is that
    it outlives the people passing through it.

    `placement` is here so a note CAN be pinned to who was driving at the time,
    because "bumper scuffed" means more when you know whose hands it was in.
    It is optional and it is not the parent: deleting the placement would be
    wrong, so it is SET_NULL, and the note survives.

    PRIVATE. NOT "MOSTLY PRIVATE".
    ------------------------------
    Only the owner reads these. Not the driver, not staff, not a support
    screen. That is a deliberate boundary rather than an oversight:

    Every claim about how somebody BEHAVED already has a home on this site, and
    it is the double-blind `Review` tied to a placement both people confirmed.
    That system is slow and strict on purpose — neither side sees the other's
    words until both have written or a fortnight has gone. A note the driver
    could read would be a way round all of it: an accusation with no
    confirmation behind it, no blind, and nowhere for them to answer it.

    So the rule is: if it is about the car, it goes here. If it is about the
    person, it goes in a review. `Kind.INCIDENT` is the line to watch — the day
    somebody proposes showing notes to drivers, this docstring is the argument
    against it.
    """

    class Kind(models.TextChoices):
        SERVICE = "service", "Service"
        REPAIR = "repair", "Repair"
        INCIDENT = "incident", "Incident"
        PAYMENT = "payment", "Payment"
        LICENSING = "licensing", "Licensing"
        OTHER = "other", "Note"

    listing = models.ForeignKey(
        "VehicleListing", on_delete=models.CASCADE, related_name="notes"
    )
    # Who wrote it. Kept even though only the owner can write today, because a
    # car can change hands through a claim and the log should still say who
    # said what.
    author = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="vehicle_notes"
    )
    placement = models.ForeignKey(
        "placements.Placement", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="vehicle_notes",
    )

    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.OTHER)
    body = models.TextField(max_length=2000)

    # Separate from created_at, because "when the car was last serviced" is a
    # fact about the car and not about when somebody got round to typing it up.
    happened_on = models.DateField()
    odometer_km = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Optional. What makes a service history answer 'when is the "
                  "next one due' rather than only 'when was the last one'.",
    )

    class Meta:
        ordering = ["-happened_on", "-created_at"]
        indexes = [
            models.Index(fields=["listing", "-happened_on"]),
            # Serves "last service" on the fleet page for every car at once.
            models.Index(fields=["listing", "kind", "-happened_on"]),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} on {self.listing_id} ({self.happened_on})"


class ServiceReminder(TimeStampedModel):
    """
    One reminder that has already gone out, so it does not go out again.

    WHY THIS EXISTS AT ALL
    ----------------------
    The cron runs daily and the condition it checks stays true for as long as
    the car is due — which without a record would mean a notification every
    morning until somebody services it. A reminder that arrives every day is a
    reminder people learn to dismiss without reading, and then the one that
    mattered gets dismissed too.

    Keyed on `due_at_km` rather than a date, so it resets by itself: log a
    service, `next_service_km` moves on, and the next cycle is a different key
    that has never been sent. Nothing has to be cleaned up.
    """

    class Stage(models.TextChoices):
        WARNING = "warning", "Service coming up"
        DUE = "due", "Service due"

    listing = models.ForeignKey(
        "VehicleListing", on_delete=models.CASCADE, related_name="service_reminders"
    )
    stage = models.CharField(max_length=7, choices=Stage.choices)
    due_at_km = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["listing", "stage", "due_at_km"],
                name="one_service_reminder_per_stage_per_cycle",
            )
        ]

    def __str__(self):
        return f"{self.stage} for {self.listing_id} at {self.due_at_km}"
