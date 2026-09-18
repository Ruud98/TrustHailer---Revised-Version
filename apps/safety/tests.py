"""
Tests for reporting, blocking and the private document store.

The two that matter most are that a blocked person actually disappears
everywhere — not just from the one page somebody remembered to filter — and
that an uploaded identity document is not reachable by URL. Both are the kind
of promise that is easy to make in copy and easy to break in a later refactor.
"""
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.utils import IntegrityError
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from apps.accounts.models import VerificationDocument
from apps.listings.models import DriverListing, VehicleListing
from apps.listings.tests import ListingTestCase

from .models import Block, Report, blocked_user_ids, is_blocked_between


def upload(name="id.jpg", size=(1000, 700)):
    buffer = io.BytesIO()
    Image.new("RGB", size, (90, 90, 90)).save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


class SafetyTestCase(ListingTestCase):
    def setUp(self):
        super().setUp()
        self.car = self.make_listing()
        self.driver_listing = DriverListing.objects.create(
            driver=self.driver,
            headline="Four years on Uber",
            years_experience=4,
            home_suburb=self.soweto,
            status=DriverListing.Status.ACTIVE,
        )


class ReportTests(SafetyTestCase):
    def test_a_member_can_report_a_car(self):
        self.login(self.driver)
        response = self.client.post(
            reverse("safety:report") + f"?car={self.car.uuid}",
            {"reason": Report.Reason.SCAM, "detail": "Asked for a deposit up front."},
        )
        self.assertRedirects(response, self.car.get_absolute_url())
        report = Report.objects.get()
        self.assertEqual(report.reporter, self.driver)
        self.assertEqual(report.target, self.car)
        self.assertEqual(report.status, Report.Status.OPEN)

    def test_a_member_can_report_a_person(self):
        self.login(self.driver)
        self.client.post(
            reverse("safety:report") + f"?user={self.owner.handle}",
            {"reason": Report.Reason.ABUSE, "detail": "Threatening messages."},
        )
        self.assertEqual(Report.objects.get().target, self.owner)

    def test_reporting_twice_does_not_stack_the_queue(self):
        """
        Six reports from one person is not six people, and a queue that counts
        them as six makes the loudest complaint look like the most serious.
        """
        self.login(self.driver)
        url = reverse("safety:report") + f"?car={self.car.uuid}"
        self.client.post(url, {"reason": Report.Reason.SCAM, "detail": "one"})
        self.client.post(url, {"reason": Report.Reason.SPAM, "detail": "two"})
        self.assertEqual(Report.objects.count(), 1)

    def test_the_constraint_holds_even_without_the_view(self):
        Report.objects.create(
            reporter=self.driver, target=self.car, reason=Report.Reason.SCAM
        )
        with self.assertRaises(IntegrityError):
            Report.objects.create(
                reporter=self.driver, target=self.car, reason=Report.Reason.SPAM
            )

    def test_other_needs_a_reason_written_out(self):
        self.login(self.driver)
        response = self.client.post(
            reverse("safety:report") + f"?car={self.car.uuid}",
            {"reason": Report.Reason.OTHER, "detail": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Report.objects.exists())

    def test_you_cannot_report_your_own_things(self):
        """A queue with self-reports in it is a queue staff learn to skim."""
        self.login(self.owner)
        for query in (f"?car={self.car.uuid}", f"?user={self.owner.handle}"):
            with self.subTest(query=query):
                self.client.post(
                    reverse("safety:report") + query,
                    {"reason": Report.Reason.SPAM, "detail": "oops"},
                )
        self.assertFalse(Report.objects.exists())

    def test_reporting_needs_a_login(self):
        response = self.client.get(reverse("safety:report") + f"?car={self.car.uuid}")
        self.assertEqual(response.status_code, 302)

    def test_resolving_records_who_and_when(self):
        report = Report.objects.create(
            reporter=self.driver, target=self.car, reason=Report.Reason.SCAM
        )
        report.resolve(by=self.owner, actioned=True, note="Listing removed.")
        report.refresh_from_db()
        self.assertEqual(report.status, Report.Status.ACTIONED)
        self.assertEqual(report.handled_by, self.owner)
        self.assertIsNotNone(report.handled_at)


class BlockTests(SafetyTestCase):
    def block_owner(self):
        Block.objects.create(user=self.driver, blocked_user=self.owner)

    def test_blocking_takes_effect_from_the_form(self):
        self.login(self.driver)
        response = self.client.post(
            reverse("safety:block", args=[self.owner.handle]), {"reason": "Rude"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Block.objects.filter(user=self.driver, blocked_user=self.owner).exists())

    def test_you_cannot_block_yourself(self):
        with self.assertRaises(IntegrityError):
            Block.objects.create(user=self.driver, blocked_user=self.driver)

    def test_the_effect_runs_both_ways_even_though_the_record_does_not(self):
        self.block_owner()
        self.assertTrue(is_blocked_between(self.driver, self.owner))
        self.assertTrue(is_blocked_between(self.owner, self.driver))
        self.assertEqual(blocked_user_ids(self.driver), {self.owner.pk})
        self.assertEqual(blocked_user_ids(self.owner), {self.driver.pk})

    def test_a_blocked_persons_car_leaves_the_browse_page(self):
        self.block_owner()
        self.login(self.driver)
        response = self.client.get(reverse("listings:browse"))
        self.assertEqual(response.context["total"], 0)

    def test_and_leaves_it_for_the_other_side_too(self):
        """The half people forget. A one-way effect is a false sense of safety."""
        self.block_owner()
        self.login(self.owner)
        response = self.client.get(reverse("drivers:browse"))
        self.assertEqual(response.context["total"], 0)

    def test_a_blocked_persons_listing_is_gone_from_search(self):
        self.block_owner()
        self.login(self.driver)
        response = self.client.get(reverse("search") + "?q=Corolla")
        self.assertEqual(response.context["car_count"], 0)

    def test_the_detail_page_is_gone_too(self):
        """A block that a shared link walks straight through is not a block."""
        self.block_owner()
        self.login(self.driver)
        self.assertEqual(
            self.client.get(self.car.get_absolute_url()).status_code, 404
        )
        self.login(self.owner)
        self.assertEqual(
            self.client.get(self.driver_listing.get_absolute_url()).status_code, 404
        )

    def test_the_profile_is_gone_in_both_directions(self):
        self.block_owner()
        self.login(self.driver)
        self.assertEqual(self.client.get(self.owner.get_absolute_url()).status_code, 404)
        self.login(self.owner)
        self.assertEqual(self.client.get(self.driver.get_absolute_url()).status_code, 404)

    def test_neither_side_can_answer_the_other_s_listing(self):
        """
        The refusal is silent — a flash message and a bounce back to the page,
        not "you have been blocked". Telling somebody they were blocked turns a
        quiet exit into a confrontation, which is what the person blocking was
        trying to avoid.
        """
        from apps.messaging.models import Interest

        self.block_owner()
        for user, kind, uuid in (
            (self.driver, "car", self.car.uuid),
            (self.owner, "driver", self.driver_listing.uuid),
        ):
            with self.subTest(user=user):
                self.login(user)
                self.client.post(
                    reverse("messaging:interested", args=[kind, uuid])
                )
        self.assertFalse(Interest.objects.exists())

    def test_unblocking_puts_everything_back(self):
        self.block_owner()
        self.login(self.driver)
        self.client.post(reverse("safety:unblock", args=[self.owner.handle]))
        response = self.client.get(reverse("listings:browse"))
        self.assertEqual(response.context["total"], 1)

    def test_an_unclaimed_import_is_never_hidden_by_a_block(self):
        """It has no owner, so there is nobody it could belong to."""
        VehicleListing.objects.create(
            owner=None, make="Nissan", model="Almera", year=2018,
            transmission="manual", arrangement="weekly", weekly_rate=2300,
            suburb=self.soweto, status=VehicleListing.Status.ACTIVE,
            source=VehicleListing.Source.FACEBOOK,
            source_url="https://www.facebook.com/groups/1/posts/2/",
        )
        self.block_owner()
        self.login(self.driver)
        response = self.client.get(reverse("listings:browse"))
        self.assertEqual(response.context["total"], 1)

    def test_nobody_who_is_blocked_is_told(self):
        """
        The 404 is the same one an unknown listing gives. A different message
        for a block would turn a quiet exit into a confrontation.
        """
        self.block_owner()
        self.login(self.owner)
        response = self.client.get(self.driver.get_absolute_url())
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, "block", status_code=404)


class VerificationDocumentTests(SafetyTestCase):
    """
    Sprint 5's other half: the upload flow, and the promise about the file.
    """

    def send_document(self, kind=VerificationDocument.Kind.ID, **extra):
        self.login(self.driver)
        payload = {"kind": kind, "file": upload()}
        payload.update(extra)
        return self.client.post(reverse("accounts:verification"), payload)

    def test_a_member_can_send_a_document(self):
        self.send_document()
        document = VerificationDocument.objects.get()
        self.assertEqual(document.user, self.driver)
        self.assertEqual(document.status, VerificationDocument.Status.PENDING)
        self.assertTrue(document.file)

    def test_the_document_is_not_stored_under_media_root(self):
        """
        The bug this whole arrangement exists for. MEDIA_ROOT is served at
        /media/ in development and is a public bucket in production; an ID scan
        in either is one guessed path from being read.
        """
        from django.conf import settings

        self.send_document()
        document = VerificationDocument.objects.get()
        path = document.file.path
        self.assertNotIn(str(settings.MEDIA_ROOT), path)
        self.assertIn(str(settings.KYC_ROOT), path)

    def test_the_document_has_no_public_url(self):
        self.send_document()
        document = VerificationDocument.objects.get()
        with self.assertRaises(ValueError):
            document.file.url

    def test_only_staff_can_read_a_document(self):
        self.send_document()
        document = VerificationDocument.objects.get()
        url = reverse("accounts:kyc_document", args=[document.pk])

        # Not even the person who uploaded it — there is no reason to re-read
        # it, and every extra path to a stored ID is another way to leak one.
        self.login(self.driver)
        self.assertNotEqual(self.client.get(url).status_code, 200)

        staff = self._make_user("staff3@example.com", "Staff Three", verified=True)
        staff.is_staff = True
        staff.save()
        self.login(staff)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store, max-age=0")

    def test_a_licence_needs_an_expiry_date(self):
        response = self.send_document(kind=VerificationDocument.Kind.LICENCE)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(VerificationDocument.objects.exists())

    def test_an_expired_document_is_refused(self):
        response = self.send_document(
            kind=VerificationDocument.Kind.LICENCE, expires_on="2020-01-01"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(VerificationDocument.objects.exists())

    def test_you_cannot_queue_the_same_kind_twice(self):
        self.send_document()
        self.send_document()
        self.assertEqual(VerificationDocument.objects.count(), 1)

    def test_a_pdf_is_accepted_and_passed_through(self):
        self.login(self.driver)
        self.client.post(
            reverse("accounts:verification"),
            {
                "kind": VerificationDocument.Kind.PROOF_ADDRESS,
                "file": SimpleUploadedFile(
                    "statement.pdf", b"%PDF-1.4 not really", content_type="application/pdf"
                ),
            },
        )
        self.assertTrue(VerificationDocument.objects.exists())

    def test_an_executable_is_not(self):
        self.login(self.driver)
        self.client.post(
            reverse("accounts:verification"),
            {
                "kind": VerificationDocument.Kind.ID,
                "file": SimpleUploadedFile("nasty.exe", b"MZ", content_type="application/exe"),
            },
        )
        self.assertFalse(VerificationDocument.objects.exists())

    def test_the_page_says_what_happens_to_the_file(self):
        """
        Somebody about to photograph their ID for a site they found in a
        Facebook group deserves the answer above the button, not in the terms.
        """
        self.login(self.driver)
        response = self.client.get(reverse("accounts:verification"))
        self.assertContains(response, "then it is deleted")

    def test_approving_in_the_admin_still_destroys_the_file(self):
        from apps.accounts.admin import VerificationDocumentAdmin
        from django.contrib.admin.sites import site

        self.send_document()
        document = VerificationDocument.objects.get()
        staff = self._make_user("staff4@example.com", "Staff Four", verified=True)
        staff.is_staff = True
        staff.save()

        admin_instance = VerificationDocumentAdmin(VerificationDocument, site)
        request = type("R", (), {"user": staff})()
        admin_instance._finish(
            request, VerificationDocument.objects.all(), VerificationDocument.Status.APPROVED
        )

        document.refresh_from_db()
        self.assertEqual(document.status, VerificationDocument.Status.APPROVED)
        self.assertFalse(document.file)
        self.driver.verification.refresh_from_db()
        self.assertIsNotNone(self.driver.verification.id_verified_at)


class SafetyPageTests(TestCase):
    def test_the_guidance_page_is_public(self):
        """
        A driver being pressured for a deposit right now has to be able to
        reach it from a WhatsApp link without signing in.
        """
        response = self.client.get(reverse("safety"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Never pay anything before you have seen the car")
