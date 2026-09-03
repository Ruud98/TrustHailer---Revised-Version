"""
The upload form behind /me/verification/.

WHY A SEPARATE MODULE FROM forms.py
-----------------------------------
`forms.py` is the signup and profile surface — the things a user touches on the
way in. This is the one form on the site that handles a photograph of somebody's
identity document, and the rules around it are different enough (private
storage, expiry dates, one pending document per kind, immediate deletion on
review) that burying it among the onboarding forms would make both harder to
read.
"""
from datetime import date

from django import forms

from apps.core.images import ImageProcessingError, process_upload

from .models import VerificationDocument

TEXT = {"class": "form-control"}
SELECT = {"class": "form-select"}

# What a phone camera or a scanner actually produces. PDFs are allowed because
# licence renewals and PrDP receipts often arrive as one.
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".pdf"}
MAX_BYTES = 8 * 1024 * 1024


class VerificationDocumentForm(forms.ModelForm):
    """
    Upload one document for review.

    IMAGES ARE RE-ENCODED, WHICH IS NOT JUST HOUSEKEEPING
    -----------------------------------------------------
    A photograph of an ID taken on a phone carries EXIF, and EXIF carries GPS.
    Somebody photographing their ID at home is photographing their home address
    into the file. The document is deleted on review either way, but "we only
    held your address for a day" is not an answer anybody wants to give, and
    running the existing pipeline over it costs nothing.

    PDFs are passed through as they are. Stripping metadata from a PDF needs a
    different library than the one already here, and a scanned PDF from a
    licensing office does not carry a home address the way a phone photo does.
    """

    class Meta:
        model = VerificationDocument
        fields = ["kind", "file", "expires_on"]
        widgets = {
            "kind": forms.Select(attrs=SELECT),
            "file": forms.ClearableFileInput(
                attrs={**TEXT, "accept": "image/*,application/pdf"}
            ),
            "expires_on": forms.DateInput(attrs={**TEXT, "type": "date"}),
        }
        labels = {
            "kind": "What are you sending?",
            "file": "Photo or scan",
            "expires_on": "Expiry date on the document",
        }
        help_texts = {
            "expires_on": "Licences and PrDPs expire. Leave blank if the document has "
                          "no expiry date.",
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        self.fields["expires_on"].required = False
        self.fields["file"].help_text = (
            "All four corners in the frame and the text readable. We delete it the "
            "moment somebody has checked it — we never keep a copy."
        )

    def clean_file(self):
        upload = self.cleaned_data["file"]
        name = (upload.name or "").lower()

        if not any(name.endswith(ext) for ext in ALLOWED_EXTENSIONS):
            raise forms.ValidationError("Send a photo or a PDF.")
        if upload.size and upload.size > MAX_BYTES:
            raise forms.ValidationError(
                f"That file is too big. Maximum {MAX_BYTES // (1024 * 1024)}MB."
            )
        return upload

    def clean_expires_on(self):
        expires_on = self.cleaned_data.get("expires_on")
        if expires_on and expires_on < date.today():
            raise forms.ValidationError(
                "That document has already expired. Send the current one."
            )
        return expires_on

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("kind")

        if kind in (VerificationDocument.Kind.LICENCE, VerificationDocument.Kind.PRDP):
            if not cleaned.get("expires_on"):
                self.add_error(
                    "expires_on",
                    "We need the expiry date — a verified licence that has quietly "
                    "run out is worse than none.",
                )

        if kind and self.user:
            pending = VerificationDocument.objects.filter(
                user=self.user, kind=kind, status=VerificationDocument.Status.PENDING
            ).exists()
            if pending:
                raise forms.ValidationError(
                    "You have already sent that one. We are looking at it."
                )
        return cleaned

    def save(self, commit=True):
        document = super().save(commit=False)
        document.user = self.user

        upload = self.cleaned_data["file"]
        if not (upload.name or "").lower().endswith(".pdf"):
            try:
                # Bigger and less compressed than a car photo. The reviewer has
                # to read an ID number off this; 1400px at quality 78 turns
                # small print into porridge, and a document nobody can read
                # gets rejected and re-uploaded, which costs the user data and
                # us a second review.
                display, _thumb = process_upload(
                    upload, max_edge=2000, quality=90, prefix="kyc"
                )
                document.file = display
            except ImageProcessingError:
                # Not a readable image, but it passed the extension check —
                # keep the original rather than losing somebody's upload. The
                # reviewer will say so if it is unreadable.
                document.file = upload

        if commit:
            document.save()
        return document
