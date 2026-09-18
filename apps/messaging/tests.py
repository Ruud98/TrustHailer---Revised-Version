"""
Direct messages.

The rule worth defending is the gate: a thread needs a reason, and the reasons
are the ones `can_message` lists. It is what stops a DM becoming the way round
the placement, and with it the review the placement exists to make possible.

The second rule, since interests replaced introductions, is that a number only
travels when the person it belongs to sends it. That used to be an approve
button; it is now a Share my number button, and the tests below check the two
are equally hard to get round.
"""
from datetime import date

from django.db.utils import IntegrityError
from django.urls import reverse

from apps.accounts.models import User
from apps.follows import services as follows
from apps.intros.models import IntroRequest
from apps.listings.models import DriverListing, VehicleListing
from apps.listings.tests import ListingTestCase
from apps.messaging import services
from apps.messaging.models import Interest, Message, Thread
from apps.notifications.models import Notification
from apps.placements.models import Placement
from apps.safety.models import Block


class GateTests(ListingTestCase):
    """Four ways in, and what each one costs."""

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


class InterestTests(ListingTestCase):
    """
    "I am interested" — the button that replaced asking for an introduction.

    What has to be true: it opens a conversation, the conversation says which
    listing it is about, it cannot be used twice on the same listing, and it
    does not become a way to write to people who never advertised anything.
    """

    def setUp(self):
        super().setUp()
        self.car = self.make_listing()

    def interested(self, user, kind="car", uuid=None):
        self.login(user)
        return self.client.post(
            reverse("messaging:interested",
                    args=[kind, uuid or self.car.uuid])
        )

    def test_one_tap_opens_a_thread_that_names_the_car(self):
        response = self.interested(self.driver)
        thread = Thread.objects.get()
        self.assertRedirects(response, reverse("messaging:thread", args=[thread.pk]))

        interest = Interest.objects.get()
        self.assertEqual(interest.user, self.driver)
        self.assertEqual(interest.vehicle_listing, self.car)
        self.assertEqual(interest.thread, thread)

        opening = Message.objects.get()
        self.assertEqual(opening.sender, self.driver)
        self.assertIn(self.car.title, opening.body)

    def test_the_owner_can_reply(self):
        """
        The gate is symmetric or it is useless: an advert you may answer but
        whose owner may not answer you is a dead end.
        """
        self.interested(self.driver)
        self.assertTrue(services.can_message(self.owner, self.driver))

    def test_answering_twice_reopens_the_chat_instead_of_writing_again(self):
        self.interested(self.driver)
        response = self.interested(self.driver)

        self.assertEqual(Interest.objects.count(), 1)
        self.assertEqual(Message.objects.count(), 1)
        thread = Thread.objects.get()
        self.assertRedirects(response, reverse("messaging:thread", args=[thread.pk]))

    def test_you_cannot_answer_your_own_listing(self):
        self.interested(self.owner)
        self.assertFalse(Interest.objects.exists())

    def test_you_cannot_answer_a_listing_that_is_not_live(self):
        self.car.status = VehicleListing.Status.PAUSED
        self.car.save(update_fields=["status"])
        self.interested(self.driver)
        self.assertFalse(Interest.objects.exists())

    def test_an_unclaimed_import_has_nobody_to_write_to(self):
        """
        Imported adverts have no owner on this site. Without this check the
        view would try to open a thread with None.
        """
        orphan = VehicleListing.objects.create(
            owner=None, make="Nissan", model="Almera", year=2018,
            transmission="manual", arrangement="weekly", weekly_rate=2300,
            suburb=self.soweto, status=VehicleListing.Status.ACTIVE,
            source=VehicleListing.Source.FACEBOOK,
            source_url="https://www.facebook.com/groups/1/posts/2/",
        )
        self.interested(self.driver, uuid=orphan.uuid)
        self.assertFalse(Interest.objects.exists())

    def test_an_owner_can_answer_a_driver_listing_the_same_way(self):
        listing = DriverListing.objects.create(
            driver=self.driver, headline="Four years on Uber, Soweto based",
            years_experience=4, home_suburb=self.soweto,
            status=DriverListing.Status.ACTIVE,
        )
        self.interested(self.owner, kind="driver", uuid=listing.uuid)

        interest = Interest.objects.get()
        self.assertEqual(interest.driver_listing, listing)
        self.assertIn(listing.headline, Message.objects.get().body)

    def test_an_interest_names_exactly_one_listing(self):
        thread = Thread.between(self.driver, self.owner)
        with self.assertRaises(IntegrityError):
            Interest.objects.create(user=self.driver, thread=thread)

    def test_the_car_page_offers_the_button_and_then_the_chat(self):
        self.login(self.driver)
        response = self.client.get(self.car.get_absolute_url())
        self.assertContains(response, "I am interested")

        self.interested(self.driver)
        response = self.client.get(self.car.get_absolute_url())
        self.assertContains(response, "open the chat")
        self.assertNotContains(response, "I am interested")

    def test_the_thread_says_which_listing_it_is_about(self):
        self.interested(self.driver)
        thread = Thread.objects.get()
        response = self.client.get(reverse("messaging:thread", args=[thread.pk]))
        self.assertContains(response, self.car.title)
        self.assertContains(response, self.car.get_absolute_url())


class NumberSharingTests(ListingTestCase):
    """
    The consent that used to be an approve button.

    It moved to a point where somebody can actually make the decision — after
    a few messages, knowing who they are talking to. What must not move is who
    it belongs to: my pressing the button releases MY number, in THIS
    conversation, and nothing else.
    """

    def setUp(self):
        super().setUp()
        self.car = self.make_listing()
        self.thread, _ = services.express_interest(self.driver, self.car)

    def test_a_number_is_stripped_until_its_owner_shares_it(self):
        message = services.send(self.thread, self.driver, "Call me on 082 123 4567")
        self.assertNotIn("082 123 4567", message.body)

    def test_sharing_sends_the_number_and_lifts_it_for_that_person(self):
        self.login(self.owner)
        self.client.post(reverse("messaging:share_number", args=[self.thread.pk]))

        self.thread.refresh_from_db()
        self.assertTrue(self.thread.number_shared_by(self.owner))
        self.assertIn(self.owner.phone, Message.objects.latest("created_at").body)

        kept = services.send(self.thread, self.owner, f"Again: {self.owner.phone}")
        self.assertIn(self.owner.phone, kept.body)

    def test_sharing_mine_does_not_release_theirs(self):
        """
        The single most important line here. One button, one person's number.
        A shared flag on the thread rather than on the person would have made
        the owner's decision release the driver's number too.
        """
        self.login(self.owner)
        self.client.post(reverse("messaging:share_number", args=[self.thread.pk]))

        theirs = services.send(self.thread, self.driver, "Mine is 082 123 4567")
        self.assertNotIn("082 123 4567", theirs.body)

    def test_sharing_here_does_not_release_it_everywhere(self):
        third = self._make_user("third@example.com", "Third Person", verified=True)
        other_thread, _ = services.express_interest(third, self.car)

        self.login(self.owner)
        self.client.post(reverse("messaging:share_number", args=[self.thread.pk]))

        other_thread.refresh_from_db()
        self.assertFalse(other_thread.number_shared_by(self.owner))
        message = services.send(other_thread, self.owner, f"Ring {self.owner.phone}")
        self.assertNotIn(self.owner.phone, message.body)

    def test_pressing_it_twice_is_not_a_second_decision(self):
        self.assertTrue(self.thread.share_number(self.owner))
        first = self.thread.a_shared_number_at or self.thread.b_shared_number_at
        self.assertFalse(self.thread.share_number(self.owner))
        self.assertIn(first, (self.thread.a_shared_number_at,
                              self.thread.b_shared_number_at))

    def test_with_no_number_on_file_it_says_so_instead_of_sending_none(self):
        User.objects.filter(pk=self.owner.pk).update(phone=None)
        self.owner.refresh_from_db()

        with self.assertRaises(services.CannotMessage):
            services.share_number(self.thread, self.owner)


class WorkingTogetherTests(ListingTestCase):
    """
    The owner's button at the top of the chat, and the driver's answer to it.

    THE TWO-SIDED RULE IS THE WHOLE POINT
    -------------------------------------
    The owner's tap confirms the owner's side only. A review exists only
    against a placement both people confirmed, and one person being unable to
    invent a working relationship is what the entire review system rests on.
    Anything here that quietly confirmed both sides would hand anybody with two
    accounts a review factory.
    """

    def setUp(self):
        super().setUp()
        self.car = self.make_listing()
        self.thread, _ = services.express_interest(self.driver, self.car)

    def agree(self, user=None):
        self.login(user or self.owner)
        return self.client.post(
            reverse("messaging:start_placement", args=[self.thread.pk])
        )

    def test_the_owner_starts_the_record_and_confirms_only_their_own_side(self):
        self.agree()
        placement = Placement.objects.get()
        self.assertEqual(placement.owner, self.owner)
        self.assertEqual(placement.driver, self.driver)
        self.assertEqual(placement.vehicle_listing, self.car)
        self.assertTrue(placement.confirmed_by_owner)
        self.assertFalse(placement.confirmed_by_driver)
        self.assertFalse(placement.is_confirmed)

    def test_the_driver_confirms_from_the_same_chat(self):
        self.agree()
        placement = Placement.objects.get()

        # The button is offered to the side that has not confirmed, so this is
        # read as the driver. The owner sees "waiting for them" instead.
        self.login(self.driver)
        response = self.client.get(reverse("messaging:thread", args=[self.thread.pk]))
        self.assertContains(
            response, reverse("placements:confirm", args=[placement.uuid])
        )

        self.client.post(reverse("placements:confirm", args=[placement.uuid]))
        placement.refresh_from_db()
        self.assertTrue(placement.is_confirmed)

    def test_the_driver_is_not_offered_the_button(self):
        """
        The owner hands over a car, so the owner is the one who knows it
        happened. A driver who could start the record could start one against
        any owner who ever answered them.
        """
        self.login(self.driver)
        response = self.client.get(reverse("messaging:thread", args=[self.thread.pk]))
        self.assertNotContains(
            response, reverse("messaging:start_placement", args=[self.thread.pk])
        )

        self.agree(user=self.driver)
        self.assertFalse(Placement.objects.exists())

    def test_it_says_so_in_the_thread_as_well_as_in_a_notification(self):
        self.agree()
        self.assertIn(self.car.title, Message.objects.latest("created_at").body)
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.driver, kind=Notification.Kind.PLACEMENT_CONFIRM
            ).exists()
        )

    def test_the_car_comes_off_the_market(self):
        self.agree()
        self.car.refresh_from_db()
        self.assertEqual(self.car.status, VehicleListing.Status.PLACED)

    def test_it_cannot_be_pressed_twice(self):
        self.agree()
        self.agree()
        self.assertEqual(Placement.objects.count(), 1)

    def test_a_chat_about_no_car_of_mine_offers_nothing_to_record(self):
        """
        A mutual follow is a reason to talk, not a car. Without a listing there
        is nothing to record the placement against.
        """
        stranger = self._make_user("stranger@example.com", "A Stranger", verified=True)
        follows.follow(self.owner, stranger)
        follows.follow(stranger, self.owner)
        thread = Thread.between(self.owner, stranger)

        self.login(self.owner)
        response = self.client.get(reverse("messaging:thread", args=[thread.pk]))
        self.assertNotContains(
            response, reverse("messaging:start_placement", args=[thread.pk])
        )
