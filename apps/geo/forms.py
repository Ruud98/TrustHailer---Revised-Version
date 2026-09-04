"""
Typed city and suburb, shared by every form that asks where someone is.

The dropdowns these replace could only ever offer what had been seeded, and the
person they failed was the one with a car to list in a suburb nobody had entered
yet. Now the boxes take anything and `geo.resolve` turns what was typed into a
row — see that module for why the row still matters.
"""
from django import forms
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

from .models import City, Country, Suburb
from .resolve import clean_place_name, resolve_city, resolve_suburb

PLACE_SUGGESTION_LIMIT = 200


class PlaceInput(forms.TextInput):
    """
    A text box that suggests places already on the site without restricting the
    answer to them.

    The suggestions ride along as a `<datalist>` rendered by the widget itself,
    so this drops into any existing template with no markup change and no
    JavaScript. A browser offers the list while still accepting anything typed,
    which is exactly the behaviour wanted: helpful, never a gate.
    """

    def __init__(self, attrs=None, suggestions=()):
        self.suggestions = suggestions
        super().__init__(attrs)

    def render(self, name, value, attrs=None, renderer=None):
        options = list(self.suggestions)
        if not options:
            return super().render(name, value, attrs, renderer)

        list_id = f"places-{name}"
        attrs = {**(attrs or {}), "list": list_id, "autocomplete": "off"}
        field = super().render(name, value, attrs, renderer)
        datalist = format_html(
            '<datalist id="{}">{}</datalist>',
            list_id,
            mark_safe("".join(f'<option value="{escape(o)}">' for o in options)),
        )
        return mark_safe(field + datalist)


class FreeTextLocationMixin:
    """
    Swaps a form's city/suburb selects for boxes and resolves them on the way in.

    Set `SUBURB_FIELD` to the name of the model's suburb FK. The resolved
    `Suburb` is written back into `cleaned_data` under that name, so Django's
    own `construct_instance` assigns it to the instance and nothing else in the
    form has to know this happened.

    The country comes from the member's profile, not from the form. It decides
    which country a newly typed city is filed under, and two markets are allowed
    to hold cities of the same name without colliding.
    """

    SUBURB_FIELD = "suburb"
    CITY_PLACEHOLDER = "Johannesburg"
    SUBURB_PLACEHOLDER = "Tembisa"

    def _install_place_fields(self, country, city_initial="", suburb_initial=""):
        self.place_country = country or Country.ZA

        self.fields["city"] = forms.CharField(
            max_length=80,
            label="City",
            initial=city_initial,
            widget=PlaceInput(
                attrs={"class": "form-control", "placeholder": self.CITY_PLACEHOLDER},
                suggestions=self._city_suggestions(),
            ),
            help_text="Type it in — if it is not on the list yet, we will add it.",
        )
        self.fields[self.SUBURB_FIELD] = forms.CharField(
            max_length=80,
            label="Suburb",
            initial=suburb_initial,
            widget=PlaceInput(
                attrs={"class": "form-control", "placeholder": self.SUBURB_PLACEHOLDER},
                suggestions=self._suburb_suggestions(),
            ),
            help_text="The area people would name if they were telling someone where you are.",
        )

    def _city_suggestions(self):
        return list(
            City.objects.filter(province__country=self.place_country)
            .order_by("name")
            .values_list("name", flat=True)[:PLACE_SUGGESTION_LIMIT]
        )

    def _suburb_suggestions(self):
        return list(
            Suburb.objects.filter(city__province__country=self.place_country)
            .order_by("name")
            .values_list("name", flat=True)[:PLACE_SUGGESTION_LIMIT]
        )

    def clean(self):
        cleaned = super().clean()

        # Onboarding asks for the country on this same form; everywhere else it
        # is already settled on the profile. Whichever is present wins.
        country = cleaned.get("country") or self.place_country

        city_name = clean_place_name(cleaned.get("city", ""))
        suburb_name = clean_place_name(cleaned.get(self.SUBURB_FIELD, ""))

        if not city_name:
            self.add_error("city", "Which city?")
        if not suburb_name:
            self.add_error(self.SUBURB_FIELD, "Which suburb or area?")
        if not city_name or not suburb_name:
            return cleaned

        city = resolve_city(city_name, country)
        if city is None:
            self.add_error("city", "Write the city in letters so we can save it.")
            return cleaned

        suburb = resolve_suburb(suburb_name, city)
        if suburb is None:
            self.add_error(self.SUBURB_FIELD, "Write the suburb in letters so we can save it.")
            return cleaned

        # A Suburb instance under the model field's own name, which is what
        # ModelForm.construct_instance will read when it builds the object.
        cleaned[self.SUBURB_FIELD] = suburb
        cleaned["city"] = city.name
        return cleaned
