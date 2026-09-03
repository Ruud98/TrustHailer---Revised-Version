"""
Tests for notifications.

The load-bearing rule is that every existing trigger point actually raises one
— an intro requested, approved, or declined; a placement recorded or
confirmed; a review published, at write time and off the 14-day timer; a
comment or reply; a claim decided; a business verified. The second rule worth
protecting is that nobody is ever notified about their own action.
"""
from datetime import timedelta

from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.feed.models import Comment, Post
from apps.intros.models import IntroRequest
from apps.listings.models import DriverListing, ListingClaim, VehicleListing
from apps.listings.tests import AUTH_BACKEND, ListingTestCase
from apps.placements.models import Placement, Review

from .models import Notification
from .services import notify


class NotifyServiceTests(ListingTestCase):
    def test_it_creates_a_notification(self):
        n = notify(self.driver, Notification.Kind.POST_COMMENT, "Hello", url="/x/")
        self.assertIsNotNone(n)
        self.assertEqual(n.recipient, self.driver)
        self.assertFalse(n.is_read)

    def test_nobody_is_notified_about_their_own_action(self):
        n = notify(self.driver, Notification.Kind.POST_COMMENT, "Hello", actor=self.driver)
        self.assertIsNone(n)
        self.assertFalse(Notification.objects.exists())

    def test_no_recipient_is_a_silent_no_op(self):
        n = notify(None, Notification.Kind.POST_COMMENT, "Hello")
        self.assertIsNone(n)


class IntroNotificationTests(ListingTestCase):
    def setUp(self):
        super().setUp()
        self.car = self.make_listing()

    def test_requesting_notifies_the_owner(self):
        self.login(self.driver)
        self.client.post(
            reverse("intros:create") + f"?car={self.car.uuid}",
            {"message": "Hi, I have four years on Uber and want this car."},
        )
        n = Notification.objects.get(recipient=self.owner)
        self.assertEqual(n.kind, Notification.Kind.INTRO_REQUESTED)
        self.assertEqual(n.actor, self.driver)
        self.assertIn("Driver", n.message)
        self.assertIn(self.car.title, n.message)

    def test_approving_notifies_the_asker(self):
        intro = IntroRequest.objects.create(
            from_user=self.driver, to_user=self.owner, vehicle_listing=self.car,
            message="Interested in this one please.",
        )
        self.login(self.owner)
        self.client.post(reverse("intros:approve", args=[intro.uuid]))
        n = Notification.objects.get(recipient=self.driver, kind=Notification.Kind.INTRO_APPROVED)
        self.assertEqual(n.actor, self.owner)

    def test_declining_notifies_the_asker(self):
        intro = IntroRequest.objects.create(
            from_user=self.driver, to_user=self.owner, vehicle_listing=self.car,
            message="Interested in this one please.",
        )
        self.login(self.owner)
        self.client.post(reverse("intros:decline", args=[intro.uuid]))
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.driver, kind=Notification.Kind.INTRO_DECLINED
            ).exists()
        )

    def test_withdrawing_notifies_nobody(self):
        """Nobody was promised anything about a withdrawal — see the intros app."""
        intro = IntroRequest.objects.create(
            from_user=self.driver, to_user=self.owner, vehicle_listing=self.car,
            message="Interested in this one please.",
        )
        self.login(self.driver)
        self.client.post(reverse("intros:withdraw", args=[intro.uuid]))
        self.assertFalse(Notification.objects.exists())


class PlacementNotificationTests(ListingTestCase):
    def setUp(self):
        super().setUp()
        self.car = self.make_listing()
        self.intro = IntroRequest.objects.create(
            from_user=self.driver, to_user=self.owner, vehicle_listing=self.car,
            message="Would like to drive this one please.",
        )
        self.intro.approve(by=self.owner)

    def test_recording_a_placement_notifies_the_other_side(self):
        self.login(self.owner)
        self.client.post(
            reverse("placements:create", args=[self.car.uuid]),
            {"intro": self.intro.pk, "started_on": timezone.localdate().isoformat()},
        )
        n = Notification.objects.get(
            recipient=self.driver, kind=Notification.Kind.PLACEMENT_CONFIRM
        )
        self.assertEqual(n.actor, self.owner)

    def test_confirming_notifies_only_when_both_sides_have_confirmed(self):
        placement = Placement.objects.create(
            vehicle_listing=self.car, owner=self.owner, driver=self.driver,
            intro=self.intro, started_on=timezone.localdate(), confirmed_by_owner=True,
        )
        self.login(self.driver)
        self.client.post(reverse("placements:confirm", args=[placement.uuid]))

        n = Notification.objects.get(kind=Notification.Kind.PLACEMENT_CONFIRMED)
        # The owner already created it and is looking at their own screen —
        # the notification goes to whoever did NOT just act.
        self.assertEqual(n.recipient, self.owner)
        self.assertEqual(n.actor, self.driver)

    def test_a_single_sided_confirm_notifies_nobody_yet(self):
        placement = Placement.objects.create(
            vehicle_listing=self.car, owner=self.owner, driver=self.driver,
            intro=self.intro, started_on=timezone.localdate(),
        )
        self.login(self.owner)
        self.client.post(reverse("placements:confirm", args=[placement.uuid]))
        self.assertFalse(
            Notification.objects.filter(kind=Notification.Kind.PLACEMENT_CONFIRMED).exists()
        )


class ReviewNotificationTests(ListingTestCase):
    def setUp(self):
        super().setUp()
        self.car = self.make_listing()
        self.placement = Placement.objects.create(
            vehicle_listing=self.car, owner=self.owner, driver=self.driver,
            started_on=timezone.localdate() - timedelta(days=30),
            confirmed_by_owner=True, confirmed_by_driver=True,
        )

    def write(self, author, **overrides):
        payload = {"overall": 5, "communication": 5, "body": "Good to deal with."}
        payload.update(overrides)
        self.login(author)
        return self.client.post(
            reverse("placements:review", args=[self.placement.uuid]), payload
        )

    def test_the_first_review_notifies_nobody_it_is_sealed(self):
        self.write(self.owner)
        self.assertFalse(
            Notification.objects.filter(kind=Notification.Kind.REVIEW_PUBLISHED).exists()
        )

    def test_the_second_review_notifies_both_subjects_at_once(self):
        self.write(self.owner)
        self.write(self.driver)

        notifications = Notification.objects.filter(kind=Notification.Kind.REVIEW_PUBLISHED)
        self.assertEqual(notifications.count(), 2)
        recipients = set(notifications.values_list("recipient_id", flat=True))
        self.assertEqual(recipients, {self.owner.pk, self.driver.pk})

    def test_the_14_day_timer_notifies_too(self):
        """
        This is the path with nobody watching — the recipient has no other
        way of finding out a review appeared than this notification.
        """
        self.write(self.owner)
        review = Review.objects.get()
        Review.objects.filter(pk=review.pk).update(
            created_at=timezone.now() - timedelta(days=Review.BLIND_DAYS + 1)
        )
        call_command("publish_reviews", verbosity=0)

        n = Notification.objects.get(kind=Notification.Kind.REVIEW_PUBLISHED)
        self.assertEqual(n.recipient, self.driver)
        self.assertEqual(n.actor, self.owner)


class FeedNotificationTests(ListingTestCase):
    def test_a_top_level_comment_notifies_the_post_author(self):
        post = Post.objects.create(author=self.owner, body="Anyone know a good tyre place?")
        self.login(self.driver)
        self.client.post(
            reverse("feed:comment", args=[post.uuid]),
            {"body": "Try the one on Main Road, very fair."},
        )
        n = Notification.objects.get(kind=Notification.Kind.POST_COMMENT)
        self.assertEqual(n.recipient, self.owner)
        self.assertEqual(n.actor, self.driver)

    def test_a_reply_notifies_the_comment_author_not_the_post_author(self):
        post = Post.objects.create(author=self.owner, body="Anyone know a good tyre place?")
        top = Comment.objects.create(post=post, author=self.owner, body="Try Speedy Tyres.")
        self.login(self.driver)
        self.client.post(
            reverse("feed:comment", args=[post.uuid]),
            {"body": "Thanks, will do.", "parent": top.pk},
        )
        notifications = list(Notification.objects.filter(kind=Notification.Kind.COMMENT_REPLY))
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].recipient, self.owner)

    def test_commenting_on_your_own_post_notifies_nobody(self):
        post = Post.objects.create(author=self.owner, body="Talking to myself here.")
        self.login(self.owner)
        self.client.post(
            reverse("feed:comment", args=[post.uuid]), {"body": "Adding more context."}
        )
        self.assertFalse(Notification.objects.exists())


class ClaimNotificationTests(ListingTestCase):
    def make_import(self):
        return VehicleListing.objects.create(
            owner=None, make="Nissan", model="Almera", year=2018,
            transmission="manual", arrangement="weekly", weekly_rate=2300,
            suburb=self.soweto, status=VehicleListing.Status.ACTIVE,
            source=VehicleListing.Source.FACEBOOK,
            source_url="https://www.facebook.com/groups/1/posts/2/",
        )

    def test_approving_a_claim_notifies_the_claimant(self):
        listing = self.make_import()
        claim = ListingClaim.objects.create(listing=listing, claimant=self.driver)
        claim.approve(by=self.owner)
        n = Notification.objects.get(kind=Notification.Kind.CLAIM_APPROVED)
        self.assertEqual(n.recipient, self.driver)

    def test_rejecting_a_claim_notifies_the_claimant(self):
        listing = self.make_import()
        claim = ListingClaim.objects.create(listing=listing, claimant=self.driver)
        claim.reject(by=self.owner, reason="Could not match you to the post.")
        n = Notification.objects.get(kind=Notification.Kind.CLAIM_REJECTED)
        self.assertEqual(n.recipient, self.driver)


class InboxTests(ListingTestCase):
    def test_the_inbox_lists_notifications_newest_first(self):
        older = notify(self.owner, Notification.Kind.POST_COMMENT, "Older one")
        newer = notify(self.owner, Notification.Kind.POST_COMMENT, "Newer one")
        self.login(self.owner)
        response = self.client.get(reverse("notifications:inbox"))
        self.assertEqual(list(response.context["page"].object_list), [newer, older])

    def test_opening_the_inbox_marks_everything_read(self):
        notify(self.owner, Notification.Kind.POST_COMMENT, "Unread")
        self.login(self.owner)
        self.client.get(reverse("notifications:inbox"))
        self.assertFalse(Notification.objects.filter(recipient=self.owner, is_read=False).exists())

    def test_the_page_still_shows_the_unread_mark_for_this_one_load(self):
        n = notify(self.owner, Notification.Kind.POST_COMMENT, "Unread")
        self.login(self.owner)
        response = self.client.get(reverse("notifications:inbox"))
        self.assertIn(n.pk, response.context["unread_ids"])

    def test_you_only_see_your_own_notifications(self):
        notify(self.driver, Notification.Kind.POST_COMMENT, "Not yours")
        self.login(self.owner)
        response = self.client.get(reverse("notifications:inbox"))
        self.assertEqual(len(response.context["page"].object_list), 0)


class BadgeTests(ListingTestCase):
    def test_the_unread_count_appears_on_every_page(self):
        notify(self.owner, Notification.Kind.POST_COMMENT, "One")
        notify(self.owner, Notification.Kind.POST_COMMENT, "Two")
        self.login(self.owner)
        response = self.client.get(reverse("home"))
        self.assertEqual(response.context["unread_notification_count"], 2)

    def test_reading_the_inbox_clears_the_badge_on_the_next_page(self):
        notify(self.owner, Notification.Kind.POST_COMMENT, "One")
        self.login(self.owner)
        self.client.get(reverse("notifications:inbox"))
        response = self.client.get(reverse("home"))
        self.assertEqual(response.context["unread_notification_count"], 0)

    def test_logged_out_gets_no_count_at_all(self):
        response = self.client.get(reverse("home"))
        self.assertNotIn("unread_notification_count", response.context)
