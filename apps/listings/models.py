import uuid
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone

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

    def ranked(self):
        """
        Boosted first, then newest.

        `boost_expires_at` is null for the overwhelming majority of rows, so
        nulls_last is doing the real work here — without it Postgres sorts nulls
        first on a descending order and every unboosted listing outranks every
        boosted one.
        """
        return self.order_by(
            models.F("boost_expires_at").desc(nulls_last=True),
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

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    owner = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="vehicle_listings"
    )

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

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "suburb"]),
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["owner", "status"]),
            models.Index(fields=["status", "arrangement", "weekly_rate"]),
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
