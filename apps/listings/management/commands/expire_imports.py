"""
Archive imported adverts once they are old enough to be lying.

WHY THIS IS NOT OPTIONAL
------------------------
An imported advert is a photograph of a Facebook post, taken once. The car
behind it is usually gone within a fortnight, and nobody phones us to say so —
there is no owner on the site to pause the listing, which is the whole
difference between an import and a real one.

A stale import is worse than an empty page. A driver on a prepaid bundle spends
data opening it and airtime chasing it, gets told the car went a month ago, and
now knows the site wastes their money. One of those is enough; they will not
check back to see whether we improved.

So imports get a shelf life and remove themselves. Claimed listings are exempt:
a claim means a real person is behind it now and can pause it themselves.

Run it nightly, alongside `purge_kyc`.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.listings.models import VehicleListing


class Command(BaseCommand):
    help = "Archive imported adverts older than VehicleListing.IMPORT_STALE_DAYS."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=VehicleListing.IMPORT_STALE_DAYS,
            help="Override the shelf life, in days.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Say what would be archived and change nothing.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        cutoff = timezone.now() - timedelta(days=days)

        stale = (
            VehicleListing.objects.imported()
            .unclaimed()
            .filter(status=VehicleListing.Status.ACTIVE, published_at__lt=cutoff)
        )

        if options["dry_run"]:
            for listing in stale:
                self.stdout.write(f"would archive: {listing.uuid} — {listing}")
            self.stdout.write(self.style.WARNING(f"{stale.count()} would be archived."))
            return

        count = stale.update(status=VehicleListing.Status.ARCHIVED)
        self.stdout.write(
            self.style.SUCCESS(f"Archived {count} imported advert(s) older than {days} days.")
        )
