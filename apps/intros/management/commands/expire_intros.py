"""
Close introduction requests nobody answered.

WHY A REQUEST HAS TO DIE OF SOMETHING
-------------------------------------
Owners go quiet. They place a driver and forget the inbox, they lose the phone,
or they cannot face typing no. A request that stays "waiting for a reply"
forever tells the person who sent it nothing at all, and the tenth silent one
teaches them the site does not work.

After seven days the honest reading is no. Saying so lets somebody stop waiting
and ask elsewhere, and it keeps the received tab down to the requests that are
still real — which is what keeps people opening it.

`IntroRequest.is_expired` already reports the truth on the page, so a day
without this command running is cosmetic rather than misleading. This is what
makes the stored status agree. Run it nightly, with `purge_kyc` and
`expire_imports`.
"""
from django.core.management.base import BaseCommand

from apps.intros.models import IntroRequest


class Command(BaseCommand):
    help = "Mark unanswered introduction requests as expired."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Report and change nothing."
        )

    def handle(self, *args, **options):
        stale = IntroRequest.objects.stale()

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(f"{stale.count()} would be expired."))
            return

        count = stale.update(status=IntroRequest.Status.EXPIRED)
        self.stdout.write(self.style.SUCCESS(f"Expired {count} introduction request(s)."))
