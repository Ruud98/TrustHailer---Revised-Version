"""
Sprint 3 — driver listings, rating proofs, global search, saved searches.

Shares the world built by `ListingTestCase`: one metro, two suburbs, two
platforms, an owner and a driver.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.geo.models import City, Country, Province, Suburb

from .forms import DriverFilterForm
from .models import (
    Arrangement,
    DriverListing,
    LicenceCode,
    PlatformRatingProof,
    SavedSearch,
    VehicleListing,
)
from .tests import ListingTestCase, upload


class DriverTestCase(ListingTestCase):
    def make_driver_listing(self, driver=None, status=DriverListing.Status.ACTIVE, **kwargs):
        defaults = dict(
            headline="Five years on Uber, own PrDP",
            years_experience=5,
            licence_code=LicenceCode.B,
            has_prdp=True,
            home_suburb=self.soweto,
        )
        work = kwargs.pop("work_suburbs", None)
        defaults.update(kwargs)
        listing = DriverListing.objects.create(
            driver=driver or self.driver, status=status, **defaults
        )
        listing.platforms_experience.add(self.uber)
        if work:
            listing.work_suburbs.set(work)
        return listing

    def make_proof(self, driver=None, platform=None, rating="4.87", approved=False):
        proof = PlatformRatingProof.objects.create(
            driver=driver or self.driver,
            platform=platform or self.uber,
            rating=Decimal(rating),
            trips=3400,
        )
        if approved:
            proof.status = PlatformRatingProof.Status.APPROVED
            proof.save(update_fields=["status"])
        return proof


class DriverModelTests(DriverTestCase):
    def test_currency_follows_the_country_of_the_home_suburb(self):
        province = Province.objects.create(country=Country.ZW, name="Harare", slug="zw-harare2")
        city = City.objects.create(
            province=province, name="Harare", slug="harare2", is_launch_market=True
        )
        mbare = Suburb.objects.create(city=city, name="Mbare", slug="mbare2")
        listing = self.make_driver_listing(home_suburb=mbare)
        self.assertEqual(listing.currency, "USD")

    def test_going_live_stamps_published_at(self):
        listing = self.make_driver_listing(status=DriverListing.Status.DRAFT)
        self.assertIsNone(listing.published_at)
        listing.status = DriverListing.Status.ACTIVE
        listing.save()
        self.assertIsNotNone(listing.published_at)

    def test_work_area_display_leads_with_the_home_suburb(self):
        listing = self.make_driver_listing(work_suburbs=[self.tembisa, self.soweto])
        display = listing.work_area_display
        self.assertTrue(display.startswith("Soweto"))
        self.assertIn("Tembisa", display)
        # The home suburb is not repeated just because it is also a work area.
        self.assertEqual(display.count("Soweto"), 1)

    def test_only_reviewed_ratings_count_as_verified(self):
        listing = self.make_driver_listing()
        self.make_proof(rating="4.90", approved=False)
        self.make_proof(platform=self.bolt, rating="4.70", approved=True)
        listing.refresh_from_db()

        self.assertEqual([p.rating for p in listing.verified_ratings], [Decimal("4.70")])
        self.assertEqual([p.rating for p in listing.claimed_ratings], [Decimal("4.90")])
        # The higher number is the unverified one, and it must not win.
        self.assertEqual(listing.best_verified_rating.rating, Decimal("4.70"))

    def test_hidden_profiles_drop_out_of_searchable(self):
        listing = self.make_driver_listing()
        self.assertIn(listing, DriverListing.objects.searchable())

        self.driver.profile.hide_from_search = True
        self.driver.profile.save()
        self.assertNotIn(listing, DriverListing.objects.searchable())
        # Still live — hidden is not the same as paused.
        self.assertIn(listing, DriverListing.objects.live())

    def test_ranked_puts_verified_drivers_first(self):
        unverified = self._make_user("new@example.com", "New Driver")
        unverified.verification.phone_verified_at = None
        unverified.verification.save()

        # The unverified driver is both newer and more experienced, so only the
        # trust ranking can put the verified one on top.
        self.make_driver_listing(driver=unverified, years_experience=20)
        verified_listing = self.make_driver_listing(years_experience=1)

        ranked = list(DriverListing.objects.searchable().ranked())
        self.assertEqual(ranked[0], verified_listing)


class RatingProofTests(DriverTestCase):
    def test_review_records_the_outcome_and_destroys_the_screenshot(self):
        proof = self.make_proof()
        proof.screenshot.save("shot.webp", upload(), save=True)
        self.assertTrue(proof.screenshot)

        staff = self._make_user("staff@example.com", "Staff Member")
        proof.review(approved=True, by=staff)
        proof.refresh_from_db()

        self.assertTrue(proof.verified)
        self.assertEqual(proof.reviewed_by, staff)
        # The number and the fact of review survive; the image does not.
        self.assertFalse(proof.screenshot)
        self.assertEqual(proof.rating, Decimal("4.87"))

    def test_rejection_also_destroys_the_screenshot(self):
        proof = self.make_proof()
        proof.screenshot.save("shot.webp", upload(), save=True)

        proof.review(approved=False, reason="Screenshot was for a different account.")
        proof.refresh_from_db()

        self.assertEqual(proof.status, PlatformRatingProof.Status.REJECTED)
        self.assertFalse(proof.screenshot)
        self.assertEqual(proof.reject_reason, "Screenshot was for a different account.")

    def test_purge_after_defaults_to_the_retention_window(self):
        proof = self.make_proof()
        expected = timezone.localdate() + timedelta(days=PlatformRatingProof.RETENTION_DAYS)
        self.assertEqual(proof.purge_after, expected)

    def test_reupload_replaces_the_previous_attempt(self):
        self.login(self.driver)
        self.make_proof()

        response = self.client.post(
            reverse("drivers:ratings"),
            {"platform": self.uber.pk, "rating": "4.95", "screenshot": upload()},
        )
        self.assertEqual(response.status_code, 302)

        proofs = PlatformRatingProof.objects.filter(driver=self.driver, platform=self.uber)
        self.assertEqual(proofs.count(), 1)
        self.assertEqual(proofs.first().rating, Decimal("4.95"))
        # A corrected rating goes back into the queue rather than staying rejected.
        self.assertTrue(proofs.first().is_pending)

    def test_purge_kyc_sweeps_abandoned_screenshots(self):
        proof = self.make_proof()
        proof.screenshot.save("shot.webp", upload(), save=True)
        PlatformRatingProof.objects.filter(pk=proof.pk).update(
            purge_after=timezone.localdate() - timedelta(days=1)
        )

        call_command("purge_kyc")
        proof.refresh_from_db()
        self.assertFalse(proof.screenshot)
        # The row stays — only the file goes.
        self.assertTrue(PlatformRatingProof.objects.filter(pk=proof.pk).exists())


class DriverBrowseTests(DriverTestCase):
    def test_browse_lists_live_drivers(self):
        self.make_driver_listing()
        response = self.client.get(reverse("drivers:browse"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Five years on Uber")

    def test_browse_hides_paused_and_hidden_drivers(self):
        self.make_driver_listing(status=DriverListing.Status.PAUSED)
        response = self.client.get(reverse("drivers:browse"))
        self.assertNotContains(response, "Five years on Uber")

        self.make_driver_listing(headline="Hide me please")
        self.driver.profile.hide_from_search = True
        self.driver.profile.save()
        response = self.client.get(reverse("drivers:browse"))
        self.assertNotContains(response, "Hide me please")

    def test_suburb_filter_matches_home_or_work_areas(self):
        # Lives in Soweto, works Tembisa. An owner filtering Tembisa wants them.
        self.make_driver_listing(work_suburbs=[self.tembisa])
        response = self.client.get(reverse("drivers:browse"), {"suburb": self.tembisa.pk})
        self.assertContains(response, "Five years on Uber")

    def test_prdp_and_experience_filters_narrow_the_list(self):
        rookie = self._make_user("rookie@example.com", "Rookie Driver")
        self.make_driver_listing(
            driver=rookie, headline="Just starting out", years_experience=0, has_prdp=False
        )
        self.make_driver_listing()

        response = self.client.get(reverse("drivers:browse"), {"has_prdp": "on"})
        self.assertContains(response, "Five years on Uber")
        self.assertNotContains(response, "Just starting out")

        response = self.client.get(reverse("drivers:browse"), {"min_experience": "3"})
        self.assertNotContains(response, "Just starting out")

    def test_rated_only_filter_ignores_unverified_ratings(self):
        self.make_driver_listing()
        self.make_proof(approved=False)

        response = self.client.get(reverse("drivers:browse"), {"rated_only": "on"})
        self.assertNotContains(response, "Five years on Uber")

        PlatformRatingProof.objects.update(status=PlatformRatingProof.Status.APPROVED)
        response = self.client.get(reverse("drivers:browse"), {"rated_only": "on"})
        self.assertContains(response, "Five years on Uber")

    def test_bad_filter_input_is_ignored_not_fatal(self):
        self.make_driver_listing()
        response = self.client.get(
            reverse("drivers:browse"), {"suburb": "banana", "min_experience": "lots"}
        )
        self.assertEqual(response.status_code, 200)

    def test_htmx_request_returns_only_the_results_fragment(self):
        self.make_driver_listing()
        response = self.client.get(reverse("drivers:browse"), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<!doctype html")

    def test_card_shows_verified_ratings_only(self):
        self.make_driver_listing()
        self.make_proof(rating="4.99", approved=False)

        response = self.client.get(reverse("drivers:browse"))
        self.assertNotContains(response, "4.99")

        PlatformRatingProof.objects.update(status=PlatformRatingProof.Status.APPROVED)
        response = self.client.get(reverse("drivers:browse"))
        self.assertContains(response, "4.99")


class DriverDetailTests(DriverTestCase):
    def test_detail_never_shows_the_raw_phone_number(self):
        listing = self.make_driver_listing()
        self.login(self.owner)
        response = self.client.get(listing.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.driver.phone)

    def test_unverified_rating_is_labelled_self_reported(self):
        listing = self.make_driver_listing()
        self.make_proof(rating="4.91", approved=False)
        response = self.client.get(listing.get_absolute_url())
        self.assertContains(response, "Self-reported")
        self.assertNotContains(response, "Verified platform ratings")

    def test_paused_listing_is_visible_to_its_driver_only(self):
        listing = self.make_driver_listing(status=DriverListing.Status.PAUSED)

        self.assertEqual(self.client.get(listing.get_absolute_url()).status_code, 404)

        self.login(self.driver)
        self.assertEqual(self.client.get(listing.get_absolute_url()).status_code, 200)

    def test_hidden_driver_is_404_for_everyone_else(self):
        listing = self.make_driver_listing()
        self.driver.profile.hide_from_search = True
        self.driver.profile.save()

        self.login(self.owner)
        self.assertEqual(self.client.get(listing.get_absolute_url()).status_code, 404)

        self.login(self.driver)
        self.assertEqual(self.client.get(listing.get_absolute_url()).status_code, 200)

    def test_view_count_ignores_the_driver_themselves(self):
        listing = self.make_driver_listing()
        self.login(self.driver)
        self.client.get(listing.get_absolute_url())
        listing.refresh_from_db()
        self.assertEqual(listing.view_count, 0)

        self.login(self.owner)
        self.client.get(listing.get_absolute_url())
        listing.refresh_from_db()
        self.assertEqual(listing.view_count, 1)


class DriverCrudTests(DriverTestCase):
    def _payload(self, **overrides):
        payload = {
            "headline": "Four years on Bolt, clean record",
            "years_experience": 4,
            "licence_code": LicenceCode.B,
            "has_prdp": "on",
            "platforms_experience": [self.bolt.pk],
            "preferred_arrangement": Arrangement.WEEKLY,
            "max_weekly_rate": "2200",
            "city": self.city.pk,
            "home_suburb": self.soweto.pk,
            "work_suburbs": [self.soweto.pk, self.tembisa.pk],
            "about": "I look after a car.",
        }
        payload.update(overrides)
        return payload

    def test_creating_a_listing_goes_live_immediately(self):
        self.login(self.driver)
        response = self.client.post(reverse("drivers:create"), self._payload())
        self.assertEqual(response.status_code, 302)

        listing = DriverListing.objects.get(driver=self.driver)
        self.assertEqual(listing.status, DriverListing.Status.ACTIVE)
        self.assertIsNotNone(listing.published_at)

    def test_listing_a_driver_does_not_require_a_verified_phone(self):
        """
        The opposite of the car flow, and deliberately so — see the
        `DriverListing` docstring. A driver publishing a profile gives nothing
        away until an introduction is approved, and approval is gated.
        """
        unverified = self._make_user("unverified@example.com", "Unverified Driver")
        unverified.verification.phone_verified_at = None
        unverified.verification.save()
        self.assertTrue(unverified.needs_phone_verification)

        self.login(unverified)
        response = self.client.post(reverse("drivers:create"), self._payload())
        self.assertEqual(response.status_code, 302)
        self.assertTrue(DriverListing.objects.filter(driver=unverified).exists())

    def test_a_second_listing_redirects_to_editing_the_first(self):
        listing = self.make_driver_listing()
        self.login(self.driver)
        response = self.client.get(reverse("drivers:create"))
        self.assertRedirects(response, reverse("drivers:edit", args=[listing.uuid]))
        self.assertEqual(DriverListing.objects.filter(driver=self.driver).count(), 1)

    def test_home_suburb_outside_a_launch_market_is_rejected(self):
        quiet_city = City.objects.create(
            province=self.city.province, name="Polokwane", slug="pk", is_launch_market=False
        )
        seshego = Suburb.objects.create(city=quiet_city, name="Seshego", slug="seshego")

        self.login(self.driver)
        response = self.client.post(
            reverse("drivers:create"),
            self._payload(city=quiet_city.pk, home_suburb=seshego.pk, work_suburbs=[]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(DriverListing.objects.filter(driver=self.driver).exists())

    def test_too_many_work_suburbs_is_rejected(self):
        extras = [
            Suburb.objects.create(city=self.city, name=f"Suburb {i}", slug=f"suburb-{i}").pk
            for i in range(DriverListing.MAX_WORK_SUBURBS + 1)
        ]
        self.login(self.driver)
        response = self.client.post(
            reverse("drivers:create"), self._payload(work_suburbs=extras)
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "at most")
        self.assertFalse(DriverListing.objects.filter(driver=self.driver).exists())

    def test_a_driver_cannot_edit_someone_elses_listing(self):
        listing = self.make_driver_listing()
        self.login(self.owner)
        response = self.client.get(reverse("drivers:edit", args=[listing.uuid]))
        self.assertEqual(response.status_code, 404)

    def test_status_changes_are_limited_to_known_values(self):
        listing = self.make_driver_listing()
        self.login(self.driver)
        self.client.post(
            reverse("drivers:set_status", args=[listing.uuid]), {"status": "deleted"}
        )
        listing.refresh_from_db()
        self.assertEqual(listing.status, DriverListing.Status.ACTIVE)

    def test_suspended_drivers_cannot_list(self):
        self.driver.is_suspended = True
        self.driver.save()
        self.login(self.driver)
        response = self.client.post(reverse("drivers:create"), self._payload())
        self.assertEqual(response.status_code, 302)
        self.assertFalse(DriverListing.objects.filter(driver=self.driver).exists())


class GlobalSearchTests(DriverTestCase):
    def setUp(self):
        super().setUp()
        self.make_listing()
        self.make_driver_listing()

    def test_search_finds_cars_and_drivers_together(self):
        response = self.client.get(reverse("search"), {"q": "Soweto"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Corolla Quest")
        self.assertContains(response, "Five years on Uber")

    def test_one_letter_is_refused_rather_than_scanned(self):
        response = self.client.get(reverse("search"), {"q": "a"})
        self.assertContains(response, "Two letters at least")

    def test_empty_search_offers_both_browse_pages(self):
        response = self.client.get(reverse("search"))
        self.assertContains(response, "Browse cars")
        self.assertContains(response, "Browse drivers")

    def test_search_excludes_drafts_and_hidden_people(self):
        VehicleListing.objects.update(status=VehicleListing.Status.DRAFT)
        self.driver.profile.hide_from_search = True
        self.driver.profile.save()

        response = self.client.get(reverse("search"), {"q": "Soweto"})
        self.assertNotContains(response, "Corolla Quest")
        self.assertNotContains(response, "Five years on Uber")

    def test_search_never_leaks_a_phone_number(self):
        self.login(self.owner)
        response = self.client.get(reverse("search"), {"q": "Soweto"})
        self.assertNotContains(response, self.driver.phone)


class SavedSearchTests(DriverTestCase):
    def test_saving_a_driver_search_stores_only_validated_params(self):
        self.login(self.owner)
        response = self.client.post(
            reverse("save_search"),
            {
                "kind": "drivers",
                "label": "PrDP drivers in Tembisa",
                "frequency": SavedSearch.Frequency.DAILY,
                "suburb": self.tembisa.pk,
                "has_prdp": "on",
                "drop_tables": "please",     # not a field on the form
            },
        )
        self.assertEqual(response.status_code, 302)

        search = SavedSearch.objects.get(user=self.owner)
        self.assertEqual(search.kind, SavedSearch.Kind.DRIVERS)
        self.assertEqual(search.params["suburb"], self.tembisa.pk)
        self.assertEqual(search.params["has_prdp"], "on")
        # Anything the form doesn't recognise never reaches the database.
        self.assertNotIn("drop_tables", search.params)

    def test_a_saved_search_replays_as_a_working_querystring(self):
        self.make_driver_listing(work_suburbs=[self.tembisa])
        self.login(self.owner)
        self.client.post(
            reverse("save_search"),
            {
                "kind": "drivers",
                "label": "Tembisa",
                "frequency": SavedSearch.Frequency.NEVER,
                "suburb": self.tembisa.pk,
            },
        )
        search = SavedSearch.objects.get(user=self.owner)

        response = self.client.get(search.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Five years on Uber")

    def test_car_searches_point_back_at_the_car_browse_page(self):
        self.login(self.owner)
        self.client.post(
            reverse("save_search"),
            {
                "kind": "cars",
                "label": "Cheap autos",
                "frequency": SavedSearch.Frequency.WEEKLY,
                "max_price": "2500",
            },
        )
        search = SavedSearch.objects.get(user=self.owner)
        self.assertEqual(search.kind, SavedSearch.Kind.CARS)
        self.assertTrue(search.get_absolute_url().startswith(reverse("listings:browse")))
        self.assertIn("max_price=2500", search.get_absolute_url())

    def test_duplicate_labels_are_refused(self):
        self.login(self.owner)
        payload = {"kind": "cars", "label": "Same name", "frequency": "daily"}
        self.client.post(reverse("save_search"), payload)
        response = self.client.post(reverse("save_search"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SavedSearch.objects.filter(user=self.owner).count(), 1)

    def test_the_cap_is_enforced(self):
        for index in range(SavedSearch.MAX_PER_USER):
            SavedSearch.objects.create(
                user=self.owner, label=f"Search {index}", kind=SavedSearch.Kind.CARS
            )
        self.login(self.owner)
        response = self.client.post(
            reverse("save_search"),
            {"kind": "cars", "label": "One too many", "frequency": "daily"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            SavedSearch.objects.filter(user=self.owner).count(), SavedSearch.MAX_PER_USER
        )

    def test_you_can_only_delete_your_own(self):
        search = SavedSearch.objects.create(
            user=self.owner, label="Mine", kind=SavedSearch.Kind.CARS
        )
        self.login(self.driver)
        response = self.client.post(reverse("delete_saved_search", args=[search.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(SavedSearch.objects.filter(pk=search.pk).exists())

    def test_an_unknown_kind_is_refused(self):
        self.login(self.owner)
        response = self.client.get(reverse("save_search"), {"kind": "posts"})
        self.assertEqual(response.status_code, 404)


class FilterSerialisationTests(DriverTestCase):
    def test_as_saved_params_drops_empties_and_flattens_models(self):
        form = DriverFilterForm(
            {"suburb": str(self.soweto.pk), "has_prdp": "on", "q": "", "sort": "newest"}
        )
        params = form.as_saved_params()

        self.assertEqual(params["suburb"], self.soweto.pk)
        self.assertEqual(params["has_prdp"], "on")
        self.assertNotIn("q", params)
        self.assertNotIn("verified_only", params)

    def test_invalid_input_serialises_to_nothing(self):
        form = DriverFilterForm({"suburb": "banana"})
        self.assertEqual(form.as_saved_params(), {})


class PageRenderTests(DriverTestCase):
    """Every page this sprint adds, fetched once, so a template typo fails here."""

    def test_saved_searches_page_renders_empty_and_populated(self):
        self.login(self.owner)
        response = self.client.get(reverse("saved_searches"))
        self.assertContains(response, "Nothing saved yet")

        SavedSearch.objects.create(
            user=self.owner, label="Cheap autos", kind=SavedSearch.Kind.CARS
        )
        response = self.client.get(reverse("saved_searches"))
        self.assertContains(response, "Cheap autos")
        # Say plainly that alerts don't run yet, rather than implying they do.
        self.assertContains(response, "aren't switched on yet")

    def test_ratings_page_renders(self):
        self.login(self.driver)
        self.make_proof(approved=True)
        response = self.client.get(reverse("drivers:ratings"))
        self.assertContains(response, "Platform ratings")
        self.assertContains(response, "4.87")

    def test_save_search_confirm_page_shows_what_is_being_saved(self):
        self.login(self.owner)
        response = self.client.get(
            reverse("save_search"), {"kind": "drivers", "has_prdp": "on"}
        )
        self.assertContains(response, "Save this search")
        self.assertContains(response, "Has a PrDP")

    def test_driver_form_renders(self):
        self.login(self.driver)
        response = self.client.get(reverse("drivers:create"))
        self.assertContains(response, "List yourself as a driver")


class ProfileSurfaceTests(DriverTestCase):
    def test_profile_shows_live_listings_from_both_sides(self):
        self.make_listing(owner=self.owner)
        self.make_driver_listing(driver=self.driver)

        response = self.client.get(self.owner.get_absolute_url())
        self.assertContains(response, "Corolla Quest")

        response = self.client.get(self.driver.get_absolute_url())
        self.assertContains(response, "Five years on Uber")

    def test_profile_hides_other_peoples_drafts(self):
        self.make_listing(owner=self.owner, status=VehicleListing.Status.DRAFT)
        self.make_driver_listing(driver=self.driver, status=DriverListing.Status.PAUSED)

        response = self.client.get(self.owner.get_absolute_url())
        self.assertNotContains(response, "Corolla Quest")

        response = self.client.get(self.driver.get_absolute_url())
        self.assertNotContains(response, "Five years on Uber")

    def test_you_see_your_own_drafts_on_your_own_profile(self):
        self.make_listing(owner=self.owner, status=VehicleListing.Status.DRAFT)
        self.login(self.owner)
        response = self.client.get(self.owner.get_absolute_url())
        self.assertContains(response, "Corolla Quest")

    def test_profile_still_never_shows_a_raw_number(self):
        self.make_driver_listing(driver=self.driver)
        self.login(self.owner)
        response = self.client.get(self.driver.get_absolute_url())
        self.assertNotContains(response, self.driver.phone)


class NavigationTests(DriverTestCase):
    def test_the_drivers_tab_is_wired_up(self):
        self.login(self.owner)
        response = self.client.get(reverse("home"))
        self.assertContains(response, reverse("drivers:browse"))

    def test_the_top_bar_searches_both_sides(self):
        self.login(self.owner)
        response = self.client.get(reverse("home"))
        self.assertContains(response, reverse("search"))
