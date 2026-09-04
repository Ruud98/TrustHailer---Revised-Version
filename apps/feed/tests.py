"""
Tests for the feed.

The rules worth defending: contact details never publish in a post or a
comment, a blocked person's posts disappear from the feed in both directions,
comment nesting never goes past one level however it is attacked, and a hidden
post behaves as if it does not exist to anyone but staff.
"""
from django.urls import reverse

from apps.listings.tests import AUTH_BACKEND, ListingTestCase
from apps.safety.models import Block

from .models import Comment, Like, Post


class FeedTestCase(ListingTestCase):
    def make_post(self, author=None, **overrides):
        defaults = dict(body="Anyone know a good tyre place near Soweto?", topic=Post.Topic.ADVICE)
        defaults.update(overrides)
        return Post.objects.create(author=author or self.owner, **defaults)

    def _make_staff(self, email):
        staff = self._make_user(email, "Staff Member", verified=True)
        staff.is_staff = True
        staff.is_superuser = True
        staff.save()
        return staff


class ComposeTests(FeedTestCase):
    def test_a_member_can_post(self):
        self.login(self.driver)
        response = self.client.post(
            reverse("feed:create"),
            {"body": "Roadblock on the R21 heading to the airport this morning.",
             "topic": Post.Topic.ALERT},
        )
        post = Post.objects.get()
        self.assertRedirects(response, post.get_absolute_url())
        self.assertEqual(post.author, self.driver)
        self.assertEqual(post.topic, Post.Topic.ALERT)

    def test_posting_needs_no_phone_verification(self):
        """
        A verification wall in front of the compose box would empty the feed,
        and an empty feed is the one failure this sprint cannot survive.
        """
        unverified = self._make_user("new@example.com", "New Person", verified=False)
        self.login(unverified)
        response = self.client.post(
            reverse("feed:create"),
            {"body": "Just joined, looking forward to it.", "topic": Post.Topic.GENERAL},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Post.objects.exists())

    def test_a_suspended_account_cannot_post(self):
        self.owner.is_suspended = True
        self.owner.save()
        self.login(self.owner)
        self.client.post(
            reverse("feed:create"),
            {"body": "Trying to post anyway.", "topic": Post.Topic.GENERAL},
        )
        self.assertFalse(Post.objects.exists())

    def test_a_phone_number_in_a_post_is_refused(self):
        """
        The most common post in the group this replaces, and the one that
        would hollow out the structured listings side if it were allowed here.
        """
        self.login(self.owner)
        response = self.client.post(
            reverse("feed:create"),
            {"body": "Corolla available for rent, call 082 123 4567.",
             "topic": Post.Topic.GENERAL},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "list it properly")
        self.assertFalse(Post.objects.exists())

    def test_a_one_word_post_is_refused(self):
        self.login(self.owner)
        response = self.client.post(
            reverse("feed:create"), {"body": "hi", "topic": Post.Topic.GENERAL}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Post.objects.exists())

    def test_posting_is_rate_limited(self):
        """
        A feed is only worth reading if one person cannot fill it. The limit
        has to bind through the view — seeding rows directly in the database
        would not touch the counter `hit()` keeps.
        """
        self.login(self.owner)
        for i in range(20):
            self.client.post(
                reverse("feed:create"),
                {"body": f"Post number {i} for the rate limit test today.",
                 "topic": Post.Topic.GENERAL},
            )
        self.assertEqual(Post.objects.count(), 20)

        response = self.client.post(
            reverse("feed:create"),
            {"body": "One more for good measure today.", "topic": Post.Topic.GENERAL},
        )
        self.assertEqual(Post.objects.count(), 20)
        self.assertRedirects(response, reverse("feed:feed"))


class FeedListTests(FeedTestCase):
    def test_the_feed_shows_visible_posts_newest_first(self):
        first = self.make_post(body="Older post about maintenance tips.")
        second = self.make_post(body="Newer post about a scam warning.")
        response = self.client.get(reverse("feed:feed"))
        posts = list(response.context["page"].object_list)
        self.assertEqual(posts, [second, first])

    def test_a_hidden_post_does_not_show(self):
        self.make_post(is_hidden=True)
        response = self.client.get(reverse("feed:feed"))
        self.assertEqual(response.context["page"].paginator.count, 0)

    def test_the_topic_filter_narrows_the_feed(self):
        self.make_post(topic=Post.Topic.SCAM, body="Somebody tried the deposit scam on me.")
        self.make_post(topic=Post.Topic.GENERAL, body="Just saying hello to everyone.")
        response = self.client.get(reverse("feed:feed") + f"?topic={Post.Topic.SCAM}")
        self.assertEqual(response.context["page"].paginator.count, 1)

    def test_the_city_filter_keeps_untagged_posts(self):
        """
        A warning nobody tagged with a city is still worth reading, and
        dropping it would make the filter feel broken the first time anyone
        used it.
        """
        self.make_post(city=self.city, body="Something happened in Johannesburg today.")
        self.make_post(city=None, body="This one applies everywhere honestly.")
        self.make_post(city=self.other_city, body="This one is only about Ekurhuleni.")

        response = self.client.get(reverse("feed:feed") + f"?city={self.city.pk}")
        self.assertEqual(response.context["page"].paginator.count, 2)

    def test_a_blocked_persons_posts_disappear_both_ways(self):
        self.make_post(author=self.owner)
        Block.objects.create(user=self.driver, blocked_user=self.owner)

        self.login(self.driver)
        self.assertEqual(
            self.client.get(reverse("feed:feed")).context["page"].paginator.count, 0
        )
        self.login(self.owner)
        other_post = self.make_post(author=self.driver)
        response = self.client.get(reverse("feed:feed"))
        self.assertNotIn(other_post, response.context["page"].object_list)

    def test_logged_out_still_sees_the_feed(self):
        self.make_post()
        response = self.client.get(reverse("feed:feed"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page"].paginator.count, 1)

    def test_the_home_page_serves_the_feed_when_logged_in(self):
        self.make_post()
        self.login(self.owner)
        response = self.client.get(reverse("home"))
        self.assertTemplateUsed(response, "feed/feed.html")

    def test_infinite_scroll_returns_a_bare_fragment_for_htmx(self):
        """
        The continuation page must not re-wrap itself in `id="posts"`, or the
        sentinel swap nests a second copy of that id inside the first on every
        scroll.
        """
        for i in range(20):
            self.make_post(body=f"Post number {i} for pagination testing today.")
        response = self.client.get(
            reverse("feed:feed") + "?page=2", HTTP_HX_REQUEST="true"
        )
        self.assertTemplateUsed(response, "feed/_posts.html")
        self.assertNotContains(response, 'id="posts"')


class DetailAndCommentTests(FeedTestCase):
    def test_the_detail_page_renders(self):
        post = self.make_post()
        response = self.client.get(post.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, post.body)

    def test_a_hidden_post_404s_for_everyone_but_staff(self):
        post = self.make_post(is_hidden=True)
        self.assertEqual(self.client.get(post.get_absolute_url()).status_code, 404)

        staff = self._make_user("staff6@example.com", "Staff Six", verified=True)
        staff.is_staff = True
        staff.save()
        self.login(staff)
        self.assertEqual(self.client.get(post.get_absolute_url()).status_code, 200)

    def test_a_member_can_comment(self):
        post = self.make_post()
        self.login(self.driver)
        self.client.post(
            reverse("feed:comment", args=[post.uuid]),
            {"body": "The one on Main Road is decent and fair on price."},
        )
        comment = Comment.objects.get()
        self.assertEqual(comment.author, self.driver)
        self.assertEqual(comment.post, post)
        post.refresh_from_db()
        self.assertEqual(post.comment_count, 1)

    def test_a_reply_nests_one_level(self):
        post = self.make_post()
        top = Comment.objects.create(post=post, author=self.owner, body="Try Speedy Tyres.")
        self.login(self.driver)
        self.client.post(
            reverse("feed:comment", args=[post.uuid]),
            {"body": "Thanks, will do.", "parent": top.pk},
        )
        reply = Comment.objects.get(parent=top)
        self.assertEqual(reply.parent, top)

    def test_a_reply_to_a_reply_flattens_to_the_top(self):
        """
        Past one level a group thread on a phone becomes a column of six
        characters. `Comment.save()` re-points anything deeper at the top.
        """
        post = self.make_post()
        top = Comment.objects.create(post=post, author=self.owner, body="Try Speedy Tyres.")
        reply = Comment.objects.create(
            post=post, author=self.driver, parent=top, body="Thanks!"
        )
        grandchild = Comment.objects.create(
            post=post, author=self.owner, parent=reply, body="No problem."
        )
        self.assertEqual(grandchild.parent, top)

    def test_hiding_a_comment_in_the_admin_takes_it_out_of_the_count(self):
        post = self.make_post()
        comment = Comment.objects.create(post=post, author=self.driver, body="One comment.")
        Post.objects.filter(pk=post.pk).update(comment_count=1)

        staff = self._make_staff("staff8@example.com")
        self.client.force_login(staff, backend=AUTH_BACKEND)
        self.client.post(
            reverse("admin:feed_comment_changelist"),
            {"action": "hide_comments", "_selected_action": [comment.pk]},
        )

        post.refresh_from_db()
        self.assertEqual(post.comment_count, 0)

    def test_a_number_in_a_comment_is_refused(self):
        post = self.make_post()
        self.login(self.driver)
        response = self.client.post(
            reverse("feed:comment", args=[post.uuid]),
            {"body": "Call the mechanic on 082 123 4567, he is great."},
        )
        self.assertRedirects(response, post.get_absolute_url())
        self.assertFalse(Comment.objects.exists())

    def test_the_author_can_delete_their_own_comment(self):
        post = self.make_post()
        comment = Comment.objects.create(post=post, author=self.driver, body="Delete me.")
        Post.objects.filter(pk=post.pk).update(comment_count=1)
        self.login(self.driver)
        self.client.post(reverse("feed:delete_comment", args=[comment.pk]))
        self.assertFalse(Comment.objects.exists())
        post.refresh_from_db()
        self.assertEqual(post.comment_count, 0)

    def test_you_cannot_delete_somebody_elses_comment(self):
        post = self.make_post()
        comment = Comment.objects.create(post=post, author=self.driver, body="Not yours.")
        self.login(self.owner)
        response = self.client.post(reverse("feed:delete_comment", args=[comment.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Comment.objects.filter(pk=comment.pk).exists())

    def test_a_blocked_persons_comments_are_hidden_from_the_thread(self):
        post = self.make_post(author=self.owner)
        Comment.objects.create(post=post, author=self.driver, body="A comment from them.")
        Block.objects.create(user=self.owner, blocked_user=self.driver)

        self.login(self.owner)
        response = self.client.get(post.get_absolute_url())
        self.assertNotContains(response, "A comment from them")


class LikeTests(FeedTestCase):
    def test_liking_toggles(self):
        post = self.make_post()
        self.login(self.driver)
        self.client.post(reverse("feed:like", args=[post.uuid]))
        self.assertTrue(Like.objects.filter(post=post, user=self.driver).exists())
        post.refresh_from_db()
        self.assertEqual(post.like_count, 1)

        self.client.post(reverse("feed:like", args=[post.uuid]))
        self.assertFalse(Like.objects.filter(post=post, user=self.driver).exists())
        post.refresh_from_db()
        self.assertEqual(post.like_count, 0)

    def test_one_like_per_person_however_it_is_attacked(self):
        post = self.make_post()
        from django.db.utils import IntegrityError

        Like.objects.create(post=post, user=self.driver)
        with self.assertRaises(IntegrityError):
            Like.objects.create(post=post, user=self.driver)

    def test_liking_needs_a_login(self):
        post = self.make_post()
        response = self.client.post(reverse("feed:like", args=[post.uuid]))
        self.assertEqual(response.status_code, 302)


class DeletionTests(FeedTestCase):
    def test_the_author_can_delete_their_own_post(self):
        post = self.make_post(author=self.driver)
        self.login(self.driver)
        self.client.post(reverse("feed:delete", args=[post.uuid]))
        self.assertFalse(Post.objects.exists())

    def test_nobody_else_can(self):
        post = self.make_post(author=self.driver)
        self.login(self.owner)
        response = self.client.post(reverse("feed:delete", args=[post.uuid]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Post.objects.exists())


class ModerationTests(FeedTestCase):
    def test_hiding_a_post_in_the_admin_keeps_the_row(self):
        post = self.make_post()
        staff = self._make_staff("staff7@example.com")
        self.client.force_login(staff, backend=AUTH_BACKEND)
        self.client.post(
            reverse("admin:feed_post_changelist"),
            {"action": "hide_posts", "_selected_action": [post.pk]},
        )

        post.refresh_from_db()
        self.assertTrue(post.is_hidden)
        self.assertTrue(Post.objects.filter(pk=post.pk).exists())


class SearchIntegrationTests(FeedTestCase):
    def test_a_matching_post_shows_up_in_global_search(self):
        self.make_post(body="Watch out for the deposit scam going around Soweto.")
        response = self.client.get(reverse("search") + "?q=deposit")
        self.assertEqual(response.context["post_count"], 1)

    def test_search_never_shows_a_hidden_post(self):
        self.make_post(body="A hidden scam warning that should not show.", is_hidden=True)
        response = self.client.get(reverse("search") + "?q=scam")
        self.assertEqual(response.context["post_count"], 0)
