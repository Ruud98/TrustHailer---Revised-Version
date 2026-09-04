from django import forms
from django.conf import settings

from apps.core import phone as phone_utils
from apps.core.images import ImageProcessingError, process_upload
from apps.geo.forms import FreeTextLocationMixin
from apps.geo.models import Country

from .models import Profile, User


class JoinForm(forms.Form):
    """Step one: an email address. Nothing else, and no password ever."""

    email = forms.EmailField(
        widget=forms.EmailInput(
            attrs={
                "class": "form-control form-control-lg",
                "placeholder": "you@example.com",
                "inputmode": "email",
                "autocomplete": "email",
                "autocapitalize": "off",
                "spellcheck": "false",
                "autofocus": "autofocus",
            }
        )
    )

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()


class OTPForm(forms.Form):
    code = forms.CharField(
        max_length=8,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-lg otp-input",
                "placeholder": "······",
                "inputmode": "numeric",
                "autocomplete": "one-time-code",
                "autofocus": "autofocus",
                "pattern": "[0-9]*",
            }
        ),
    )

    def clean_code(self):
        code = "".join(ch for ch in self.cleaned_data["code"] if ch.isdigit())
        if len(code) != settings.OTP_LENGTH:
            raise forms.ValidationError(f"Enter the {settings.OTP_LENGTH}-digit code we sent you.")
        return code


class RoleForm(forms.ModelForm):
    """
    Onboarding step 1. Checkboxes, not radios — owner-drivers are common and
    forcing a single choice here makes them start with a lie.
    """

    class Meta:
        model = Profile
        fields = ["is_owner", "is_driver", "is_business"]
        widgets = {
            f: forms.CheckboxInput(attrs={"class": "form-check-input"})
            for f in ["is_owner", "is_driver", "is_business"]
        }

    def clean(self):
        cleaned = super().clean()
        if not any([cleaned.get("is_owner"), cleaned.get("is_driver"), cleaned.get("is_business")]):
            raise forms.ValidationError("Pick at least one — you can change this later.")
        return cleaned


class LocationForm(FreeTextLocationMixin, forms.ModelForm):
    """
    Onboarding step 2. Country, then city and suburb, all typed.

    Suburb-level location is the whole reason this beats a Facebook group, so
    it's required rather than optional.

    The country is asked here, and asked before the two boxes below it, because
    it is the one piece that cannot be worked out from the rest: a city nobody
    has typed before has to be filed under a country, and the platforms offered
    later depend on which market this person works in. It used to be inferred
    from the seeded city, which stopped being possible the moment cities became
    something you type. `ProfileDetailsForm` reads it back off the profile
    rather than asking a second time.
    """

    country = forms.ChoiceField(
        choices=Country.choices,
        initial=Country.ZA,
        label="Country",
        widget=forms.Select(attrs={"class": "form-select form-select-lg"}),
        help_text="Which market you work in. It sets the platforms you'll be offered.",
    )

    class Meta:
        model = Profile
        fields = ["country", "suburb"]
        labels = {"suburb": "Suburb"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        suburb = self.instance.suburb if self.instance and self.instance.suburb_id else None
        self._install_place_fields(
            country=self.instance.country if self.instance.pk else Country.ZA,
            city_initial=suburb.city.name if suburb else "",
            suburb_initial=suburb.name if suburb else "",
        )
        for name in ("city", self.SUBURB_FIELD):
            self.fields[name].widget.attrs["class"] = "form-control form-control-lg"


class ProfileDetailsForm(forms.ModelForm):
    """
    Onboarding step 3, reused for profile editing.

    The phone number is collected here and stored UNVERIFIED. It isn't shown to
    anyone until an introduction is approved, and it isn't verified until the
    user does something where trust carries weight. Collecting it now — while
    they're already filling in a form — is much easier than chasing it later.
    """

    full_name = forms.CharField(
        max_length=120,
        widget=forms.TextInput(
            attrs={"class": "form-control form-control-lg", "placeholder": "Thabo Mokoena"}
        ),
        help_text="Use the name on your ID. Verification will check it.",
    )
    phone = forms.CharField(
        max_length=24,
        label="Mobile number",
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-lg",
                "placeholder": "082 123 4567",
                "inputmode": "tel",
                "autocomplete": "tel",
            }
        ),
        help_text="Hidden until you approve an introduction. Deals here happen on the phone.",
    )
    avatar_file = forms.ImageField(
        required=False,
        label="Profile photo",
        widget=forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/*"}),
    )

    class Meta:
        model = Profile
        fields = ["bio", "whatsapp_ok"]
        widgets = {
            "bio": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "maxlength": 500,
                    "placeholder": "A line or two about you. Owners and drivers read this.",
                }
            ),
            "whatsapp_ok": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        labels = {"whatsapp_ok": "Show a WhatsApp button once my contacts are released"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.user_id:
            user = self.instance.user
            self.fields["full_name"].initial = user.full_name
            if user.phone:
                self.fields["phone"].initial = phone_utils.display(user.phone)

    def clean(self):
        cleaned = super().clean()
        raw = cleaned.get("phone")
        # The market chosen at the location step doubles as the dialling code for
        # a number typed in local form. Asking for the country twice in one
        # signup was the friction worth removing; a member whose number is from
        # somewhere else can still type it in full international form.
        country = getattr(self.instance, "country", None) or Country.ZA
        if raw:
            try:
                normalised = phone_utils.normalise(raw, default_country=country)
            except phone_utils.PhoneError as exc:
                self.add_error("phone", exc.messages[0] if exc.messages else str(exc))
            else:
                clash = User.objects.filter(phone=normalised)
                if self.instance and self.instance.user_id:
                    clash = clash.exclude(pk=self.instance.user_id)
                if clash.exists():
                    self.add_error(
                        "phone", "That number is already on another account."
                    )
                else:
                    cleaned["phone"] = normalised
        return cleaned

    def clean_avatar_file(self):
        upload = self.cleaned_data.get("avatar_file")
        if not upload:
            return None
        try:
            self._processed = process_upload(upload, max_edge=600, prefix="avatar")
        except ImageProcessingError as exc:
            raise forms.ValidationError(exc.messages[0] if exc.messages else str(exc))
        return upload

    def save(self, commit=True):
        profile = super().save(commit=False)
        user = profile.user
        new_phone = self.cleaned_data.get("phone")

        user.full_name = self.cleaned_data["full_name"]
        updated = ["full_name"]

        if new_phone and new_phone != user.phone:
            user.phone = new_phone
            updated.append("phone")

        if user.refresh_handle_if_placeholder():
            updated.append("handle")

        processed = getattr(self, "_processed", None)
        if processed:
            display, thumb = processed
            profile.avatar.save(display.name, display, save=False)
            profile.avatar_thumb.save(thumb.name, thumb, save=False)

        if commit:
            user.save(update_fields=updated)
            profile.save()
        return profile


class PhoneVerifyStartForm(forms.Form):
    """Confirm the number on file before spending anything to verify it."""

    confirm = forms.BooleanField(required=False, widget=forms.HiddenInput, initial=True)


class SettingsForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["is_owner", "is_driver", "is_business", "whatsapp_ok", "hide_from_search"]
        widgets = {
            f: forms.CheckboxInput(attrs={"class": "form-check-input"})
            for f in ["is_owner", "is_driver", "is_business", "whatsapp_ok", "hide_from_search"]
        }
        labels = {"hide_from_search": "Hide my profile from search results"}
