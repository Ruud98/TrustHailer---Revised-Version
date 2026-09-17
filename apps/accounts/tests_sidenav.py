"""
The nav rail.

Its whole safety argument is that it duplicates navigation that exists
elsewhere, so it can be dropped below 1280px without anybody losing a
destination. These tests hold that argument up.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from django.urls import reverse

# The shared world already builds a fully onboarded member. Building one by
# hand here would mean re-deriving what OnboardingMiddleware considers
# finished, and getting it wrong shows up as a 302 rather than a clear failure.
from apps.listings.tests import ListingTestCase


class SideNavTests(ListingTestCase):
    def test_signed_in_gets_their_own_sections(self):
        self.login(self.owner)
        response = self.client.get(reverse("home"))

        self.assertContains(response, "siderail--right")
        for url in [reverse("listings:browse"), reverse("drivers:browse"),
                    reverse("intros:inbox"), reverse("notifications:inbox"),
                    reverse("listings:mine"), reverse("accounts:me"),
                    reverse("listings:create")]:
            self.assertContains(response, f'href="{url}"')

    def test_signed_out_gets_the_public_set_and_no_account_links(self):
        response = self.client.get(reverse("home"))

        self.assertContains(response, "siderail--right")
        self.assertContains(response, f'href="{reverse("listings:browse")}"')
        self.assertContains(response, f'href="{reverse("safety")}"')
        # Nothing that needs an account, and no second "Get started" competing
        # with the one already in the column.
        self.assertNotContains(response, f'href="{reverse("notifications:inbox")}"')
        self.assertNotContains(response, f'href="{reverse("listings:create")}"')

    def test_every_nav_link_exists_somewhere_else(self):
        """
        The rail is display:none below 1280px, so a destination reachable ONLY
        from here would vanish for every phone. Each one has to also be in the
        avatar menu, which is what this checks.
        """
        import re

        self.login(self.owner)
        page = self.client.get(reverse("home")).content.decode()

        nav = page[page.index('siderail--right'):]
        nav = nav[: nav.index("</nav>")]
        nav_links = set(re.findall(r'href="(/[^"]*)"', nav))

        topbar = page[page.index("dropdown-menu"): page.index("siderail--right")]
        menu_links = set(re.findall(r'href="(/[^"]*)"', topbar))

        self.assertTrue(
            nav_links <= menu_links,
            f"Only reachable from the nav rail: {sorted(nav_links - menu_links)}",
        )

    def test_the_nav_follows_you_onto_other_pages(self):
        """
        The rail is in base.html, not in the two templates that opt into the
        promo rail. If it drifts back to being per-page, the "you are here"
        highlight becomes unreachable — it can only ever show on a page the
        rail is present on.
        """
        self.login(self.owner)
        for url in [reverse("home"), reverse("listings:browse"),
                    reverse("drivers:browse"), reverse("accounts:me")]:
            with self.subTest(url=url):
                # follow=True because /me/ is a redirect to the handle URL.
                response = self.client.get(url, follow=True)
                self.assertContains(response, "siderail--right")

    def test_the_promo_rail_does_not_follow_you(self):
        """Adverts beside a listing is the case this whole thing opts out of."""
        self.login(self.owner)
        self.assertNotContains(
            self.client.get(reverse("listings:browse")), "siderail--left"
        )

    def test_no_nav_while_onboarding(self):
        """
        Every link would be bounced straight back to the wizard by
        OnboardingMiddleware, so there is nothing useful to show.
        """
        half_done = self._make_user("half@example.com", "Half Done")
        half_done.profile.onboarding_completed_at = None
        half_done.profile.save()

        self.login(half_done)
        response = self.client.get(reverse("accounts:onboarding"))
        self.assertNotContains(response, "siderail--right")


class RailVisibilityTests(SimpleTestCase):
    """
    The rails are hidden below 1280px by `.siderail { display: none }` and
    switched on inside one media query. Any top-level rule that also sets
    `display` on a rail sits later in the file at equal specificity, wins, and
    leaves the rail on screen at every width — which is what `.sidenav`
    originally did.

    Checked by reading the stylesheet because the cascade is the thing being
    asserted, and Django's test client never evaluates CSS.
    """

    # `.siderail` / `.sidenav` themselves, not their BEM children — a rule like
    # `.sidenav__sprite { display: none }` is hiding the icon definitions and
    # has nothing to do with whether the rail is on screen.
    RAIL_ROOT = re.compile(r"\.side(?:rail|nav)(?![\w-])")

    def test_no_top_level_rule_sets_display_on_a_rail(self):
        css = (Path(settings.BASE_DIR) / "static" / "css" / "app.css").read_text(
            encoding="utf-8"
        )

        for selector, body in self.top_level_rules(css):
            if not self.RAIL_ROOT.search(selector):
                continue
            declarations = body.replace(" ", "")
            # The one legitimate top-level display: the rule that does the
            # hiding in the first place.
            if selector == ".siderail" and declarations == "display:none;":
                continue
            self.assertNotIn(
                "display:", declarations,
                f"`{selector}` sets display outside the media query; it will beat "
                f"`.siderail {{ display: none }}` and show at every width.",
            )

    @staticmethod
    def top_level_rules(css):
        """Yield (selector, body) for rules that are not inside an @media block."""
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        depth, buffer, out = 0, "", []
        for char in css:
            if char == "{":
                depth += 1
                if depth == 1:
                    selector, buffer = buffer.strip(), ""
                    continue
            elif char == "}":
                depth -= 1
                if depth == 0:
                    # A nested block means `selector` was an at-rule, not a rule.
                    if "@" not in selector:
                        out.append((selector, buffer))
                    buffer = ""
                    continue
            if depth <= 1:
                buffer += char
        return out


class LogoutReachabilityTests(ListingTestCase):
    """
    The way out was the seventeenth and last item in a seventeen-item menu,
    which is findable in the sense that a needle in a haystack is findable.
    These pin it to the three places somebody actually looks.
    """

    def setUp(self):
        super().setUp()
        self.login(self.owner)
        self.logout_url = reverse("accounts:logout")

    def test_the_avatar_menu_offers_it(self):
        page = self.client.get(reverse("home")).content.decode()
        menu = page[page.index("dropdown-menu"):]
        menu = menu[: menu.index("</ul>")]
        self.assertIn(self.logout_url, menu)
        # Styled as its own kind of thing, not a seventeenth grey row.
        self.assertIn("dropdown-item--logout", menu)

    def test_the_settings_page_offers_it(self):
        """
        The second place people look, and the first on a phone — a menu you
        have to open is a menu you have to know about.
        """
        self.assertContains(
            self.client.get(reverse("accounts:settings")), self.logout_url
        )

    def test_the_nav_rail_offers_it(self):
        page = self.client.get(reverse("home")).content.decode()
        rail = page[page.index("siderail--right"):]
        rail = rail[: rail.index("</nav>")]
        self.assertIn(self.logout_url, rail)

    def test_logging_out_is_a_post_everywhere_it_appears(self):
        """
        A GET logout means any <img> or link on any page can sign somebody out,
        and a prefetching browser can do it without anybody clicking.
        """
        self.assertEqual(self.client.get(self.logout_url).status_code, 405)

    def test_it_actually_logs_you_out(self):
        """
        Asserted on the session rather than the markup: the nav rail renders
        for signed-out visitors too, as its public variant, so its absence is
        not what "logged out" means.
        """
        self.client.post(self.logout_url)
        self.assertNotIn("_auth_user_id", self.client.session)

        page = self.client.get(reverse("home")).content.decode()
        self.assertNotIn("sidenav__link--me", page)   # your own profile row
        self.assertNotIn(self.logout_url, page)
