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


def full_name_field():
    return forms.CharField(
        max_length=120,
        widget=forms.TextInput(
            attrs={"class": "form-control form-control-lg", "placeholder": "Thabo Mokoena"}
        ),
        help_text="Use the name on your ID. Verification will check it.",
    )


def phone_field():
    return forms.CharField(
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


class NameAndPhoneMixin:
    """
    The two things every member has to give us, wherever they are asked for.

    Onboarding and profile editing both collect a name and a number, and both
    have to normalise the number, check nobody else holds it, and write the
    pair onto the User rather than the Profile the form is bound to. Two copies
    of that drifted apart once already, so the behaviour lives here.

    The FIELDS are declared by each form from the factories above rather than on
    this mixin. Django's form metaclass only harvests `declared_fields` off bases
    that it built itself, so a field sitting on a plain mixin is silently never
    registered — the form renders without it and `self.fields["full_name"]`
    raises a KeyError. One definition, two declarations, is the honest way round
    that.
    """

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
        if not raw:
            return cleaned

        # The market on the form doubles as the dialling code for a number typed
        # in local form. Someone whose number is from elsewhere can still type it
        # in full international form.
        country = (
            cleaned.get("country") or getattr(self.instance, "country", None) or Country.ZA
        )
        try:
            normalised = phone_utils.normalise(raw, default_country=country)
        except phone_utils.PhoneError as exc:
            self.add_error("phone", exc.messages[0] if exc.messages else str(exc))
            return cleaned

        clash = User.objects.filter(phone=normalised)
        if self.instance and self.instance.user_id:
            clash = clash.exclude(pk=self.instance.user_id)
        if clash.exists():
            self.add_error("phone", "That number is already on another account.")
        else:
            cleaned["phone"] = normalised
        return cleaned

    def apply_name_and_phone(self, profile) -> list:
        """Write both onto the User. Returns the fields to save."""
        user = profile.user
        user.full_name = self.cleaned_data["full_name"]
        updated = ["full_name"]

        new_phone = self.cleaned_data.get("phone")
        if new_phone and new_phone != user.phone:
            user.phone = new_phone
            updated.append("phone")

        if user.refresh_handle_if_placeholder():
            updated.append("handle")
        return updated


class OnboardingForm(NameAndPhoneMixin, FreeTextLocationMixin, forms.ModelForm):
    """
    Everything we ask a new member, on one screen.

    It used to be three: roles, then location, then name and number. Three
    screens is three chances to close the tab, and the middle one asked for a
    city from a dropdown that might not contain it. What is left is the
    shortest set of answers the marketplace cannot work without — who you are,
    how someone reaches you once you agree to be reached, and where you are.

    The roles are the exception: optional, at the bottom, and off by default.
    They drive one pricing rule and some admin filters, which is not worth
    blocking a signup over. Anyone who skips them can set them in Settings, and
    the marketplace works the same either way.

    Photo, bio and the WhatsApp preference are not here at all. None of them
    changes what the site can do for someone, and every one of them is a reason
    to abandon a form on a phone with one bar of signal. They live in profile
    editing, where somebody who wants them will go looking.
    """

    ROLE_FIELDS = ["is_owner", "is_driver", "is_business"]

    full_name = full_name_field()
    phone = phone_field()
    country = forms.ChoiceField(
        choices=Country.choices,
        initial=Country.ZA,
        label="Country",
        widget=forms.Select(attrs={"class": "form-select form-select-lg"}),
        help_text="Which market you work in. It sets the platforms you'll be offered.",
    )

    class Meta:
        model = Profile
        fields = ["country", "suburb", "is_owner", "is_driver", "is_business"]
        widgets = {
            f: forms.CheckboxInput(attrs={"class": "form-check-input"})
            for f in ["is_owner", "is_driver", "is_business"]
        }
        labels = {
            "is_owner": "I have a car to rent out",
            "is_driver": "I'm looking for a car to drive",
            "is_business": "I run a business drivers use",
        }

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
        for name in self.ROLE_FIELDS:
            self.fields[name].required = False

    def save(self, commit=True):
        profile = super().save(commit=False)
        updated = self.apply_name_and_phone(profile)
        if commit:
            profile.user.save(update_fields=updated)
            profile.save()
        return profile


class ProfileDetailsForm(NameAndPhoneMixin, forms.ModelForm):
    """
    Onboarding step 3, reused for profile editing.

    The phone number is collected here and stored UNVERIFIED. It isn't shown to
    anyone until an introduction is approved, and it isn't verified until the
    user does something where trust carries weight. Collecting it now — while
    they're already filling in a form — is much easier than chasing it later.
    """

    full_name = full_name_field()
    phone = phone_field()
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
        updated = self.apply_name_and_phone(profile)

        processed = getattr(self, "_processed", None)
        if processed:
            display, thumb = processed
            profile.avatar.save(display.name, display, save=False)
            profile.avatar_thumb.save(thumb.name, thumb, save=False)

        if commit:
            profile.user.save(update_fields=updated)
            profile.save()
        return profile


class SettingsForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ["is_owner", "is_driver", "is_business", "whatsapp_ok", "hide_from_search"]
        widgets = {
            f: forms.CheckboxInput(attrs={"class": "form-check-input"})
            for f in ["is_owner", "is_driver", "is_business", "whatsapp_ok", "hide_from_search"]
        }
        labels = {"hide_from_search": "Hide my profile from search results"}
