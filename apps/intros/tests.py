"""
Tests for introductions.

The load-bearing ones are about the number: that it stays masked until both
people agree, that it releases to both of them at once, and that it never
appears anywhere it should not — not on a listing page, not in an email, not
to a third party who happens to have the URL. Everything else here is
housekeeping by comparison.
"""
from datetime import timedelta

from django.core import mail
from django.core.management import call_command
from django.db.utils import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.listings.models import DriverListing, VehicleListing
from apps.listings.tests import ListingTestCase

from .models import IntroRequest

MESSAGE = "Hi, I have four years on Uber and I am looking for a car in Soweto."


class IntroTestCase(ListingTestCase):
    """
    One owner with a car, one driver with a listing, on the shared world.

    NOTHING HERE CREATES AN INTRODUCTION THROUGH THE SITE ANY MORE
    -------------------------------------------------------------
    `Interest` took over that job — see `apps.messaging`. These build rows
    directly, because what still has to work is everything that happens to the
    introductions already in the database: they can be answered, they still
    release numbers, they still expire, and an approved one still opens a
    thread. Deleting these tests along with the create view would have left all
    of that unguarded.
    """

    def setUp(self):
        super().setUp()
        self.car = self.make_listing()
        self.driver_listing = DriverListing.objects.create(
            driver=self.driver,
            headline="Four years on Uber, Soweto based",
            years_experience=4,
            home_suburb=self.soweto,
            status=DriverListing.Status.ACTIVE,
        )

    def make_intro(self, **overrides):
        defaults = dict(
            from_user=self.driver, to_user=self.owner,
            vehicle_listing=self.car, message=MESSAGE,
        )
        defaults.update(overrides)
        return IntroRequest.objects.create(**defaults)


class AnsweringTests(IntroTestCase):
    def test_approval_releases_both_numbers_at_once(self):
        intro = self.make_intro()
        self.login(self.owner)
        self.client.post(reverse("intros:approve", args=[intro.uuid]))

        intro.refresh_from_db()
        self.assertEqual(intro.status, IntroRequest.Status.APPROVED)
        self.assertIsNotNone(intro.contacts_released_at)
        self.assertTrue(intro.contacts_released)

        # Both sides, same page, same moment.
        for user, expected in ((self.driver, self.owner), (self.owner, self.driver)):
            with self.subTest(user=user):
                self.login(user)
                response = self.client.get(intro.get_absolute_url())
                self.assertContains(response, expected.display_phone)

    def test_the_number_is_masked_until_approval(self):
        intro = self.make_intro()
        self.login(self.driver)
        response = self.client.get(intro.get_absolute_url())
        self.assertNotContains(response, self.owner.phone)
        self.assertContains(response, self.owner.masked_phone)

    def test_a_declined_request_never_releases_anything(self):
        intro = self.make_intro()
        self.login(self.owner)
        self.client.post(reverse("intros:decline", args=[intro.uuid]))

        intro.refresh_from_db()
        self.assertEqual(intro.status, IntroRequest.Status.DECLINED)
        self.assertIsNone(intro.contacts_released_at)

        self.login(self.driver)
        response = self.client.get(intro.get_absolute_url())
        self.assertNotContains(response, self.owner.phone)

    def test_only_the_recipient_can_approve(self):
        intro = self.make_intro()
        self.login(self.driver)
        response = self.client.post(reverse("intros:approve", args=[intro.uuid]))
        self.assertEqual(response.status_code, 404)
        intro.refresh_from_db()
        self.assertTrue(intro.is_open)

    def test_approving_needs_no_phone_verification(self):
        unverified = self._make_user("owner2@example.com", "Owner Two", verified=False)
        car = self.make_listing(owner=unverified)
        intro = self.make_intro(to_user=unverified, vehicle_listing=car)
        self.login(unverified)
        self.client.post(reverse("intros:approve", args=[intro.uuid]))
        intro.refresh_from_db()
        self.assertFalse(intro.is_open, "Approval should go through and close the request")

    def test_declining_works_for_anyone(self):
        """Saying no releases nothing, so nothing stands in front of it."""
        unverified = self._make_user("owner3@example.com", "Owner Three", verified=False)
        car = self.make_listing(owner=unverified)
        intro = self.make_intro(to_user=unverified, vehicle_listing=car)
        self.login(unverified)
        self.client.post(reverse("intros:decline", args=[intro.uuid]))
        intro.refresh_from_db()
        self.assertEqual(intro.status, IntroRequest.Status.DECLINED)

    def test_the_asker_can_withdraw(self):
        intro = self.make_intro()
        self.login(self.driver)
        self.client.post(reverse("intros:withdraw", args=[intro.uuid]))
        intro.refresh_from_db()
        self.assertEqual(intro.status, IntroRequest.Status.WITHDRAWN)

    def test_a_closed_request_cannot_be_reopened_by_approving_it(self):
        intro = self.make_intro()
        intro.withdraw()
        self.login(self.owner)
        self.client.post(reverse("intros:approve", args=[intro.uuid]))
        intro.refresh_from_db()
        self.assertEqual(intro.status, IntroRequest.Status.WITHDRAWN)
        self.assertIsNone(intro.contacts_released_at)

    def test_an_expired_request_cannot_be_approved(self):
        intro = self.make_intro()
        IntroRequest.objects.filter(pk=intro.pk).update(
            expires_at=timezone.now() - timedelta(days=1)
        )
        self.login(self.owner)
        self.client.post(reverse("intros:approve", args=[intro.uuid]))
        intro.refresh_from_db()
        self.assertIsNone(intro.contacts_released_at)

    def test_approval_is_free_and_records_the_price_it_asked_for(self):
        intro = self.make_intro()
        self.login(self.owner)
        self.client.post(reverse("intros:approve", args=[intro.uuid]))
        intro.refresh_from_db()
        self.assertEqual(intro.credits_charged, 0)


class PrivacyTests(IntroTestCase):
    def test_a_stranger_cannot_open_somebody_elses_request(self):
        intro = self.make_intro()
        intro.approve(by=self.owner)
        stranger = self._make_user("nosy@example.com", "Nosy Person", verified=True)
        self.login(stranger)
        response = self.client.get(intro.get_absolute_url())
        self.assertEqual(response.status_code, 404)

    def test_staff_get_no_back_door_into_an_approved_request(self):
        """
        An approved introduction is two people's private numbers. There is no
        support question that needs them, so there is no path to them.
        """
        intro = self.make_intro()
        intro.approve(by=self.owner)
        staff = self._make_user("staff2@example.com", "Staff Two", verified=True)
        staff.is_staff = True
        staff.save()
        self.login(staff)
        response = self.client.get(intro.get_absolute_url())
        self.assertEqual(response.status_code, 404)

    def test_the_car_page_still_masks_the_number_after_an_introduction(self):
        """
        Release is per-introduction, not a switch that unmasks a listing. The
        number lives on the request page, where it can still be pulled.
        """
        intro = self.make_intro()
        intro.approve(by=self.owner)
        self.login(self.driver)
        response = self.client.get(self.car.get_absolute_url())
        self.assertNotContains(response, self.owner.phone)

    def test_no_phone_number_goes_into_any_email(self):
        intro = self.make_intro()
        mail.outbox = []
        self.login(self.owner)
        self.client.post(reverse("intros:approve", args=[intro.uuid]))

        # One now rather than two: nothing files a request any more, so the
        # only email left in this flow is the answer to one already filed.
        self.assertGreaterEqual(len(mail.outbox), 1)
        for message in mail.outbox:
            body = message.body + "".join(str(alt[0]) for alt in message.alternatives)
            with self.subTest(subject=message.subject):
                self.assertNotIn(self.owner.phone, body)
                self.assertNotIn(self.driver.phone, body)
                self.assertNotIn(self.owner.display_phone, body)


class NotificationTests(IntroTestCase):
    def test_the_asker_is_emailed_either_way(self):
        for action, expected in (("approve", 1), ("decline", 1)):
            with self.subTest(action=action):
                intro = self.make_intro(from_user=self._make_user(
                    f"asker-{action}@example.com", "Asker", verified=True
                ))
                mail.outbox = []
                self.login(self.owner)
                self.client.post(reverse(f"intros:{action}", args=[intro.uuid]))
                self.assertEqual(len(mail.outbox), expected)
                self.assertEqual(mail.outbox[0].to, [intro.from_user.email])

    def test_a_bad_afternoon_at_the_email_provider_does_not_lose_the_approval(self):
        """
        The release is the row, not the mail.

        An approval that 500s after it has already written the release leaves
        somebody looking at an error page while their number is, in fact,
        released — the worst of both. So a provider throwing is caught inside
        `notify`, the redirect still happens, and the request page tells the
        truth.
        """
        from unittest.mock import patch

        intro = self.make_intro()
        self.login(self.owner)
        with patch(
            "django.core.mail.EmailMultiAlternatives.send",
            side_effect=Exception("provider down"),
        ):
            response = self.client.post(reverse("intros:approve", args=[intro.uuid]))

        self.assertRedirects(response, intro.get_absolute_url())
        intro.refresh_from_db()
        self.assertTrue(intro.contacts_released)


class InboxTests(IntroTestCase):
    def test_received_and_sent_show_the_right_rows(self):
        self.make_intro()

        self.login(self.owner)
        received = self.client.get(reverse("intros:inbox"))
        self.assertEqual(len(received.context["page"].object_list), 1)
        self.assertEqual(received.context["waiting"], 1)

        sent = self.client.get(reverse("intros:inbox") + "?tab=sent")
        self.assertEqual(len(sent.context["page"].object_list), 0)

        self.login(self.driver)
        sent = self.client.get(reverse("intros:inbox") + "?tab=sent")
        self.assertEqual(len(sent.context["page"].object_list), 1)

    def test_an_expired_request_stops_counting_as_waiting(self):
        intro = self.make_intro()
        IntroRequest.objects.filter(pk=intro.pk).update(
            expires_at=timezone.now() - timedelta(days=1)
        )
        self.login(self.owner)
        response = self.client.get(reverse("intros:inbox"))
        self.assertEqual(response.context["waiting"], 0)


class ExpiryTests(IntroTestCase):
    def test_the_page_tells_the_truth_before_the_cron_runs(self):
        intro = self.make_intro()
        IntroRequest.objects.filter(pk=intro.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        intro.refresh_from_db()
        self.assertTrue(intro.is_expired)
        self.assertFalse(intro.is_open)
        self.assertEqual(intro.effective_status, IntroRequest.Status.EXPIRED)

    def test_the_command_closes_stale_requests_and_leaves_the_rest(self):
        stale = self.make_intro()
        fresh = self.make_intro(from_user=self._make_user(
            "fresh@example.com", "Fresh Asker", verified=True
        ))
        IntroRequest.objects.filter(pk=stale.pk).update(
            expires_at=timezone.now() - timedelta(days=1)
        )

        call_command("expire_intros", verbosity=0)

        stale.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual(stale.status, IntroRequest.Status.EXPIRED)
        self.assertEqual(fresh.status, IntroRequest.Status.PENDING)

    def test_an_answered_request_is_never_expired_over(self):
        intro = self.make_intro()
        intro.approve(by=self.owner)
        IntroRequest.objects.filter(pk=intro.pk).update(
            expires_at=timezone.now() - timedelta(days=30)
        )
        call_command("expire_intros", verbosity=0)
        intro.refresh_from_db()
        self.assertEqual(intro.status, IntroRequest.Status.APPROVED)
