import importlib
import json
import os
import re
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.http import HttpResponse
from django.templatetags.static import static
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

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
        # Anchored to the start of a line, because the selector also appears
        # inside `.post-card__body:hover .post-card__title` further up the
        # file — an unanchored search finds that hover rule, which carries a
        # colour and no padding, and reports a regression that is not one.
        match = re.search(r"^\.post-card__title\s*\{(.*?)\}", css, re.S | re.M)
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


class AppShellTests(TestCase):
    """
    The three documents that make this installable.

    Every assertion here is something that fails silently in a browser. A
    manifest served as text/html is ignored without a word; a worker served
    from the wrong path registers and then controls nothing; an icon that 404s
    means the install option never appears and nobody can say why. None of it
    produces an error anybody would see, which is exactly why it is tested.
    """

    def test_the_manifest_is_valid_json(self):
        response = self.client.get(reverse("manifest"))
        self.assertEqual(response.status_code, 200)
        json.loads(response.content.decode("utf-8"))

    def test_the_manifest_is_served_as_a_manifest(self):
        """Chrome ignores a manifest that does not claim to be one."""
        response = self.client.get(reverse("manifest"))
        self.assertEqual(response["Content-Type"], "application/manifest+json")

    def test_the_manifest_asks_for_a_standalone_window(self):
        data = json.loads(self.client.get(reverse("manifest")).content)
        self.assertEqual(data["display"], "standalone")
        self.assertEqual(data["scope"], "/")

    def test_the_manifest_has_both_icon_sizes_and_a_maskable_one(self):
        """
        192 and 512 are the two Chrome requires before it will offer an
        install at all, and the maskable one is what stops Android cropping
        the corners off the mark.
        """
        icons = json.loads(self.client.get(reverse("manifest")).content)["icons"]
        sizes = {icon["sizes"] for icon in icons}
        self.assertIn("192x192", sizes)
        self.assertIn("512x512", sizes)
        self.assertIn("maskable", {icon.get("purpose") for icon in icons})

    def test_every_icon_the_manifest_names_exists_on_disk(self):
        """
        The failure this is here for: the manifest shipped for months naming
        icon-192.png and icon-512.png, neither of which had ever been added to
        static/img. Nothing complained, and the app was simply not installable.
        """
        icons = json.loads(self.client.get(reverse("manifest")).content)["icons"]
        for icon in icons:
            name = icon["src"].rsplit("/", 1)[-1]
            self.assertTrue(
                (Path(settings.BASE_DIR) / "static" / "img" / name).exists(),
                f"{name} is named by the manifest but not in static/img",
            )

    def test_the_worker_is_served_from_the_root(self):
        """
        A service worker can only control URLs below its own path. Served from
        /static/js/ it would control /static/js/ and nothing else — it would
        register cleanly and then never see a single navigation.
        """
        self.assertEqual(reverse("service_worker"), "/sw.js")
        self.assertEqual(self.client.get("/sw.js").status_code, 200)

    def test_the_worker_is_served_as_javascript(self):
        response = self.client.get(reverse("service_worker"))
        self.assertIn("javascript", response["Content-Type"])

    def test_the_worker_is_not_cached_by_the_browser(self):
        """
        Without this a buggy worker keeps running on the phone for up to a day
        after the deploy that fixed it.
        """
        response = self.client.get(reverse("service_worker"))
        self.assertIn("no-cache", response["Cache-Control"])

    def test_the_worker_precaches_resolved_static_paths(self):
        """
        The precache list has to come out as real URLs. A template tag left
        unrendered, or a path written by hand, is a cache.addAll that rejects
        — and a rejected install means the worker never activates at all.
        """
        body = self.client.get(reverse("service_worker")).content.decode("utf-8")
        self.assertIn(static("css/app.css"), body)
        self.assertIn(static("js/app.js"), body)
        self.assertNotIn("{%", body)
        self.assertNotIn("{{", body)

    def test_the_worker_knows_the_offline_url(self):
        body = self.client.get(reverse("service_worker")).content.decode("utf-8")
        self.assertIn(reverse("offline"), body)

    def test_the_offline_page_can_be_opened_directly(self):
        response = self.client.get(reverse("offline"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "offline")

    def test_the_offline_page_carries_nothing_personal(self):
        """
        THE ONE THAT MATTERS. The worker precaches this page by fetching it
        with the signed-in user's cookies, so whatever it renders is written to
        disk and shown to whoever holds the phone next. It must not extend
        base.html, which carries the top bar, the avatar and the unread badge.
        """
        body = self.client.get(reverse("offline")).content.decode("utf-8")
        for marker in ("bottomnav", "topbar", "avatar", "sidenav"):
            self.assertNotIn(marker, body, f"the offline page renders {marker}")

    def test_the_shell_is_reachable_before_onboarding_is_finished(self):
        """
        OnboardingMiddleware redirects half-finished users to the wizard. A 302
        on sw.js is not a visible failure — it is a worker that quietly never
        registers, on exactly the accounts most likely to be new to the app.
        """
        from apps.accounts.middleware import ONBOARDING_EXEMPT

        for name in ("service_worker", "manifest", "offline"):
            self.assertIn(name, ONBOARDING_EXEMPT)


class ProductionDeployTests(SimpleTestCase):
    """
    Settings that only matter on a machine none of the other tests run on.

    Imported with the environment stubbed rather than checked by reading the
    file as text, because the questions here are about resulting VALUES —
    where WhiteNoise sits in the middleware list, which cache backend comes
    out of an unset REDIS_URL — and a grep for a class name cannot answer
    either. The module is imported directly; `django.conf.settings` is
    untouched and the rest of the suite still runs on test.py.
    """

    # Enough to get past prod.py's `required=True` checks and the private
    # bucket assertion. None of it is read for the assertions below.
    STUB_ENV = {
        "SECRET_KEY": "x" * 50,
        "DATABASE_URL": "sqlite:///deploy-test.sqlite3",
        "R2_BUCKET": "trusthailer-media",
        "R2_PRIVATE_BUCKET": "trusthailer-kyc",
        "R2_ENDPOINT": "https://example.r2.cloudflarestorage.com",
        "R2_ACCESS_KEY": "key",
        "R2_SECRET_KEY": "secret",
        # prod.py refuses to boot without this or a REDIS_URL. Every test
        # below that is not about the cache just needs the module to import.
        "ALLOW_WEAK_RATE_LIMIT": "1",
    }

    def prod(self, **extra):
        env = dict(self.STUB_ENV, **extra)
        # Cleared, so a variable set on the machine running the suite cannot
        # change the answer — REDIS_URL especially.
        with mock.patch.dict(os.environ, env, clear=True):
            module = importlib.import_module("config.settings.prod")
            return importlib.reload(module)

    # ------------------------------------------------------- static files

    def test_production_serves_its_own_static_files(self):
        """
        THE ONE THIS CLASS EXISTS FOR.

        `collectstatic` hashes the filenames and writes them to disk, and for
        a long time nothing served that disk: gunicorn does not, runserver's
        handler is development-only, and there is no nginx in front. Every
        stylesheet, script and icon 404'd the moment this was deployed.

        It is also why it went unnoticed — nothing about running the suite,
        `check --deploy`, or the development server can see it.
        """
        self.assertIn(
            "whitenoise.middleware.WhiteNoiseMiddleware", self.prod().MIDDLEWARE
        )

    def test_whitenoise_sits_directly_behind_security_middleware(self):
        """
        Order, not membership. Ahead of SecurityMiddleware and a static
        request skips the HTTPS redirect and HSTS; further down and every
        stylesheet drags a session lookup, the auth middleware, the onboarding
        funnel and the unread-count query behind it.
        """
        middleware = self.prod().MIDDLEWARE
        self.assertEqual(
            middleware[0], "django.middleware.security.SecurityMiddleware"
        )
        self.assertEqual(
            middleware[1], "whitenoise.middleware.WhiteNoiseMiddleware"
        )

    def test_static_files_are_hashed_and_pre_compressed(self):
        """
        Hashing is what makes the year-long cache header safe, and the gzip
        and brotli copies are written once at build time rather than per
        request. Both matter most to the person paying by the megabyte.
        """
        backend = self.prod().STORAGES["staticfiles"]["BACKEND"]
        self.assertEqual(
            backend, "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )

    def test_whitenoise_is_declared_as_a_dependency(self):
        """A middleware that is not installed is a container that will not boot."""
        requirements = (Path(settings.BASE_DIR) / "requirements.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("whitenoise", requirements)

    # ------------------------------------------------------------- cache

    def test_production_does_not_assume_a_local_redis(self):
        """
        `apps/core/ratelimit.py` is cache-backed and sits on the login path, so
        a CACHES entry pointing at a Redis that is not running does not degrade
        gracefully — it raises inside the sign-in flow and nobody can get in.
        Defaulting to localhost made that the behaviour of every deploy that
        had not set REDIS_URL.
        """
        cache = self.prod().CACHES["default"]
        self.assertEqual(
            cache["BACKEND"], "django.core.cache.backends.locmem.LocMemCache"
        )
        self.assertNotIn("127.0.0.1", str(cache.get("LOCATION", "")))

    def test_weak_rate_limiting_has_to_be_asked_for_by_name(self):
        """
        The point of the flag. Without a Redis AND without somebody saying they
        accept what that costs, this refuses to start rather than quietly
        running with per-worker counters that reset on every restart.

        Same shape as the R2 bucket assertion below it: a misconfiguration that
        leaves everything apparently working while a control that was supposed
        to be there is not. Fail at boot instead.
        """
        stub = dict(self.STUB_ENV)
        del stub["ALLOW_WEAK_RATE_LIMIT"]
        with mock.patch.dict(os.environ, stub, clear=True):
            with self.assertRaises(RuntimeError) as caught:
                importlib.reload(importlib.import_module("config.settings.prod"))
        # The message has to name both ways out, or it is just an obstacle.
        self.assertIn("REDIS_URL", str(caught.exception))
        self.assertIn("ALLOW_WEAK_RATE_LIMIT", str(caught.exception))

    def test_a_real_redis_needs_no_flag(self):
        """
        The flag accepts a weakness. With Redis there is no weakness to accept,
        so requiring it as well would train people to set it everywhere — which
        is exactly how it would end up set on the deploy that matters.
        """
        stub = dict(self.STUB_ENV)
        del stub["ALLOW_WEAK_RATE_LIMIT"]
        stub["REDIS_URL"] = "redis://cache.internal:6379/1"
        with mock.patch.dict(os.environ, stub, clear=True):
            prod = importlib.reload(importlib.import_module("config.settings.prod"))
        self.assertEqual(
            prod.CACHES["default"]["BACKEND"],
            "django.core.cache.backends.redis.RedisCache",
        )

    def test_redis_is_used_when_there_actually_is_one(self):
        """The fallback is a fallback, not a replacement — see prod.py on what it costs."""
        prod = self.prod(REDIS_URL="redis://cache.internal:6379/1")
        cache = prod.CACHES["default"]
        self.assertEqual(
            cache["BACKEND"], "django.core.cache.backends.redis.RedisCache"
        )
        self.assertEqual(cache["LOCATION"], "redis://cache.internal:6379/1")

    # --------------------------------------------------------- app shell

    def test_the_service_worker_version_follows_the_deploy_on_its_own(self):
        """
        Every service-worker cache is named after PWA_VERSION. An env var
        somebody has to remember to bump per deploy is one that will not get
        bumped, and the failure is invisible — the app keeps working while
        serving last month's stylesheet.
        """
        self.assertEqual(self.prod(RENDER_GIT_COMMIT="abc123").PWA_VERSION, "abc123")

    def test_an_explicit_release_still_wins(self):
        self.assertEqual(
            self.prod(RELEASE="v1.2.0", RENDER_GIT_COMMIT="abc123").PWA_VERSION,
            "v1.2.0",
        )

    # ------------------------------------------------------ build script

    def test_the_build_collects_static_and_migrates(self):
        """
        Render's free tier has no shell, so anything the database needs before
        it can serve a request has to happen in the build or it never happens
        and the first visitor gets a 500 about a missing table.
        """
        build = (Path(settings.BASE_DIR) / "build.sh").read_text(encoding="utf-8")
        self.assertIn("collectstatic", build)
        self.assertIn("migrate", build)
        self.assertIn("set -o errexit", build)

    def test_the_build_only_seeds_with_idempotent_commands(self):
        """
        Seeding runs on EVERY deploy, which is only safe while every row goes
        through get_or_create. This fails if a command is added to build.sh
        that the geo and platform seeds' property has not been checked for.
        """
        build = (Path(settings.BASE_DIR) / "build.sh").read_text(encoding="utf-8")
        # Commands actually run, not the word "manage.py" appearing in a
        # comment explaining why they are run here.
        seeds = {
            line.split("manage.py ")[1].split()[0]
            for line in build.splitlines()
            if line.strip().startswith("python manage.py ")
        }
        self.assertEqual(
            seeds, {"collectstatic", "migrate", "seed_geo", "seed_platforms"}
        )

    def test_the_blueprint_can_actually_boot(self):
        """
        prod.py refuses to start without either a REDIS_URL or the flag that
        accepts what its absence costs. A blueprint providing neither is one
        that fails on deploy with a RuntimeError — correct behaviour, read at
        the bottom of a build log, by somebody who did not expect it.

        Deleting the flag is the right move the day a Redis is added. This
        fails if it is deleted without one.
        """
        blueprint = (Path(settings.BASE_DIR) / "render.yaml").read_text(encoding="utf-8")
        self.assertTrue(
            "ALLOW_WEAK_RATE_LIMIT" in blueprint or "REDIS_URL" in blueprint,
            "render.yaml sets neither REDIS_URL nor ALLOW_WEAK_RATE_LIMIT, "
            "so prod.py will refuse to boot on deploy",
        )

    def test_the_blueprint_keeps_secrets_out_of_the_repository(self):
        """
        Every secret is `sync: false`, meaning "ask in the dashboard". A value
        typed into this file is a value in the git history forever.
        """
        blueprint = (Path(settings.BASE_DIR) / "render.yaml").read_text(encoding="utf-8")
        for secret in ("R2_SECRET_KEY", "DATABASE_URL", "EMAIL_HOST_PASSWORD"):
            # The declaration, not the first mention — every one of these is
            # named in the comment block above the list as well.
            declaration = blueprint.index(f"- key: {secret}")
            self.assertIn(
                "sync: false", blueprint[declaration:declaration + 120], secret
            )
