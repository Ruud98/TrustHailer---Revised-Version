import re
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from apps.core.middleware import NoStoreStaticMiddleware

SETTINGS_DIR = Path(settings.BASE_DIR) / "config" / "settings"


@override_settings(STATIC_URL="/static/", MEDIA_URL="/media/")
class NoStoreStaticMiddlewareTests(SimpleTestCase):
    """
    Tested as a plain callable rather than through the client, so the result
    does not depend on which settings module ran the suite — it is wired up in
    dev.py and deliberately absent from prod.py.
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = NoStoreStaticMiddleware(lambda request: HttpResponse("x"))

    def response_for(self, path):
        return self.middleware(self.factory.get(path))

    def test_static_files_are_not_stored(self):
        response = self.response_for("/static/css/app.css")
        self.assertEqual(response["Cache-Control"], "no-store, must-revalidate")

    def test_media_files_are_not_stored(self):
        self.assertIn("no-store", self.response_for("/media/promos/x.webp")["Cache-Control"])

    def test_pages_are_left_alone(self):
        """
        Django sets its own cache headers on responses that vary by session or
        carry a CSRF token. Overwriting those to debug a stylesheet would change
        how the app behaves rather than how it is delivered.
        """
        response = self.response_for("/join/")
        self.assertNotIn("Cache-Control", response)

    def test_a_path_merely_containing_static_is_left_alone(self):
        self.assertNotIn("Cache-Control", self.response_for("/u/static-sam/"))


class DevSettingsWiringTests(SimpleTestCase):
    def test_production_does_not_get_the_dev_middleware(self):
        """
        prod.py hashes static filenames, so a stale copy cannot be served and
        the assets are meant to be cached hard — no-store there would re-fetch
        every file on every page view, on connections members pay for.

        Read as source rather than imported: prod.py raises on a missing
        SECRET_KEY by design, and weakening that to make a test convenient
        would cost more than this check is worth.
        """
        source = (SETTINGS_DIR / "prod.py").read_text(encoding="utf-8")
        self.assertNotIn("NoStoreStaticMiddleware", source)

    def test_development_does_get_it(self):
        source = (SETTINGS_DIR / "dev.py").read_text(encoding="utf-8")
        self.assertIn("NoStoreStaticMiddleware", source)


class ButtonResetTests(SimpleTestCase):
    """
    A class that makes a <button> look like text has to reset the button.

    Without `appearance: none` and a border/background/padding reset, the
    browser keeps drawing its own chrome — grey fill, a 1.6px outset border,
    its own padding — and the "link" renders as a fat grey box in the middle of
    a line of text. That is what happened to the comment Reply and Delete
    actions, and no template or view test can see it.
    """

    # Classes that exist to make a <button> read as text or as an icon.
    TEXTUAL = [
        "link-quiet",
        "post-action",
        "comment__action",
        "comment__more",
        "comment__menu-item",
        "compose__send",
        "reactions__pick",
        "reactions__trigger",
        "sidenav__link--logout",
    ]

    def setUp(self):
        self.css = (Path(settings.BASE_DIR) / "static" / "css" / "app.css").read_text(
            encoding="utf-8"
        )

    def declarations_for(self, name):
        """Every declaration block whose selector list mentions `.name`."""
        pattern = re.compile(
            r"([^{}]*\.%s\b[^{}]*)\{([^{}]*)\}" % re.escape(name)
        )
        return "".join(
            body for selector, body in pattern.findall(self.css)
            # Only the plain class, not `.name:hover` / `.name.is-x` variants,
            # which are allowed to add back a background on purpose.
            if re.search(r"\.%s\s*(,|\{|$)" % re.escape(name), selector + "{")
        )

    def test_textual_button_classes_clear_the_native_chrome(self):
        for name in self.TEXTUAL:
            with self.subTest(klass=name):
                body = self.declarations_for(name).replace(" ", "")
                self.assertTrue(body, f".{name} has no rule in app.css")
                self.assertIn("border:0", body, f".{name} keeps the native border")
                self.assertTrue(
                    "background:none" in body or "appearance:none" in body,
                    f".{name} keeps the native background",
                )


class TemplateCommentTests(SimpleTestCase):
    """
    Django's `{# #}` is a SINGLE-LINE comment.

    Spread it over two lines and the closing `#}` is never found on the opening
    line, so the whole thing renders into the page as text. It fails silently:
    the template compiles, the view returns 200, every assertContains still
    passes, and the prose just appears on screen in the middle of a menu. That
    is exactly what happened to the note above the Log out item, and only a
    screenshot caught it.

    Multi-line commentary belongs in `{% comment %}`…`{% endcomment %}`, or —
    as this codebase does everywhere — one `{# … #}` per line.
    """

    def test_no_template_comment_spans_lines(self):
        offenders = []
        for path in (Path(settings.BASE_DIR) / "templates").rglob("*.html"):
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"\{#", text):
                line_end = text.find("\n", match.start())
                close = text.find("#}", match.start())
                if close == -1 or (line_end != -1 and close > line_end):
                    snippet = text[match.start():match.start() + 50].split("\n")[0]
                    offenders.append(f"{path.name}: {snippet}…")

        self.assertEqual(
            offenders, [],
            "These {# #} comments run past their line and will render as page "
            "text:\n  " + "\n  ".join(offenders),
        )


class AccountMenuTests(SimpleTestCase):
    """
    The avatar menu has to be bounded and scrollable.

    Bootstrap's dropdown has no height of its own, so a long one simply runs
    off the bottom of the screen with nothing to scroll. At eighteen items on a
    360x640 phone that put four of them out of reach — including Log out, which
    meant the way out of the site could not be pressed at all.

    Checked by reading the stylesheet: no view test can see a menu that renders
    past the viewport, and no template test can either.
    """

    def setUp(self):
        self.css = (Path(settings.BASE_DIR) / "static" / "css" / "app.css").read_text(
            encoding="utf-8"
        )
        pattern = re.compile(
            r"\.topbar\s+\.dropdown-menu\s*\{([^}]*)\}", re.S
        )
        self.blocks = [m.group(1).replace(" ", "") for m in pattern.finditer(self.css)]

    def test_the_menu_is_bounded_and_scrolls(self):
        self.assertTrue(self.blocks, ".topbar .dropdown-menu has no rule in app.css")
        joined = "".join(self.blocks)
        self.assertIn("max-height:", joined, "the menu has no height bound")
        self.assertIn("overflow-y:auto", joined, "the menu cannot be scrolled")

    def test_the_bound_leaves_room_for_the_fixed_chrome(self):
        """
        A menu that merely fits the viewport still has its last item behind the
        bottom nav, which is fixed over the page. Both bars have to come off
        the height, or the fix only half works.
        """
        first = self.blocks[0]
        self.assertIn("--topbar-h", first, "does not clear the top bar")
        self.assertIn("--bottomnav-h", first, "does not clear the bottom nav")

    def test_it_measures_the_visible_viewport(self):
        """
        `dvh`, not `vh`. A phone's address bar shrinks the visible viewport as
        you scroll and `vh` keeps measuring the tall version — the same bug
        again, a little smaller.
        """
        self.assertIn("dvh", self.blocks[0])
        self.assertNotIn("100vh", self.blocks[0])


class PostTitleTests(SimpleTestCase):
    """
    The headline is inset like everything else on the card.

    `.post-card__body` is a bare block with no padding of its own — every child
    brings its own — and the title was written as though it inherited one, so
    it alone sat flush against the card's left edge.
    """

    def test_the_headline_is_not_flush_with_the_card_edge(self):
        css = (Path(settings.BASE_DIR) / "static" / "css" / "app.css").read_text(
            encoding="utf-8"
        )
        match = re.search(r"\.post-card__title\s*\{(.*?)\}", css, re.S)
        self.assertIsNotNone(match, ".post-card__title has no rule in app.css")
        block = match.group(1)
        self.assertRegex(
            block.replace(" ", ""),
            r"padding:0\s*16px|padding:[^;]*16px",
            "the headline needs the same 16px inset the body text has",
        )


class PostImageTests(SimpleTestCase):
    """
    A feed photo is bounded by the window, not only by the card.

    Capping at the card's width was right for phones and still let one photo
    fill 49% of an 862px-tall desktop window. Half the screen for one picture
    means two posts never fit together, and a feed you can see only one of is a
    feed you scroll rather than read. The `dvh` bound is the one that fixes
    that, so it is the one worth pinning.

    These moved from `.post-card__image` to `.gallery__img` when a post grew
    from one photo to a gallery of up to six, and the inset moved to the feed's
    `--inset` variant when a listing started using the same component. The rule
    being defended did not move: whatever a photo is inside, in the feed it is
    still bounded by the window.
    """

    def setUp(self):
        css = (Path(settings.BASE_DIR) / "static" / "css" / "app.css").read_text(
            encoding="utf-8"
        )
        self.block = self._rule(css, r"\.gallery--inset \.gallery__img")
        self.gallery = self._rule(css, r"\.gallery--inset")

    def _rule(self, css, selector):
        match = re.search(selector + r"\s*\{([^}]*)\}", css, re.S)
        self.assertIsNotNone(match, f"{selector} has no rule in app.css")
        return match.group(1).replace(" ", "")

    def test_it_is_bounded_by_the_visible_window(self):
        self.assertIn("dvh", self.block, "no viewport-relative bound")
        self.assertNotIn("100vh", self.block, "vh mismeasures a phone's viewport")

    def test_it_is_also_bounded_by_the_card(self):
        """The bound that keeps phones sane, where dvh is not the binding one."""
        self.assertIn("cqw", self.block)

    def test_it_is_inset_rather_than_full_bleed(self):
        """
        Edge to edge is what made it read as a slab. The inset used to be cut
        out of the image's own width; it is now padding on the gallery around
        it, because that gutter is also where the arrows sit. Either way the
        photo does not touch the edge of the card.
        """
        self.assertIn("padding:10pxvar(--gallery-gutter,", self.gallery)

    def test_the_gutter_is_wider_than_the_text_inset(self):
        """
        The photo sits further in than the words do — that is what reads as a
        picture placed on the card rather than a picture the card is made of,
        and it is the room the arrows live in. The text is inset 16px.
        """
        gutter = re.search(r"--gallery-gutter,\s*(\d+)px", self.gallery)
        self.assertIsNotNone(gutter, "no default gutter to check")
        self.assertGreater(int(gutter.group(1)), 16)
