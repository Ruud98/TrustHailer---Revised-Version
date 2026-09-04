"""
Seed the e-hailing platforms.

    python manage.py seed_platforms

Idempotent.

ON VEHICLE AGE LIMITS
---------------------
Every entry below leaves `max_vehicle_age_years` blank. That is deliberate, not
an oversight.

Each platform sets its own vehicle age rule, the rules differ between cities,
and they change without announcement. A number baked in here would quietly
mislead owners the moment it went stale — and an owner who lists a car on the
strength of a wrong limit, then gets rejected at the inspection centre, blames
the site.

Confirm the current rule from each platform directly, then set the value in the
admin (Platforms → edit → max vehicle age). Until you do, the site simply
doesn't warn, which is the honest failure mode.

ON THE LIST BEING PER-COUNTRY
-----------------------------
A member is offered the platforms of the market they work in, not the union of
both. Uber and Bolt do not run in Zimbabwe, so offering them to a Harare owner
would invite a listing nobody can act on; Hwindi is the local equivalent there.
The forms do the filtering — see `Profile.country`.

ON REMOVAL BEING DEACTIVATION
-----------------------------
A platform dropped from the list above is switched off, never deleted. Existing
listings and verified rating proofs point at these rows, and deleting one would
strip a platform off adverts their owners never touched. `is_active=False`
takes it out of every form and filter while leaving history intact.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.geo.models import Country
from apps.listings.models import Platform

PLATFORMS = [
    # (name, slug, country, order)
    ("Uber", "uber-za", Country.ZA, 1),
    ("Bolt", "bolt-za", Country.ZA, 2),
    ("inDrive", "indrive-za", Country.ZA, 3),
    ("Hwindi", "hwindi-zw", Country.ZW, 1),
    ("inDrive", "indrive-zw", Country.ZW, 2),
]


class Command(BaseCommand):
    help = "Seed e-hailing platforms for the launch markets."

    @transaction.atomic
    def handle(self, *args, **options):
        created = 0
        for name, slug, country, order in PLATFORMS:
            _, was_created = Platform.objects.get_or_create(
                slug=slug,
                defaults={"name": name, "country": country, "order": order},
            )
            created += int(was_created)

        # Anything seeded by an earlier run and since dropped from the list is
        # retired here, or the command would only ever add and a removal would
        # need a hand-written query to take effect.
        retired = Platform.objects.exclude(
            slug__in=[slug for _, slug, _, _ in PLATFORMS]
        ).filter(is_active=True)
        retired_names = sorted(f"{p.name} ({p.slug})" for p in retired)
        retired.update(is_active=False)

        self.stdout.write(
            self.style.SUCCESS(
                f"{Platform.objects.filter(is_active=True).count()} active platforms "
                f"({created} new)."
            )
        )
        if retired_names:
            self.stdout.write(
                self.style.WARNING(
                    "Deactivated (kept for existing listings): " + ", ".join(retired_names)
                )
            )
        self.stdout.write(
            self.style.WARNING(
                "Vehicle age limits are unset. Confirm each platform's current rule "
                "and set it in the admin before relying on age warnings."
            )
        )
