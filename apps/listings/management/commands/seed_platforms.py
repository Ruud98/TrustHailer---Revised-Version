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
    ("Private hire", "private-za", Country.ZA, 9),
    ("Hwindi", "hwindi-zw", Country.ZW, 1),
    ("inDrive", "indrive-zw", Country.ZW, 2),
    ("Private hire", "private-zw", Country.ZW, 9),
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

        self.stdout.write(
            self.style.SUCCESS(f"{Platform.objects.count()} platforms ({created} new).")
        )
        self.stdout.write(
            self.style.WARNING(
                "Vehicle age limits are unset. Confirm each platform's current rule "
                "and set it in the admin before relying on age warnings."
            )
        )
