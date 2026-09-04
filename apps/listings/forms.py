from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlparse

from django import forms
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from apps.core.images import ImageProcessingError, process_upload
from apps.core.redact import redact_contacts
from apps.geo.forms import FreeTextLocationMixin
from apps.geo.resolve import clean_place_name, resolve_suburb
from apps.geo.models import City, Country, Suburb

from .models import (
    Arrangement,
    DriverListing,
    FuelType,
    ListingClaim,
    ListingPhoto,
    PaidBy,
    Platform,
    PlatformRatingProof,
    SavedSearch,
    Transmission,
    VehicleListing,
)

def platform_choices(user, already=None):
    """
    The platforms to offer someone, scoped to the market they work in.

    A Harare owner has no use for Uber and a Johannesburg one has none for
    Hwindi, so the list follows `Profile.country` rather than showing the union
    and leaving people to guess which apply.

    `already` is whatever the listing has ticked now. Those stay selectable even
    if the platform has since been retired or sits under another country —
    without that, an owner who moved suburb and then edited their price would
    silently drop a platform off their own advert.
    """
    scoped = Platform.objects.filter(is_active=True)

    country = getattr(getattr(user, "profile", None), "country", None)
    if country:
        scoped = scoped.filter(country=country)

    if already is None:
        return scoped

    keep = set(scoped.values_list("pk", flat=True)) | set(
        already.values_list("pk", flat=True)
    )
    return Platform.objects.filter(pk__in=keep)


TEXT = {"class": "form-control"}
TEXT_LG = {"class": "form-control form-control-lg"}
SELECT = {"class": "form-select"}
CHECK = {"class": "form-check-input"}


class VehicleListingForm(FreeTextLocationMixin, forms.ModelForm):
    """
    Create and edit a car.

    Long, but every field earns its place — these are the questions a driver
    would otherwise ask in five back-and-forth messages. Asking once, in
    structured form, is the whole efficiency gain over a Facebook group.
    """

    class Meta:
        model = VehicleListing
        fields = [
            "make", "model", "year", "transmission", "fuel_type", "colour",
            "platforms", "arrangement", "weekly_rate", "daily_rate",
            "deposit_amount", "earnings_share_pct", "rent_to_own_months",
            "fuel_paid_by", "maintenance_paid_by", "insurance_paid_by",
            "licensing_paid_by", "tracker_paid_by",
            "has_tracker", "has_insurance", "weekly_km_limit",
            "min_experience_years", "requires_prdp",
            "suburb", "available_from", "description",
        ]
        widgets = {
            "make": forms.TextInput(attrs={**TEXT_LG, "placeholder": "Toyota"}),
            "model": forms.TextInput(attrs={**TEXT_LG, "placeholder": "Corolla Quest"}),
            "year": forms.NumberInput(attrs={**TEXT, "placeholder": "2019", "inputmode": "numeric"}),
            "transmission": forms.Select(attrs=SELECT),
            "fuel_type": forms.Select(attrs=SELECT),
            "colour": forms.TextInput(attrs={**TEXT, "placeholder": "White"}),
            "platforms": forms.CheckboxSelectMultiple(attrs=CHECK),
            "arrangement": forms.Select(attrs=SELECT),
            "weekly_rate": forms.NumberInput(attrs={**TEXT, "inputmode": "decimal", "step": "1"}),
            "daily_rate": forms.NumberInput(attrs={**TEXT, "inputmode": "decimal", "step": "1"}),
            "deposit_amount": forms.NumberInput(attrs={**TEXT, "inputmode": "decimal", "step": "1"}),
            "earnings_share_pct": forms.NumberInput(attrs={**TEXT, "inputmode": "numeric"}),
            "rent_to_own_months": forms.NumberInput(attrs={**TEXT, "inputmode": "numeric"}),
            "fuel_paid_by": forms.Select(attrs=SELECT),
            "maintenance_paid_by": forms.Select(attrs=SELECT),
            "insurance_paid_by": forms.Select(attrs=SELECT),
            "licensing_paid_by": forms.Select(attrs=SELECT),
            "tracker_paid_by": forms.Select(attrs=SELECT),
            "has_tracker": forms.CheckboxInput(attrs=CHECK),
            "has_insurance": forms.CheckboxInput(attrs=CHECK),
            "weekly_km_limit": forms.NumberInput(
                attrs={**TEXT, "inputmode": "numeric", "placeholder": "Blank = unlimited"}
            ),
            "min_experience_years": forms.NumberInput(attrs={**TEXT, "inputmode": "numeric"}),
            "requires_prdp": forms.CheckboxInput(attrs=CHECK),
            "suburb": forms.Select(attrs=SELECT),
            "available_from": forms.DateInput(attrs={**TEXT, "type": "date"}),
            "description": forms.Textarea(
                attrs={
                    **TEXT,
                    "rows": 4,
                    "maxlength": 2000,
                    "placeholder": "Anything a driver should know: service history, "
                                   "where handover happens, what you expect.",
                }
            ),
        }
        labels = {
            "weekly_km_limit": "Weekly km limit",
            "min_experience_years": "Minimum e-hailing experience (years)",
            "requires_prdp": "Driver must have a valid PrDP",
            "has_tracker": "Tracker fitted",
            "has_insurance": "Vehicle is insured",
            "available_from": "Available from",
        }
        help_texts = {
            "platforms": "Which platforms is this car approved for?",
            "deposit_amount": "What the driver pays up front. Enter 0 if none.",
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["platforms"].queryset = platform_choices(
            user, already=self.instance.platforms.all() if self.instance.pk else None
        )

        suburb = self.instance.suburb if self.instance and self.instance.suburb_id else None
        self._install_place_fields(
            country=getattr(getattr(user, "profile", None), "country", None) or Country.ZA,
            city_initial=suburb.city.name if suburb else "",
            suburb_initial=suburb.name if suburb else "",
        )

        for name in ["weekly_rate", "daily_rate", "earnings_share_pct", "rent_to_own_months"]:
            self.fields[name].required = False

    def clean_year(self):
        year = self.cleaned_data["year"]
        next_year = timezone.localdate().year + 1
        if year > next_year:
            raise forms.ValidationError(f"That's in the future — {next_year} is the latest.")
        return year

    def clean(self):
        cleaned = super().clean()
        arrangement = cleaned.get("arrangement")

        # Each arrangement has exactly one number that matters. Requiring it
        # here stops listings going live with no price, which is the single most
        # common reason a driver skips past a card.
        required_for = {
            Arrangement.WEEKLY: ("weekly_rate", "Enter the weekly rental amount."),
            Arrangement.DAILY: ("daily_rate", "Enter the daily rental amount."),
            Arrangement.SHARE: ("earnings_share_pct", "Enter the driver's percentage share."),
            Arrangement.RENT2OWN: ("weekly_rate", "Enter the weekly amount."),
        }
        if arrangement in required_for:
            field, message = required_for[arrangement]
            if cleaned.get(field) in (None, ""):
                self.add_error(field, message)

        if arrangement == Arrangement.RENT2OWN and not cleaned.get("rent_to_own_months"):
            self.add_error("rent_to_own_months", "How many months until the driver owns it?")

        if cleaned.get("has_tracker") is False and cleaned.get("tracker_paid_by") not in (
            PaidBy.NA, None, ""
        ):
            cleaned["tracker_paid_by"] = PaidBy.NA

        return cleaned


class MultipleFileInput(forms.ClearableFileInput):
    """
    Django 5 refuses `multiple` on ClearableFileInput, because the default
    FileField only ever cleans one file and would silently discard the rest.
    Pair this with MultipleFileField below, which does handle the list.
    """

    allow_multiple_selected = True


class MultipleFileField(forms.ImageField):
    """
    The other half of multi-upload, and the part that's easy to forget.

    With `allow_multiple_selected`, the widget hands the field a LIST of files.
    A plain ImageField calls `to_python` on that list, finds no `.name` on it,
    and rejects the whole submission with "No file was submitted" — before any
    custom `clean_<field>` method gets a look in. The fix is to run the normal
    per-file validation across each item, so every upload is still checked for
    being a real image.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        clean_one = super().clean
        if isinstance(data, (list, tuple)):
            return [clean_one(item, initial) for item in data if item]
        return [clean_one(data, initial)] if data else []


class ListingPhotoForm(forms.Form):
    """
    Multi-upload. Files are re-encoded by the shared pipeline: WebP, resized,
    EXIF stripped. That last one matters here more than anywhere else on the
    site — a car photographed on the owner's driveway carries GPS coordinates
    pointing at their home.
    """

    images = MultipleFileField(
        required=True,
        label="Photos",
        widget=MultipleFileInput(
            attrs={"class": "form-control", "accept": "image/*", "multiple": True}
        ),
        help_text=f"Up to {ListingPhoto.MAX_PER_LISTING} photos. "
                  "Outside, in daylight, showing the whole car.",
    )

    def __init__(self, *args, listing=None, **kwargs):
        self.listing = listing
        super().__init__(*args, **kwargs)

    def clean_images(self):
        # Read the cleaned list, not self.files — the field has already checked
        # each upload is a real, decodable image by this point.
        files = self.cleaned_data.get("images") or []
        if not files:
            raise forms.ValidationError("Choose at least one photo.")

        existing = self.listing.photos.count() if self.listing else 0
        room = ListingPhoto.MAX_PER_LISTING - existing
        if room <= 0:
            raise forms.ValidationError(
                f"This listing already has {ListingPhoto.MAX_PER_LISTING} photos. "
                "Delete one first."
            )
        if len(files) > room:
            raise forms.ValidationError(
                f"You can add {room} more photo{'s' if room > 1 else ''} to this listing."
            )

        processed = []
        for upload in files:
            try:
                processed.append(process_upload(upload, prefix="car"))
            except ImageProcessingError as exc:
                raise forms.ValidationError(
                    f"{upload.name}: {exc.messages[0] if exc.messages else exc}"
                )
        self.processed = processed
        return files

    def save(self):
        listing = self.listing
        start = listing.photos.count()
        has_primary = listing.photos.filter(is_primary=True).exists()
        created = []
        for index, (display, thumb) in enumerate(self.processed):
            photo = ListingPhoto(
                listing=listing,
                order=start + index,
                is_primary=(not has_primary and index == 0),
            )
            photo.image.save(display.name, display, save=False)
            photo.thumbnail.save(thumb.name, thumb, save=False)
            photo.save()
            created.append(photo)
        return created


class FilterFormMixin:
    """
    Shared behaviour for the two browse filter forms.

    Both of them do the same two things beyond filtering: report how many
    filters are on, so the Filter button can carry a badge, and hand back a
    JSON-safe copy of themselves for `SavedSearch.params`.

    That second one is the reason this is a mixin rather than a duplicated
    method. A saved search is replayed against a queryset weeks later, and the
    only safe source for its contents is a form that has already validated
    them. Serialising `cleaned_data` — never `request.GET` — is what makes that
    true for both forms at once, and keeps it true for the third one.
    """

    COUNT_IGNORES = {"sort"}

    @property
    def active_filter_count(self) -> int:
        """Drives the badge on the Filter button, so the user can see state."""
        if not self.is_valid():
            return 0
        return sum(
            1
            for name, value in self.cleaned_data.items()
            if name not in self.COUNT_IGNORES and value not in (None, "", [], False)
        )

    def as_saved_params(self) -> dict:
        """
        Validated filters, flattened to something JSON can hold and a
        querystring can carry.

        Model instances become primary keys, decimals and dates become strings.
        Anything empty is dropped, so a saved search stores the filters someone
        actually chose rather than a snapshot of every field on the form.
        """
        if not self.is_valid():
            return {}

        params = {}
        for name, value in self.cleaned_data.items():
            if value in (None, "", [], False):
                continue
            if isinstance(value, (list, tuple)):
                params[name] = [_scalar(item) for item in value]
            else:
                params[name] = _scalar(value)
        return params


def _scalar(value):
    if isinstance(value, models.Model):
        return value.pk
    if isinstance(value, bool):
        return "on"          # what a checkbox posts, so replay works unchanged
    if isinstance(value, (Decimal, date)):
        return str(value)
    return value


class VehicleFilterForm(FilterFormMixin, forms.Form):
    """
    Browse filters, bound to the querystring.

    Every field is optional and every field is validated, because this form's
    cleaned_data is fed straight into a queryset. Reading raw request.GET into
    a filter is how you end up with a 500 on `?year=banana`.
    """

    ARRANGEMENT_CHOICES = [("", "Any arrangement")] + list(Arrangement.choices)
    COVERED_CHOICES = [
        ("maintenance", "Maintenance covered"),
        ("insurance", "Insurance covered"),
        ("fuel", "Fuel covered"),
        ("tracker", "Tracker included"),
    ]
    SORT_CHOICES = [
        ("", "Best match"),
        ("price_asc", "Price: low to high"),
        ("price_desc", "Price: high to low"),
        ("newest", "Newest first"),
    ]

    q = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Toyota, Polo, Quest…"}
        ),
    )
    city = forms.ModelChoiceField(
        queryset=City.objects.none(),
        required=False,
        empty_label="Any city",
        widget=forms.Select(
            attrs={
                "class": "form-select",
                "hx-get": "/geo/suburb-options/",
                "hx-target": "#id_suburb",
                "hx-trigger": "change",
            }
        ),
    )
    suburb = forms.ModelChoiceField(
        queryset=Suburb.objects.none(),
        required=False,
        empty_label="Any suburb",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    platform = forms.ModelChoiceField(
        queryset=Platform.objects.none(),
        required=False,
        empty_label="Any platform",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    arrangement = forms.ChoiceField(
        required=False, choices=ARRANGEMENT_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    max_price = forms.DecimalField(
        required=False, min_value=0, max_digits=10, decimal_places=2,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "placeholder": "Max per week", "inputmode": "decimal"}
        ),
    )
    covered = forms.MultipleChoiceField(
        required=False, choices=COVERED_CHOICES,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"}),
        label="Owner covers",
    )
    no_prdp = forms.BooleanField(
        required=False, label="No PrDP required",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    sort = forms.ChoiceField(
        required=False, choices=SORT_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # No launch-market filter here. Members create their own cities and
        # suburbs now, and a place you cannot filter by is a listing nobody
        # finds — the browse dropdowns follow what exists, not what was seeded.
        self.fields["city"].queryset = City.objects.select_related("province").order_by("name")
        self.fields["platform"].queryset = Platform.objects.filter(is_active=True)

        suburbs = Suburb.objects.all()
        chosen_city = self.data.get("city") if self.is_bound else None
        if chosen_city and str(chosen_city).isdigit():
            suburbs = suburbs.filter(city_id=chosen_city)
        self.fields["suburb"].queryset = suburbs.select_related("city").order_by("name")

    def apply(self, queryset):
        """Narrow a queryset using validated input. Unknown or bad input is
        simply ignored rather than raising."""
        if not self.is_valid():
            return queryset.ranked()

        data = self.cleaned_data

        if data.get("q"):
            term = data["q"].strip()
            queryset = queryset.filter(
                models.Q(make__icontains=term)
                | models.Q(model__icontains=term)
                | models.Q(description__icontains=term)
            )
        if data.get("suburb"):
            queryset = queryset.filter(suburb=data["suburb"])
        elif data.get("city"):
            queryset = queryset.filter(suburb__city=data["city"])
        if data.get("platform"):
            queryset = queryset.filter(platforms=data["platform"])
        if data.get("arrangement"):
            queryset = queryset.filter(arrangement=data["arrangement"])
        if data.get("max_price") is not None:
            queryset = queryset.filter(weekly_rate__lte=data["max_price"])
        if data.get("no_prdp"):
            queryset = queryset.filter(requires_prdp=False)

        covered_map = {
            "maintenance": "maintenance_paid_by",
            "insurance": "insurance_paid_by",
            "fuel": "fuel_paid_by",
        }
        for key in data.get("covered") or []:
            if key == "tracker":
                queryset = queryset.filter(has_tracker=True)
            elif key in covered_map:
                queryset = queryset.filter(
                    **{f"{covered_map[key]}__in": [PaidBy.OWNER, PaidBy.SHARED]}
                )

        sort = data.get("sort")
        if sort == "price_asc":
            return queryset.order_by(models.F("weekly_rate").asc(nulls_last=True), "-created_at")
        if sort == "price_desc":
            return queryset.order_by(models.F("weekly_rate").desc(nulls_last=True), "-created_at")
        if sort == "newest":
            return queryset.order_by("-created_at")
        return queryset.ranked()


# ===========================================================================
#  Drivers
# ===========================================================================


class DriverListingForm(FreeTextLocationMixin, forms.ModelForm):
    """
    Create and edit a driver's availability profile.

    Shorter than the vehicle form on purpose. An owner scanning drivers is
    deciding on four things — experience, licence, area, price ceiling — and
    every extra required field here costs us a driver who abandoned the form
    on a phone with one bar of signal.
    """

    SUBURB_FIELD = "home_suburb"

    work_suburbs = forms.CharField(
        required=False,
        label="Other areas you'll work",
        widget=forms.TextInput(
            attrs={**TEXT, "placeholder": "Sandton, Midrand, Rosebank"}
        ),
        help_text="Separate them with commas. Leave blank if you only work your home area.",
    )

    class Meta:
        model = DriverListing
        fields = [
            "headline", "years_experience", "licence_code", "has_prdp",
            "platforms_experience", "preferred_arrangement", "max_weekly_rate",
            "home_suburb", "work_suburbs", "available_from", "about",
        ]
        widgets = {
            "headline": forms.TextInput(
                attrs={
                    **TEXT_LG,
                    "placeholder": "Five years on Uber, own PrDP, Soweto and south",
                }
            ),
            "years_experience": forms.NumberInput(
                attrs={**TEXT, "inputmode": "numeric", "min": 0}
            ),
            "licence_code": forms.Select(attrs=SELECT),
            "has_prdp": forms.CheckboxInput(attrs=CHECK),
            "platforms_experience": forms.CheckboxSelectMultiple(attrs=CHECK),
            "preferred_arrangement": forms.Select(attrs=SELECT),
            "max_weekly_rate": forms.NumberInput(
                attrs={**TEXT, "inputmode": "decimal", "step": "1",
                       "placeholder": "Blank if it depends on the car"}
            ),
            "home_suburb": forms.Select(attrs=SELECT),
            "available_from": forms.DateInput(attrs={**TEXT, "type": "date"}),
            "about": forms.Textarea(
                attrs={
                    **TEXT,
                    "rows": 4,
                    "maxlength": 1500,
                    "placeholder": "What an owner would want to know: what you drive now, "
                                   "how long you've been at it, references you can give.",
                }
            ),
        }
        labels = {
            "headline": "Your one line",
            "years_experience": "Years driving e-hailing",
            "licence_code": "Licence code",
            "has_prdp": "I hold a valid PrDP",
            "platforms_experience": "Platforms you've driven",
            "preferred_arrangement": "Arrangement you prefer",
            "max_weekly_rate": "Most you'll pay a week",
            "home_suburb": "Home suburb",
            "work_suburbs": "Other areas you'll work",
            "available_from": "Available from",
            "about": "About you",
        }
        help_texts = {
            "has_prdp": "Most owners filter for this. Verify it later under Verification.",
            "preferred_arrangement": "Leave blank if you're open to anything.",
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["platforms_experience"].queryset = platform_choices(
            user,
            already=self.instance.platforms_experience.all() if self.instance.pk else None,
        )
        self.fields["preferred_arrangement"].required = False

        home = self.instance.home_suburb if self.instance.pk else None
        self._install_place_fields(
            country=getattr(getattr(user, "profile", None), "country", None) or Country.ZA,
            city_initial=home.city.name if home else "",
            suburb_initial=home.name if home else "",
        )
        self.fields["city"].label = "Home city"
        self.fields["home_suburb"].label = "Home suburb"

        if self.instance.pk:
            self.fields["work_suburbs"].initial = ", ".join(
                s.name for s in self.instance.work_suburbs.all()
            )

    def clean(self):
        """
        Work areas are resolved after the home city, because that is where a new
        one gets filed.

        They are NOT confined to the home city. Drivers cross metro borders
        daily — living in Tembisa and working the Sandton rank is the normal
        case here, not the edge case — so an area already known anywhere in the
        member's country matches before anything is created.
        """
        cleaned = super().clean()

        home = cleaned.get(self.SUBURB_FIELD)
        raw = cleaned.get("work_suburbs") or ""
        names = [clean_place_name(part) for part in raw.split(",")]
        names = [name for name in names if name]

        if not names or home is None:
            cleaned["work_suburbs"] = []
            return cleaned

        # A driver who names every area is telling an owner nothing, and makes
        # the suburb filter useless for everyone else.
        if len(names) > DriverListing.MAX_WORK_SUBURBS:
            self.add_error(
                "work_suburbs",
                f"Name at most {DriverListing.MAX_WORK_SUBURBS} areas. "
                "Listing everywhere tells an owner nothing.",
            )
            return cleaned

        country = getattr(getattr(self.user, "profile", None), "country", None) or Country.ZA
        resolved, seen = [], set()
        for name in names:
            match = (
                Suburb.objects.filter(city__province__country=country)
                .filter(models.Q(slug=slugify(name)[:50]) | models.Q(name__iexact=name))
                .order_by("pk")
                .first()
            )
            suburb = match or resolve_suburb(name, home.city)
            if suburb and suburb.pk not in seen:
                seen.add(suburb.pk)
                resolved.append(suburb)

        cleaned["work_suburbs"] = resolved
        return cleaned


class RatingProofForm(forms.ModelForm):
    """
    Upload a platform rating with a screenshot behind it.

    The screenshot is re-encoded by the shared pipeline like every other image,
    then deleted the moment a reviewer decides (see
    `PlatformRatingProof.review`). It exists to be looked at once.
    """

    screenshot = forms.ImageField(
        label="Screenshot",
        widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/*"}),
        help_text="The ratings screen in your driver app. We delete it as soon as "
                  "we've checked it.",
    )

    class Meta:
        model = PlatformRatingProof
        fields = ["platform", "rating", "trips"]
        widgets = {
            "platform": forms.Select(attrs=SELECT),
            "rating": forms.NumberInput(
                attrs={**TEXT, "step": "0.01", "min": "1", "max": "5",
                       "inputmode": "decimal", "placeholder": "4.87"}
            ),
            "trips": forms.NumberInput(
                attrs={**TEXT, "inputmode": "numeric", "placeholder": "Optional"}
            ),
        }
        labels = {"trips": "Completed trips"}

    def __init__(self, *args, driver=None, **kwargs):
        self.driver = driver
        super().__init__(*args, **kwargs)
        self.fields["platform"].queryset = platform_choices(driver)
        self.fields["trips"].required = False

    def clean_screenshot(self):
        upload = self.cleaned_data["screenshot"]
        try:
            self.processed = process_upload(upload, prefix="rating")
        except ImageProcessingError as exc:
            raise forms.ValidationError(exc.messages[0] if exc.messages else str(exc))
        return upload

    def save(self, commit=True):
        """
        Upsert on (driver, platform).

        A re-upload replaces the previous attempt rather than creating a second
        row — the model has a unique constraint on that pair, and a driver
        correcting a rejected rating shouldn't hit an integrity error.
        """
        display, _thumb = self.processed
        data = self.cleaned_data
        proof, _created = PlatformRatingProof.objects.update_or_create(
            driver=self.driver,
            platform=data["platform"],
            defaults={
                "rating": data["rating"],
                "trips": data.get("trips"),
                "status": PlatformRatingProof.Status.PENDING,
                "reviewed_by": None,
                "reviewed_at": None,
                "reject_reason": "",
                "purge_after": timezone.localdate()
                + timedelta(days=PlatformRatingProof.RETENTION_DAYS),
            },
        )
        # A thumbnail would be a second copy of the same personal information
        # to remember to delete. One file, one delete.
        proof.screenshot.save(display.name, display, save=True)
        return proof


class DriverFilterForm(FilterFormMixin, forms.Form):
    """
    Browse filters for drivers, bound to the querystring.

    Mirrors `VehicleFilterForm` field for field where it can, because an owner
    who has learnt one filter panel should not have to learn a second.
    """

    EXPERIENCE_CHOICES = [
        ("", "Any experience"),
        ("1", "1+ years"),
        ("3", "3+ years"),
        ("5", "5+ years"),
    ]
    SORT_CHOICES = [
        ("", "Best match"),
        ("experience", "Most experienced"),
        ("newest", "Newest first"),
    ]

    q = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Name, area, platform…"}
        ),
    )
    city = forms.ModelChoiceField(
        queryset=City.objects.none(),
        required=False,
        empty_label="Any city",
        widget=forms.Select(
            attrs={
                "class": "form-select",
                "hx-get": "/geo/suburb-options/",
                "hx-target": "#id_suburb",
                "hx-trigger": "change",
            }
        ),
    )
    suburb = forms.ModelChoiceField(
        queryset=Suburb.objects.none(),
        required=False,
        empty_label="Any suburb",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    platform = forms.ModelChoiceField(
        queryset=Platform.objects.none(),
        required=False,
        empty_label="Any platform",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    arrangement = forms.ChoiceField(
        required=False,
        choices=[("", "Any arrangement")] + list(Arrangement.choices),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    min_experience = forms.ChoiceField(
        required=False, choices=EXPERIENCE_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Experience",
    )
    has_prdp = forms.BooleanField(
        required=False, label="Has a PrDP",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    rated_only = forms.BooleanField(
        required=False, label="Has a verified platform rating",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    sort = forms.ChoiceField(
        required=False, choices=SORT_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # No launch-market filter here. Members create their own cities and
        # suburbs now, and a place you cannot filter by is a listing nobody
        # finds — the browse dropdowns follow what exists, not what was seeded.
        self.fields["city"].queryset = City.objects.select_related("province").order_by("name")
        self.fields["platform"].queryset = Platform.objects.filter(is_active=True)

        suburbs = Suburb.objects.all()
        chosen_city = self.data.get("city") if self.is_bound else None
        if chosen_city and str(chosen_city).isdigit():
            suburbs = suburbs.filter(city_id=chosen_city)
        self.fields["suburb"].queryset = suburbs.select_related("city").order_by("name")

    def apply(self, queryset):
        if not self.is_valid():
            return queryset.ranked()

        data = self.cleaned_data

        if data.get("q"):
            term = data["q"].strip()
            queryset = queryset.filter(
                models.Q(headline__icontains=term)
                | models.Q(about__icontains=term)
                | models.Q(driver__full_name__icontains=term)
                | models.Q(home_suburb__name__icontains=term)
                | models.Q(work_suburbs__name__icontains=term)
                | models.Q(platforms_experience__name__icontains=term)
            ).distinct()

        if data.get("suburb"):
            # A driver matches on where they live OR where they'll work. An
            # owner in Midrand wants the Tembisa driver who works Midrand, and
            # filtering on home suburb alone would hide exactly that person.
            suburb = data["suburb"]
            queryset = queryset.filter(
                models.Q(home_suburb=suburb) | models.Q(work_suburbs=suburb)
            ).distinct()
        elif data.get("city"):
            city = data["city"]
            queryset = queryset.filter(
                models.Q(home_suburb__city=city) | models.Q(work_suburbs__city=city)
            ).distinct()

        if data.get("platform"):
            queryset = queryset.filter(platforms_experience=data["platform"])
        if data.get("arrangement"):
            queryset = queryset.filter(preferred_arrangement=data["arrangement"])
        if data.get("min_experience"):
            queryset = queryset.filter(years_experience__gte=int(data["min_experience"]))
        if data.get("has_prdp"):
            queryset = queryset.filter(has_prdp=True)
        if data.get("rated_only"):
            queryset = queryset.filter(
                driver__rating_proofs__status=PlatformRatingProof.Status.APPROVED
            ).distinct()

        sort = data.get("sort")
        if sort == "experience":
            return queryset.order_by("-years_experience", "-created_at")
        if sort == "newest":
            return queryset.order_by("-created_at")
        return queryset.ranked()


class SaveSearchForm(forms.ModelForm):
    """
    Names a filter set the user is already looking at.

    The params come from the filter form that produced the current page, not
    from the request — see `views.save_search`.
    """

    class Meta:
        model = SavedSearch
        fields = ["label", "frequency"]
        widgets = {
            "label": forms.TextInput(
                attrs={**TEXT, "placeholder": "Autos under R2 500 in Tembisa"}
            ),
            "frequency": forms.Select(attrs=SELECT),
        }
        labels = {"label": "Call it what you like", "frequency": "Tell me about new matches"}
        help_texts = {
            "frequency": "Alerts aren't switched on yet — we'll use this when they are.",
        }

    def __init__(self, *args, user=None, kind=None, params=None, **kwargs):
        self.user = user
        self.kind = kind
        self.params = params or {}
        super().__init__(*args, **kwargs)

    def clean_label(self):
        label = self.cleaned_data["label"].strip()
        existing = SavedSearch.objects.filter(user=self.user, label__iexact=label)
        if self.instance.pk:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise forms.ValidationError("You already have a saved search with that name.")
        return label

    def clean(self):
        cleaned = super().clean()
        if SavedSearch.objects.filter(user=self.user).count() >= SavedSearch.MAX_PER_USER:
            raise forms.ValidationError(
                f"You can keep {SavedSearch.MAX_PER_USER} saved searches. "
                "Delete one to make room."
            )
        return cleaned

    def save(self, commit=True):
        search = super().save(commit=False)
        search.user = self.user
        search.kind = self.kind
        search.params = self.params
        if commit:
            search.save()
        return search


# ===========================================================================
#  Imported adverts
# ===========================================================================


class AdvertPasteForm(forms.Form):
    """
    Step one of an import: the link, and the advert as it was written.

    Two fields, because this is the step that happens on a phone with Facebook
    open in the other tab. Everything else is guessed from the paste and
    corrected on the next screen.
    """

    # Facebook has a lot of front doors. Anything else is a link copied from
    # the wrong place, and an import whose attribution goes nowhere is worse
    # than no import.
    ALLOWED_HOSTS = (
        "facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com",
        "fb.com", "fb.me", "fb.watch",
    )

    source_url = forms.URLField(
        label="Link to the Facebook post",
        max_length=500,
        widget=forms.URLInput(
            attrs={
                **TEXT,
                "placeholder": "https://www.facebook.com/groups/.../posts/...",
                "inputmode": "url",
            }
        ),
        help_text="Use the permalink of the post itself, not the group address.",
    )
    raw_text = forms.CharField(
        label="Paste the advert",
        widget=forms.Textarea(
            attrs={
                **TEXT,
                "rows": 10,
                "placeholder": "Paste the whole post here.",
            }
        ),
        help_text=(
            "Read once to pre-fill the next screen, then thrown away. "
            "Nothing you paste here is published as it stands."
        ),
    )

    def clean_source_url(self):
        url = self.cleaned_data["source_url"].strip()
        host = (urlparse(url).hostname or "").lower()
        if host not in self.ALLOWED_HOSTS:
            raise forms.ValidationError(
                "That is not a Facebook post link. The link is the attribution, so it has "
                "to point at the original."
            )
        if VehicleListing.objects.filter(source_url=url).exists():
            raise forms.ValidationError("That post is already on the site.")
        return url


class ImportedListingForm(VehicleListingForm):
    """
    Step two: the structured listing, with its provenance attached.

    THE DIFFERENCES FROM THE OWNER FORM, AND WHY
    --------------------------------------------
    `description` is a SUMMARY. It is capped short and scrubbed of contact
    details on the way in, for two reasons, neither optional:

    * Copyright. The advert is somebody else's writing. Facts about a car —
      make, year, price, who pays for the fuel — are not protected, and they
      are exactly what this site turns into filters. The paragraph written
      around those facts is protected. So we take the facts, write our own
      line, and link to the original for anyone who wants the words.

    * Contact details. Somebody put their number in a group they chose. They
      did not agree to it appearing on a website they have never heard of, and
      republishing it would route straight around the introduction flow.
      `redact_contacts` runs on this field unconditionally — see
      `apps.core.redact` for why that check is not left to the person typing.

    The confirmation checkbox is deliberately not a model field. It would
    record nothing useful, being True on every row, and its whole job is to
    make somebody read one sentence before publishing another person's advert.
    """

    SUMMARY_MAX = 600

    summarised = forms.BooleanField(
        required=True,
        label="This summary is in my own words",
        help_text="Facts, not the original wording. The link is how we credit the writer.",
        widget=forms.CheckboxInput(attrs=CHECK),
    )

    class Meta(VehicleListingForm.Meta):
        fields = VehicleListingForm.Meta.fields + [
            "source_url", "source_author_name", "source_posted_at",
        ]
        widgets = {
            **VehicleListingForm.Meta.widgets,
            "description": forms.Textarea(
                attrs={
                    **TEXT,
                    "rows": 4,
                    "maxlength": 600,
                    "placeholder": "A line or two in your own words: what the car is, what "
                                   "the deal is, anything a driver has to know.",
                }
            ),
            "source_url": forms.URLInput(attrs={**TEXT, "readonly": "readonly"}),
            "source_author_name": forms.TextInput(
                attrs={**TEXT, "placeholder": "Name on the post"}
            ),
            "source_posted_at": forms.DateTimeInput(
                attrs={**TEXT, "type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
        }
        labels = {
            **VehicleListingForm.Meta.labels,
            "description": "Summary",
            "source_url": "Original post",
            "source_author_name": "Posted by",
            "source_posted_at": "Posted on",
        }

    def __init__(self, *args, imported_by=None, **kwargs):
        self.imported_by = imported_by
        super().__init__(*args, **kwargs)
        self.fields["source_url"].required = True
        self.fields["source_author_name"].required = False
        self.fields["source_posted_at"].required = False
        self.fields["description"].required = True
        self.fields["description"].help_text = (
            f"Up to {self.SUMMARY_MAX} characters, in your own words. Phone numbers and "
            "email addresses are stripped out automatically."
        )

    def clean_description(self):
        """Scrub first, then measure. Redaction can only make it shorter."""
        summary = redact_contacts(self.cleaned_data.get("description", "")).strip()
        if not summary:
            raise forms.ValidationError("Write a line or two about the car.")
        if len(summary) > self.SUMMARY_MAX:
            raise forms.ValidationError(
                f"Keep it under {self.SUMMARY_MAX} characters. It is a summary, not the "
                "whole post — the link goes to the original."
            )
        return summary

    def save(self, commit=True):
        listing = super().save(commit=False)
        listing.source = VehicleListing.Source.FACEBOOK
        listing.imported_by = self.imported_by
        listing.owner = None
        if commit:
            listing.save()
            self.save_m2m()
        return listing


class ClaimListingForm(forms.ModelForm):
    """That is my car. A request, not a switch — see `ListingClaim`."""

    class Meta:
        model = ListingClaim
        fields = ["message"]
        widgets = {
            "message": forms.Textarea(
                attrs={
                    **TEXT,
                    "rows": 4,
                    "maxlength": 600,
                    "placeholder": "Anything that helps us match you to the post: the group "
                                   "it was in, the name you posted under, the registration.",
                }
            ),
        }
        labels = {"message": "How can we check this is yours?"}

    def __init__(self, *args, listing=None, claimant=None, **kwargs):
        self.listing = listing
        self.claimant = claimant
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        if not self.listing.is_claimable:
            raise forms.ValidationError("This listing is not open to claims.")
        existing = ListingClaim.objects.filter(
            listing=self.listing, claimant=self.claimant
        ).first()
        if existing:
            if existing.is_pending:
                raise forms.ValidationError(
                    "You have already claimed this one — we are checking it."
                )
            raise forms.ValidationError(
                existing.reject_reason or "We have already looked at your claim on this listing."
            )
        return cleaned

    def save(self, commit=True):
        claim = super().save(commit=False)
        claim.listing = self.listing
        claim.claimant = self.claimant
        if commit:
            claim.save()
        return claim
