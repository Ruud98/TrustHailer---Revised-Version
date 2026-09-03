from datetime import date, timedelta

from django import forms
from django.utils import timezone
from django.utils.formats import date_format

from apps.core.redact import contains_contact_details
from apps.intros.models import IntroRequest

from .models import Placement, Review

TEXT = {"class": "form-control"}
SELECT = {"class": "form-select"}


class PlacementForm(forms.ModelForm):
    """
    Record that a deal happened, choosing from people you were introduced to.

    THE DROPDOWN IS THE SECURITY MODEL
    ----------------------------------
    `intro` is a choice field over this user's approved introductions, not a
    free text box and not a search over every member. That is what ties every
    future review back to two people who each pressed a button agreeing to be
    put in touch. A "type a name" field here would quietly reopen the fake
    review problem the whole design exists to close.
    """

    intro = forms.ModelChoiceField(
        queryset=IntroRequest.objects.none(),
        empty_label="Choose who…",
        widget=forms.Select(attrs=SELECT),
        label="Who is driving it?",
        help_text="Only people you have been introduced to through the site.",
    )

    class Meta:
        model = Placement
        fields = ["started_on"]
        widgets = {
            "started_on": forms.DateInput(attrs={**TEXT, "type": "date"}),
        }
        labels = {"started_on": "When did they start?"}

    def __init__(self, *args, user=None, listing=None, **kwargs):
        self.user = user
        self.listing = listing
        super().__init__(*args, **kwargs)
        self.fields["intro"].queryset = self._candidates()
        self.fields["intro"].label_from_instance = self._describe
        self.fields["started_on"].initial = date.today()

    def _candidates(self):
        """
        Approved introductions about this car, in either direction, whose other
        party is not already driving it.
        """
        from django.db.models import Q

        taken = Placement.objects.filter(
            vehicle_listing=self.listing, ended_on__isnull=True
        ).values_list("driver_id", flat=True)

        return (
            IntroRequest.objects.filter(
                status=IntroRequest.Status.APPROVED, vehicle_listing=self.listing
            )
            .filter(Q(from_user=self.user) | Q(to_user=self.user))
            .exclude(from_user__in=taken)
            .exclude(to_user__in=taken)
            .select_related("from_user", "to_user")
        )

    def _describe(self, intro):
        other = intro.other_party(self.user)
        # Django's date formatter rather than strftime: "%-d" is a glibc
        # extension and raises "Invalid format string" on Windows, which is
        # where this was first run.
        when = date_format(timezone.localtime(intro.created_at), "j M Y")
        return f"{other.full_name or other.handle} — introduced {when}"

    def clean_started_on(self):
        started_on = self.cleaned_data["started_on"]
        if started_on > date.today() + timedelta(days=1):
            raise forms.ValidationError("That is in the future.")
        if started_on < date.today() - timedelta(days=365 * 3):
            raise forms.ValidationError("That is too far back to record here.")
        return started_on

    def save(self, commit=True):
        placement = super().save(commit=False)
        intro = self.cleaned_data["intro"]
        other = intro.other_party(self.user)

        placement.vehicle_listing = self.listing
        placement.intro = intro
        placement.owner = self.listing.owner
        placement.driver = other if self.listing.owner_id == self.user.pk else self.user

        # Creating it is confirming it. The other side still has to agree — see
        # `Placement`, and the two-sided rule that hangs off it.
        if placement.owner_id == self.user.pk:
            placement.confirmed_by_owner = True
        else:
            placement.confirmed_by_driver = True

        if commit:
            placement.save()
        return placement


class EndPlacementForm(forms.ModelForm):
    """
    Close a placement.

    The reason is required and structured, and it is never published — it goes
    to nobody but us. Free text here would become a second, unmoderated review
    field with none of the double-blind protection, and a dropdown gives us
    something countable about why arrangements in this market break down, which
    is worth knowing.
    """

    class Meta:
        model = Placement
        fields = ["ended_on", "end_reason"]
        widgets = {
            "ended_on": forms.DateInput(attrs={**TEXT, "type": "date"}),
            "end_reason": forms.Select(attrs=SELECT),
        }
        labels = {"ended_on": "When did it end?", "end_reason": "Why did it end?"}

    def __init__(self, *args, placement=None, **kwargs):
        self.placement = placement
        super().__init__(*args, **kwargs)
        self.fields["ended_on"].initial = date.today()
        self.fields["end_reason"].required = True
        self.fields["end_reason"].help_text = "Only we see this. It is never published."

    def clean_ended_on(self):
        ended_on = self.cleaned_data["ended_on"]
        if ended_on > date.today():
            raise forms.ValidationError("That is in the future.")
        if self.placement and ended_on < self.placement.started_on:
            raise forms.ValidationError("That is before it started.")
        return ended_on


RATING_CHOICES = [
    (5, "5 — no complaints at all"),
    (4, "4 — good"),
    (3, "3 — fine"),
    (2, "2 — problems"),
    (1, "1 — would not do it again"),
]


class ReviewForm(forms.ModelForm):
    """
    The review, with different questions depending on which side you are on.

    An owner is asked about payment and how the car came back. A driver is
    asked about fairness, whether things got fixed, and whether the deposit
    came back. Asking both the same generic questions would throw away exactly
    what each side is looking for.
    """

    class Meta:
        model = Review
        fields = [
            "overall", "communication",
            "payment_reliability", "vehicle_care",
            "fairness", "maintenance_response", "deposit_returned",
            "body",
        ]
        widgets = {
            "body": forms.Textarea(
                attrs={
                    **TEXT,
                    "rows": 5,
                    "maxlength": 1500,
                    "placeholder": "What was it actually like? What would somebody else "
                                   "want to know before doing this deal?",
                }
            ),
            "deposit_returned": forms.Select(
                attrs=SELECT, choices=[(None, "Not applicable"), (True, "Yes"), (False, "No")]
            ),
        }
        labels = {
            "overall": "Overall",
            "communication": "Communication",
            "payment_reliability": "Paid on time",
            "vehicle_care": "Looked after the car",
            "fairness": "Fair to deal with",
            "maintenance_response": "Fixed things when they broke",
            "deposit_returned": "Did you get your deposit back?",
            "body": "In your own words",
        }

    def __init__(self, *args, placement=None, author=None, **kwargs):
        self.placement = placement
        self.author = author
        super().__init__(*args, **kwargs)

        for name in ("overall", "communication", "payment_reliability", "vehicle_care",
                     "fairness", "maintenance_response"):
            self.fields[name].widget = forms.Select(
                choices=[("", "Choose…")] + RATING_CHOICES, attrs=SELECT
            )

        reviewing_the_driver = placement and author.pk == placement.owner_id
        drop = (
            ["fairness", "maintenance_response", "deposit_returned"]
            if reviewing_the_driver
            else ["payment_reliability", "vehicle_care"]
        )
        for name in drop:
            del self.fields[name]

        for name in list(self.fields):
            if name not in ("overall", "communication"):
                self.fields[name].required = False

        self.fields["body"].help_text = (
            "Neither review appears until you have both written one, or 14 days have "
            "passed. Nobody sees what you wrote before they write theirs."
        )

    def clean_body(self):
        body = (self.cleaned_data.get("body") or "").strip()
        if contains_contact_details(body):
            # A review is a public page that outlives the deal. A phone number
            # in one is somebody's number republished to strangers for good.
            raise forms.ValidationError(
                "Leave phone numbers and email addresses out — a review stays up."
            )
        return body

    def save(self, commit=True):
        review = super().save(commit=False)
        review.placement = self.placement
        review.author = self.author
        review.subject = self.placement.other_party(self.author)
        if commit:
            review.save()
        return review
