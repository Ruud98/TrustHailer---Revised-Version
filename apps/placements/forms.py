from datetime import date, timedelta

from django import forms
from django.db import models
from django.utils import timezone
from django.utils.formats import date_format

from apps.core.redact import contains_contact_details
from apps.accounts.models import User
from apps.intros.models import IntroRequest

from .models import Placement, Review

TEXT = {"class": "form-control"}
SELECT = {"class": "form-select"}


class PlacementForm(forms.ModelForm):
    """
    Record that a deal happened, choosing from people who answered this car.

    THE DROPDOWN IS THE SECURITY MODEL
    ----------------------------------
    `other` is a choice field over the people this car has actually been
    discussed with — anyone who tapped "I am interested" on it, plus anyone
    introduced about it back when introductions were how conversations began.
    It is not a free text box and not a search over every member. That is what
    ties every future review to two people who demonstrably talked about this
    car. A "type a name" field here would quietly reopen the fake review
    problem the whole design exists to close.

    It is a weaker link than the old approved-introduction one, and worth
    saying so plainly: an interest is one-sided, so this list can contain
    somebody the owner never replied to. What it cannot do is produce a
    reviewable record on its own, because the other side still has to confirm.
    That second signature, not this dropdown, is what a fake review has to
    forge.
    """

    other = forms.ModelChoiceField(
        queryset=User.objects.none(),
        empty_label="Choose who…",
        widget=forms.Select(attrs=SELECT),
        label="Who is driving it?",
        help_text="People who have asked about this car through the site.",
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
        self.fields["other"].queryset = self._candidates()
        self.fields["other"].label_from_instance = self._describe
        self.fields["started_on"].initial = date.today()

    def _candidates(self):
        """
        Everybody this car has been discussed with, minus whoever is already
        driving it and minus the person filling the form in.
        """
        from django.db.models import Q

        from apps.messaging.models import Interest

        taken = Placement.objects.filter(
            vehicle_listing=self.listing, ended_on__isnull=True
        ).values_list("driver_id", flat=True)

        # Asked from whichever side is filling the form in. An owner sees
        # everybody who answered the advert; a driver sees the owner, and only
        # if they answered it themselves. Without the second case a driver
        # recording their own placement has an empty dropdown, since the only
        # interest on the listing is their own and you cannot place yourself.
        if self.listing.owner_id == self.user.pk:
            interested = Interest.objects.filter(
                vehicle_listing=self.listing
            ).values_list("user_id", flat=True)
        elif Interest.objects.filter(
            vehicle_listing=self.listing, user=self.user
        ).exists():
            interested = [self.listing.owner_id]
        else:
            interested = []

        introduced = IntroRequest.objects.filter(
            status=IntroRequest.Status.APPROVED, vehicle_listing=self.listing
        ).filter(Q(from_user=self.user) | Q(to_user=self.user))

        return (
            User.objects.filter(
                Q(pk__in=interested)
                | Q(pk__in=introduced.values_list("from_user_id", flat=True))
                | Q(pk__in=introduced.values_list("to_user_id", flat=True))
            )
            .exclude(pk=self.user.pk)
            .exclude(pk__in=taken)
            .order_by("full_name")
        )

    def _describe(self, person):
        return person.full_name or person.handle

    def clean_started_on(self):
        started_on = self.cleaned_data["started_on"]
        if started_on > date.today() + timedelta(days=1):
            raise forms.ValidationError("That is in the future.")
        if started_on < date.today() - timedelta(days=365 * 3):
            raise forms.ValidationError("That is too far back to record here.")
        return started_on

    def save(self, commit=True):
        placement = super().save(commit=False)
        other = self.cleaned_data["other"]

        placement.vehicle_listing = self.listing
        # Kept where there is one, so the older records stay traceable back to
        # the introduction they came from. Null for everything started since.
        placement.intro = (
            IntroRequest.objects.filter(
                status=IntroRequest.Status.APPROVED,
                vehicle_listing=self.listing,
            )
            .filter(
                models.Q(from_user=self.user, to_user=other)
                | models.Q(from_user=other, to_user=self.user)
            )
            .first()
        )
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
