"""
Tests for notifications.

The load-bearing rule is that every existing trigger point actually raises one
— an intro requested, approved, or declined; a placement recorded or
confirmed; a review published, at write time and off the 14-day timer; a
comment or reply; a claim decided; a business verified. The second rule worth
protecting is that nobody is ever notified about their own action.
"""
import json
from datetime import timedelta
from unittest import mock

from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.feed.models import Comment, Post
from apps.intros.models import IntroRequest
from apps.listings.models import DriverListing, ListingClaim, VehicleListing
from apps.listings.tests import AUTH_BACKEND, ListingTestCase
from apps.placements.models import Placement, Review

from . import push
from .models import Notification, PushSubscription
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

    def test_answering_a_listing_notifies_the_owner(self):
        """
        Replaces the introduction-requested notification. The owner must hear
        about an answer to their advert by the same route they used to hear
        about a request for their number — an inbox nobody is told about is an
        inbox nobody opens.
        """
        self.login(self.driver)
        self.client.post(
            reverse("messaging:interested", args=["car", self.car.uuid])
        )
        n = Notification.objects.get(recipient=self.owner)
        self.assertEqual(n.kind, Notification.Kind.NEW_MESSAGE)
        self.assertEqual(n.actor, self.driver)

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
        from apps.messaging import services as messaging

        self.thread, _ = messaging.express_interest(self.driver, self.car)

    def test_recording_a_placement_notifies_the_other_side(self):
        self.login(self.owner)
        self.client.post(
            reverse("placements:create", args=[self.car.uuid]),
            {"other": self.driver.pk, "started_on": timezone.localdate().isoformat()},
        )
        n = Notification.objects.get(
            recipient=self.driver, kind=Notification.Kind.PLACEMENT_CONFIRM
        )
        self.assertEqual(n.actor, self.owner)

    def test_confirming_notifies_only_when_both_sides_have_confirmed(self):
        placement = Placement.objects.create(
            vehicle_listing=self.car, owner=self.owner, driver=self.driver,
            started_on=timezone.localdate(), confirmed_by_owner=True,
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
            started_on=timezone.localdate(),
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


class PushSubscriptionTests(ListingTestCase):
    """
    The endpoints pwa.js talks to, and the rule that a push never takes a page
    down with it.
    """

    SUBSCRIPTION = {
        "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
        "keys": {"p256dh": "BPublicKeyBytes", "auth": "AuthSecret"},
    }

    def setUp(self):
        super().setUp()
        self.client.force_login(self.driver, backend=AUTH_BACKEND)
        self.url = reverse("notifications:push_subscribe")

    def post(self, url, payload):
        return self.client.post(
            url, data=json.dumps(payload), content_type="application/json"
        )

    def test_a_browser_can_register_a_subscription(self):
        response = self.post(self.url, self.SUBSCRIPTION)
        self.assertEqual(response.status_code, 200)
        subscription = PushSubscription.objects.get()
        self.assertEqual(subscription.user, self.driver)
        self.assertEqual(subscription.auth, "AuthSecret")

    def test_registering_the_same_endpoint_twice_updates_it(self):
        """
        pwa.js posts on every load once permission is granted, because browsers
        rotate subscriptions on their own. A second post must not be a second
        row, or a busy user accumulates one duplicate push per page view.
        """
        self.post(self.url, self.SUBSCRIPTION)
        rotated = dict(self.SUBSCRIPTION, keys={"p256dh": "BNewKey", "auth": "NewAuth"})
        self.post(self.url, rotated)

        self.assertEqual(PushSubscription.objects.count(), 1)
        self.assertEqual(PushSubscription.objects.get().auth, "NewAuth")

    def test_an_endpoint_moves_to_whoever_is_signed_in_now(self):
        """
        A shared phone. The second person to sign in on this browser gets the
        endpoint, because it is this installation the push service delivers to
        — leaving it with the first person means their messages appear on a
        lock screen somebody else is holding.
        """
        self.post(self.url, self.SUBSCRIPTION)
        self.client.force_login(self.owner, backend=AUTH_BACKEND)
        self.post(self.url, self.SUBSCRIPTION)

        self.assertEqual(PushSubscription.objects.count(), 1)
        self.assertEqual(PushSubscription.objects.get().user, self.owner)

    def test_an_incomplete_subscription_is_refused(self):
        response = self.post(self.url, {"endpoint": "https://example.com/x"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(PushSubscription.objects.exists())

    def test_rubbish_is_refused_rather_than_raising(self):
        response = self.client.post(
            self.url, data="not json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    def test_signing_out_of_push_removes_the_row(self):
        self.post(self.url, self.SUBSCRIPTION)
        response = self.post(
            reverse("notifications:push_unsubscribe"),
            {"endpoint": self.SUBSCRIPTION["endpoint"]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PushSubscription.objects.exists())

    def test_nobody_can_unsubscribe_somebody_else(self):
        """
        An endpoint is not a secret — it travels to the push service and back.
        Deleting by endpoint alone would let anyone holding one switch off
        another person's notifications.
        """
        self.post(self.url, self.SUBSCRIPTION)
        self.client.force_login(self.owner, backend=AUTH_BACKEND)
        self.post(
            reverse("notifications:push_unsubscribe"),
            {"endpoint": self.SUBSCRIPTION["endpoint"]},
        )
        self.assertEqual(PushSubscription.objects.count(), 1)

    def test_a_signed_out_browser_cannot_register(self):
        self.client.logout()
        response = self.post(self.url, self.SUBSCRIPTION)
        self.assertEqual(response.status_code, 302)


class PushSendingTests(ListingTestCase):
    KEYS = {
        "VAPID_PUBLIC_KEY": "BPublicKeyBytes",
        "VAPID_PRIVATE_KEY": "PrivateKeyBytes",
        "VAPID_CONTACT": "mailto:test@example.com",
    }

    def subscribe(self, user, endpoint="https://fcm.googleapis.com/fcm/send/abc"):
        return PushSubscription.objects.create(
            user=user, endpoint=endpoint, p256dh="BKey", auth="Auth"
        )

    @override_settings(VAPID_PUBLIC_KEY="", VAPID_PRIVATE_KEY="")
    def test_nothing_is_sent_when_no_keys_are_configured(self):
        """The state every development box, and any un-configured deploy, is in."""
        self.subscribe(self.driver)
        with mock.patch("pywebpush.webpush") as webpush:
            self.assertEqual(push.send_to_user(self.driver, title="T", body="B"), 0)
        webpush.assert_not_called()

    @override_settings(**KEYS)
    def test_it_sends_to_every_device_a_person_has(self):
        self.subscribe(self.driver, "https://fcm.googleapis.com/fcm/send/phone")
        self.subscribe(self.driver, "https://fcm.googleapis.com/fcm/send/tablet")
        with mock.patch("pywebpush.webpush") as webpush:
            sent = push.send_to_user(self.driver, title="TrustHailer", body="Hello")
        self.assertEqual(sent, 2)
        self.assertEqual(webpush.call_count, 2)

    @override_settings(**KEYS)
    def test_the_payload_carries_the_whole_notification(self):
        """
        The worker must never have to go back to the network to find out what
        to show — a push landing on a phone that has just lost signal is still
        readable.
        """
        self.subscribe(self.driver)
        with mock.patch("pywebpush.webpush") as webpush:
            push.send_to_user(
                self.driver,
                title="TrustHailer",
                body="Thabo asked about your Corolla",
                url="/requests/",
            )
        payload = json.loads(webpush.call_args.kwargs["data"])
        self.assertEqual(payload["body"], "Thabo asked about your Corolla")
        self.assertEqual(payload["url"], "/requests/")

    @override_settings(**KEYS)
    def test_a_dead_subscription_is_deleted(self):
        """
        410 Gone is the only notice we ever get that a browser threw the
        subscription away. Keeping the row means retrying it forever.
        """
        self.subscribe(self.driver)
        from pywebpush import WebPushException

        gone = mock.Mock(status_code=410)
        with mock.patch(
            "pywebpush.webpush", side_effect=WebPushException("gone", response=gone)
        ):
            push.send_to_user(self.driver, title="T", body="B")
        self.assertFalse(PushSubscription.objects.exists())

    @override_settings(**KEYS)
    def test_a_temporary_failure_keeps_the_subscription(self):
        """A push service having a bad minute is not a reason to lose a device."""
        self.subscribe(self.driver)
        from pywebpush import WebPushException

        broken = mock.Mock(status_code=503)
        with mock.patch(
            "pywebpush.webpush", side_effect=WebPushException("later", response=broken)
        ):
            push.send_to_user(self.driver, title="T", body="B")
        self.assertEqual(PushSubscription.objects.count(), 1)

    @override_settings(**KEYS)
    def test_a_failing_push_never_breaks_the_thing_that_raised_it(self):
        """
        The load-bearing rule. Approving an introduction must succeed even if
        every push service on earth is down, and the bell must still be right.
        """
        self.subscribe(self.driver)
        with mock.patch("pywebpush.webpush", side_effect=RuntimeError("boom")):
            notification = notify(self.driver, Notification.Kind.POST_COMMENT, "Hello")
        self.assertIsNotNone(notification)
        self.assertEqual(Notification.objects.count(), 1)

    @override_settings(**KEYS)
    def test_raising_a_notification_pushes_it(self):
        self.subscribe(self.driver)
        with mock.patch("pywebpush.webpush") as webpush:
            notify(
                self.driver,
                Notification.Kind.NEW_MESSAGE,
                "A new message",
                url="/messages/1/",
            )
        payload = json.loads(webpush.call_args.kwargs["data"])
        self.assertEqual(payload["body"], "A new message")
        self.assertEqual(payload["url"], "/messages/1/")

    @override_settings(**KEYS)
    def test_a_suppressed_notification_is_not_pushed(self):
        """Nobody is notified about their own action, and nobody is pushed one."""
        self.subscribe(self.driver)
        with mock.patch("pywebpush.webpush") as webpush:
            notify(self.driver, Notification.Kind.POST_COMMENT, "Hi", actor=self.driver)
        webpush.assert_not_called()
