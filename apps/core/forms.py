"""
Form pieces shared by more than one app.

These lived in `apps.listings.forms`, which is where the first multi-upload was
written. The feed needs the same pair now, and importing them from there would
point the feed at the listings app for a widget — a dependency in the wrong
direction, and one that risks a cycle, because listings already imports feed
models. Core is where both can reach without either owning the other.
"""
from django import forms


class MultipleFileInput(forms.ClearableFileInput):
    """
    Django 5 refuses `multiple` on ClearableFileInput, because the default
    FileField only ever cleans one file and would silently discard the rest.
    Pair this with MultipleFileField below, which does handle the list.
    """

    allow_multiple_selected = True


class MultipleFileField(forms.ImageField):
    """
    The other half of multi-upload, and the part that's easy to forget.

    With `allow_multiple_selected`, the widget hands the field a LIST of files.
    A plain ImageField calls `to_python` on that list, finds no `.name` on it,
    and rejects the whole submission with "No file was submitted" — before any
    custom `clean_<field>` method gets a look in. The fix is to run the normal
    per-file validation across each item, so every upload is still checked for
    being a real image.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        clean_one = super().clean
        if isinstance(data, (list, tuple)):
            return [clean_one(item, initial) for item in data if item]
        return [clean_one(data, initial)] if data else []
