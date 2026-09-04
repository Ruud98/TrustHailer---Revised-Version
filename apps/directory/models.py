"""
The business directory — mechanics, panel beaters, tyre shops, trackers,
insurance, car washes, spares, dashcams, driving schools.

WHY A DIRECTORY ENTRY IS NOT A LISTING LIKE A CAR OR A DRIVER
----------------------------------------------------------------
A car listing hides the owner's number until an introduction is approved,
because releasing it is a decision two people make together. A business wants
the opposite: the entire reason to be in this directory is that a stranger can
find the number and phone it, right now, with no approval step. `phone` and
`whatsapp` are plain fields, shown in full, everywhere the listing appears.
There is no introduction flow here and there should not be one.

WHY VERIFICATION MEANS SOMETHING DIFFERENT HERE TOO
------------------------------------------------------
Everywhere else on the site, "verified" is about a *person* — an ID, a
licence, a phone number reaching them. `is_verified` here is staff confirming
the business is real: a real premises, a real number that was actually
answered, not merely typed into a form. It says nothing about whether the
mechanic is any good, and the detail page says so in the same sentence that
explains the badge, the same way the safety page already does for the
verification ladder on people.
"""
import uuid

from django.db import models
from django.urls import reverse
from django.utils.text import slugify

from apps.core.models import TimeStampedModel


class BusinessListingQuerySet(models.QuerySet):
    def visible(self):
        return self.filter(status=BusinessListing.Status.ACTIVE, is_hidden=False)

    def with_display_data(self):
        return self.select_related("suburb__city", "owner_user")


class BusinessListing(TimeStampedModel):
    class Category(models.TextChoices):
        MECHANIC = "mechanic", "Mechanic"
        PANELBEATER = "panel", "Panel beater"
        TYRES = "tyres", "Tyres"
        TRACKER = "tracker", "Tracker"
        INSURANCE = "insurance", "Insurance"
        CARWASH = "carwash", "Car wash"
        SPARES = "spares", "Spares"
        DASHCAM = "dashcam", "Dashcam"
        DRIVINGSCHOOL = "school", "Driving school"

    class Status(models.TextChoices):
        # The same five-value vocabulary VehicleListing and DriverListing use,
        # so the status pill and the manage screen read identically across
        # every kind of listing on the site rather than inventing a fourth
        # shape for the same idea.
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Live"
        PAUSED = "paused", "Paused"
        ARCHIVED = "archived", "Archived"

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    slug = models.SlugField(max_length=140, unique=True, editable=False)

    # Nullable and SET_NULL on purpose: staff can hand-enter a business that
    # has not signed up for an account yet — the same "carry it in by hand so
    # the directory is not empty on day one" move the Facebook-advert importer
    # makes for cars — and the listing survives if the owner's account is ever
    # deleted.
    owner_user = models.ForeignKey(
        "accounts.User", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="businesses",
    )

    name = models.CharField(max_length=120)
    category = models.CharField(max_length=12, choices=Category.choices)
    description = models.TextField(max_length=1500)
    phone = models.CharField(max_length=20)
    whatsapp = models.CharField(max_length=20, blank=True)
    suburb = models.ForeignKey("geo.Suburb", on_delete=models.PROTECT, related_name="businesses")
    logo = models.ImageField(upload_to="biz/", blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)

    # The paid tier is data and plumbing, not a live feature — see
    # apps.core.pricing and the platform-is-free decision the whole codebase
    # already made. `is_paid` / `paid_until` exist so a boost or a featured
    # slot has somewhere to write to the day pricing switches on; nothing sets
    # them today.
    is_paid = models.BooleanField(default=False)
    paid_until = models.DateField(null=True, blank=True)

    # Staff-confirmed real, not owner-self-reported real. See the module
    # docstring for why this is a different kind of "verified" from the
    # person-facing ladder.
    is_verified = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)

    # Staff moderation, exactly like Post.is_hidden: the row stays so the
    # trail stays, and it is a separate switch from `status` because the two
    # answer different questions — status is the owner saying "not now",
    # is_hidden is staff saying "not here".
    is_hidden = models.BooleanField(
        default=False, help_text="Hidden by staff. The row stays so the moderation trail does."
    )

    objects = BusinessListingQuerySet.as_manager()

    class Meta:
        ordering = ["-is_paid", "-created_at"]
        indexes = [
            models.Index(fields=["status", "is_hidden", "category"]),
            models.Index(fields=["status", "is_hidden", "suburb"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_category_display()})"

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:120] or "business"
            candidate = base
            suffix = 1
            while BusinessListing.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
                suffix += 1
                candidate = f"{base}-{suffix}"
            self.slug = candidate
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("directory:detail", args=[self.pk, self.slug])

    @property
    def is_live(self):
        return self.status == self.Status.ACTIVE and not self.is_hidden
