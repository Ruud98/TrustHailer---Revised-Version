from django import forms

from apps.core.redact import contains_contact_details, redact_contacts
from apps.listings.models import VehicleListing

from .models import IntroRequest

TEXT = {"class": "form-control"}


class IntroRequestForm(forms.ModelForm):
    """
    The note that goes with a request.

    WHY THE MESSAGE IS SCRUBBED OF PHONE NUMBERS
    --------------------------------------------
    Because otherwise the first thing everybody types is their number, and the
    whole double opt-in becomes a formality somebody routes around by writing
    "call me on 082...". That is not a hypothetical: it is what people do on
    every classifieds site that asks for a message before it releases a
    contact, and it takes about a week.

    Letting it happen would hand a number to somebody who has not agreed to
    receive it — the exact thing the masking exists to prevent — and it makes
    the approval meaningless for the person on the other end, who now gets
    phoned whether they said yes or not. So the same redactor the advert
    importer uses runs here, and the form says so rather than silently eating
    what somebody typed.
    """

    class Meta:
        model = IntroRequest
        fields = ["message"]
        widgets = {
            "message": forms.Textarea(
                attrs={
                    **TEXT,
                    "rows": 5,
                    "maxlength": 400,
                    "placeholder": "Who you are, what you are looking for, and when you "
                                   "could meet. A few honest lines beat a long one.",
                }
            ),
        }
        labels = {"message": "Your message"}

    def __init__(self, *args, from_user=None, to_user=None, listing=None, **kwargs):
        self.from_user = from_user
        self.to_user = to_user
        self.listing = listing
        super().__init__(*args, **kwargs)
        self.fields["message"].help_text = (
            "Leave your number out — it is released to both of you automatically if "
            "they say yes. Numbers typed here are removed."
        )

    def clean_message(self):
        message = (self.cleaned_data.get("message") or "").strip()
        if len(message) < 20:
            raise forms.ValidationError(
                "Write a little more. A one-word request is the easiest kind to ignore."
            )
        if contains_contact_details(message):
            # Refuse rather than silently redact. Somebody who typed their
            # number deserves to be told why it will not go, or they will
            # assume the site is broken and send it again.
            raise forms.ValidationError(
                "Leave contact details out. If they say yes, you both get each other's "
                "number straight away."
            )
        return redact_contacts(message)

    def save(self, commit=True):
        intro = super().save(commit=False)
        intro.from_user = self.from_user
        intro.to_user = self.to_user
        if isinstance(self.listing, VehicleListing):
            intro.vehicle_listing = self.listing
        else:
            intro.driver_listing = self.listing
        if commit:
            intro.save()
        return intro
