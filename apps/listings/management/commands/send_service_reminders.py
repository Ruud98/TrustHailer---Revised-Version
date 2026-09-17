"""
Tell owners — and whoever is driving — that a service is coming up.

RUN IT DAILY. IT IS SAFE TO RUN AS OFTEN AS YOU LIKE.
-----------------------------------------------------
Every send is recorded against the cycle it belongs to, so a second run the
same hour sends nothing. That matters more than it sounds: the condition this
checks stays true for as long as the car is due, and without the record a
member would get the same reminder every morning until somebody serviced the
car. A notification that arrives daily is one people learn to dismiss without
reading — and then the one that mattered gets dismissed with it.

The cycle key is the odometer figure the service is due at, not a date. Log the
service, `next_service_km` moves on, and the next cycle is a key that has never
been sent. Nothing needs cleaning up and nothing needs resetting.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import IntegrityError

from apps.listings.models import ServiceReminder, VehicleListing
from apps.notifications.models import Notification
from apps.notifications.services import notify


class Command(BaseCommand):
    help = "Notify owners and drivers about services coming up or overdue."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would be sent without sending or recording it.",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]
        sent = 0

        candidates = (
            VehicleListing.objects.filter(
                service_interval_km__isnull=False, odometer_km__isnull=False
            )
            .select_related("owner")
            .prefetch_related("notes")
        )

        for listing in candidates:
            state = listing.service_state
            if state not in ("due", "overdue"):
                continue

            stage = (
                ServiceReminder.Stage.DUE if state == "overdue"
                else ServiceReminder.Stage.WARNING
            )
            due_at = listing.next_service_km

            if ServiceReminder.objects.filter(
                listing=listing, stage=stage, due_at_km=due_at
            ).exists():
                continue

            if dry:
                self.stdout.write(f"would send {stage} for {listing} at {due_at}")
                sent += 1
                continue

            try:
                # Written BEFORE the notifications, so a crash halfway through
                # a send cannot produce a second one on the next run. A missed
                # reminder is a smaller failure than a repeating one.
                ServiceReminder.objects.create(
                    listing=listing, stage=stage, due_at_km=due_at
                )
            except IntegrityError:
                # Two runs at once. The constraint is the guard.
                continue

            for person in self._recipients(listing):
                notify(
                    recipient=person,
                    kind=Notification.Kind.SERVICE_DUE,
                    message=self._message(listing, state),
                    url=listing.get_absolute_url(),
                )
            sent += 1

        asked = self._ask_for_readings(dry)

        self.stdout.write(
            self.style.SUCCESS(
                f"{sent} service reminder(s) {'considered' if dry else 'sent'}, "
                f"{asked} odometer reading(s) requested."
            )
        )

    # ------------------------------------------------------------- readings

    STALE_AFTER_DAYS = 30

    def _ask_for_readings(self, dry):
        """
        Nudge whoever has the car when the reading has gone stale.

        THIS IS WHAT KEEPS THE ESTIMATE HONEST
        --------------------------------------
        Projecting forward from a reading makes a stale one usable, but the
        projection drifts — a rate measured over one quiet month is wrong for a
        busy one. A confirmed figure every few weeks resets the drift, and the
        driver is the only person who can supply one without making a trip.

        Once a month, not weekly. The whole reason this feature is worth having
        is that the service reminder gets read, and the fastest way to lose that
        is to put a second, more frequent notification in front of it.
        """
        from apps.placements.models import Placement

        cutoff = timezone.now() - timedelta(days=self.STALE_AFTER_DAYS)
        asked = 0

        placements = (
            Placement.objects.confirmed()
            .filter(
                ended_on__isnull=True,
                vehicle_listing__service_interval_km__isnull=False,
            )
            .select_related("vehicle_listing", "driver")
        )

        for placement in placements:
            listing = placement.vehicle_listing
            # A car nobody has ever logged needs the FIRST reading just as much
            # as one whose reading has aged out.
            fresh_enough = listing.odometer_at and listing.odometer_at > cutoff
            if fresh_enough or placement.driver_id == listing.owner_id:
                continue

            if dry:
                self.stdout.write(f"would ask {placement.driver} for {listing}")
                asked += 1
                continue

            notify(
                recipient=placement.driver,
                kind=Notification.Kind.ODOMETER_ASK,
                message=f"What does the odometer on {listing.title} read?",
                url=listing.get_absolute_url(),
            )
            asked += 1

        return asked

    def _recipients(self, listing):
        """
        The owner, and whoever currently has the car.

        Only a confirmed, unended placement counts. Telling somebody to service
        a car they have already handed back is worse than telling nobody.
        """
        from apps.placements.models import Placement

        people = [listing.owner]
        placement = (
            Placement.objects.confirmed()
            .filter(vehicle_listing=listing, ended_on__isnull=True)
            .select_related("driver")
            .first()
        )
        if placement and placement.driver_id != listing.owner_id:
            people.append(placement.driver)
        return people

    def _message(self, listing, state):
        remaining = listing.km_to_service
        if state == "overdue":
            return f"{listing.title} is {abs(remaining):,} km past its service"
        return f"{listing.title} is due for a service in {remaining:,} km"
