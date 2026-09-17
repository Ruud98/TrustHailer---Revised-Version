"""
The starting set of rail content.

WHY THERE IS A SEED COMMAND FOR THIS AT ALL
-------------------------------------------
The rails render nothing when the table is empty, which is correct but means a
fresh install looks exactly like an install where the feature is broken. This
puts the site's own case for itself in there — what the badges mean, what a
review costs to fake, what it costs to join — so the rails have something true
to say on day one and staff have working examples to copy rather than a blank
"Add promo" form.

Idempotent: matched on `title`, so running it twice does not double the rail,
and editing a seeded promo in the admin does not get overwritten by the next
run. Pass --reset if you want the copy below back.

Images are left blank on purpose. Text-only cards are what this set is for and
they render properly; artwork is a thing staff attach in the admin when there
is artwork worth attaching, not a placeholder shipped in the repository.
"""
from django.core.management.base import BaseCommand

from apps.promos.models import Promo
from apps.promos.services import clear_cache

ADS = [
    {
        "title": "Free while we build it out",
        "body": "No listing fee, no commission. Free for drivers, always.",
        "kind": Promo.Kind.BENEFIT,
        "animation": Promo.Animation.ZOOM,
        "url": "/cars/",
        "cta_label": "Browse cars",
    },
    {
        "title": "Put your business in front of drivers",
        "body": "Mechanics, tyres, trackers, panel beaters. A free directory listing.",
        "kind": Promo.Kind.BUSINESS,
        "animation": Promo.Animation.FLIP,
        "url": "/directory/",
        "cta_label": "List your business",
    },
    {
        "title": "Know who you are dealing with",
        "body": "ID, licence and PrDP checked before anyone gets your number.",
        "kind": Promo.Kind.FEATURE,
        "animation": Promo.Animation.SHUFFLE,
        "url": "/safety/",
        "cta_label": "How verification works",
    },
]

SLIDES = [
    {
        "title": "Reviews you cannot buy",
        "body": "A review only exists against a rental both people confirmed. "
                "No confirmation, no review.",
        "kind": Promo.Kind.FEATURE,
        "url": "/safety/",
    },
    {
        "title": "Both sides rate each other",
        "body": "Neither review appears until both are in. Nobody writes a reply "
                "to a reply.",
        "kind": Promo.Kind.FEATURE,
        "url": "/safety/",
    },
    {
        "title": "Your number stays yours",
        "body": "Hidden until you approve the introduction yourself.",
        "kind": Promo.Kind.BENEFIT,
        "url": "/safety/",
    },
    {
        "title": "A verified Uber or Bolt rating",
        "body": "The strongest single thing on a driver listing. We check the "
                "screenshot, then delete it.",
        "kind": Promo.Kind.BENEFIT,
        "url": "/drivers/",
    },
    {
        "title": "No password to forget",
        "body": "An email address and a six-digit code. That is the whole sign-up.",
        "kind": Promo.Kind.BENEFIT,
        "url": "/join/",
    },
    {
        "title": "Cars near you, not near everyone",
        "body": "Filter by suburb and weekly rate, then save the search.",
        "kind": Promo.Kind.FEATURE,
        "url": "/cars/",
    },
]


class Command(BaseCommand):
    help = "Create the starting set of side-rail promos. Safe to re-run."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset", action="store_true",
            help="Overwrite the copy on promos this command previously created.",
        )

    def handle(self, *args, **options):
        created = updated = 0

        specs = (
            [(Promo.Slot.AD, spec) for spec in ADS]
            + [(Promo.Slot.RAIL, spec) for spec in SLIDES]
        )

        for order, (slot, spec) in enumerate(specs, start=1):
            defaults = {**spec, "slot": slot, "sort_order": order * 10}

            promo, was_created = Promo.objects.get_or_create(
                title=spec["title"], defaults=defaults
            )
            if was_created:
                created += 1
            elif options["reset"]:
                for field, value in defaults.items():
                    setattr(promo, field, value)
                promo.save()
                updated += 1

        clear_cache()
        self.stdout.write(self.style.SUCCESS(
            f"{created} promo(s) created, {updated} updated. "
            f"{Promo.objects.live().count()} live."
        ))
