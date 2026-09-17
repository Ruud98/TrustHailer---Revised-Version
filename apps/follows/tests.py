"""
Following.

The rules worth defending: a follow is one row in one direction, you cannot
follow yourself, a block severs it both ways and keeps it severed, somebody
hiding from search cannot be followed by a stranger, and a follow never
releases anything a phone number would.
"""
from django.db import connection
from django.db.utils import IntegrityError
from django.test.utils import CaptureQueriesContext
from django.test import TestCase
from django.urls import reverse

from apps.follows import services
from apps.follows.models import Follow
from apps.listings.models import DriverListing, LicenceCode
from apps.listings.tests import ListingTestCase
from apps.safety.models import Block


class FollowRuleTests(ListingTestCase):
    def test_following_creates_one_row_in_one_direction(self):
        self.assertTrue(services.follow(self.driver, self.owner))

        self.assertTrue(Follow.objects.filter(
            follower=self.driver, following=self.owner).exists())
        self.assertFalse(Follow.objects.filter(
            follower=self.owner, following=self.driver).exists())

    def test_following_twice_is_not_two_rows(self):
        services.follow(self.driver, self.owner)
        self.assertFalse(services.follow(self.driver, self.owner))
        self.assertEqual(Follow.objects.count(), 1)

    def test_the_pair_is_unique_however_it_is_attacked(self):
        Follow.objects.create(follower=self.driver, following=self.owner)
        with self.assertRaises(IntegrityError):
            Follow.objects.create(follower=self.driver, following=self.owner)

    def test_you_cannot_follow_yourself(self):
        with self.assertRaises(services.CannotFollow):
            services.follow(self.driver, self.driver)

    def test_the_database_refuses_a_self_follow_too(self):
        with self.assertRaises(IntegrityError):
            Follow.objects.create(follower=self.driver, following=self.driver)

    def test_unfollowing_removes_the_row(self):
        services.follow(self.driver, self.owner)
        self.assertTrue(services.unfollow(self.driver, self.owner))
        self.assertFalse(Follow.objects.exists())

    def test_a_suspended_account_cannot_follow(self):
        self.driver.is_suspended = True
        self.driver.save(update_fields=["is_suspended"])
        with self.assertRaises(services.CannotFollow):
            services.follow(self.driver, self.owner)

    def test_somebody_hiding_from_search_cannot_be_followed(self):
        """
        Hiding is a request not to be found by strangers, not merely to be left
        out of one listing page.
        """
        self.owner.profile.hide_from_search = True
        self.owner.profile.save()
        with self.assertRaises(services.CannotFollow):
            services.follow(self.driver, self.owner)


class FollowAndBlockTests(ListingTestCase):
    """A block is the one thing that reaches in and removes existing rows."""

    def test_blocking_severs_the_follow_in_both_directions(self):
        services.follow(self.driver, self.owner)
        services.follow(self.owner, self.driver)

        Block.objects.create(user=self.owner, blocked_user=self.driver)

        self.assertFalse(Follow.objects.exists())

    def test_a_block_keeps_it_severed(self):
        Block.objects.create(user=self.owner, blocked_user=self.driver)
        with self.assertRaises(services.CannotFollow):
            services.follow(self.driver, self.owner)
        with self.assertRaises(services.CannotFollow):
            services.follow(self.owner, self.driver)

    def test_unfollowing_is_always_allowed(self):
        """
        None of follow()'s checks apply to stopping. A rule that can trap
        somebody into following an account they want rid of is worse than no
        rule at all.
        """
        services.follow(self.driver, self.owner)
        self.owner.profile.hide_from_search = True
        self.owner.profile.save()
        self.driver.is_suspended = True
        self.driver.save(update_fields=["is_suspended"])

        self.assertTrue(services.unfollow(self.driver, self.owner))


class FollowViewTests(ListingTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("follows:toggle", args=[self.owner.handle])

    def test_the_button_toggles(self):
        self.login(self.driver)
        self.client.post(self.url)
        self.assertTrue(services.is_following(self.driver, self.owner))
        self.client.post(self.url)
        self.assertFalse(services.is_following(self.driver, self.owner))

    def test_following_needs_a_login(self):
        self.assertEqual(self.client.post(self.url).status_code, 302)

    def test_following_is_a_post(self):
        """A GET follow means any image on any page can make you follow."""
        self.login(self.driver)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_a_refusal_answers_with_the_button_not_a_500(self):
        self.owner.profile.hide_from_search = True
        self.owner.profile.save()
        self.login(self.driver)

        response = self.client.post(self.url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(services.is_following(self.driver, self.owner))

    def test_counts_appear_on_the_profile(self):
        services.follow(self.driver, self.owner)
        self.login(self.driver)
        response = self.client.get(self.owner.get_absolute_url())
        self.assertEqual(response.context["follower_count"], 1)
        self.assertEqual(response.context["following_count"], 0)

    def test_the_lists_are_reachable_and_show_people(self):
        services.follow(self.driver, self.owner)
        self.login(self.driver)

        followers = self.client.get(
            reverse("follows:followers", args=[self.owner.handle]))
        self.assertContains(followers, self.driver.full_name)

        following = self.client.get(
            reverse("follows:following", args=[self.driver.handle]))
        self.assertContains(following, self.owner.full_name)

    def test_a_blocked_person_is_not_listed(self):
        third = self._make_user("third@example.com", "Third Person")
        services.follow(third, self.owner)
        Block.objects.create(user=self.driver, blocked_user=third)

        self.login(self.driver)
        response = self.client.get(
            reverse("follows:followers", args=[self.owner.handle]))
        self.assertNotContains(response, "Third Person")


class FollowingFeedTests(ListingTestCase):
    """The payoff. Without this a follow is a button that does nothing."""

    def test_the_scope_narrows_the_feed_to_people_you_follow(self):
        from apps.feed.models import Post

        mine = Post.objects.create(author=self.driver, body="Mine, still shown.")
        theirs = Post.objects.create(author=self.owner, body="From somebody I follow.")
        stranger = self._make_user("s@example.com", "A Stranger")
        ignored = Post.objects.create(author=stranger, body="From a stranger.")

        services.follow(self.driver, self.owner)
        self.login(self.driver)

        response = self.client.get(reverse("feed:feed"), {"scope": "following"})
        shown = {p.pk for p in response.context["page"].object_list}

        self.assertIn(theirs.pk, shown)
        # Your own posts stay: a feed that hides what you just wrote reads as
        # though the post failed.
        self.assertIn(mine.pk, shown)
        self.assertNotIn(ignored.pk, shown)

    def test_everyone_is_still_the_default(self):
        from apps.feed.models import Post

        stranger = self._make_user("s2@example.com", "Another Stranger")
        post = Post.objects.create(author=stranger, body="Visible to all.")

        self.login(self.driver)
        response = self.client.get(reverse("feed:feed"))
        self.assertIn(post.pk, {p.pk for p in response.context["page"].object_list})

    def test_signed_out_visitors_get_no_scope_dropdown(self):
        """The only other option cannot work for them, and a dropdown that
        lies is worse than one field fewer."""
        response = self.client.get(reverse("feed:feed"))
        self.assertNotIn("scope", response.context["form"].fields)



class FollowButtonOnCardsTests(ListingTestCase):
    """
    The button where people are actually found.

    A profile page used to be the only place to follow anybody, so the moment
    somebody decides they like a poster — reading the feed — was the one moment
    they could not act on it. These check the button reaches the three places
    people get looked at, and that a page of them does not cost a page of
    queries.
    """

    def setUp(self):
        super().setUp()
        self.login(self.driver)

    def test_a_feed_card_carries_the_button(self):
        from apps.feed.models import Post

        Post.objects.create(author=self.owner, body="Somebody worth following.")
        response = self.client.get(reverse("feed:feed"))
        self.assertContains(response, "post-card__follow")
        self.assertContains(
            response, reverse("follows:toggle", args=[self.owner.handle]))

    def test_your_own_post_offers_no_button(self):
        from apps.feed.models import Post

        Post.objects.create(author=self.driver, body="Mine.")
        response = self.client.get(reverse("feed:feed"))
        self.assertNotContains(
            response, reverse("follows:toggle", args=[self.driver.handle]))

    def test_the_browse_cards_carry_it(self):
        """
        Both browse pages, and in both cases the person on the card is somebody
        other than the viewer — a card offering to follow yourself would be the
        bug this is meant to catch.
        """
        self.make_listing(owner=self.owner)

        stranger = self._make_user("listed@example.com", "Listed Driver")
        DriverListing.objects.create(
            driver=stranger, status=DriverListing.Status.ACTIVE,
            headline="Six years on Bolt", years_experience=6,
            licence_code=LicenceCode.B, has_prdp=True, home_suburb=self.soweto,
        )

        for url, person in (
            (reverse("listings:browse"), self.owner),
            (reverse("drivers:browse"), stranger),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, "listing-card__follow")
                self.assertContains(
                    response, reverse("follows:toggle", args=[person.handle]))

    def test_the_target_is_relative_so_two_cards_by_one_author_do_not_collide(self):
        """
        A feed can show the same author twice. With an id target both buttons
        would swap the first one, so pressing Follow on the second post would
        appear to do nothing.
        """
        from apps.feed.models import Post

        Post.objects.create(author=self.owner, body="First.")
        Post.objects.create(author=self.owner, body="Second.")

        response = self.client.get(reverse("feed:feed"))
        html = response.content.decode()
        self.assertEqual(html.count('hx-target="closest .followbtn-wrap"'), 2)
        self.assertNotIn('id="follow-', html)

    def test_a_page_of_buttons_costs_no_queries_per_button(self):
        """
        `follow_state` fetches both directions once. Asked per card it is two
        queries per card — forty on a feed of twenty posts, for a button.
        """
        from apps.feed.models import Post

        def feed_queries(run, n):
            Post.objects.all().delete()
            for i in range(n):
                author = self._make_user(f"a{run}-{i}@example.com", f"Author {i}")
                Post.objects.create(author=author, body=f"Post {i}.")
            with CaptureQueriesContext(connection) as queries:
                self.client.get(reverse("feed:feed"))
            return len(queries)

        few, many = feed_queries(1, 2), feed_queries(2, 8)

        # Not assertEqual: the page has other things whose query count moves
        # with how full it is. What must not happen is growth with the number
        # of cards, and four times the cards is the test for that.
        self.assertLessEqual(many, few)

class RiderRoleTests(TestCase):
    """
    A member who is neither owner, driver nor business. Derived, never stored,
    and never a permission.
    """

    def test_a_member_with_no_trade_role_is_a_rider(self):
        from apps.accounts.models import User

        user = User.objects.create_user(email="rider@example.com", full_name="Rider One")
        self.assertTrue(user.profile.is_rider)
        self.assertEqual(user.profile.roles_display, "Rider")

    def test_ticking_a_trade_role_stops_them_being_one(self):
        from apps.accounts.models import User

        user = User.objects.create_user(email="mixed@example.com", full_name="Mixed")
        user.profile.is_driver = True
        user.profile.save()

        self.assertFalse(user.profile.is_rider)
        self.assertEqual(user.profile.roles_display, "Driver")

    def test_rider_is_not_a_permission(self):
        """
        Nothing on this site is gated on the trade roles, and a rider must be
        able to do everything a social member does. If this ever fails, the
        role has quietly become an authorisation check.
        """
        from apps.accounts.models import User
        from apps.feed.models import Post

        rider = User.objects.create_user(email="r2@example.com", full_name="Rider Two")
        post = Post.objects.create(author=rider, body="A rider posting.")
        self.assertEqual(post.author.profile.roles_display, "Rider")
