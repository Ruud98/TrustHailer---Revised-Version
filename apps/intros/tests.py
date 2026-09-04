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
    """One owner with a car, one driver with a listing, on the shared world."""

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

    def ask_about_car(self, user=None, message=MESSAGE):
        self.login(user or self.driver)
        return self.client.post(
            reverse("intros:create") + f"?car={self.car.uuid}", {"message": message}
        )

    def make_intro(self, **overrides):
        defaults = dict(
            from_user=self.driver, to_user=self.owner,
            vehicle_listing=self.car, message=MESSAGE,
        )
        defaults.update(overrides)
        return IntroRequest.objects.create(**defaults)


class RequestingTests(IntroTestCase):
    def test_a_driver_can_ask_about_a_car(self):
        response = self.ask_about_car()
        intro = IntroRequest.objects.get()
        self.assertRedirects(response, intro.get_absolute_url())
        self.assertEqual(intro.from_user, self.driver)
        self.assertEqual(intro.to_user, self.owner)
        self.assertEqual(intro.vehicle_listing, self.car)
        self.assertTrue(intro.is_open)
        self.assertIsNone(intro.contacts_released_at)

    def test_an_owner_can_ask_about_a_driver(self):
        self.login(self.owner)
        self.client.post(
            reverse("intros:create") + f"?driver={self.driver_listing.uuid}",
            {"message": MESSAGE},
        )
        intro = IntroRequest.objects.get()
        self.assertEqual(intro.driver_listing, self.driver_listing)
        self.assertEqual(intro.to_user, self.driver)

    def test_the_expiry_is_seven_days_out(self):
        self.ask_about_car()
        intro = IntroRequest.objects.get()
        expected = timezone.now() + timedelta(days=IntroRequest.EXPIRY_DAYS)
        self.assertLess(abs((intro.expires_at - expected).total_seconds()), 60)

    def test_asking_needs_a_verified_phone(self):
        """
        Approval releases BOTH numbers. An unverified asker would take one and
        give nothing back, which is the one shape this flow refuses to have.
        """
        unverified = self._make_user("new@example.com", "New Person", verified=False)
        response = self.ask_about_car(user=unverified)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(IntroRequest.objects.exists())

    def test_a_suspended_account_cannot_ask(self):
        self.driver.is_suspended = True
        self.driver.save()
        self.ask_about_car()
        self.assertFalse(IntroRequest.objects.exists())

    def test_you_cannot_ask_about_your_own_listing(self):
        self.ask_about_car(user=self.owner)
        self.assertFalse(IntroRequest.objects.exists())

    def test_you_cannot_ask_twice_while_one_is_open(self):
        self.ask_about_car()
        self.ask_about_car()
        self.assertEqual(IntroRequest.objects.count(), 1)

    def test_you_can_ask_again_after_a_decline(self):
        """
        Scoped to pending on purpose. Months later the car may be free, or the
        driver may have the experience the owner wanted the first time.
        """
        intro = self.make_intro()
        intro.decline()
        self.ask_about_car()
        self.assertEqual(IntroRequest.objects.count(), 2)

    def test_a_number_in_the_message_is_refused_with_a_reason(self):
        """
        Otherwise the double opt-in becomes a formality people route around by
        typing "call me on 082..." — which hands a number to somebody who has
        not agreed to receive it.
        """
        response = self.ask_about_car(
            message="Hi there, I am keen on this car, please call me on 082 123 4567."
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Leave contact details out")
        self.assertFalse(IntroRequest.objects.exists())

    def test_a_one_word_request_is_refused(self):
        response = self.ask_about_car(message="interested")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(IntroRequest.objects.exists())

    def test_you_cannot_ask_about_an_unclaimed_import(self):
        """There is nobody on the site to introduce anyone to."""
        imported = VehicleListing.objects.create(
            owner=None, make="Nissan", model="Almera", year=2018,
            transmission="manual", arrangement="weekly", weekly_rate=2300,
            suburb=self.soweto, status=VehicleListing.Status.ACTIVE,
            source=VehicleListing.Source.FACEBOOK,
            source_url="https://www.facebook.com/groups/1/posts/2/",
        )
        self.login(self.driver)
        response = self.client.get(reverse("intros:create") + f"?car={imported.uuid}")
        self.assertEqual(response.status_code, 404)

    def test_you_cannot_ask_about_a_listing_that_is_not_live(self):
        self.car.status = VehicleListing.Status.PAUSED
        self.car.save()
        self.login(self.driver)
        response = self.client.get(reverse("intros:create") + f"?car={self.car.uuid}")
        self.assertEqual(response.status_code, 404)

    def test_a_request_names_exactly_one_listing(self):
        with self.assertRaises(IntegrityError):
            IntroRequest.objects.create(
                from_user=self.driver, to_user=self.owner, message=MESSAGE
            )


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

    def test_approving_needs_a_verified_phone(self):
        unverified = self._make_user("owner2@example.com", "Owner Two", verified=False)
        car = self.make_listing(owner=unverified)
        intro = self.make_intro(to_user=unverified, vehicle_listing=car)
        self.login(unverified)
        self.client.post(reverse("intros:approve", args=[intro.uuid]))
        intro.refresh_from_db()
        self.assertTrue(intro.is_open)

    def test_declining_does_not_need_a_verified_phone(self):
        """Saying no releases nothing. A wall here would only produce silence."""
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
        mail.outbox = []
        self.ask_about_car()
        intro = IntroRequest.objects.get()
        self.login(self.owner)
        self.client.post(reverse("intros:approve", args=[intro.uuid]))

        self.assertGreaterEqual(len(mail.outbox), 2)
        for message in mail.outbox:
            body = message.body + "".join(str(alt[0]) for alt in message.alternatives)
            with self.subTest(subject=message.subject):
                self.assertNotIn(self.owner.phone, body)
                self.assertNotIn(self.driver.phone, body)
                self.assertNotIn(self.owner.display_phone, body)


class NotificationTests(IntroTestCase):
    def test_the_recipient_is_emailed_about_a_new_request(self):
        mail.outbox = []
        self.ask_about_car()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.owner.email])

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


class ListingPageTests(IntroTestCase):
    def test_the_car_page_offers_the_request_button(self):
        self.login(self.driver)
        response = self.client.get(self.car.get_absolute_url())
        self.assertContains(response, "Request an introduction")
        self.assertContains(response, f"?car={self.car.uuid}")

    def test_the_button_changes_once_you_have_asked(self):
        self.make_intro()
        self.login(self.driver)
        response = self.client.get(self.car.get_absolute_url())
        self.assertContains(response, "waiting for an answer")
        self.assertNotContains(response, "Request an introduction")

    def test_the_driver_page_offers_it_too(self):
        self.login(self.owner)
        response = self.client.get(self.driver_listing.get_absolute_url())
        self.assertContains(response, f"?driver={self.driver_listing.uuid}")
