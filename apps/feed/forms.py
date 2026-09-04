from django import forms

from apps.core.images import ImageProcessingError, process_upload
from apps.core.redact import contains_contact_details
from apps.geo.models import City

from .models import Comment, Post

TEXT = {"class": "form-control"}
SELECT = {"class": "form-select"}


class ContactFreeBodyMixin:
    """
    Shared rule: nothing published on this site carries a phone number.

    It is the same rule the advert importer, the introduction message and the
    review body all follow, and it is worth having in one place so it cannot
    drift into three slightly different rules. The message differs by surface —
    here it points at the listing form, because "car available, call me" is a
    listing wearing a post's clothes.
    """

    contact_error = "Leave phone numbers and email addresses out."

    def _clean_body(self, body):
        body = (body or "").strip()
        if contains_contact_details(body):
            raise forms.ValidationError(self.contact_error)
        return body


class PostForm(ContactFreeBodyMixin, forms.ModelForm):
    """
    The composer.

    NOT GATED ON PHONE VERIFICATION, ON PURPOSE
    -------------------------------------------
    Listing a car needs a verified number because it precedes handing over
    keys. Saying something does not. A verification wall in front of the
    compose box would empty the feed, and an empty feed is the one failure this
    sprint cannot survive — nobody comes back to check whether a quiet room got
    louder.
    """

    contact_error = (
        "Leave phone numbers out of posts. If you are advertising a car, list it "
        "properly instead — drivers can filter listings, and nobody can filter a "
        "paragraph."
    )

    class Meta:
        model = Post
        fields = ["body", "topic", "city", "image"]
        widgets = {
            "body": forms.Textarea(
                attrs={
                    **TEXT,
                    "rows": 5,
                    "maxlength": 3000,
                    "placeholder": "What is going on out there?",
                }
            ),
            "topic": forms.Select(attrs=SELECT),
            "city": forms.Select(attrs=SELECT),
            "image": forms.ClearableFileInput(attrs={**TEXT, "accept": "image/*"}),
        }
        labels = {
            "body": "Your post",
            "topic": "What is it about?",
            "city": "Which city?",
            "image": "Photo (optional)",
        }

    def __init__(self, *args, author=None, **kwargs):
        self.author = author
        super().__init__(*args, **kwargs)
        self.fields["city"].queryset = (
            City.objects.select_related("province").order_by("province__country", "name")
        )
        self.fields["city"].required = False
        self.fields["city"].empty_label = "Everywhere"
        self.fields["city"].help_text = (
            "Leave it on Everywhere if it is not about one place."
        )
        self.fields["image"].required = False

        # Default to where the poster is. Most posts are about the poster's own
        # city, and a default that is right most of the time beats a required
        # field people will pick at random to get past.
        if author is not None and not self.is_bound:
            profile = getattr(author, "profile", None)
            if profile and profile.suburb_id:
                self.fields["city"].initial = profile.suburb.city_id

    def clean_body(self):
        body = self._clean_body(self.cleaned_data.get("body"))
        if len(body) < 5:
            raise forms.ValidationError("Say a bit more than that.")
        return body

    def save(self, commit=True):
        post = super().save(commit=False)
        post.author = self.author

        upload = self.cleaned_data.get("image")
        if upload and hasattr(upload, "file"):
            # Same pipeline as everything else: WebP, resized, EXIF stripped.
            # A photo of a roadblock taken from the driver's seat carries GPS.
            try:
                display, _thumb = process_upload(upload, prefix="post")
                post.image = display
            except ImageProcessingError as exc:
                raise forms.ValidationError(str(exc))

        if commit:
            post.save()
        return post


class CommentForm(ContactFreeBodyMixin, forms.ModelForm):
    class Meta:
        model = Comment
        fields = ["body"]
        widgets = {
            "body": forms.Textarea(
                attrs={**TEXT, "rows": 2, "maxlength": 1500, "placeholder": "Reply…"}
            ),
        }
        labels = {"body": ""}

    def __init__(self, *args, post=None, author=None, parent=None, **kwargs):
        self.post = post
        self.author = author
        self.parent = parent
        super().__init__(*args, **kwargs)

    def clean_body(self):
        body = self._clean_body(self.cleaned_data.get("body"))
        if not body:
            raise forms.ValidationError("Write something.")
        return body

    def save(self, commit=True):
        comment = super().save(commit=False)
        comment.post = self.post
        comment.author = self.author
        comment.parent = self.parent
        if commit:
            comment.save()
        return comment


class FeedFilterForm(forms.Form):
    """
    Topic and city, in the querystring so a filtered feed is a shareable link.
    """

    topic = forms.ChoiceField(
        required=False,
        choices=[("", "Everything")] + list(Post.Topic.choices),
        widget=forms.Select(attrs=SELECT),
        label="Topic",
    )
    city = forms.ModelChoiceField(
        required=False,
        queryset=City.objects.none(),
        empty_label="Everywhere",
        widget=forms.Select(attrs=SELECT),
        label="City",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["city"].queryset = (
            City.objects.select_related("province").order_by("province__country", "name")
        )

    def apply(self, queryset):
        if not self.is_valid():
            return queryset
        topic = self.cleaned_data.get("topic")
        city = self.cleaned_data.get("city")
        if topic:
            queryset = queryset.filter(topic=topic)
        if city:
            # City-scoped posts AND the ones marked Everywhere. A warning nobody
            # tagged is still worth reading, and dropping untagged posts would
            # make the filter feel broken the first time somebody used it.
            from django.db.models import Q

            queryset = queryset.filter(Q(city=city) | Q(city__isnull=True))
        return queryset

    @property
    def active_count(self):
        if not self.is_valid():
            return 0
        return sum(1 for value in self.cleaned_data.values() if value)
