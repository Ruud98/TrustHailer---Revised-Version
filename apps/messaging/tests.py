"""
Direct messages.

The rule worth defending is the gate. Everything else here is convenience; the
gate is what stops a DM becoming the way round the introduction, and with it
the placement and the review that the introduction exists to make possible.
"""
from datetime import date

from django.db.utils import IntegrityError
from django.urls import reverse

from apps.follows import services as follows
from apps.intros.models import IntroRequest
from apps.listings.tests import ListingTestCase
from apps.messaging import services
from apps.messaging.models import Message, Thread
from apps.placements.models import Placement
from apps.safety.models import Block


class GateTests(ListingTestCase):
    """Three ways in, and every one of them needed the recipient's agreement."""

    def test_two_strangers_cannot_message(self):
        self.assertFalse(services.can_message(self.driver, self.owner))

    def test_an_approved_introduction_opens_it(self):
        car = self.make_listing()
        intro = IntroRequest.objects.create(
            from_user=self.driver, to_user=self.owner,
            vehicle_listing=car, message="Can I drive this one please?",
        )
        intro.approve(by=self.owner)

        self.assertTrue(services.can_message(self.driver, self.owner))
        self.assertTrue(services.can_message(self.owner, self.driver))

    def test_a_pending_introduction_does_not(self):
        """
        The gate is "the recipient agreed", not "somebody asked". A request
        that opened a channel by existing would be no gate at all.
        """
        car = self.make_listing()
        IntroRequest.objects.create(
            from_user=self.driver, to_user=self.owner,
            vehicle_listing=car, message="Can I drive this one please?",
        )
        self.assertFalse(services.can_message(self.driver, self.owner))

    def test_a_confirmed_placement_opens_it(self):
        car = self.make_listing()
        Placement.objects.create(
            vehicle_listing=car, owner=self.owner, driver=self.driver,
            started_on=date.today(),
            confirmed_by_owner=True, confirmed_by_driver=True,
        )
        self.assertTrue(services.can_message(self.driver, self.owner))

    def test_an_unconfirmed_placement_does_not(self):
        car = self.make_listing()
        Placement.objects.create(
            vehicle_listing=car, owner=self.owner, driver=self.driver,
            started_on=date.today(), confirmed_by_owner=True,
        )
        self.assertFalse(services.can_message(self.driver, self.owner))

    def test_a_mutual_follow_opens_it(self):
        follows.follow(self.driver, self.owner)
        self.assertFalse(services.can_message(self.driver, self.owner))

        follows.follow(self.owner, self.driver)
        self.assertTrue(services.can_message(self.driver, self.owner))

    def test_a_one_way_follow_does_not(self):
        """
        Otherwise following somebody would be a way to put yourself in their
        inbox — a thing the sender can do entirely alone.
        """
        follows.follow(self.driver, self.owner)
        self.assertFalse(services.can_message(self.driver, self.owner))
        self.assertFalse(services.can_message(self.owner, self.driver))

    def test_a_block_closes_it_again(self):
        follows.follow(self.driver, self.owner)
        follows.follow(self.owner, self.driver)
        Block.objects.create(user=self.owner, blocked_user=self.driver)

        self.assertFalse(services.can_message(self.driver, self.owner))

    def test_you_cannot_message_yourself(self):
        self.assertFalse(services.can_message(self.driver, self.driver))


class ThreadTests(ListingTestCase):
    def setUp(self):
        super().setUp()
        follows.follow(self.driver, self.owner)
        follows.follow(self.owner, self.driver)

    def test_the_pair_gets_one_thread_whoever_starts_it(self):
        first = services.start(self.driver, self.owner)
        second = services.start(self.owner, self.driver)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Thread.objects.count(), 1)

    def test_the_database_refuses_a_duplicate_pair(self):
        services.start(self.driver, self.owner)
        low, high = sorted([self.driver, self.owner], key=lambda u: u.pk)
        with self.assertRaises(IntegrityError):
            Thread.objects.create(user_a=low, user_b=high)

    def test_the_database_refuses_an_unordered_pair(self):
        """
        The ordering convention is what lets the unique constraint see the same
        two people as the same pair. Enforced in the database so nothing can
        route around `Thread.between`.
        """
        low, high = sorted([self.driver, self.owner], key=lambda u: u.pk)
        with self.assertRaises(IntegrityError):
            Thread.objects.create(user_a=high, user_b=low)

    def test_a_thread_is_not_a_licence_that_outlives_its_reason(self):
        """
        Created while they followed each other, then one unfollows. The thread
        still exists; writing to it must not.
        """
        thread = services.start(self.driver, self.owner)
        follows.unfollow(self.owner, self.driver)

        with self.assertRaises(services.CannotMessage):
            services.send(thread, self.driver, "Still there?")

    def test_a_stranger_cannot_open_somebody_elses_thread(self):
        thread = services.start(self.driver, self.owner)
        third = self._make_user("third@example.com", "Third Person")

        self.login(third)
        response = self.client.get(reverse("messaging:thread", args=[thread.pk]))
        self.assertEqual(response.status_code, 404)


class RedactionTests(ListingTestCase):
    """
    Numbers survive only where an introduction already released them, and
    redaction happens on the way IN so the raw number never reaches storage.
    """

    def setUp(self):
        super().setUp()
        follows.follow(self.driver, self.owner)
        follows.follow(self.owner, self.driver)

    def approve_intro(self):
        car = self.make_listing()
        intro = IntroRequest.objects.create(
            from_user=self.driver, to_user=self.owner,
            vehicle_listing=car, message="Can I drive this one please?",
        )
        intro.approve(by=self.owner)

    def test_a_number_is_stripped_before_an_introduction(self):
        thread = services.start(self.driver, self.owner)
        services.send(thread, self.driver, "Call me on 082 555 1234")

        stored = Message.objects.get().body
        self.assertNotIn("082 555 1234", stored)

    def test_a_number_survives_once_an_introduction_is_approved(self):
        self.approve_intro()
        thread = services.start(self.driver, self.owner)
        services.send(thread, self.driver, "Call me on 082 555 1234")

        self.assertIn("082 555 1234", Message.objects.get().body)

    def test_the_raw_address_never_reaches_the_database(self):
        """
        Redacting on render would leave the real thing in storage, one missed
        call site away from leaking through an export or an admin screen.
        """
        thread = services.start(self.driver, self.owner)
        services.send(thread, self.driver, "mail me at me@example.com")
        self.assertNotIn("me@example.com", Message.objects.get().body)


class InboxTests(ListingTestCase):
    def setUp(self):
        super().setUp()
        follows.follow(self.driver, self.owner)
        follows.follow(self.owner, self.driver)
        self.thread = services.start(self.driver, self.owner)

    def test_sending_and_reading(self):
        services.send(self.thread, self.owner, "Morning.")
        self.assertEqual(services.unread_count(self.driver), 1)
        self.assertEqual(services.unread_count(self.owner), 0)

        self.login(self.driver)
        self.client.get(reverse("messaging:thread", args=[self.thread.pk]))
        self.assertEqual(services.unread_count(self.driver), 0)

    def test_your_own_message_is_never_unread_for_you(self):
        services.send(self.thread, self.driver, "Mine.")
        self.assertEqual(services.unread_count(self.driver), 0)

    def test_an_empty_thread_stays_out_of_the_inbox(self):
        """A thread with nothing in it is a row nobody asked for."""
        self.login(self.driver)
        response = self.client.get(reverse("messaging:inbox"))
        self.assertEqual(list(response.context["threads"]), [])

        services.send(self.thread, self.owner, "Now there is something.")
        response = self.client.get(reverse("messaging:inbox"))
        self.assertEqual(len(response.context["threads"]), 1)

    def test_the_profile_only_offers_a_button_when_the_gate_is_open(self):
        self.login(self.driver)
        self.assertTrue(
            self.client.get(self.owner.get_absolute_url()).context["can_message"]
        )

        stranger = self._make_user("stranger@example.com", "A Stranger")
        self.assertFalse(
            self.client.get(stranger.get_absolute_url()).context["can_message"]
        )

    def test_starting_a_thread_with_a_stranger_is_refused(self):
        stranger = self._make_user("s2@example.com", "Another Stranger")
        self.login(self.driver)
        self.client.post(reverse("messaging:start", args=[stranger.handle]))
        self.assertFalse(
            Thread.objects.filter(user_a=stranger).exists()
            or Thread.objects.filter(user_b=stranger).exists()
        )
