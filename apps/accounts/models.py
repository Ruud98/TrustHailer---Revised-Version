import re
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from apps.core import phone as phone_utils
from apps.core.models import TimeStampedModel
from apps.geo.models import Country

from .managers import UserManager
from .storages import kyc_storage


class User(AbstractBaseUser, PermissionsMixin):
    """
    Email-first user with a deferred phone number.

    WHY EMAIL IS THE LOGIN CREDENTIAL
    ---------------------------------
    Email costs nothing to send. SMS costs roughly R0.25 a message, so an
    SMS-at-signup design bills you for every curious visitor who never returns.

    WHY THE PHONE NUMBER IS STILL HERE, AND STILL MATTERS
    -----------------------------------------------------
    The product releases contact details between owners and drivers, and that
    contact detail is a phone number — nobody in this market is emailing about a
    car. So we collect it at onboarding and verify it later, at the moment it
    starts carrying weight: listing a car, or approving an introduction. See
    `needs_phone_verification`.

    The trade-off to hold in mind: email is a weaker identity signal here than a
    phone number. Plenty of drivers have a Gmail address only because Android
    asked for one at setup. That is exactly why phone verification is deferred
    rather than dropped.
    """

    email = models.EmailField(unique=True)
    phone = models.CharField(
        max_length=20,
        unique=True,
        null=True,
        blank=True,
        help_text="E.164 format, e.g. +27821234567. Collected at onboarding, verified later.",
    )
    full_name = models.CharField(max_length=120)
    handle = models.SlugField(max_length=40, unique=True, help_text="Public URL: /u/<handle>/")

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_suspended = models.BooleanField(
        default=False,
        help_text="Suspended users can log in but cannot post, list or request intros.",
    )
    suspension_reason = models.CharField(max_length=200, blank=True)

    date_joined = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    PLACEHOLDER_HANDLE = re.compile(r"^user(-\d+)?$")

    class Meta:
        ordering = ["-date_joined"]
        indexes = [models.Index(fields=["handle"])]

    def __str__(self):
        return f"{self.full_name or 'Member'} <{self.email}>"

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        if not self.phone:
            # An empty string would collide on the unique index for a second
            # user; NULL does not. Normalise "" to None on every write.
            self.phone = None
        if not self.handle:
            self.handle = self.generate_handle()
        super().save(*args, **kwargs)

    def generate_handle(self):
        base = slugify(self.full_name)[:28] or "user"
        candidate = base
        while User.objects.filter(handle=candidate).exclude(pk=self.pk).exists():
            candidate = f"{base}-{secrets.randbelow(9000) + 1000}"
        return candidate

    @property
    def has_placeholder_handle(self) -> bool:
        """
        True while the handle is still the fallback assigned at signup, when all
        we knew was an email address. Once a name arrives we upgrade
        /u/user-4821/ to /u/thabo-mokoena/ — then it freezes, because someone
        may already have shared the link.
        """
        return bool(self.PLACEHOLDER_HANDLE.match(self.handle or ""))

    def refresh_handle_if_placeholder(self) -> bool:
        if self.full_name and self.has_placeholder_handle:
            self.handle = self.generate_handle()
            return True
        return False

    def get_absolute_url(self):
        return reverse("accounts:profile", args=[self.handle])

    def get_short_name(self):
        return self.full_name.split(" ")[0] if self.full_name else ""

    def get_full_name(self):
        return self.full_name

    @property
    def display_phone(self):
        return phone_utils.display(self.phone) if self.phone else ""

    @property
    def masked_phone(self):
        return phone_utils.mask(self.phone) if self.phone else ""

    @property
    def can_participate(self):
        return self.is_active and not self.is_suspended

    @property
    def needs_phone_verification(self) -> bool:
        """
        Gate for the actions where trust starts to cost someone money: listing a
        car, approving an introduction. Browsing, posting and commenting stay
        open on an email-only account, so the funnel is never blocked by an SMS.
        """
        return not self.verification.phone_verified_at


class Profile(TimeStampedModel):
    """
    Roles are booleans rather than one choice field, on purpose. Owner-drivers
    are common in this market and a single-choice field would force them to
    misrepresent themselves on the very first screen.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")

    is_owner = models.BooleanField(default=False)
    is_driver = models.BooleanField(default=False)
    is_business = models.BooleanField(default=False)

    country = models.CharField(
        max_length=2,
        choices=Country.choices,
        default=Country.ZA,
        help_text="The market this member works in. Chosen at onboarding.",
    )
    suburb = models.ForeignKey(
        "geo.Suburb", null=True, blank=True, on_delete=models.SET_NULL, related_name="profiles"
    )
    bio = models.TextField(max_length=500, blank=True)

    avatar = models.ImageField(upload_to="avatars/%Y/%m/", blank=True)
    avatar_thumb = models.ImageField(upload_to="avatars/thumbs/%Y/%m/", blank=True)

    whatsapp_ok = models.BooleanField(
        default=True, help_text="Show a WhatsApp button once contacts are released."
    )
    hide_from_search = models.BooleanField(default=False)

    onboarding_completed_at = models.DateTimeField(null=True, blank=True)

    # Denormalised from published reviews — see apps/placements/models.py. The
    # rating appears on every card in a browse list, and averaging per card
    # would be a query per card. Written only when a review publishes, which is
    # rare, so it is recalculated whole rather than nudged: a counter that
    # drifts is worse than a query nobody notices.
    rating_avg = models.DecimalField(
        max_digits=3, decimal_places=2, null=True, blank=True,
        help_text="Average of published reviews received. Null until there are any.",
    )
    rating_count = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Profile<{self.user.handle}>"

    @property
    def is_onboarded(self):
        return self.onboarding_completed_at is not None

    @property
    def roles_display(self):
        labels = []
        if self.is_owner:
            labels.append("Car owner")
        if self.is_driver:
            labels.append("Driver")
        if self.is_business:
            labels.append("Business")
        return " · ".join(labels) or "Member"

    @property
    def city(self):
        return self.suburb.city if self.suburb else None



class Verification(TimeStampedModel):
    """
    Cached verification state.

    Documents are deleted after review (see VerificationDocument). What survives
    is this row: the fact of verification and the expiry date. That is all we
    need, and holding more is a liability with no upside.

    Email sits at the bottom of the ladder deliberately. It proves someone can
    read a mailbox, nothing more. Phone is the first rung that means anything to
    a person deciding whether to hand over car keys.
    """

    LEVEL_NONE = 0
    LEVEL_EMAIL = 1
    LEVEL_PHONE = 2
    LEVEL_ID = 3
    LEVEL_LICENCE = 4
    LEVEL_REFERENCES = 5

    LEVEL_LABELS = {
        LEVEL_NONE: "Unverified",
        LEVEL_EMAIL: "Email verified",
        LEVEL_PHONE: "Phone verified",
        LEVEL_ID: "ID verified",
        LEVEL_LICENCE: "Licence verified",
        LEVEL_REFERENCES: "Fully verified",
    }

    BADGE_CLASSES = {
        LEVEL_NONE: "badge-unverified",
        LEVEL_EMAIL: "badge-email",
        LEVEL_PHONE: "badge-phone",
        LEVEL_ID: "badge-id",
        LEVEL_LICENCE: "badge-licence",
        LEVEL_REFERENCES: "badge-full",
    }

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="verification")

    email_verified_at = models.DateTimeField(null=True, blank=True)
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    id_verified_at = models.DateTimeField(null=True, blank=True)
    licence_verified_at = models.DateTimeField(null=True, blank=True)
    licence_expires_on = models.DateField(null=True, blank=True)
    prdp_verified_at = models.DateTimeField(null=True, blank=True)
    prdp_expires_on = models.DateField(null=True, blank=True)
    references_checked_at = models.DateTimeField(null=True, blank=True)

    phone_verified_manually_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="phones_verified",
        help_text="Set when staff confirm a number over WhatsApp instead of by SMS.",
    )

    def __str__(self):
        return f"Verification<{self.user.handle}: {self.label}>"

    @property
    def licence_valid(self):
        if not self.licence_verified_at:
            return False
        if self.licence_expires_on and self.licence_expires_on < timezone.localdate():
            return False
        return True

    @property
    def prdp_valid(self):
        if not self.prdp_verified_at:
            return False
        if self.prdp_expires_on and self.prdp_expires_on < timezone.localdate():
            return False
        return True

    @property
    def level(self) -> int:
        if self.references_checked_at and self.licence_valid:
            return self.LEVEL_REFERENCES
        if self.licence_valid:
            return self.LEVEL_LICENCE
        if self.id_verified_at:
            return self.LEVEL_ID
        if self.phone_verified_at:
            return self.LEVEL_PHONE
        if self.email_verified_at:
            return self.LEVEL_EMAIL
        return self.LEVEL_NONE

    @property
    def label(self) -> str:
        return self.LEVEL_LABELS[self.level]

    @property
    def badge_class(self) -> str:
        return self.BADGE_CLASSES[self.level]


def kyc_upload_path(instance, filename):
    return f"kyc/{instance.user_id}/{uuid.uuid4().hex}"


class VerificationDocument(TimeStampedModel):
    """
    Short-lived storage for identity documents.

    The file is deleted as soon as a reviewer approves or rejects it.
    `purge_after` is a backstop for anything abandoned in the queue, swept by
    the nightly `purge_kyc` command. Never relax this. A permanent archive of ID
    scans is the largest legal exposure this project can create, and it buys
    nothing once the flag on Verification is set.
    """

    class Kind(models.TextChoices):
        ID = "id", "ID / Passport"
        LICENCE = "licence", "Driver's licence"
        PRDP = "prdp", "Professional Driving Permit"
        VEHICLE_REG = "reg", "Vehicle registration (NaTIS)"
        PROOF_ADDRESS = "addr", "Proof of address"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="kyc_documents")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    # Private storage, never the public media bucket. See accounts/storages.py
    # for what went wrong before and why nothing renders a URL to one of these.
    file = models.FileField(upload_to=kyc_upload_path, storage=kyc_storage, blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="kyc_reviewed"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reject_reason = models.CharField(max_length=200, blank=True)

    expires_on = models.DateField(
        null=True, blank=True, help_text="Expiry printed on the document, if any."
    )
    purge_after = models.DateField()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "created_at"])]

    def save(self, *args, **kwargs):
        if not self.purge_after:
            self.purge_after = timezone.localdate() + timedelta(days=30)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_kind_display()} for {self.user.handle} ({self.status})"

    def purge_file(self):
        """Delete the stored file but keep the audit row."""
        if self.file:
            self.file.delete(save=False)
            self.file = ""
            self.save(update_fields=["file", "updated_at"])


class OTPChallenge(models.Model):
    """
    One outstanding code, on either channel.

    Generalised over email and SMS from the start, so switching a flow to SMS
    later is a parameter rather than a second implementation. The channel field
    also records what each code cost: filter on `channel="sms"` to see the
    entire SMS bill.

    The code is stored hashed, so a dump of this table lets nobody log in as
    anyone. `attempts` is what stops a six-digit code being brute-forced.
    """

    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"

    class Purpose(models.TextChoices):
        LOGIN = "login", "Log in or sign up"
        PHONE = "phone", "Verify phone number"
        EMAIL_CHANGE = "email_change", "Change email address"

    channel = models.CharField(max_length=6, choices=Channel.choices, default=Channel.EMAIL)
    destination = models.CharField(
        max_length=254, db_index=True, help_text="Email address or E.164 phone number."
    )
    code_hash = models.CharField(max_length=128)
    purpose = models.CharField(max_length=14, choices=Purpose.choices, default=Purpose.LOGIN)

    user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.CASCADE, related_name="otp_challenges"
    )

    attempts = models.PositiveSmallIntegerField(default=0)
    is_used = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    # Kept for abuse investigation only; purged with the row after a week.
    request_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["destination", "is_used", "expires_at"]),
            models.Index(fields=["channel", "created_at"]),
        ]

    def __str__(self):
        return f"OTP<{self.channel}:{self.destination} {'used' if self.is_used else 'live'}>"

    @classmethod
    def issue(cls, destination, *, channel=Channel.EMAIL, purpose=Purpose.LOGIN,
              user=None, ip=None):
        """Invalidate previous codes for this destination and purpose, then
        create a new one. Returns (challenge, raw_code)."""
        cls.objects.filter(
            destination=destination, purpose=purpose, is_used=False
        ).update(is_used=True)
        raw = "".join(str(secrets.randbelow(10)) for _ in range(settings.OTP_LENGTH))
        challenge = cls.objects.create(
            channel=channel,
            destination=destination,
            code_hash=make_password(raw),
            purpose=purpose,
            user=user,
            request_ip=ip,
            expires_at=timezone.now() + timedelta(seconds=settings.OTP_TTL_SECONDS),
        )
        return challenge, raw

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_live(self):
        return (
            not self.is_used
            and not self.is_expired
            and self.attempts < settings.OTP_MAX_ATTEMPTS
        )

    def verify(self, raw_code) -> bool:
        """
        Check a submitted code. Consumes an attempt whether or not it matches.
        Returns True only once — the code is burned on success.
        """
        if not self.is_live:
            return False
        self.attempts += 1
        if check_password(str(raw_code).strip(), self.code_hash):
            self.is_used = True
            self.save(update_fields=["attempts", "is_used"])
            return True
        self.save(update_fields=["attempts"])
        return False
