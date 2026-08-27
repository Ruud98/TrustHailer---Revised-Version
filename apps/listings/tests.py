import io
from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.accounts.models import User
from apps.geo.models import City, Country, Province, Suburb

from .forms import VehicleFilterForm
from .models import (
    Arrangement,
    ListingPhoto,
    PaidBy,
    Platform,
    Transmission,
    VehicleListing,
)

AUTH_BACKEND = "apps.accounts.backends.EmailBackend"


def upload(name="car.jpg", size=(1200, 900), colour=(180, 40, 40)):
    buffer = io.BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


class ListingTestCase(TestCase):
    """Shared world: one metro, two suburbs, two platforms, one verified owner."""

    def setUp(self):
        province = Province.objects.create(country=Country.ZA, name="Gauteng", slug="za-gauteng")
        self.city = City.objects.create(
            province=province, name="Johannesburg", slug="jhb", is_launch_market=True
        )
        self.other_city = City.objects.create(
            province=province, name="Ekurhuleni", slug="ekurhuleni", is_launch_market=True
        )
        self.soweto = Suburb.objects.create(city=self.city, name="Soweto", slug="soweto")
        self.tembisa = Suburb.objects.create(city=self.other_city, name="Tembisa", slug="tembisa")

        self.uber = Platform.objects.create(name="Uber", slug="uber-za", country=Country.ZA)
        self.bolt = Platform.objects.create(name="Bolt", slug="bolt-za", country=Country.ZA)

        self.owner = self._make_user("owner@example.com", "Owner One", verified=True)
        self.owner.profile.is_owner = True
        self.owner.profile.save()

        self.driver = self._make_user("driver@example.com", "Driver Two", verified=True)
        self.driver.profile.is_driver = True
        self.driver.profile.save()

    def _make_user(self, email, name, verified=False, phone=None):
        user = User.objects.create_user(email=email, full_name=name)
        User.objects.filter(pk=user.pk).update(phone=phone or f"+2782{user.pk:07d}")
        user.refresh_from_db()
        user.profile.onboarding_completed_at = timezone.now()
        user.profile.suburb = self.soweto
        user.profile.save()
        verification = user.verification
        verification.email_verified_at = timezone.now()
        if verified:
            verification.phone_verified_at = timezone.now()
        verification.save()
        return user

    def make_listing(self, owner=None, status=VehicleListing.Status.ACTIVE, **kwargs):
        defaults = dict(
            make="Toyota", model="Corolla Quest", year=2019,
            transmission=Transmission.MANUAL,
            arrangement=Arrangement.WEEKLY, weekly_rate=Decimal("2500"),
            deposit_amount=Decimal("5000"), suburb=self.soweto,
        )
        defaults.update(kwargs)
        listing = VehicleListing.objects.create(
            owner=owner or self.owner, status=status, **defaults
        )
        listing.platforms.add(self.uber)
        return listing

    def login(self, user):
        self.client.force_login(user, backend=AUTH_BACKEND)


class ModelLogicTests(ListingTestCase):
    def test_headline_price_matches_the_arrangement(self):
        weekly = self.make_listing()
        self.assertEqual(weekly.headline_price, (Decimal("2500"), "per week"))

        share = self.make_listing(
            arrangement=Arrangement.SHARE, weekly_rate=None, earnings_share_pct=70
        )
        self.assertEqual(share.headline_price, (70, "% to driver"))

    def test_driver_covered_costs_lists_only_what_the_owner_pays(self):
        listing = self.make_listing(
            fuel_paid_by=PaidBy.DRIVER,
            maintenance_paid_by=PaidBy.OWNER,
            insurance_paid_by=PaidBy.OWNER,
            licensing_paid_by=PaidBy.OWNER,
            tracker_paid_by=PaidBy.SHARED,
        )
        covered = listing.driver_covered_costs
        self.assertIn("Maintenance", covered)
        self.assertIn("Tracker", covered)
        self.assertNotIn("Fuel", covered)

    def test_currency_follows_the_country_of_the_suburb(self):
        zw_province = Province.objects.create(country=Country.ZW, name="Harare", slug="zw-harare")
        zw_city = City.objects.create(
            province=zw_province, name="Harare", slug="harare", is_launch_market=True
        )
        mbare = Suburb.objects.create(city=zw_city, name="Mbare", slug="mbare")
        listing = self.make_listing(suburb=mbare)
        self.assertEqual(listing.currency, "USD")
        self.assertEqual(self.make_listing().currency, "ZAR")

    def test_publishing_stamps_published_at_once_only(self):
        listing = self.make_listing(status=VehicleListing.Status.DRAFT)
        self.assertIsNone(listing.published_at)
        listing.status = VehicleListing.Status.ACTIVE
        listing.save()
        first = listing.published_at
        self.assertIsNotNone(first)

        listing.status = VehicleListing.Status.PAUSED
        listing.save()
        listing.status = VehicleListing.Status.ACTIVE
        listing.save()
        self.assertEqual(listing.published_at, first, "Re-publishing must not reset the date")

    def test_no_age_warning_when_no_rule_is_recorded(self):
        """We don't guess platform rules. Null means silent, not a false alarm."""
        listing = self.make_listing(year=2005)
        self.assertEqual(listing.platform_age_warnings(), [])

    def test_age_warning_fires_once_a_rule_is_set(self):
        self.uber.max_vehicle_age_years = 8
        self.uber.save()
        listing = self.make_listing(year=timezone.localdate().year - 12)
        self.assertIn(self.uber, listing.platform_age_warnings())


class RankingTests(ListingTestCase):
    def test_boosted_listings_come_first_and_nulls_do_not_win(self):
        old_boosted = self.make_listing(make="Boosted")
        old_boosted.boost_expires_at = timezone.now() + timedelta(days=3)
        old_boosted.save()
        newer_plain = self.make_listing(make="Plain")

        ranked = list(VehicleListing.objects.live().ranked())
        self.assertEqual(ranked[0], old_boosted)
        self.assertEqual(ranked[1], newer_plain)

    def test_expired_boost_stops_ranking_first(self):
        stale = self.make_listing(make="Stale")
        stale.boost_expires_at = timezone.now() - timedelta(days=1)
        stale.save()
        self.assertFalse(stale.is_boosted)


class FilterTests(ListingTestCase):
    def setUp(self):
        super().setUp()
        self.cheap = self.make_listing(make="Volkswagen", model="Polo Vivo",
                                       weekly_rate=Decimal("1800"), suburb=self.soweto)
        self.dear = self.make_listing(make="Toyota", model="Corolla",
                                      weekly_rate=Decimal("3200"), suburb=self.tembisa,
                                      requires_prdp=False,
                                      maintenance_paid_by=PaidBy.OWNER,
                                      fuel_paid_by=PaidBy.DRIVER)

    def _filter(self, **params):
        form = VehicleFilterForm(params)
        return list(form.apply(VehicleListing.objects.live()))

    def test_suburb_filter_narrows_to_that_suburb(self):
        self.assertEqual(self._filter(suburb=self.tembisa.pk), [self.dear])

    def test_city_filter_used_when_no_suburb_chosen(self):
        self.assertEqual(self._filter(city=self.city.pk), [self.cheap])

    def test_max_price_excludes_dearer_cars(self):
        self.assertEqual(self._filter(max_price="2000"), [self.cheap])

    def test_text_search_matches_make_and_model(self):
        self.assertEqual(self._filter(q="Vivo"), [self.cheap])
        self.assertEqual(self._filter(q="volkswagen"), [self.cheap])

    def test_no_prdp_filter(self):
        self.assertEqual(self._filter(no_prdp="on"), [self.dear])

    def test_covered_filter_finds_owner_paid_costs(self):
        results = self._filter(covered=["maintenance"])
        self.assertIn(self.dear, results)

    def test_bad_input_is_ignored_rather_than_raising(self):
        """cleaned_data feeds a queryset, so `?max_price=banana` must not 500."""
        for params in [{"max_price": "banana"}, {"city": "abc"},
                       {"suburb": "999999"}, {"arrangement": "nonsense"}]:
            with self.subTest(params=params):
                self.assertIsInstance(self._filter(**params), list)

    def test_price_sort_puts_priceless_listings_last(self):
        self.make_listing(make="NoPrice", arrangement=Arrangement.SHARE,
                          weekly_rate=None, earnings_share_pct=60)
        results = self._filter(sort="price_asc")
        self.assertEqual(results[0], self.cheap)
        self.assertIsNone(results[-1].weekly_rate)

    def test_active_filter_count_ignores_sort(self):
        form = VehicleFilterForm({"sort": "newest"})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.active_filter_count, 0)

        form = VehicleFilterForm({"suburb": self.soweto.pk, "sort": "newest"})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.active_filter_count, 1)


class VisibilityTests(ListingTestCase):
    def test_drafts_are_hidden_from_browse(self):
        self.make_listing(status=VehicleListing.Status.DRAFT)
        response = self.client.get(reverse("listings:browse"))
        self.assertEqual(response.context["total"], 0)

    def test_draft_detail_404s_for_strangers_but_not_the_owner(self):
        listing = self.make_listing(status=VehicleListing.Status.DRAFT)
        url = reverse("listings:detail", args=[listing.uuid])
        self.assertEqual(self.client.get(url).status_code, 404)

        self.login(self.owner)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_detail_never_shows_the_owners_raw_number(self):
        listing = self.make_listing()
        self.login(self.driver)
        response = self.client.get(reverse("listings:detail", args=[listing.uuid]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.owner.phone)
        self.assertContains(response, self.owner.masked_phone)

    def test_scam_warning_is_on_the_detail_page(self):
        listing = self.make_listing()
        response = self.client.get(reverse("listings:detail", args=[listing.uuid]))
        self.assertContains(response, "Never pay a deposit")

    def test_view_count_increments_for_visitors_not_the_owner(self):
        listing = self.make_listing()
        url = reverse("listings:detail", args=[listing.uuid])

        self.client.get(url)
        listing.refresh_from_db()
        self.assertEqual(listing.view_count, 1)

        self.login(self.owner)
        self.client.get(url)
        listing.refresh_from_db()
        self.assertEqual(listing.view_count, 1, "An owner viewing their own car isn't a view")


class PermissionTests(ListingTestCase):
    def test_creating_requires_login(self):
        response = self.client.get(reverse("listings:create"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/join/", response.headers["Location"])

    def test_creating_requires_a_verified_phone(self):
        """The payoff of deferred verification: browsing is free, listing isn't."""
        unverified = self._make_user("new@example.com", "New Owner", verified=False)
        self.login(unverified)
        response = self.client.get(reverse("listings:create"))
        self.assertRedirects(response, reverse("accounts:verify_phone"))

    def test_browsing_does_not_require_a_verified_phone(self):
        unverified = self._make_user("new@example.com", "New Owner", verified=False)
        self.login(unverified)
        self.assertEqual(self.client.get(reverse("listings:browse")).status_code, 200)

    def test_suspended_users_cannot_create(self):
        self.owner.is_suspended = True
        self.owner.save()
        self.login(self.owner)
        response = self.client.get(reverse("listings:create"))
        self.assertRedirects(response, "/")

    def test_a_stranger_cannot_edit_someone_elses_listing(self):
        listing = self.make_listing()
        self.login(self.driver)
        self.assertEqual(
            self.client.get(reverse("listings:edit", args=[listing.uuid])).status_code, 404
        )

    def test_a_stranger_cannot_change_status(self):
        listing = self.make_listing()
        self.login(self.driver)
        response = self.client.post(
            reverse("listings:set_status", args=[listing.uuid]), {"status": "archived"}
        )
        self.assertEqual(response.status_code, 404)
        listing.refresh_from_db()
        self.assertEqual(listing.status, VehicleListing.Status.ACTIVE)


class CreateFlowTests(ListingTestCase):
    def _payload(self, **overrides):
        data = {
            "make": "Toyota", "model": "Corolla Quest", "year": "2019",
            "transmission": "manual", "fuel_type": "petrol", "colour": "White",
            "platforms": [self.uber.pk],
            "arrangement": "weekly", "weekly_rate": "2500", "deposit_amount": "5000",
            "fuel_paid_by": "driver", "maintenance_paid_by": "owner",
            "insurance_paid_by": "owner", "licensing_paid_by": "owner",
            "tracker_paid_by": "owner",
            "min_experience_years": "1",
            "city": self.city.pk, "suburb": self.soweto.pk,
            "description": "Well looked after.",
        }
        data.update(overrides)
        return data

    def test_a_new_listing_starts_as_a_draft(self):
        self.login(self.owner)
        response = self.client.post(reverse("listings:create"), self._payload())
        listing = VehicleListing.objects.get(owner=self.owner)
        self.assertEqual(listing.status, VehicleListing.Status.DRAFT)
        self.assertRedirects(response, reverse("listings:photos", args=[listing.uuid]))

    def test_weekly_arrangement_requires_a_weekly_rate(self):
        self.login(self.owner)
        response = self.client.post(reverse("listings:create"), self._payload(weekly_rate=""))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(VehicleListing.objects.filter(owner=self.owner).exists())

    def test_rent_to_own_requires_a_term(self):
        self.login(self.owner)
        response = self.client.post(
            reverse("listings:create"), self._payload(arrangement="rent2own")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "months until the driver owns it")

    def test_a_future_year_is_rejected(self):
        self.login(self.owner)
        response = self.client.post(
            reverse("listings:create"), self._payload(year=str(timezone.localdate().year + 5))
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(VehicleListing.objects.filter(owner=self.owner).exists())

    def test_suburb_outside_a_launch_market_is_rejected(self):
        cold_province = Province.objects.create(
            country=Country.ZA, name="Limpopo", slug="za-limpopo")
        cold_city = City.objects.create(
            province=cold_province, name="Polokwane", slug="polokwane", is_launch_market=False)
        cold = Suburb.objects.create(city=cold_city, name="Seshego", slug="seshego")

        self.login(self.owner)
        response = self.client.post(
            reverse("listings:create"), self._payload(city=cold_city.pk, suburb=cold.pk)
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(VehicleListing.objects.filter(owner=self.owner).exists())


class PhotoTests(ListingTestCase):
    def setUp(self):
        super().setUp()
        self.listing = self.make_listing(status=VehicleListing.Status.DRAFT)
        self.login(self.owner)
        self.url = reverse("listings:photos", args=[self.listing.uuid])

    def test_upload_converts_to_webp_and_makes_the_first_one_the_cover(self):
        self.client.post(self.url, {"images": [upload("a.jpg"), upload("b.jpg")]})
        photos = list(self.listing.photos.all())
        self.assertEqual(len(photos), 2)
        self.assertTrue(photos[0].is_primary)
        self.assertFalse(photos[1].is_primary)
        self.assertTrue(photos[0].image.name.endswith(".webp"))
        self.assertTrue(photos[0].thumbnail.name)

    def test_photo_limit_is_enforced(self):
        too_many = [upload(f"{i}.jpg") for i in range(ListingPhoto.MAX_PER_LISTING + 2)]
        response = self.client.post(self.url, {"images": too_many})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.listing.photos.count(), 0)

    def test_only_one_photo_is_ever_the_cover(self):
        self.client.post(self.url, {"images": [upload("a.jpg"), upload("b.jpg")]})
        second = self.listing.photos.all()[1]
        self.client.post(reverse("listings:photo_primary", args=[self.listing.uuid, second.pk]))
        self.assertEqual(self.listing.photos.filter(is_primary=True).count(), 1)
        second.refresh_from_db()
        self.assertTrue(second.is_primary)

    def test_deleting_the_cover_promotes_another_photo(self):
        self.client.post(self.url, {"images": [upload("a.jpg"), upload("b.jpg")]})
        cover = self.listing.photos.get(is_primary=True)
        self.client.post(reverse("listings:photo_delete", args=[self.listing.uuid, cover.pk]))
        self.assertEqual(self.listing.photos.count(), 1)
        self.assertTrue(self.listing.photos.first().is_primary)

    def test_a_stranger_cannot_delete_photos(self):
        self.client.post(self.url, {"images": [upload("a.jpg")]})
        photo = self.listing.photos.first()
        self.login(self.driver)
        response = self.client.post(
            reverse("listings:photo_delete", args=[self.listing.uuid, photo.pk])
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.listing.photos.count(), 1)

    def test_reorder_ignores_ids_from_another_listing(self):
        other = self.make_listing()
        self.client.post(self.url, {"images": [upload("a.jpg")]})
        foreign = ListingPhoto.objects.create(listing=other, order=0)
        mine = self.listing.photos.first()

        self.client.post(
            reverse("listings:photo_reorder", args=[self.listing.uuid]),
            {"order[]": [str(foreign.pk), str(mine.pk)]},
        )
        foreign.refresh_from_db()
        self.assertEqual(foreign.order, 0, "A photo on another listing must not move")


class PublishTests(ListingTestCase):
    def test_publishing_is_blocked_without_a_photo(self):
        listing = self.make_listing(status=VehicleListing.Status.DRAFT)
        self.login(self.owner)
        response = self.client.post(
            reverse("listings:set_status", args=[listing.uuid]), {"status": "active"}
        )
        self.assertRedirects(response, reverse("listings:photos", args=[listing.uuid]))
        listing.refresh_from_db()
        self.assertEqual(listing.status, VehicleListing.Status.DRAFT)

    def test_publishing_works_once_a_photo_exists(self):
        listing = self.make_listing(status=VehicleListing.Status.DRAFT)
        self.login(self.owner)
        self.client.post(
            reverse("listings:photos", args=[listing.uuid]), {"images": [upload()]}
        )
        self.client.post(
            reverse("listings:set_status", args=[listing.uuid]), {"status": "active"}
        )
        listing.refresh_from_db()
        self.assertEqual(listing.status, VehicleListing.Status.ACTIVE)

    def test_an_unknown_status_is_rejected(self):
        listing = self.make_listing()
        self.login(self.owner)
        self.client.post(
            reverse("listings:set_status", args=[listing.uuid]), {"status": "deleted"}
        )
        listing.refresh_from_db()
        self.assertEqual(listing.status, VehicleListing.Status.ACTIVE)


class BoostTests(ListingTestCase):
    def test_boost_is_free_while_monetisation_is_off(self):
        listing = self.make_listing()
        self.login(self.owner)
        self.client.post(reverse("listings:boost", args=[listing.uuid]))
        listing.refresh_from_db()
        self.assertTrue(listing.is_boosted)


class BrowseViewTests(ListingTestCase):
    def test_htmx_request_returns_only_the_results_fragment(self):
        self.make_listing()
        response = self.client.get(reverse("listings:browse"), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="results"')
        self.assertNotContains(response, "<!doctype html")

    def test_full_request_returns_the_whole_page(self):
        self.make_listing()
        response = self.client.get(reverse("listings:browse"))
        self.assertContains(response, "<!doctype html")

    def test_pagination_links_keep_the_filters(self):
        for index in range(14):
            self.make_listing(make=f"Car{index}")
        response = self.client.get(reverse("listings:browse"), {"city": self.city.pk})
        self.assertIn(f"city={self.city.pk}", response.context["querystring"])

    def test_empty_state_offers_a_way_out(self):
        response = self.client.get(reverse("listings:browse"), {"q": "spaceship"})
        self.assertContains(response, "No cars match that")
