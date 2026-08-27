from django import forms
from django.db import models
from django.utils import timezone

from apps.core.images import ImageProcessingError, process_upload
from apps.geo.models import City, Suburb

from .models import (
    Arrangement,
    FuelType,
    ListingPhoto,
    PaidBy,
    Platform,
    Transmission,
    VehicleListing,
)

TEXT = {"class": "form-control"}
TEXT_LG = {"class": "form-control form-control-lg"}
SELECT = {"class": "form-select"}
CHECK = {"class": "form-check-input"}


class VehicleListingForm(forms.ModelForm):
    """
    Create and edit a car.

    Long, but every field earns its place — these are the questions a driver
    would otherwise ask in five back-and-forth messages. Asking once, in
    structured form, is the whole efficiency gain over a Facebook group.
    """

    city = forms.ModelChoiceField(
        queryset=City.objects.none(),
        empty_label="Choose the city…",
        widget=forms.Select(
            attrs={
                **SELECT,
                "hx-get": "/geo/suburb-options/",
                "hx-target": "#id_suburb",
                "hx-trigger": "change",
                "name": "city",
            }
        ),
    )

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["city"].queryset = (
            City.objects.filter(is_launch_market=True)
            .select_related("province")
            .order_by("province__country", "name")
        )
        self.fields["platforms"].queryset = Platform.objects.filter(is_active=True)

        # Same guard as onboarding: only launch-market suburbs, narrowed to the
        # posted city, so a crafted POST can't place a car in an unlaunched area.
        suburbs = Suburb.objects.filter(city__is_launch_market=True)
        posted_city = self.data.get("city") if self.is_bound else None
        if posted_city and str(posted_city).isdigit():
            suburbs = suburbs.filter(city_id=posted_city)
        elif self.instance and self.instance.suburb_id:
            suburbs = suburbs.filter(city_id=self.instance.suburb.city_id)
        self.fields["suburb"].queryset = suburbs.select_related("city").order_by("name")

        if self.instance and self.instance.suburb_id:
            self.fields["city"].initial = self.instance.suburb.city_id

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


class VehicleFilterForm(forms.Form):
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
        self.fields["city"].queryset = (
            City.objects.filter(is_launch_market=True).select_related("province").order_by("name")
        )
        self.fields["platform"].queryset = Platform.objects.filter(is_active=True)

        suburbs = Suburb.objects.filter(city__is_launch_market=True)
        chosen_city = self.data.get("city") if self.is_bound else None
        if chosen_city and str(chosen_city).isdigit():
            suburbs = suburbs.filter(city_id=chosen_city)
        self.fields["suburb"].queryset = suburbs.select_related("city").order_by("name")

    @property
    def active_filter_count(self) -> int:
        """Drives the badge on the Filter button, so the user can see state."""
        if not self.is_valid():
            return 0
        ignored = {"sort"}
        return sum(
            1
            for name, value in self.cleaned_data.items()
            if name not in ignored and value not in (None, "", [], False)
        )

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
