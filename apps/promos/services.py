"""
Assembling the promo rail for a page render.

ONE FUNCTION, TWO QUERIES, CACHED
---------------------------------
`rail()` runs on the home page and the sign-in pages — the two most-hit URLs on
the site, and the one place a logged-out visitor forms an opinion. It is
furniture, so it gets a cache and a hard ceiling on rows rather than clever
ordering: two small queries once a minute, shared by every visitor, instead of
per-request work on the busiest page we have.

The cache is deliberately short. Staff put a promo up and want to see it; five
minutes of "is it broken?" costs more than the queries it saves.
"""
import random

from django.core.cache import cache

from .models import Promo

CACHE_KEY = "promos:rail:v2"
CACHE_SECONDS = 60

# A carousel longer than this is one nobody reaches the end of, and every extra
# slide is another image on a metered connection.
MAX_SLIDES = 8
MAX_ADS = 4

# How many directory listings may ride along in the carousel. Kept small on
# purpose: the rail is a sampler that says "there is a directory", not the
# directory itself, and the browse page is one click away.
MAX_BUSINESSES = 3


class BusinessSlide:
    """
    A live `BusinessListing` dressed as a `Promo` for the carousel template.

    Deliberately a thin adapter rather than a copied row — see the note in
    models.py. It answers exactly the attributes `_carousel.html` asks for, and
    nothing else, so a template change that needs a new field fails loudly here
    instead of rendering blank.
    """

    kind = Promo.Kind.BUSINESS
    cta_label = ""
    is_external = False

    def __init__(self, listing):
        self.listing = listing
        self.title = listing.name
        self.image = listing.logo
        self.image_alt = f"{listing.name} logo"
        self.url = listing.get_absolute_url()
        self.body = f"{listing.get_category_display()} · {listing.suburb.name}"

    def get_kind_display(self):
        return "In the directory"


def rail(*, include_businesses=True):
    """
    Return `{"ads": [...], "slides": [...]}` for the left rail.

    Returns empty lists rather than raising when there is nothing to show; the
    template renders no rail at all in that case, which is the correct
    behaviour on a fresh install.
    """
    cached = cache.get(CACHE_KEY)
    if cached is None:
        cached = _build()
        cache.set(CACHE_KEY, cached, CACHE_SECONDS)

    if not include_businesses:
        return cached

    return {**cached, "slides": cached["slides"] + _business_slides()}


def _build():
    live = list(Promo.objects.live())
    return {
        "ads": [p for p in live if p.slot == Promo.Slot.AD][:MAX_ADS],
        "slides": [p for p in live if p.slot == Promo.Slot.RAIL][:MAX_SLIDES],
    }


def _business_slides():
    """
    A sample of directory listings, paid and verified ones first.

    Sampled in Python from a small, already-ordered window rather than with
    `order_by("?")`, which is a full table sort in SQLite and Postgres both.
    Randomised at all so the same three mechanics are not the face of the
    directory for a week.
    """
    from apps.directory.models import BusinessListing

    window = list(
        BusinessListing.objects.visible()
        .filter(logo__gt="")
        .with_display_data()
        .order_by("-is_paid", "-is_verified", "-created_at")[: MAX_BUSINESSES * 4]
    )
    random.shuffle(window)
    return [BusinessSlide(listing) for listing in window[:MAX_BUSINESSES]]


def clear_cache():
    cache.delete(CACHE_KEY)
