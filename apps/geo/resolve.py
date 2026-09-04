"""
Turning a place somebody typed into a row in the database.

WHY THIS EXISTS
---------------
Cities and suburbs are typed, not picked from a list. A seeded dropdown is
always missing somewhere — and the person it is missing for is exactly the
owner with a car to list, who then leaves. So the box accepts anything.

But the suburb has to stay a real row, because four things downstream depend on
it being one: the suburb filter that makes a search shareable, radius search off
`Suburb.latitude/longitude`, saved searches that store an id and replay it weeks
later, and the driver's work-areas M2M. Free text in a CharField would break all
four and quietly split the market — "Tembisa", "tembisa" and "Tembisa " would be
three places nobody could filter across.

So: type anything, and we match it to an existing row or create one.

MATCHING IS ON THE SLUG *OR* THE NAME
-------------------------------------
`slugify` folds case, punctuation and spacing in one step, so "Ivory Park",
"ivory park" and "Ivory-Park" all land on `ivory-park` and therefore the same
row — which `iexact` on the name alone would miss.

But the slug alone is not enough either, and a test caught why: seeded rows
carry hand-written slugs that are not what `slugify` would produce from their
name. Johannesburg is seeded as `jhb`, so somebody typing "Johannesburg" would
match nothing and quietly found a second Johannesburg. Both checks together
close that, and either one alone leaves a way to split a city in two.

The name is stored as the FIRST person typed it. Later arrivals with different
spacing join that row rather than renaming it, so the label stays stable for
everyone already filtering on it.

WHAT THIS DOES NOT DO
---------------------
It does not geocode. A newly created suburb has no coordinates, so radius search
falls back to the city centroid for it — the fallback `seed_geo` already warns
about. It also cannot tell a real place from a typo: "Tembisa" and "Tembisaa"
become two rows. Merging those is a moderation job, not something to guess at
here, because guessing wrong moves somebody's listing to the wrong town.
"""
import re

from django.db.models import Q
from django.utils.text import slugify

from .models import City, Country, Province, Suburb

# A city somebody typed has no province, and asking for one would be a question
# about administrative geography that nobody listing a car wants to answer. They
# land here instead, under the right country, for staff to re-file in the admin
# if it ever matters. Province is not shown anywhere user-facing — `City.__str__`
# is the bare name — so this stays invisible to members.
UNFILED_PROVINCE_NAME = "Unfiled"


def clean_place_name(raw: str) -> str:
    """Trim, collapse runs of whitespace, and cap at the column width."""
    return re.sub(r"\s+", " ", (raw or "").strip())[:80]


def _unfiled_province(country: str) -> Province:
    province, _ = Province.objects.get_or_create(
        slug=f"{str(country).lower()}-unfiled",
        defaults={"country": country, "name": UNFILED_PROVINCE_NAME},
    )
    return province


def resolve_city(raw_name: str, country: str = Country.ZA) -> City | None:
    """
    The city with this name in this country, created if it is new.

    Scoped to the country so that two places sharing a name in different
    markets stay separate rows.
    """
    name = clean_place_name(raw_name)
    slug = slugify(name)
    if not slug:
        return None

    existing = (
        City.objects.filter(province__country=country)
        .filter(Q(slug=slug) | Q(name__iexact=name))
        .order_by("pk")
        .first()
    )
    if existing:
        return existing

    return City.objects.create(
        province=_unfiled_province(country),
        name=name,
        slug=slug,
        # The launch-market flag no longer gates who may list — see the field's
        # help text. Setting it keeps a member-created city on the same footing
        # as a seeded one everywhere the flag is still read.
        is_launch_market=True,
    )


def resolve_suburb(raw_name: str, city: City) -> Suburb | None:
    """The suburb with this name in this city, created if it is new."""
    name = clean_place_name(raw_name)
    slug = slugify(name)[:50]
    if not slug or city is None:
        return None

    existing = (
        Suburb.objects.filter(city=city)
        .filter(Q(slug=slug) | Q(name__iexact=name))
        .order_by("pk")
        .first()
    )
    if existing:
        return existing

    return Suburb.objects.create(city=city, name=name, slug=slug)


def resolve(city_name: str, suburb_name: str, country: str = Country.ZA) -> Suburb | None:
    """
    Both halves in one call: the suburb, with its city created first if needed.

    Returns None if either box was left empty or held nothing sluggable, which
    the form turns into a validation error rather than a silent miss.
    """
    city = resolve_city(city_name, country)
    if city is None:
        return None
    return resolve_suburb(suburb_name, city)
