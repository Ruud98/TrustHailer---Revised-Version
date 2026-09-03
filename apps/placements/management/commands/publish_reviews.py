"""
Publish reviews the blind period has run out on.

WHY THE TIMER EXISTS
--------------------
Reviews are double-blind: neither appears until both are written. Without an
escape hatch, one person staying silent freezes the other's review forever —
which is a free veto for anybody who suspects the review is unflattering, and
the most motivated person to use it is exactly the one worth reading about.

Fourteen days is long enough that a genuine pair of reviews usually lands
together, and short enough that a review is still about something recent by the
time anybody reads it.

The pair is published immediately when the second review is written, so this
command only ever handles the one-sided case. Run it nightly with the others.
"""
from django.core.management.base import BaseCommand

from apps.placements.models import Review


class Command(BaseCommand):
    help = "Publish reviews whose 14-day blind period has expired."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report and change nothing.")

    def handle(self, *args, **options):
        due = Review.objects.due_for_publication()

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(f"{due.count()} would be published."))
            return

        count = 0
        for review in due:
            review.publish()
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Published {count} review(s)."))
