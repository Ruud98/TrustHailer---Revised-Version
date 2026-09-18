from django import forms
from django.db.models import Q
from django.utils import timezone

from apps.core.forms import MultipleFileField, MultipleFileInput
from apps.core.images import ImageProcessingError, process_upload
from apps.core.redact import contains_contact_details
from apps.geo.models import City

from . import anon_names
from .models import Comment, Post, PostImage

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

    remove_images = forms.ModelMultipleChoiceField(
        required=False,
        queryset=PostImage.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        label="Remove",
    )

    images = MultipleFileField(
        required=False,
        label="Photos (optional)",
        widget=MultipleFileInput(
            attrs={**TEXT, "accept": "image/*", "multiple": True}
        ),
        help_text=f"Up to {PostImage.MAX_PER_POST}. Pick them all at once.",
    )

    class Meta:
        model = Post
        fields = ["title", "body", "topic", "city", "is_anonymous", "anon_name"]
        widgets = {
            "title": forms.TextInput(
                attrs={
                    **TEXT,
                    "maxlength": 120,
                    "placeholder": "Add one if it helps people scan",
                }
            ),
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
            "is_anonymous": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "anon_name": forms.RadioSelect(),
        }
        labels = {
            "title": "Headline (optional)",
            "body": "Your post",
            "topic": "What is it about?",
            "city": "Which city?",
            "is_anonymous": "Post without my name",
            "anon_name": "Show me as",
        }

    def __init__(self, *args, author=None, **kwargs):
        self.author = author
        super().__init__(*args, **kwargs)

        # A fresh handful every time the form is built. The bound case keeps
        # whatever was submitted in the list too, so a post that fails
        # validation on another field comes back with the name still selected
        # rather than silently reset to the generic label.
        offered = anon_names.offer()
        chosen = (self.data.get("anon_name") or self.initial.get("anon_name") or "")
        if chosen and chosen not in offered:
            offered = [chosen] + offered[:-1]
        # Only this post's own photos may be removed, whatever is posted.
        self.fields["remove_images"].queryset = (
            self.instance.images.all() if self.instance.pk else PostImage.objects.none()
        )

        self.fields["anon_name"] = forms.ChoiceField(
            required=False,
            label="Show me as",
            widget=forms.RadioSelect,
            choices=[("", anon_names.DEFAULT_LABEL)] + [(n, n) for n in offered],
            help_text="Only used when you post without your name.",
        )
        self.fields["city"].queryset = (
            City.objects.select_related("province").order_by("province__country", "name")
        )
        self.fields["city"].required = False
        self.fields["city"].empty_label = "Everywhere"
        self.fields["city"].help_text = (
            "Leave it on Everywhere if it is not about one place."
        )
        self.fields["title"].required = False
        self.fields["title"].help_text = (
            "Worth adding on a scam warning or a road alert. Skip it if the "
            "post speaks for itself."
        )

        # Default to where the poster is. Most posts are about the poster's own
        # city, and a default that is right most of the time beats a required
        # field people will pick at random to get past.
        if author is not None and not self.is_bound:
            profile = getattr(author, "profile", None)
            if profile and profile.suburb_id:
                self.fields["city"].initial = profile.suburb.city_id

    def clean_title(self):
        """
        The same contact rule as the body, for the same reason.

        Without this the rule is decorative: "Car available 082..." simply
        moves up one field and publishes. `_clean_body` is named for the
        field it was written for, but the check it runs is about published
        text, and a headline is published text.
        """
        return self._clean_body(self.cleaned_data.get("title"))

    def clean_body(self):
        body = self._clean_body(self.cleaned_data.get("body"))
        if len(body) < 5:
            raise forms.ValidationError("Say a bit more than that.")
        return body

    # The two topics that name other people. Anonymity is offered everywhere
    # else and withheld here — see the note on `Post.is_anonymous`.
    NAMED_TOPICS = {Post.Topic.SCAM, Post.Topic.ALERT}

    def clean_anon_name(self):
        """
        Accept only a name this site could have generated.

        Checked against the list rather than against a pattern like
        `anonymous_[a-z]+`, which would cheerfully accept `anonymous_admin`.
        """
        name = (self.cleaned_data.get("anon_name") or "").strip()
        if name and not anon_names.is_valid(name):
            raise forms.ValidationError("Pick one of the names offered.")
        return name

    def clean(self):
        cleaned = super().clean()

        # A name on a signed post is a field nobody can see the effect of, and
        # one that would surface if the post were ever made anonymous later.
        if not cleaned.get("is_anonymous"):
            cleaned["anon_name"] = ""
        if cleaned.get("is_anonymous") and cleaned.get("topic") in self.NAMED_TOPICS:
            # On the field rather than the form, so the error appears under the
            # box somebody has to change rather than in a banner above two that
            # look equally guilty.
            self.add_error(
                "is_anonymous",
                "A scam warning or a road alert has to carry your name. These "
                "are the posts that name other people, and a reader deciding "
                "whether to believe one has nothing else to go on.",
            )
        return cleaned

    def clean_images(self):
        """
        Check the count, then re-encode every file before anything is saved.

        Both halves belong here rather than in `save`. A post that has already
        been written to the database when the fourth photo turns out to be a
        renamed PDF is a post that publishes without it and tells nobody — so
        the whole set is processed while there is still a form to put an error
        on, and `save` does nothing that can fail.
        """
        files = self.cleaned_data.get("images") or []

        # Counted against what the post will END UP with, not against what was
        # uploaded: on an edit there are already photos there, and some of them
        # may be on their way out in the same submission.
        keeping = 0
        if self.instance.pk:
            removing = self.data.getlist("remove_images") if hasattr(self.data, "getlist") else []
            keeping = self.instance.images.exclude(pk__in=removing or []).count()
        if keeping + len(files) > PostImage.MAX_PER_POST:
            raise forms.ValidationError(
                f"That would be {keeping + len(files)} photos. "
                f"{PostImage.MAX_PER_POST} is the limit."
            )

        processed = []
        for upload in files:
            # Same pipeline as everything else: WebP, resized, EXIF stripped.
            # A photo of a roadblock taken from the driver's seat carries GPS.
            try:
                display, _thumb = process_upload(upload, prefix="post")
            except ImageProcessingError as exc:
                raise forms.ValidationError(
                    f"{upload.name}: {exc.messages[0] if exc.messages else exc}"
                )
            processed.append(display)

        self.processed_images = processed
        return files

    @property
    def images_to_remove(self):
        return self.cleaned_data.get("remove_images") or []

    def clean_is_anonymous(self):
        """
        Anonymity is decided once, when the post goes up.

        Turning it ON later cannot un-see a name: everybody who read the post
        before the edit already has it, and some of them have replied to it by
        name. Offering the switch would promise something the site cannot
        deliver. Turning it OFF later is a reveal that cannot be taken back,
        which is not a thing to leave one mis-tap away.

        Editing everything else is free. This one field is frozen, and the
        template does not render it on an edit at all — this is the backstop.
        """
        if self.instance.pk is None:
            return self.cleaned_data.get("is_anonymous")
        return self.instance.is_anonymous

    def save(self, commit=True):
        post = super().save(commit=False)
        post.author = self.author

        if not commit:
            # No post id yet, so no rows to hang the images off. The caller
            # asked for an unsaved instance and gets exactly that.
            return post

        if post.pk is not None and self.instance.pk is not None and self.has_changed():
            post.edited_at = timezone.now()

        post.save()

        # Photos the author asked to drop. Deleted one at a time rather than
        # with a queryset delete, so `PostImage.delete` runs and takes the file
        # with it — a bulk delete is SQL and leaves the file on disk.
        for photo in self.images_to_remove:
            photo.delete()

        for index, display in enumerate(getattr(self, "processed_images", [])):
            image = PostImage(post=post, position=index)
            image.image.save(display.name, display, save=False)
            image.save()
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
    Who, topic and city, in the querystring so a filtered feed is a shareable
    link.

    "From" is the payoff for following anybody: without a way to see only the
    people you chose, a follow is a button that does nothing you can point at.
    It is a plain ChoiceField rather than a checkbox so there is room for
    further scopes later without the querystring changing shape.
    """

    SCOPE_EVERYONE = ""
    SCOPE_FOLLOWING = "following"

    scope = forms.ChoiceField(
        required=False,
        choices=[(SCOPE_EVERYONE, "Everyone"), (SCOPE_FOLLOWING, "People I follow")],
        widget=forms.Select(attrs=SELECT),
        label="From",
    )
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

    def __init__(self, *args, viewer=None, **kwargs):
        self.viewer = viewer
        super().__init__(*args, **kwargs)
        self.fields["city"].queryset = (
            City.objects.select_related("province").order_by("province__country", "name")
        )
        # Nothing to scope by if you are not signed in, and a dropdown whose
        # only other option cannot work is a dropdown that lies.
        if viewer is None or not viewer.is_authenticated:
            del self.fields["scope"]

    def apply(self, queryset):
        if not self.is_valid():
            return queryset
        if self.cleaned_data.get("scope") == self.SCOPE_FOLLOWING:
            from apps.follows.models import Follow

            # Your own posts stay in, the way they do on Instagram: a feed that
            # hides what you just wrote reads as though the post failed.
            followed = Follow.objects.followed_ids(self.viewer)
            queryset = queryset.filter(
                Q(author_id__in=followed) | Q(author=self.viewer)
            )
            # ANONYMOUS POSTS LEAVE THIS FILTER, EXCEPT YOUR OWN.
            #
            # "People I follow" is a list the viewer built, so an anonymous
            # post surfacing inside it narrows the author to that list —
            # and somebody who follows one person has not been shown an
            # anonymous post at all, they have been shown that person's post
            # with the name taken off. The filter is the leak, not the card.
            queryset = queryset.exclude(
                Q(is_anonymous=True) & ~Q(author=self.viewer)
            )
        topic = self.cleaned_data.get("topic")
        city = self.cleaned_data.get("city")
        if topic:
            queryset = queryset.filter(topic=topic)
        if city:
            # City-scoped posts AND the ones marked Everywhere. A warning nobody
            # tagged is still worth reading, and dropping untagged posts would
            # make the filter feel broken the first time somebody used it.
            queryset = queryset.filter(Q(city=city) | Q(city__isnull=True))
        return queryset

    @property
    def active_count(self):
        if not self.is_valid():
            return 0
        return sum(1 for value in self.cleaned_data.values() if value)
