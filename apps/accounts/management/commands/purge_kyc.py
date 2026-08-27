"""
Nightly cleanup. Run from cron:

    0 3 * * *  cd /srv/trusthailer && ./manage.py purge_kyc

Two jobs:
  1. Delete identity document files that outlived their retention window.
     Approval already deletes the file; this catches anything abandoned in the
     review queue. POPIA requires you not to keep personal information longer
     than necessary, and this is the mechanism that makes that true rather
     than aspirational.
  2. Delete platform-rating screenshots that outlived their window. Same
     reasoning: a driver-app screenshot carries a photo, a legal name and a
     trip history. Approval already deletes it; this catches the queue.
  3. Delete spent OTP rows. They carry a phone number and an IP address and
     have no value after a week.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import OTPChallenge, VerificationDocument
from apps.listings.models import PlatformRatingProof

OTP_RETENTION_DAYS = 7


class Command(BaseCommand):
    help = "Delete expired KYC files and stale OTP records."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        dry = options["dry_run"]
        today = timezone.localdate()

        stale_docs = VerificationDocument.objects.filter(
            purge_after__lt=today
        ).exclude(file="")
        doc_count = stale_docs.count()
        if not dry:
            for doc in stale_docs.iterator():
                doc.purge_file()

        stale_ratings = PlatformRatingProof.objects.filter(
            purge_after__lt=today
        ).exclude(screenshot="")
        rating_count = stale_ratings.count()
        if not dry:
            for proof in stale_ratings.iterator():
                proof.purge_screenshot()

        cutoff = timezone.now() - timedelta(days=OTP_RETENTION_DAYS)
        stale_otps = OTPChallenge.objects.filter(created_at__lt=cutoff)
        otp_count = stale_otps.count()
        if not dry:
            stale_otps.delete()

        prefix = "[dry-run] would purge" if dry else "Purged"
        self.stdout.write(self.style.SUCCESS(
            f"{prefix} {doc_count} document file(s), {rating_count} rating "
            f"screenshot(s) and {otp_count} OTP record(s)."
        ))
