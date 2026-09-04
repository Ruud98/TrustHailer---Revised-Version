from django import forms
from django.contrib.contenttypes.models import ContentType

from .models import Block, Report

TEXT = {"class": "form-control"}
SELECT = {"class": "form-select"}


class ReportForm(forms.ModelForm):
    """
    Reason, and room to say what happened.

    The detail box is optional except on "something else", where a report with
    no words in it is unactionable — staff would be looking at a listing with
    no idea what they are supposed to be seeing.
    """

    class Meta:
        model = Report
        fields = ["reason", "detail"]
        widgets = {
            "reason": forms.RadioSelect(attrs={"class": "form-check-input"}),
            "detail": forms.Textarea(
                attrs={
                    **TEXT,
                    "rows": 5,
                    "maxlength": 1000,
                    "placeholder": "What happened, and when. Dates and amounts help.",
                }
            ),
        }
        labels = {"reason": "What is wrong?", "detail": "Tell us more"}

    def __init__(self, *args, reporter=None, target=None, **kwargs):
        self.reporter = reporter
        self.target = target
        super().__init__(*args, **kwargs)
        self.fields["detail"].required = False
        self.fields["detail"].help_text = (
            "We do not tell the other person who reported them. If money has already "
            "changed hands, report it to the police as well — we cannot recover it."
        )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("reason") == Report.Reason.OTHER and not cleaned.get("detail"):
            self.add_error("detail", "Tell us what is wrong, or we cannot act on it.")
        return cleaned

    def save(self, commit=True):
        report = super().save(commit=False)
        report.reporter = self.reporter
        report.content_type = ContentType.objects.get_for_model(self.target)
        report.object_id = self.target.pk
        if commit:
            report.save()
        return report


class BlockForm(forms.ModelForm):
    """
    A reason, for our eyes only and entirely optional.

    It is never shown to the person blocked and never used to decide anything
    automatically. It exists because a pattern across many blocks of the same
    account is a signal worth having, and because people often want to say why.
    """

    class Meta:
        model = Block
        fields = ["reason"]
        widgets = {
            "reason": forms.TextInput(
                attrs={**TEXT, "placeholder": "Optional, and only we see it"}
            ),
        }
        labels = {"reason": "Why, if you want to say"}

    def __init__(self, *args, user=None, blocked_user=None, **kwargs):
        self.user = user
        self.blocked_user = blocked_user
        super().__init__(*args, **kwargs)
        self.fields["reason"].required = False

    def clean(self):
        cleaned = super().clean()
        if self.user and self.blocked_user and self.user.pk == self.blocked_user.pk:
            raise forms.ValidationError("You cannot block yourself.")
        return cleaned

    def save(self, commit=True):
        block, _created = Block.objects.get_or_create(
            user=self.user,
            blocked_user=self.blocked_user,
            defaults={"reason": self.cleaned_data.get("reason", "")},
        )
        return block
