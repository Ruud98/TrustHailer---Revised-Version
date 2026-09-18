"""
Tests for the feed.

The rules worth defending: contact details never publish in a post or a
comment, a blocked person's posts disappear from the feed in both directions,
comment nesting never goes past one level however it is attacked, and a hidden
post behaves as if it does not exist to anyone but staff.
"""
import os

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.listings.models import VehicleListing
from apps.listings.tests import AUTH_BACKEND, ListingTestCase, upload
from apps.safety.models import Block

from . import reactions
from .models import Comment, Post, PostImage, Reaction


class FeedTestCase(ListingTestCase):
    def make_post(self, author=None, images=0, **overrides):
        defaults = dict(body="Anyone know a good tyre place near Soweto?", topic=Post.Topic.ADVICE)
        defaults.update(overrides)
        post = Post.objects.create(author=author or self.owner, **defaults)
        for position in range(images):
            photo = PostImage(post=post, position=position)
            photo.image.save(f"shot{position}.webp", upload(), save=False)
            photo.save()
        return post

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


class ReactionTests(FeedTestCase):
    def react(self, post, kind=None):
        data = {"kind": kind} if kind else {}
        return self.client.post(reverse("feed:react", args=[post.uuid]), data)

    def test_reacting_toggles(self):
        post = self.make_post()
        self.login(self.driver)
        self.react(post)
        self.assertTrue(Reaction.objects.filter(post=post, user=self.driver).exists())
        post.refresh_from_db()
        self.assertEqual(post.reaction_count, 1)

        self.react(post)
        self.assertFalse(Reaction.objects.filter(post=post, user=self.driver).exists())
        post.refresh_from_db()
        self.assertEqual(post.reaction_count, 0)

    def test_a_bare_post_means_like(self):
        """The trigger button with nothing picked yet sends no kind."""
        post = self.make_post()
        self.login(self.driver)
        self.react(post)
        self.assertEqual(
            Reaction.objects.get(post=post, user=self.driver).kind, Reaction.Kind.LIKE
        )

    def test_changing_your_mind_updates_the_row_it_does_not_add_one(self):
        """
        The anti-pile-on rule. Seven presses must leave one row and a count of
        one, or a single person can run a post's number up on their own.
        """
        post = self.make_post()
        self.login(self.driver)
        for kind, _label, _emoji in Reaction.picker_for(post):
            self.react(post, kind)

        self.assertEqual(Reaction.objects.filter(post=post).count(), 1)
        post.refresh_from_db()
        self.assertEqual(post.reaction_count, 1)
        self.assertEqual(
            Reaction.objects.get(post=post).kind, Reaction.Kind.ANGRY
        )

    def test_pressing_the_same_kind_twice_takes_it_off(self):
        post = self.make_post()
        self.login(self.driver)
        self.react(post, Reaction.Kind.LOVE)
        self.react(post, Reaction.Kind.LOVE)

        self.assertFalse(Reaction.objects.filter(post=post).exists())
        post.refresh_from_db()
        self.assertEqual(post.reaction_count, 0)

    def test_a_made_up_kind_falls_back_rather_than_erroring(self):
        post = self.make_post()
        self.login(self.driver)
        self.react(post, "thumbsdown")
        self.assertEqual(
            Reaction.objects.get(post=post).kind, Reaction.Kind.LIKE
        )

    def test_one_reaction_per_person_however_it_is_attacked(self):
        post = self.make_post()
        from django.db.utils import IntegrityError

        Reaction.objects.create(post=post, user=self.driver)
        with self.assertRaises(IntegrityError):
            Reaction.objects.create(
                post=post, user=self.driver, kind=Reaction.Kind.ANGRY
            )

    def test_reacting_needs_a_login(self):
        post = self.make_post()
        self.assertEqual(self.react(post).status_code, 302)

    def test_the_count_is_people_not_presses(self):
        post = self.make_post()
        Reaction.objects.create(post=post, user=self.owner, kind=Reaction.Kind.ANGRY)
        Reaction.objects.create(post=post, user=self.driver, kind=Reaction.Kind.HAHA)
        post.recount()
        post.refresh_from_db()
        self.assertEqual(post.reaction_count, 2)


class ReactionSummaryTests(FeedTestCase):
    """The two-query summary in apps/feed/reactions.py."""

    def test_the_whole_page_costs_two_queries_whatever_the_post_count(self):
        from apps.feed import reactions

        posts = [self.make_post() for _ in range(6)]
        for post in posts:
            Reaction.objects.create(post=post, user=self.owner, kind=Reaction.Kind.WOW)

        self.login(self.driver)
        response = self.client.get(reverse("feed:feed"))
        user = response.wsgi_request.user

        with self.assertNumQueries(2):
            reactions.for_posts(posts, user)

    def test_the_summary_is_ordered_by_popularity_and_capped(self):
        from apps.feed import reactions

        post = self.make_post()
        people = [
            self._make_user(f"r{i}@example.com", f"Person {i}") for i in range(5)
        ]
        # Three wow, two haha, one sad — four distinct kinds would exceed the cap.
        for person in people[:3]:
            Reaction.objects.create(post=post, user=person, kind=Reaction.Kind.WOW)
        for person in people[3:5]:
            Reaction.objects.create(post=post, user=person, kind=Reaction.Kind.HAHA)
        Reaction.objects.create(post=post, user=self.owner, kind=Reaction.Kind.SAD)

        top = reactions.for_post(post, self.driver)["top"]
        self.assertEqual([kind for kind, _emoji, _n in top], ["wow", "haha", "sad"])
        self.assertEqual([n for _kind, _emoji, n in top], [3, 2, 1])

    def test_your_own_reaction_comes_back_as_mine(self):
        from apps.feed import reactions

        post = self.make_post()
        Reaction.objects.create(post=post, user=self.driver, kind=Reaction.Kind.CARE)
        Reaction.objects.create(post=post, user=self.owner, kind=Reaction.Kind.ANGRY)

        self.assertEqual(reactions.for_post(post, self.driver)["mine"], "care")
        self.assertEqual(reactions.for_post(post, self.owner)["mine"], "angry")

    def test_a_post_nobody_touched_is_simply_absent(self):
        from apps.feed import reactions

        post = self.make_post()
        self.assertEqual(reactions.for_posts([post], self.driver), {})
        self.assertEqual(
            reactions.for_post(post, self.driver),
            {"mine": None, "top": [], "total": 0},
        )


class CommentReactionTests(FeedTestCase):
    """
    The same seven reactions, on a comment. Facebook has no dislike and neither
    does this — see Reaction.Kind.
    """

    def setUp(self):
        super().setUp()
        self.post = self.make_post()
        self.comment = Comment.objects.create(
            post=self.post, author=self.owner, body="Try Mbare Auto."
        )

    def react(self, comment=None, kind=None):
        data = {"kind": kind} if kind else {}
        return self.client.post(
            reverse("feed:react_comment", args=[(comment or self.comment).pk]), data
        )

    def test_reacting_to_a_comment_toggles(self):
        self.login(self.driver)
        self.react()
        self.assertTrue(
            Reaction.objects.filter(comment=self.comment, user=self.driver).exists()
        )
        self.react()
        self.assertFalse(Reaction.objects.filter(comment=self.comment).exists())

    def test_changing_your_mind_updates_the_row_it_does_not_add_one(self):
        self.login(self.driver)
        for kind, _label, _emoji in Reaction.picker_for(self.comment):
            self.react(kind=kind)

        self.assertEqual(Reaction.objects.filter(comment=self.comment).count(), 1)
        self.assertEqual(
            Reaction.objects.get(comment=self.comment).kind, Reaction.Kind.ANGRY
        )

    def test_one_reaction_per_person_per_comment_however_it_is_attacked(self):
        from django.db.utils import IntegrityError

        Reaction.objects.create(comment=self.comment, user=self.driver)
        with self.assertRaises(IntegrityError):
            Reaction.objects.create(
                comment=self.comment, user=self.driver, kind=Reaction.Kind.WOW
            )

    def test_comment_reactions_do_not_touch_the_post_count(self):
        """
        `post` is null on a comment reaction, which is what keeps every
        per-post query correct without remembering to exclude them.
        """
        self.login(self.driver)
        self.react(kind=Reaction.Kind.LOVE)

        self.post.refresh_from_db()
        self.assertEqual(self.post.reaction_count, 0)
        self.post.recount()
        self.post.refresh_from_db()
        self.assertEqual(self.post.reaction_count, 0)

    def test_post_and_comment_reactions_are_counted_separately(self):
        self.login(self.driver)
        self.client.post(reverse("feed:react", args=[self.post.uuid]))
        self.react(kind=Reaction.Kind.HAHA)

        self.post.refresh_from_db()
        self.assertEqual(self.post.reaction_count, 1)
        self.assertEqual(
            reactions.for_comment(self.comment, self.driver)["total"], 1
        )
        self.assertEqual(
            reactions.for_post(self.post, self.driver)["mine"], Reaction.Kind.LIKE
        )
        self.assertEqual(
            reactions.for_comment(self.comment, self.driver)["mine"], Reaction.Kind.HAHA
        )

    def test_a_reaction_must_target_exactly_one_thing(self):
        from django.db.utils import IntegrityError

        with self.assertRaises(IntegrityError):
            Reaction.objects.create(
                post=self.post, comment=self.comment, user=self.driver
            )

    def test_a_reaction_must_target_something(self):
        from django.db.utils import IntegrityError

        with self.assertRaises(IntegrityError):
            Reaction.objects.create(user=self.driver)

    def test_reacting_needs_a_login(self):
        self.assertEqual(self.react().status_code, 302)

    def test_a_hidden_comment_cannot_be_reacted_to(self):
        self.login(self.driver)
        Comment.objects.filter(pk=self.comment.pk).update(is_hidden=True)
        self.assertEqual(self.react().status_code, 404)
        self.assertFalse(Reaction.objects.filter(comment=self.comment).exists())

    def test_a_blocked_author_hides_their_comment_from_reacting(self):
        from apps.safety.models import Block

        Block.objects.create(user=self.owner, blocked_user=self.driver)
        self.login(self.driver)
        self.assertEqual(self.react().status_code, 404)

    def test_a_whole_thread_costs_two_queries(self):
        people = [self._make_user(f"c{i}@example.com", f"P{i}") for i in range(4)]
        comments = [self.comment]
        for i in range(5):
            comments.append(
                Comment.objects.create(
                    post=self.post, author=self.owner, body=f"Reply {i}"
                )
            )
        for comment in comments:
            for person in people:
                Reaction.objects.create(
                    comment=comment, user=person, kind=Reaction.Kind.WOW
                )

        with self.assertNumQueries(2):
            reactions.for_comments(comments, self.driver)

    def test_the_detail_page_renders_a_bar_per_comment(self):
        child = Comment.objects.create(
            post=self.post, author=self.driver, parent=self.comment, body="Thanks"
        )
        self.login(self.driver)
        page = self.client.get(self.post.get_absolute_url()).content.decode()

        self.assertIn(f'id="reactions-comment-{self.comment.pk}"', page)
        self.assertIn(f'id="reactions-comment-{child.pk}"', page)
        self.assertIn(f'id="reactions-post-{self.post.pk}"', page)

    def test_there_is_no_dislike(self):
        """
        Facebook has never shipped one, and the picker is the only place a
        reaction can come from. If a thumbs-down is ever wanted it is a
        product decision, not something that arrives by accident.
        """
        self.assertNotIn(
            "dislike", [k.value for k in Reaction.Kind]
        )
        self.assertEqual(len(Reaction.picker_for(self.comment)), 7)


class PostTitleTests(FeedTestCase):
    """
    Optional headline, the way a Facebook group post has one and a timeline
    post does not. The cases that matter are the two ends: a post without one
    must be untouched, and a post with one must not become a way round the
    rules the body already follows.
    """

    def compose(self, **overrides):
        data = {"body": "Watch out for this one.", "topic": Post.Topic.SCAM}
        data.update(overrides)
        self.login(self.driver)
        return self.client.post(reverse("feed:create"), data)

    def test_a_post_needs_no_title(self):
        response = self.compose()
        post = Post.objects.get()
        self.assertRedirects(response, post.get_absolute_url())
        self.assertEqual(post.title, "")

    def test_a_title_is_kept_when_given(self):
        self.compose(title="Fake tracker installers on Voortrekker")
        self.assertEqual(
            Post.objects.get().title, "Fake tracker installers on Voortrekker"
        )

    def test_a_phone_number_cannot_hide_in_the_title(self):
        """
        The body rejects contact details. If the headline did not, the rule
        would be decorative -- "Car available 082 555 1234" simply moves up one
        field and publishes.
        """
        response = self.compose(title="Car available 082 555 1234")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Post.objects.exists())
        self.assertContains(response, "phone numbers", status_code=200)

    def test_a_titleless_post_renders_no_heading(self):
        self.compose()
        post = Post.objects.get()
        page = self.client.get(post.get_absolute_url()).content.decode()
        self.assertNotIn("post-detail__title", page)

    def test_a_titled_post_renders_its_heading_on_card_and_detail(self):
        self.compose(title="Roadblock on the R21")
        post = Post.objects.get()

        detail = self.client.get(post.get_absolute_url()).content.decode()
        self.assertIn("post-detail__title", detail)
        self.assertIn("Roadblock on the R21", detail)

        feed = self.client.get(reverse("feed:feed")).content.decode()
        self.assertIn("post-card__title", feed)
        self.assertIn("Roadblock on the R21", feed)

    def test_the_headline_becomes_the_browser_tab_label(self):
        self.compose(title="Roadblock on the R21")
        post = Post.objects.get()
        page = self.client.get(post.get_absolute_url()).content.decode()
        self.assertIn("<title>Roadblock on the R21</title>", page)

    def test_search_matches_a_headline_not_only_a_body(self):
        self.compose(title="Fake tracker installers", body="They take the deposit.")
        response = self.client.get(reverse("search"), {"q": "tracker"})
        self.assertEqual(response.context["post_count"], 1)


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


class NewThisWeekStripTests(FeedTestCase):
    """
    The row of live listings above the posts.

    Its whole job is to stop a quiet feed reading as a dead site, so the case
    that matters most is the one where there are no posts at all.
    """

    def test_the_strip_carries_live_listings(self):
        self.make_listing()
        self.login(self.driver)
        response = self.client.get(reverse("feed:feed"))
        self.assertEqual(
            [item.pk for item in response.context["new_listings"]],
            [VehicleListing.objects.get().pk],
        )

    def test_it_is_there_even_with_an_empty_feed(self):
        self.make_listing()
        self.login(self.driver)
        response = self.client.get(reverse("feed:feed"))
        self.assertEqual(Post.objects.count(), 0)
        self.assertContains(response, "New this week")

    def test_nothing_is_rendered_when_there_is_nothing_to_show(self):
        """A heading over an empty row is worse than no heading."""
        self.login(self.driver)
        response = self.client.get(reverse("feed:feed"))
        self.assertEqual(list(response.context["new_listings"]), [])
        self.assertNotContains(response, "New this week")

    def test_drafts_and_paused_listings_stay_out_of_it(self):
        self.make_listing(status=VehicleListing.Status.DRAFT)
        self.make_listing(status=VehicleListing.Status.PAUSED)
        self.login(self.driver)
        response = self.client.get(reverse("feed:feed"))
        self.assertEqual(list(response.context["new_listings"]), [])

    def test_a_blocked_member_s_car_is_not_shown(self):
        listing = self.make_listing(owner=self.owner)
        Block.objects.create(user=self.driver, blocked_user=self.owner)
        self.login(self.driver)
        response = self.client.get(reverse("feed:feed"))
        self.assertNotIn(listing, list(response.context["new_listings"]))

    def test_the_htmx_partial_does_not_recompute_it(self):
        """
        Filtering swaps #posts only. Paying for the strip on every filter change
        would be work thrown away, and rendering it twice would duplicate it.
        """
        self.make_listing()
        self.login(self.driver)
        response = self.client.get(reverse("feed:feed"), HTTP_HX_REQUEST="true")
        self.assertNotIn("new_listings", response.context)
        self.assertNotContains(response, "New this week")

    def test_each_item_says_which_kind_it_is(self):
        self.make_listing()
        self.login(self.driver)
        response = self.client.get(reverse("feed:feed"))
        self.assertEqual(
            [item.strip_kind for item in response.context["new_listings"]], ["car"]
        )


class PostImageTests(FeedTestCase):
    """
    Several photos per post, in the order they were picked.

    The rules worth defending here: the cap holds, a bad file fails the whole
    submission rather than publishing a post that quietly lost a photo, and
    deleting a post takes its files with it — the last one because the delete
    view calls itself a real delete, and a photo still sitting on disk under a
    guessable URL makes that a lie.
    """

    def post_with_images(self, count):
        self.login(self.driver)
        return self.client.post(
            reverse("feed:create"),
            {
                "body": "Panelbeater quoted me this for the bumper.",
                "topic": Post.Topic.ADVICE,
                "images": [upload(f"shot{i}.jpg") for i in range(count)],
            },
        )

    def test_several_photos_are_kept_in_the_order_they_were_picked(self):
        self.post_with_images(3)
        post = Post.objects.get()
        self.assertEqual(
            [image.position for image in post.images.all()], [0, 1, 2]
        )

    def test_a_post_with_no_photo_still_posts(self):
        """The common case, and the one a required field would have broken."""
        self.login(self.driver)
        response = self.client.post(
            reverse("feed:create"),
            {"body": "Anyone driving Midrand tomorrow?", "topic": Post.Topic.GENERAL},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PostImage.objects.count(), 0)

    def test_the_cap_holds(self):
        response = self.post_with_images(PostImage.MAX_PER_POST + 1)
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"], "images",
            f"That is {PostImage.MAX_PER_POST + 1} photos. "
            f"{PostImage.MAX_PER_POST} is the limit.",
        )
        self.assertFalse(Post.objects.exists())

    def test_one_unreadable_file_takes_the_whole_post_down(self):
        """
        Not "publish the good ones and say nothing". A post that goes out
        missing the photo that was the point of it is worse than one that
        comes back and says which file was the problem.
        """
        self.login(self.driver)
        response = self.client.post(
            reverse("feed:create"),
            {
                "body": "Two of these are real photographs.",
                "topic": Post.Topic.ADVICE,
                "images": [
                    upload("good.jpg"),
                    SimpleUploadedFile("bad.jpg", b"not an image",
                                       content_type="image/jpeg"),
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Post.objects.exists())
        self.assertFalse(PostImage.objects.exists())

    def test_the_photos_are_re_encoded_not_stored_as_uploaded(self):
        """WebP, because EXIF on a photo taken at home is the owner's address."""
        self.post_with_images(1)
        image = PostImage.objects.get()
        self.assertTrue(image.image.name.endswith(".webp"))

    def test_deleting_a_post_deletes_its_files(self):
        self.post_with_images(2)
        post = Post.objects.get()
        paths = [image.image.path for image in post.images.all()]
        self.assertTrue(all(os.path.exists(path) for path in paths))

        self.client.post(reverse("feed:delete", args=[post.uuid]))

        self.assertFalse(Post.objects.exists())
        self.assertFalse(PostImage.objects.exists())
        self.assertFalse(any(os.path.exists(path) for path in paths))

    def test_a_feed_page_does_not_query_once_per_post_for_photos(self):
        """
        The prefetch on `for_feed`. Rendering four posts with photos has to cost
        the same number of queries as rendering one — without the prefetch it is
        one extra round trip per card, and a full page is fifteen of them.

        Measured as a comparison rather than against a fixed number, so this
        keeps testing the thing it is named for when the feed's query count
        changes for some unrelated reason.
        """
        self.login(self.driver)
        self.make_post(images=1)
        # One throwaway request first: the very first page load of a session
        # also fetches the session row and the user, and counting those once
        # would make the comparison below read as a difference in the feed.
        self.client.get(reverse("feed:feed"))
        with CaptureQueriesContext(connection) as one_post:
            self.client.get(reverse("feed:feed"))

        for _ in range(3):
            self.make_post(images=1)
        with CaptureQueriesContext(connection) as four_posts:
            self.client.get(reverse("feed:feed"))

        self.assertEqual(len(four_posts), len(one_post))

    def test_the_gallery_only_offers_arrows_when_there_is_more_than_one(self):
        self.post_with_images(1)
        single = Post.objects.get()
        response = self.client.get(single.get_absolute_url())
        self.assertNotContains(response, "data-gallery-next")

        Post.objects.all().delete()
        self.post_with_images(2)
        several = Post.objects.get()
        response = self.client.get(several.get_absolute_url())
        self.assertContains(response, "data-gallery-next")
