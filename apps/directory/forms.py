from django import forms

from apps.core import phone as phone_utils
from apps.core.images import ImageProcessingError, process_upload
from apps.geo.models import City, Suburb

from .models import BusinessListing

TEXT = {"class": "form-control"}
SELECT = {"class": "form-select"}


class BusinessListingForm(forms.ModelForm):
    """
    List a business. Unlike every other listing form on the site, the number
    entered here is shown in full, immediately, to anyone who opens the page —
    see the model docstring for why a business is not treated like a person.
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
        model = BusinessListing
        fields = ["name", "category", "description", "phone", "whatsapp", "suburb", "logo"]
        widgets = {
            "name": forms.TextInput(attrs={**TEXT, "placeholder": "Speedy Tyres"}),
            "category": forms.Select(attrs=SELECT),
            "description": forms.Textarea(
                attrs={
                    **TEXT, "rows": 4, "maxlength": 1500,
                    "placeholder": "What you do, and anything a driver should know before "
                                   "calling — hours, whether you fit while they wait.",
                }
            ),
            "phone": forms.TextInput(attrs={**TEXT, "placeholder": "082 123 4567"}),
            "whatsapp": forms.TextInput(
                attrs={**TEXT, "placeholder": "Leave blank if same as phone"}
            ),
            "suburb": forms.Select(attrs=SELECT),
            "logo": forms.ClearableFileInput(attrs={**TEXT, "accept": "image/*"}),
        }
        help_texts = {
            "phone": "Shown in full on the page — this is a directory people call from, "
                     "not a masked listing.",
            "whatsapp": "Only if it is different from the number above.",
        }

    def __init__(self, *args, owner=None, **kwargs):
        self.owner = owner
        super().__init__(*args, **kwargs)
        self.fields["logo"].required = False
        self.fields["whatsapp"].required = False
        self.fields["city"].queryset = (
            City.objects.filter(is_launch_market=True)
            .select_related("province")
            .order_by("province__country", "name")
        )

        suburbs = Suburb.objects.filter(city__is_launch_market=True)
        posted_city = self.data.get("city") if self.is_bound else None
        if posted_city and str(posted_city).isdigit():
            suburbs = suburbs.filter(city_id=posted_city)
        elif self.instance and self.instance.suburb_id:
            suburbs = suburbs.filter(city_id=self.instance.suburb.city_id)
        self.fields["suburb"].queryset = suburbs.select_related("city").order_by("name")

        if self.instance and self.instance.suburb_id:
            self.fields["city"].initial = self.instance.suburb.city_id

    def clean_phone(self):
        return self._normalise_number(self.cleaned_data.get("phone"), required=True)

    def clean_whatsapp(self):
        return self._normalise_number(self.cleaned_data.get("whatsapp"), required=False)

    def _normalise_number(self, raw, *, required):
        """
        Stored in E.164, same as every other phone number on the site — see
        the README decision on that. `mobile_only=False` because a business
        landline is a normal, correct answer here, unlike everywhere else this
        module is used, where the number has to receive an OTP.
        """
        if not raw:
            if required:
                raise forms.ValidationError("Enter a phone number.")
            return ""
        try:
            return phone_utils.normalise(raw, mobile_only=False)
        except phone_utils.PhoneError as exc:
            raise forms.ValidationError(str(exc))

    def clean_logo(self):
        upload = self.cleaned_data.get("logo")
        if not upload or not hasattr(upload, "file"):
            return upload
        try:
            display, _thumb = process_upload(upload, max_edge=800, prefix="biz")
        except ImageProcessingError as exc:
            raise forms.ValidationError(str(exc))
        return display

    def save(self, commit=True):
        listing = super().save(commit=False)
        if self.owner is not None:
            listing.owner_user = self.owner
        if commit:
            listing.save()
        return listing


class DirectoryFilterForm(forms.Form):
    category = forms.ChoiceField(
        required=False,
        choices=[("", "Any category")] + list(BusinessListing.Category.choices),
        widget=forms.Select(attrs=SELECT),
    )
    suburb = forms.ModelChoiceField(
        required=False,
        queryset=Suburb.objects.none(),
        empty_label="Any suburb",
        widget=forms.Select(attrs=SELECT),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["suburb"].queryset = (
            Suburb.objects.filter(city__is_launch_market=True)
            .select_related("city")
            .order_by("name")
        )

    def apply(self, queryset):
        if not self.is_valid():
            return queryset
        category = self.cleaned_data.get("category")
        suburb = self.cleaned_data.get("suburb")
        if category:
            queryset = queryset.filter(category=category)
        if suburb:
            queryset = queryset.filter(suburb=suburb)
        return queryset

    @property
    def active_count(self):
        if not self.is_valid():
            return 0
        return sum(1 for value in self.cleaned_data.values() if value)
