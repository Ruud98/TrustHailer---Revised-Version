"""
Tests for placements and reviews.

The rules being defended here, in order of how much damage breaking them would
do: no review without a placement both people confirmed; no review visible
before the other side has written or the timer has run out; and no review of
somebody who never asked about this car. Everything else is plumbing.
"""
from datetime import date, timedelta

from django.core.management import call_command
from django.db.utils import IntegrityError
from django.urls import reverse
from django.utils import timezone

from apps.intros.models import IntroRequest
from apps.messaging import services as messaging
from apps.listings.models import VehicleListing
from apps.listings.tests import ListingTestCase

from .models import Placement, Review, recalculate_rating

BODY = "Straight with me from the first day, and the car was always in good shape."


class PlacementTestCase(ListingTestCase):
    """An owner, a driver, a car, and the driver having asked about it."""

    def setUp(self):
        super().setUp()
        self.car = self.make_listing()
        self.thread, _ = messaging.express_interest(self.driver, self.car)

    def record_placement(self, user=None):
        self.login(user or self.owner)
        return self.client.post(
            reverse("placements:create", args=[self.car.uuid]),
            {
                "other": (self.driver if (user or self.owner) == self.owner
                          else self.owner).pk,
                "started_on": date.today().isoformat(),
            },
        )

    def confirmed_placement(self):
        placement = Placement.objects.create(
            vehicle_listing=self.car, owner=self.owner, driver=self.driver,
            started_on=date.today() - timedelta(days=30),
            confirmed_by_owner=True, confirmed_by_driver=True,
        )
        return placement

    def write_review(self, placement, author, **overrides):
        payload = {"overall": 5, "communication": 4, "body": BODY}
        payload.update(overrides)
        self.login(author)
        return self.client.post(
            reverse("placements:review", args=[placement.uuid]), payload
        )


class RecordingTests(PlacementTestCase):
    def test_an_owner_records_a_placement_with_somebody_who_asked(self):
        response = self.record_placement()
        placement = Placement.objects.get()
        self.assertRedirects(response, placement.get_absolute_url())
        self.assertEqual(placement.owner, self.owner)
        self.assertEqual(placement.driver, self.driver)

    def test_recording_it_confirms_your_own_side_only(self):
        self.record_placement()
        placement = Placement.objects.get()
        self.assertTrue(placement.confirmed_by_owner)
        self.assertFalse(placement.confirmed_by_driver)
        self.assertFalse(placement.is_confirmed)

    def test_a_driver_can_record_it_too(self):
        self.record_placement(user=self.driver)
        placement = Placement.objects.get()
        self.assertTrue(placement.confirmed_by_driver)
        self.assertFalse(placement.confirmed_by_owner)
        self.assertEqual(placement.owner, self.owner)

    def test_the_car_comes_off_the_market(self):
        """An ad left up after a placement wastes every driver who answers it."""
        self.record_placement()
        self.car.refresh_from_db()
        self.assertEqual(self.car.status, VehicleListing.Status.PLACED)

    def test_you_can_only_choose_people_who_asked_about_this_car(self):
        """
        The dropdown is the security model. Without it, a review could be
        attached to somebody who never dealt with you at all. An interest in a
        DIFFERENT car of yours is the case worth pinning: it is the nearest
        thing to a real relationship that still must not count here.
        """
        stranger = self._make_user("stranger@example.com", "A Stranger", verified=True)
        messaging.express_interest(stranger, self.make_listing())

        self.login(self.owner)
        response = self.client.post(
            reverse("placements:create", args=[self.car.uuid]),
            {"other": stranger.pk, "started_on": date.today().isoformat()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Placement.objects.exists())

    def test_an_approved_introduction_still_counts(self):
        """
        Introductions are no longer how conversations start, but the ones in
        the database still describe two people who agreed to deal with each
        other. Dropping them from the dropdown would have stranded every
        relationship formed before the change.
        """
        older = self._make_user("older@example.com", "Older Member", verified=True)
        intro = IntroRequest.objects.create(
            from_user=older, to_user=self.owner, vehicle_listing=self.car,
            message="I would like to drive this car please.",
        )
        intro.approve(by=self.owner)

        self.login(self.owner)
        self.client.post(
            reverse("placements:create", args=[self.car.uuid]),
            {"other": older.pk, "started_on": date.today().isoformat()},
        )
        placement = Placement.objects.get()
        self.assertEqual(placement.driver, older)
        self.assertEqual(placement.intro, intro)

    def test_with_nobody_interested_there_is_nothing_to_record(self):
        self.car.interests.all().delete()
        self.login(self.owner)
        response = self.client.get(reverse("placements:create", args=[self.car.uuid]))
        self.assertRedirects(response, self.car.get_absolute_url())

    def test_a_placement_needs_two_different_people(self):
        with self.assertRaises(IntegrityError):
            Placement.objects.create(
                vehicle_listing=self.car, owner=self.owner, driver=self.owner,
                started_on=date.today(),
            )

    def test_one_open_placement_per_driver_per_car(self):
        self.confirmed_placement()
        with self.assertRaises(IntegrityError):
            Placement.objects.create(
                vehicle_listing=self.car, owner=self.owner, driver=self.driver,
                started_on=date.today(),
            )

    def test_a_stranger_cannot_open_somebody_elses_placement(self):
        placement = self.confirmed_placement()
        stranger = self._make_user("nosy2@example.com", "Nosy", verified=True)
        self.login(stranger)
        self.assertEqual(
            self.client.get(placement.get_absolute_url()).status_code, 404
        )

    def test_not_even_staff(self):
        """
        A placement carries two unpublished reviews. There is no support
        question that needs to read one before it publishes.
        """
        placement = self.confirmed_placement()
        staff = self._make_user("staff5@example.com", "Staff Five", verified=True)
        staff.is_staff = True
        staff.save()
        self.login(staff)
        self.assertEqual(
            self.client.get(placement.get_absolute_url()).status_code, 404
        )


class ConfirmationTests(PlacementTestCase):
    def test_the_other_side_confirms_and_reviews_open_up(self):
        self.record_placement()
        placement = Placement.objects.get()
        self.assertFalse(placement.can_review(self.owner))

        self.login(self.driver)
        self.client.post(reverse("placements:confirm", args=[placement.uuid]))

        placement.refresh_from_db()
        self.assertTrue(placement.is_confirmed)
        self.assertTrue(placement.can_review(self.owner))
        self.assertTrue(placement.can_review(self.driver))

    def test_no_review_without_confirmation_from_both(self):
        """The rule everything else rests on."""
        self.record_placement()
        placement = Placement.objects.get()
        response = self.write_review(placement, self.owner)
        self.assertRedirects(response, placement.get_absolute_url())
        self.assertFalse(Review.objects.exists())

    def test_ending_a_placement_records_a_reason_and_keeps_reviews_open(self):
        placement = self.confirmed_placement()
        self.login(self.owner)
        self.client.post(
            reverse("placements:end", args=[placement.uuid]),
            {"ended_on": date.today().isoformat(), "end_reason": Placement.EndReason.AGREED},
        )
        placement.refresh_from_db()
        self.assertIsNotNone(placement.ended_on)
        self.assertTrue(placement.can_review(self.owner))

    def test_an_end_date_before_the_start_is_refused(self):
        placement = self.confirmed_placement()
        self.login(self.owner)
        response = self.client.post(
            reverse("placements:end", args=[placement.uuid]),
            {
                "ended_on": (placement.started_on - timedelta(days=5)).isoformat(),
                "end_reason": Placement.EndReason.AGREED,
            },
        )
        self.assertEqual(response.status_code, 200)
        placement.refresh_from_db()
        self.assertIsNone(placement.ended_on)


class DoubleBlindTests(PlacementTestCase):
    def test_the_first_review_is_sealed(self):
        placement = self.confirmed_placement()
        self.write_review(placement, self.owner)

        review = Review.objects.get()
        self.assertFalse(review.is_published)
        self.assertIsNone(review.published_at)

    def test_the_subject_cannot_read_it_before_writing_their_own(self):
        """
        The whole mechanism. If the driver can read the owner's review first,
        every review becomes an answer to a review.
        """
        placement = self.confirmed_placement()
        self.write_review(placement, self.owner, body="Late with payment twice.")

        self.login(self.driver)
        response = self.client.get(placement.get_absolute_url())
        self.assertNotContains(response, "Late with payment twice")
        self.assertIsNone(response.context["their_review"])

    def test_the_second_review_publishes_both_at_once(self):
        placement = self.confirmed_placement()
        self.write_review(placement, self.owner)
        self.write_review(placement, self.driver)

        self.assertEqual(Review.objects.filter(is_published=True).count(), 2)
        for review in Review.objects.all():
            self.assertIsNotNone(review.published_at)

    def test_and_then_each_side_can_read_the_other(self):
        placement = self.confirmed_placement()
        self.write_review(placement, self.owner, body="Looked after the car well.")
        self.write_review(placement, self.driver, body="Fixed the clutch the same week.")

        self.login(self.driver)
        response = self.client.get(placement.get_absolute_url())
        self.assertContains(response, "Looked after the car well")

        self.login(self.owner)
        response = self.client.get(placement.get_absolute_url())
        self.assertContains(response, "Fixed the clutch the same week")

    def test_the_timer_releases_a_one_sided_review(self):
        """
        Without this, one silent party freezes the other's review forever —
        a free veto for anybody who suspects it is unflattering.
        """
        placement = self.confirmed_placement()
        self.write_review(placement, self.owner)
        review = Review.objects.get()
        Review.objects.filter(pk=review.pk).update(
            created_at=timezone.now() - timedelta(days=Review.BLIND_DAYS + 1)
        )

        call_command("publish_reviews", verbosity=0)

        review.refresh_from_db()
        self.assertTrue(review.is_published)

    def test_the_timer_does_not_release_a_fresh_one(self):
        placement = self.confirmed_placement()
        self.write_review(placement, self.owner)
        call_command("publish_reviews", verbosity=0)
        self.assertFalse(Review.objects.get().is_published)

    def test_one_review_per_person_per_placement(self):
        placement = self.confirmed_placement()
        self.write_review(placement, self.owner)
        self.write_review(placement, self.owner, overall=1)
        self.assertEqual(Review.objects.filter(author=self.owner).count(), 1)
        self.assertEqual(Review.objects.get(author=self.owner).overall, 5)


class ReviewContentTests(PlacementTestCase):
    def test_an_owner_is_asked_about_the_driver(self):
        placement = self.confirmed_placement()
        self.login(self.owner)
        form = self.client.get(
            reverse("placements:review", args=[placement.uuid])
        ).context["form"]
        self.assertIn("payment_reliability", form.fields)
        self.assertIn("vehicle_care", form.fields)
        self.assertNotIn("deposit_returned", form.fields)

    def test_a_driver_is_asked_about_the_owner(self):
        placement = self.confirmed_placement()
        self.login(self.driver)
        form = self.client.get(
            reverse("placements:review", args=[placement.uuid])
        ).context["form"]
        self.assertIn("fairness", form.fields)
        self.assertIn("deposit_returned", form.fields)
        self.assertNotIn("payment_reliability", form.fields)

    def test_the_review_lands_on_the_right_person(self):
        placement = self.confirmed_placement()
        self.write_review(placement, self.owner)
        review = Review.objects.get()
        self.assertEqual(review.author, self.owner)
        self.assertEqual(review.subject, self.driver)

    def test_contact_details_are_refused(self):
        """A review is a public page that outlives the deal."""
        placement = self.confirmed_placement()
        response = self.write_review(
            placement, self.owner, body="Good driver, ring him on 082 123 4567."
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Review.objects.exists())

    def test_a_rating_outside_one_to_five_is_refused(self):
        placement = self.confirmed_placement()
        response = self.write_review(placement, self.owner, overall=9)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Review.objects.exists())

    def test_you_cannot_review_yourself(self):
        placement = self.confirmed_placement()
        with self.assertRaises(IntegrityError):
            Review.objects.create(
                placement=placement, author=self.owner, subject=self.owner,
                overall=5, communication=5,
            )


class AggregateTests(PlacementTestCase):
    def test_publishing_updates_the_subjects_rating(self):
        placement = self.confirmed_placement()
        self.write_review(placement, self.owner, overall=4)
        self.write_review(placement, self.driver, overall=5)

        self.driver.profile.refresh_from_db()
        self.owner.profile.refresh_from_db()
        self.assertEqual(float(self.driver.profile.rating_avg), 4.0)
        self.assertEqual(self.driver.profile.rating_count, 1)
        self.assertEqual(float(self.owner.profile.rating_avg), 5.0)

    def test_an_unpublished_review_counts_for_nothing(self):
        placement = self.confirmed_placement()
        self.write_review(placement, self.owner, overall=1)
        self.driver.profile.refresh_from_db()
        self.assertIsNone(self.driver.profile.rating_avg)
        self.assertEqual(self.driver.profile.rating_count, 0)

    def test_recalculating_agrees_with_the_stored_value(self):
        placement = self.confirmed_placement()
        self.write_review(placement, self.owner, overall=3)
        self.write_review(placement, self.driver, overall=3)
        profile = recalculate_rating(self.driver)
        self.assertEqual(float(profile.rating_avg), 3.0)

    def test_the_rating_shows_on_a_card(self):
        placement = self.confirmed_placement()
        self.write_review(placement, self.driver, overall=5)
        self.write_review(placement, self.owner, overall=5)
        self.car.status = VehicleListing.Status.ACTIVE
        self.car.save()

        response = self.client.get(reverse("listings:browse"))
        self.assertContains(response, "5.0★")


class PublicReviewTests(PlacementTestCase):
    def test_the_reviews_page_shows_published_ones_only(self):
        placement = self.confirmed_placement()
        self.write_review(placement, self.owner, body="Sealed for now.")

        response = self.client.get(
            reverse("placements:reviews", args=[self.driver.handle])
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Sealed for now")

        self.write_review(placement, self.driver)
        response = self.client.get(
            reverse("placements:reviews", args=[self.driver.handle])
        )
        self.assertContains(response, "Sealed for now")

    def test_the_page_is_public(self):
        response = self.client.get(
            reverse("placements:reviews", args=[self.driver.handle])
        )
        self.assertEqual(response.status_code, 200)

    def test_a_blocked_person_cannot_read_them(self):
        from apps.safety.models import Block

        Block.objects.create(user=self.driver, blocked_user=self.owner)
        self.login(self.owner)
        response = self.client.get(
            reverse("placements:reviews", args=[self.driver.handle])
        )
        self.assertEqual(response.status_code, 404)

    def test_the_profile_links_to_them_once_there_are_any(self):
        placement = self.confirmed_placement()
        self.write_review(placement, self.owner)
        self.write_review(placement, self.driver)

        response = self.client.get(self.driver.get_absolute_url())
        self.assertContains(response, reverse("placements:reviews", args=[self.driver.handle]))


class TrackRecordTests(PlacementTestCase):
    """
    The deal history, split the way an order history is.

    What matters: active and past are separated, a rating shown here is one
    that has actually published, and the page does not go quiet when you have
    nothing on the go.
    """

    def test_active_and_past_are_separated(self):
        ongoing = self.confirmed_placement()

        other_car = self.make_listing()
        finished = Placement.objects.create(
            vehicle_listing=other_car, owner=self.owner, driver=self.driver,
            started_on=date.today() - timedelta(days=200),
            confirmed_by_owner=True, confirmed_by_driver=True,
        )
        finished.end(on=date.today() - timedelta(days=10))

        self.login(self.owner)
        response = self.client.get(reverse("placements:mine"))

        self.assertIn(ongoing, response.context["active"])
        self.assertNotIn(finished, response.context["active"])
        self.assertIn(finished, response.context["past"])

    def test_a_member_with_no_deals_gets_one_empty_state_not_two(self):
        """
        Section headings over nothing read as a page that failed to load, and
        stacking "Nothing on the go" above "Nothing here yet" says the same
        thing twice.
        """
        self.login(self.owner)
        response = self.client.get(reverse("placements:mine"))
        self.assertContains(response, "Nothing here yet")
        self.assertNotContains(response, "Nothing on the go right now")

    def test_history_with_nothing_current_keeps_the_active_heading(self):
        """
        THIS is the case where a missing heading would make somebody wonder
        whether a current deal had dropped off the list.
        """
        finished = self.confirmed_placement()
        finished.end(on=date.today())

        self.login(self.owner)
        response = self.client.get(reverse("placements:mine"))
        self.assertContains(response, "Nothing on the go right now")
        self.assertContains(response, "Past (1)")
        self.assertNotContains(response, "Nothing here yet")

    def test_it_is_called_the_track_record(self):
        self.login(self.owner)
        response = self.client.get(reverse("placements:mine"))
        self.assertContains(response, "Track record")
        self.assertNotContains(response, "<h1 class=\"mb-3\">Placements</h1>")

    def test_an_unpublished_review_is_not_shown(self):
        """
        The double blind is the whole point. A rating visible here before it
        published would be the leak, and it would be invisible to every test
        that only checks the review pages.
        """
        placement = self.confirmed_placement()
        Review.objects.create(
            placement=placement, author=self.driver, subject=self.owner,
            overall=5, communication=5, is_published=False,
        )

        self.login(self.owner)
        response = self.client.get(reverse("placements:mine"))
        row = response.context["active"][0]
        self.assertIsNone(row.their_review_of_me)

    def test_a_published_review_about_you_is_shown(self):
        placement = self.confirmed_placement()
        review = Review.objects.create(
            placement=placement, author=self.driver, subject=self.owner,
            overall=4, communication=4,
        )
        review.publish()

        self.login(self.owner)
        response = self.client.get(reverse("placements:mine"))
        self.assertEqual(response.context["active"][0].their_review_of_me.overall, 4)

    def test_your_own_review_of_them_is_not_what_is_shown(self):
        """`review_by` is the other question. This page shows what was said
        about you, not what you said."""
        placement = self.confirmed_placement()
        mine = Review.objects.create(
            placement=placement, author=self.owner, subject=self.driver,
            overall=2, communication=2,
        )
        mine.publish()

        self.login(self.owner)
        response = self.client.get(reverse("placements:mine"))
        self.assertIsNone(response.context["active"][0].their_review_of_me)
