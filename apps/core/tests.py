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
