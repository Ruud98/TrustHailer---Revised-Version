import io
from datetime import date, timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.accounts.models import User
from apps.notifications.models import Notification
from apps.placements.models import Placement
from apps.geo.models import City, Country, Province, Suburb

from .forms import (
    ServiceScheduleForm,
    VehicleFilterForm,
    VehicleListingForm,
    platform_choices,
)
from .models import (
    ServiceReminder,
    VehicleNote,
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
            # ID is the first rung above email now that phone verification is
            # gone. Assigning phone_verified_at here silently did nothing once
            # the field was dropped, which is worse than failing.
            verification.id_verified_at = timezone.now()
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
        # The masked teaser is gone with the introduction flow it belonged to.
        # It promised a release that no longer happens: a number now arrives
        # because the owner sends it in the chat, not because a request was
        # approved, so a half-shown number here would be advertising a button
        # that does not exist.
        self.assertNotContains(response, self.owner.masked_phone)

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

    def test_creating_does_not_require_a_verified_phone(self):
        """Listing a car is open to any logged-in owner. Nothing is given away by
        a listing — contacts are released only when an introduction is approved,
        and that approval is still gated — so a wall here would only cost us the
        supply drivers come to browse."""
        unverified = self._make_user("new@example.com", "New Owner", verified=False)
        self.login(unverified)
        response = self.client.get(reverse("listings:create"))
        self.assertEqual(response.status_code, 200)

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
            "city": self.city.name, "suburb": self.soweto.name,
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

    def test_a_city_we_have_never_heard_of_is_accepted_and_created(self):
        """
        The dead end this removes: an owner in a town nobody had seeded could
        not list at all. Typing it now creates the place.
        """
        self.login(self.owner)
        response = self.client.post(
            reverse("listings:create"), self._payload(city="Polokwane", suburb="Seshego")
        )
        self.assertEqual(response.status_code, 302)

        listing = VehicleListing.objects.get(owner=self.owner)
        self.assertEqual(listing.suburb.name, "Seshego")
        self.assertEqual(listing.suburb.city.name, "Polokwane")
        self.assertEqual(listing.suburb.city.province.country, Country.ZA)

    def test_a_typed_suburb_joins_the_existing_row_rather_than_duplicating_it(self):
        """Casing and stray spacing must not split one suburb into three."""
        self.login(self.owner)
        self.client.post(
            reverse("listings:create"), self._payload(city="  johannesburg ", suburb="SOWETO")
        )
        listing = VehicleListing.objects.get(owner=self.owner)
        self.assertEqual(listing.suburb, self.soweto)
        self.assertEqual(Suburb.objects.filter(slug="soweto").count(), 1)


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


class PlatformScopeTests(ListingTestCase):
    """A member is offered the platforms of the market they work in."""

    def setUp(self):
        super().setUp()
        zw_province = Province.objects.create(
            country=Country.ZW, name="Harare Province", slug="zw-harare"
        )
        zw_city = City.objects.create(
            province=zw_province, name="Harare", slug="harare", is_launch_market=True
        )
        self.avondale = Suburb.objects.create(city=zw_city, name="Avondale", slug="avondale")
        self.hwindi = Platform.objects.create(
            name="Hwindi", slug="hwindi-zw", country=Country.ZW
        )

        self.zw_owner = self._make_user("harare@example.com", "Harare Owner", verified=True)
        self.zw_owner.profile.suburb = self.avondale
        self.zw_owner.profile.country = Country.ZW
        self.zw_owner.profile.save()

    def test_a_south_african_owner_is_not_offered_zimbabwean_platforms(self):
        offered = set(platform_choices(self.owner))
        self.assertIn(self.uber, offered)
        self.assertIn(self.bolt, offered)
        self.assertNotIn(self.hwindi, offered)

    def test_a_zimbabwean_owner_is_not_offered_south_african_platforms(self):
        offered = set(platform_choices(self.zw_owner))
        self.assertEqual(offered, {self.hwindi})

    def test_retired_platforms_are_never_offered(self):
        self.bolt.is_active = False
        self.bolt.save()
        self.assertNotIn(self.bolt, set(platform_choices(self.owner)))

    def test_editing_keeps_a_platform_the_listing_already_has(self):
        """
        The regression this guards: a platform is retired, the owner edits their
        price, and the advert quietly loses a platform they never touched.
        """
        listing = self.make_listing()
        listing.platforms.add(self.uber, self.bolt)
        self.bolt.is_active = False
        self.bolt.save()

        form = VehicleListingForm(instance=listing, user=self.owner)
        self.assertIn(self.bolt, set(form.fields["platforms"].queryset))

    def test_a_platform_from_another_country_is_not_offered_on_a_fresh_form(self):
        form = VehicleListingForm(user=self.zw_owner)
        self.assertEqual(set(form.fields["platforms"].queryset), {self.hwindi})


class VehicleNoteTests(ListingTestCase):
    """
    The owner's private log.

    The rule that matters is the first one: these are private. Everything else
    here is convenience; that one is the boundary between a management log and
    a backchannel review with no right of reply.
    """

    def setUp(self):
        super().setUp()
        self.car = self.make_listing()
        self.url = reverse("listings:notes", args=[self.car.uuid])

    def add(self, **overrides):
        # `save_note` is how the page tells a note from a schedule update —
        # both forms post to the same URL.
        data = {
            "save_note": "1",
            "kind": VehicleNote.Kind.SERVICE,
            "happened_on": date.today().isoformat(),
            "body": "Major service at Mbare Auto.",
        }
        data.update(overrides)
        return self.client.post(self.url, data)

    def test_an_owner_can_log_against_their_own_car(self):
        self.login(self.owner)
        self.add(odometer_km=142000)

        note = VehicleNote.objects.get()
        self.assertEqual(note.listing, self.car)
        self.assertEqual(note.author, self.owner)
        self.assertEqual(note.odometer_km, 142000)

    def test_nobody_else_can_read_the_log(self):
        self.login(self.owner)
        self.add(body="Driver keeps paying late.")

        self.login(self.driver)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    def test_nobody_else_can_write_to_it(self):
        self.login(self.driver)
        self.assertEqual(self.add().status_code, 404)
        self.assertFalse(VehicleNote.objects.exists())

    def test_staff_do_not_get_a_way_in_either(self):
        """
        Deliberate. Every other moderation surface on this site exists because
        something was published; nothing here is. A support screen that could
        read these would make "only you can see this" untrue.
        """
        self.login(self.owner)
        self.add()

        staff = self._make_user("staff@example.com", "Staff Member")
        staff.is_staff = True
        staff.save()
        self.login(staff)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_a_note_never_reaches_the_public_car_page(self):
        self.login(self.owner)
        self.add(body="Bumper scuff nobody owned up to.")

        self.login(self.driver)
        page = self.client.get(self.car.get_absolute_url()).content.decode()
        self.assertNotIn("Bumper scuff", page)

    def test_the_log_outlives_the_placement_it_mentions(self):
        """
        Why this hangs off the car. A service history that vanished when a
        driver left would leave the next one looking at a blank slate.
        """
        from apps.placements.models import Placement

        placement = Placement.objects.create(
            vehicle_listing=self.car, owner=self.owner, driver=self.driver,
            started_on=date.today() - timedelta(days=100),
            confirmed_by_owner=True, confirmed_by_driver=True,
        )
        self.login(self.owner)
        self.add(placement=placement.pk)

        placement.delete()

        note = VehicleNote.objects.get()
        self.assertIsNone(note.placement)
        self.assertEqual(note.listing, self.car)

    def test_a_future_date_is_refused(self):
        self.login(self.owner)
        self.add(happened_on=(date.today() + timedelta(days=1)).isoformat())
        self.assertFalse(VehicleNote.objects.exists())

    def test_the_placement_dropdown_only_offers_this_car(self):
        from apps.placements.models import Placement

        other_car = self.make_listing()
        elsewhere = Placement.objects.create(
            vehicle_listing=other_car, owner=self.owner, driver=self.driver,
            started_on=date.today(),
        )

        self.login(self.owner)
        response = self.client.get(self.url)
        offered = response.context["form"].fields["placement"].queryset
        self.assertNotIn(elsewhere, offered)

    def test_an_owner_can_delete_their_own_note(self):
        self.login(self.owner)
        self.add()
        note = VehicleNote.objects.get()

        self.client.post(
            reverse("listings:note_delete", args=[self.car.uuid, note.pk])
        )
        self.assertFalse(VehicleNote.objects.exists())


class FleetViewTests(ListingTestCase):
    """"Who has which car" — the question the page did not used to answer."""

    def test_it_shows_who_currently_has_each_car(self):
        from apps.placements.models import Placement

        out = self.make_listing()
        Placement.objects.create(
            vehicle_listing=out, owner=self.owner, driver=self.driver,
            started_on=date.today() - timedelta(days=20),
            confirmed_by_owner=True, confirmed_by_driver=True,
        )
        idle = self.make_listing()

        self.login(self.owner)
        cars = {c.pk: c for c in self.client.get(reverse("listings:mine")).context["listings"]}

        self.assertEqual(cars[out.pk].current_driver, self.driver)
        self.assertIsNone(cars[idle.pk].current_driver)

    def test_an_unconfirmed_placement_does_not_claim_the_car(self):
        """
        One person asserting a placement is an assertion. Showing a name
        against a car on nothing but that would put somebody's name there
        without their agreement.
        """
        from apps.placements.models import Placement

        car = self.make_listing()
        Placement.objects.create(
            vehicle_listing=car, owner=self.owner, driver=self.driver,
            started_on=date.today(), confirmed_by_owner=True,
        )

        self.login(self.owner)
        cars = {c.pk: c for c in self.client.get(reverse("listings:mine")).context["listings"]}
        self.assertIsNone(cars[car.pk].current_driver)

    def test_an_ended_placement_frees_the_car(self):
        from apps.placements.models import Placement

        car = self.make_listing()
        placement = Placement.objects.create(
            vehicle_listing=car, owner=self.owner, driver=self.driver,
            started_on=date.today() - timedelta(days=90),
            confirmed_by_owner=True, confirmed_by_driver=True,
        )
        placement.end(on=date.today())

        self.login(self.owner)
        cars = {c.pk: c for c in self.client.get(reverse("listings:mine")).context["listings"]}
        self.assertIsNone(cars[car.pk].current_driver)

    def test_the_fleet_does_not_cost_more_queries_as_it_grows(self):
        """
        An owner with six cars is the person this page is for. If the driver
        and the service note were fetched per car, the page would get slower
        the more successful its owner got.

        Asserted as "the count does not grow" rather than against a fixed
        number: the absolute figure changes whenever anything else on the page
        does, and a test that has to be renumbered every sprint gets renumbered
        without being read.
        """
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        from apps.placements.models import Placement

        def place(car):
            Placement.objects.create(
                vehicle_listing=car, owner=self.owner, driver=self.driver,
                started_on=date.today(), confirmed_by_owner=True,
                confirmed_by_driver=True,
            )

        self.login(self.owner)
        place(self.make_listing())

        # One warm-up request first. The very first hit of a session pays for
        # things that have nothing to do with the fleet — a session row, a
        # cache being filled — and counting those makes the comparison noise.
        self.client.get(reverse("listings:mine"))
        with CaptureQueriesContext(connection) as one_car:
            self.client.get(reverse("listings:mine"))

        for _ in range(4):
            place(self.make_listing())
        with CaptureQueriesContext(connection) as five_cars:
            self.client.get(reverse("listings:mine"))

        self.assertEqual(len(one_car), len(five_cars))


class ServiceScheduleTests(ListingTestCase):
    """
    The arithmetic behind a reminder. The rule that matters is that it refuses
    to guess: no interval, or no service to count from, means no schedule at
    all rather than a schedule built on an invented baseline.
    """

    def setUp(self):
        super().setUp()
        self.car = self.make_listing()

    def log_service(self, km, days_ago=0):
        return VehicleNote.objects.create(
            listing=self.car, author=self.owner, kind=VehicleNote.Kind.SERVICE,
            happened_on=date.today() - timedelta(days=days_ago),
            odometer_km=km, body="Serviced.",
        )

    def test_no_interval_means_no_schedule(self):
        self.log_service(140000)
        self.car.odometer_km = 149000
        self.assertIsNone(self.car.next_service_km)
        self.assertIsNone(self.car.service_state)

    def test_an_interval_with_no_service_logged_means_no_schedule(self):
        """
        There is nothing to count from. Guessing a baseline from today's
        reading would tell somebody their car is fine when nobody knows.
        """
        self.car.service_interval_km = 15000
        self.car.odometer_km = 149000
        self.assertIsNone(self.car.next_service_km)

    def test_the_schedule_counts_from_the_newest_service(self):
        self.log_service(120000, days_ago=400)
        self.log_service(142000, days_ago=40)

        self.car.service_interval_km = 15000
        self.assertEqual(self.car.last_service_km, 142000)
        self.assertEqual(self.car.next_service_km, 157000)

    def test_the_three_states(self):
        self.log_service(142000)
        self.car.service_interval_km = 15000   # due at 157000
        self.car.service_warn_km = 1000        # warn from 156000

        self.car.odometer_km = 150000
        self.assertEqual(self.car.service_state, "ok")

        self.car.odometer_km = 156500
        self.assertEqual(self.car.service_state, "due")

        self.car.odometer_km = 157200
        self.assertEqual(self.car.service_state, "overdue")
        self.assertEqual(self.car.km_to_service, -200)

    def test_an_odometer_below_the_last_service_is_refused(self):
        """
        A typo here does not merely look wrong — it makes the car look further
        from its service than it is, and silently postpones the reminder.
        """
        self.log_service(142000)
        form = ServiceScheduleForm(
            {"service_interval_km": 15000, "service_warn_km": 1000,
             "odometer_km": 100000},
            instance=self.car,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("odometer_km", form.errors)

    def test_the_reading_is_stamped_only_when_it_moves(self):
        form = ServiceScheduleForm(
            {"service_interval_km": 15000, "service_warn_km": 1000,
             "odometer_km": 149000},
            instance=self.car,
        )
        self.assertTrue(form.is_valid())
        car = form.save()
        first = car.odometer_at
        self.assertIsNotNone(first)

        again = ServiceScheduleForm(
            {"service_interval_km": 20000, "service_warn_km": 1000,
             "odometer_km": 149000},
            instance=car,
        )
        self.assertTrue(again.is_valid())
        self.assertEqual(again.save().odometer_at, first)


class ServiceReminderTests(ListingTestCase):
    """The cron. Its whole job is to fire once per cycle and then be quiet."""

    def setUp(self):
        super().setUp()
        self.car = self.make_listing()
        VehicleNote.objects.create(
            listing=self.car, author=self.owner, kind=VehicleNote.Kind.SERVICE,
            happened_on=date.today() - timedelta(days=60),
            odometer_km=142000, body="Serviced.",
        )
        VehicleListing.objects.filter(pk=self.car.pk).update(
            service_interval_km=15000, service_warn_km=1000, odometer_km=156500
        )
        self.car.refresh_from_db()

    def run_cron(self):
        call_command("send_service_reminders", verbosity=0)

    def test_it_notifies_the_owner(self):
        self.run_cron()
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.owner, kind=Notification.Kind.SERVICE_DUE
            ).exists()
        )

    def test_it_notifies_the_driver_who_has_the_car(self):
        Placement.objects.create(
            vehicle_listing=self.car, owner=self.owner, driver=self.driver,
            started_on=date.today() - timedelta(days=30),
            confirmed_by_owner=True, confirmed_by_driver=True,
        )
        self.run_cron()
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.driver, kind=Notification.Kind.SERVICE_DUE
            ).exists()
        )

    def test_it_does_not_notify_a_driver_who_handed_the_car_back(self):
        """Telling somebody to service a car they no longer have is worse than
        telling nobody."""
        placement = Placement.objects.create(
            vehicle_listing=self.car, owner=self.owner, driver=self.driver,
            started_on=date.today() - timedelta(days=200),
            confirmed_by_owner=True, confirmed_by_driver=True,
        )
        placement.end(on=date.today() - timedelta(days=5))

        self.run_cron()
        self.assertFalse(
            Notification.objects.filter(recipient=self.driver).exists()
        )

    def test_running_it_twice_sends_one_reminder(self):
        """
        The condition stays true until the car is serviced. Without the sent
        log this would fire every morning, and a daily notification is one
        people learn to dismiss without reading.
        """
        self.run_cron()
        self.run_cron()
        self.run_cron()

        self.assertEqual(
            Notification.objects.filter(
                recipient=self.owner, kind=Notification.Kind.SERVICE_DUE
            ).count(),
            1,
        )

    def test_the_warning_and_the_overdue_notice_are_separate(self):
        self.run_cron()   # warning, at 156500 of 157000

        VehicleListing.objects.filter(pk=self.car.pk).update(odometer_km=157500)
        self.run_cron()   # now overdue

        self.assertEqual(
            Notification.objects.filter(
                recipient=self.owner, kind=Notification.Kind.SERVICE_DUE
            ).count(),
            2,
        )

    def test_logging_the_service_starts_a_fresh_cycle(self):
        """
        The sent log is keyed on the odometer the service is due at, so a new
        service moves the key and nothing has to be reset or cleaned up.
        """
        self.run_cron()
        self.assertEqual(ServiceReminder.objects.count(), 1)

        VehicleNote.objects.create(
            listing=self.car, author=self.owner, kind=VehicleNote.Kind.SERVICE,
            happened_on=date.today(), odometer_km=157000, body="Serviced again.",
        )
        VehicleListing.objects.filter(pk=self.car.pk).update(odometer_km=171500)
        self.run_cron()

        self.assertEqual(ServiceReminder.objects.count(), 2)

    def test_a_car_that_is_fine_is_left_alone(self):
        VehicleListing.objects.filter(pk=self.car.pk).update(odometer_km=145000)
        self.run_cron()
        self.assertFalse(Notification.objects.exists())

    def test_a_dry_run_sends_and_records_nothing(self):
        call_command("send_service_reminders", dry_run=True, verbosity=0)
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(ServiceReminder.objects.exists())


class ServiceVisibilityTests(ListingTestCase):
    """What the driver sees, and everything they do not."""

    def setUp(self):
        super().setUp()
        self.car = self.make_listing()
        VehicleNote.objects.create(
            listing=self.car, author=self.owner, kind=VehicleNote.Kind.SERVICE,
            happened_on=date.today() - timedelta(days=60),
            odometer_km=142000, body="Serviced at Mbare Auto.",
        )
        VehicleNote.objects.create(
            listing=self.car, author=self.owner, kind=VehicleNote.Kind.INCIDENT,
            happened_on=date.today(), body="Bumper scuff nobody owned up to.",
        )
        VehicleListing.objects.filter(pk=self.car.pk).update(
            service_interval_km=15000, odometer_km=150000
        )
        self.car.refresh_from_db()

    def place_driver(self):
        return Placement.objects.create(
            vehicle_listing=self.car, owner=self.owner, driver=self.driver,
            started_on=date.today() - timedelta(days=10),
            confirmed_by_owner=True, confirmed_by_driver=True,
        )

    def test_the_current_driver_sees_the_next_service_figure(self):
        self.place_driver()
        self.login(self.driver)
        response = self.client.get(self.car.get_absolute_url())
        self.assertTrue(response.context["shows_service"])
        self.assertContains(response, "157000")

    def test_and_still_sees_none_of_the_log(self):
        self.place_driver()
        self.login(self.driver)
        page = self.client.get(self.car.get_absolute_url()).content.decode()
        self.assertNotIn("Bumper scuff", page)
        self.assertNotIn("Mbare Auto", page)

    def test_somebody_who_does_not_have_the_car_sees_nothing(self):
        self.login(self.driver)
        response = self.client.get(self.car.get_absolute_url())
        self.assertFalse(response.context["shows_service"])


class OdometerEstimateTests(ListingTestCase):
    """
    The fix for the whole feature. A reminder measured against a six-week-old
    reading fires when somebody next updates it, which is the moment they were
    already looking at the car.
    """

    def setUp(self):
        super().setUp()
        self.car = self.make_listing()
        VehicleNote.objects.create(
            listing=self.car, author=self.owner, kind=VehicleNote.Kind.SERVICE,
            happened_on=date.today() - timedelta(days=70),
            odometer_km=142000, body="Serviced.",
        )

    def set_reading(self, km, days_ago, weekly=None):
        VehicleListing.objects.filter(pk=self.car.pk).update(
            odometer_km=km,
            odometer_at=timezone.now() - timedelta(days=days_ago),
            service_interval_km=15000, service_warn_km=1000,
            weekly_km_limit=weekly,
        )
        self.car.refresh_from_db()

    def test_the_rate_is_measured_from_two_dated_readings(self):
        """7000 km over 70 days is 700 a week, whatever the form once said."""
        self.set_reading(149000, days_ago=0, weekly=99999)
        self.assertEqual(self.car.km_per_week, 700)

    def test_it_falls_back_to_the_declared_weekly_limit(self):
        # Reading equal to the last service leaves nothing to measure.
        self.set_reading(142000, days_ago=0, weekly=1200)
        self.assertEqual(self.car.km_per_week, 1200)

    def test_with_nothing_to_go_on_there_is_no_rate_and_no_projection(self):
        """
        A made-up default would turn no information into a confident estimate,
        which is worse than admitting there is none.
        """
        self.set_reading(142000, days_ago=40, weekly=None)
        self.assertIsNone(self.car.km_per_week)
        self.assertEqual(self.car.estimated_odometer_km, 142000)
        self.assertFalse(self.car.odometer_is_estimated)

    def test_a_stale_reading_is_projected_forward(self):
        """
        The rate spans the two READINGS, not the time since the service: the
        service was 70 days ago and the reading 42, so the car covered 7000 km
        in the 28 days between them — 1750 a week, or 250 a day. Projected
        across the 42 days since, that is +10500.
        """
        self.set_reading(149000, days_ago=42)
        self.assertEqual(self.car.km_per_week, 1750)
        self.assertEqual(self.car.estimated_odometer_km, 149000 + 10500)
        self.assertTrue(self.car.odometer_is_estimated)

    def test_the_reminder_is_measured_against_the_estimate(self):
        """
        The point of the whole change.

        Confirmed at 152 000 six weeks ago and due at 157 000, the raw reading
        says a comfortable 5000 km to go. The car has been moving since, and
        the estimate says it is past due. Against the stale figure this
        reminder would not fire until somebody next typed a number in — the
        moment they were already looking at the car.
        """
        self.set_reading(152000, days_ago=42)

        self.assertEqual(self.car.next_service_km, 157000)
        self.assertEqual(157000 - self.car.odometer_km, 5000)   # what it looked like
        self.assertLess(self.car.km_to_service, 0)              # what it is
        self.assertEqual(self.car.service_state, "overdue")

    def test_a_short_sample_window_is_not_a_rate(self):
        """
        Ten days of data extrapolated across a month amplifies whatever
        happened in those ten days. One busy fortnight would become a permanent
        4000 km/week and the car would read as overdue when it is not.
        """
        VehicleNote.objects.create(
            listing=self.car, author=self.owner, kind=VehicleNote.Kind.SERVICE,
            happened_on=date.today() - timedelta(days=12),
            odometer_km=150000, body="Serviced again.",
        )
        # Reading two days after that service: a 10-day window.
        self.set_reading(156000, days_ago=2, weekly=None)
        self.assertIsNone(self.car.km_per_week)

    def test_the_projection_stops_eventually(self):
        """
        Past the cap the figure is arithmetic rather than an estimate, and by
        then the monthly nudge has gone unanswered several times over. Better
        to under-state than to declare a car overdue on compounding guesswork.
        """
        self.set_reading(149000, days_ago=400)
        capped = self.car.estimated_odometer_km

        self.set_reading(149000, days_ago=800)
        self.assertEqual(self.car.estimated_odometer_km, capped)

    def test_a_fresh_reading_is_not_an_estimate(self):
        self.set_reading(149000, days_ago=0)
        self.assertFalse(self.car.odometer_is_estimated)
        self.assertEqual(self.car.estimated_odometer_km, self.car.odometer_km)


class OdometerConfirmationTests(ListingTestCase):
    """The driver's one field. The only current source there is."""

    def setUp(self):
        super().setUp()
        self.car = self.make_listing()
        VehicleNote.objects.create(
            listing=self.car, author=self.owner, kind=VehicleNote.Kind.SERVICE,
            happened_on=date.today() - timedelta(days=60),
            odometer_km=142000, body="Serviced.",
        )
        self.url = reverse("listings:confirm_odometer", args=[self.car.uuid])

    def place_driver(self):
        return Placement.objects.create(
            vehicle_listing=self.car, owner=self.owner, driver=self.driver,
            started_on=date.today() - timedelta(days=30),
            confirmed_by_owner=True, confirmed_by_driver=True,
        )

    def test_the_driver_holding_the_car_can_confirm(self):
        self.place_driver()
        self.login(self.driver)
        self.client.post(self.url, {"odometer_km": 149500})

        self.car.refresh_from_db()
        self.assertEqual(self.car.odometer_km, 149500)
        self.assertEqual(self.car.odometer_by, self.driver)
        self.assertIsNotNone(self.car.odometer_at)

    def test_the_owner_can_too(self):
        self.login(self.owner)
        self.client.post(self.url, {"odometer_km": 149500})
        self.car.refresh_from_db()
        self.assertEqual(self.car.odometer_by, self.owner)

    def test_somebody_who_does_not_have_the_car_cannot(self):
        self.login(self.driver)
        self.assertEqual(
            self.client.post(self.url, {"odometer_km": 1}).status_code, 404
        )

    def test_a_driver_who_handed_it_back_cannot(self):
        placement = self.place_driver()
        placement.end(on=date.today())

        self.login(self.driver)
        self.assertEqual(
            self.client.post(self.url, {"odometer_km": 1}).status_code, 404
        )

    def test_a_reading_below_the_last_service_is_refused(self):
        """A low reading silently postpones the service, which is the failure
        the whole feature exists to prevent."""
        self.place_driver()
        self.login(self.driver)
        self.client.post(self.url, {"odometer_km": 100000})

        self.car.refresh_from_db()
        self.assertIsNone(self.car.odometer_km)

    def test_rubbish_is_refused_without_a_500(self):
        self.place_driver()
        self.login(self.driver)
        response = self.client.post(self.url, {"odometer_km": "about 150k"})
        self.assertEqual(response.status_code, 302)
        self.car.refresh_from_db()
        self.assertIsNone(self.car.odometer_km)

    def test_confirming_resets_the_drift(self):
        self.place_driver()
        # The service was 60 days ago, so a reading 25 days ago leaves a
        # 35-day window — comfortably over MIN_RATE_WINDOW_DAYS, which is what
        # makes this a projection worth resetting.
        VehicleListing.objects.filter(pk=self.car.pk).update(
            odometer_km=149000,
            odometer_at=timezone.now() - timedelta(days=25),
            service_interval_km=15000,
        )
        self.car.refresh_from_db()
        self.assertTrue(self.car.odometer_is_estimated)

        self.login(self.driver)
        self.client.post(self.url, {"odometer_km": 151000})

        self.car.refresh_from_db()
        self.assertFalse(self.car.odometer_is_estimated)
        self.assertEqual(self.car.estimated_odometer_km, 151000)


class OdometerNudgeTests(ListingTestCase):
    """The monthly ask that keeps the projection from drifting forever."""

    def setUp(self):
        super().setUp()
        self.car = self.make_listing()
        VehicleListing.objects.filter(pk=self.car.pk).update(service_interval_km=15000)
        self.car.refresh_from_db()
        Placement.objects.create(
            vehicle_listing=self.car, owner=self.owner, driver=self.driver,
            started_on=date.today() - timedelta(days=60),
            confirmed_by_owner=True, confirmed_by_driver=True,
        )

    def run_cron(self):
        call_command("send_service_reminders", verbosity=0)

    def asked(self):
        return Notification.objects.filter(
            recipient=self.driver, kind=Notification.Kind.ODOMETER_ASK
        ).count()

    def test_a_car_with_no_reading_at_all_is_asked_about(self):
        self.run_cron()
        self.assertEqual(self.asked(), 1)

    def test_a_stale_reading_is_asked_about(self):
        VehicleListing.objects.filter(pk=self.car.pk).update(
            odometer_km=149000, odometer_at=timezone.now() - timedelta(days=45)
        )
        self.run_cron()
        self.assertEqual(self.asked(), 1)

    def test_a_fresh_reading_is_left_alone(self):
        VehicleListing.objects.filter(pk=self.car.pk).update(
            odometer_km=149000, odometer_at=timezone.now() - timedelta(days=3)
        )
        self.run_cron()
        self.assertEqual(self.asked(), 0)

    def test_a_car_with_no_schedule_is_left_alone(self):
        """Asking for a reading nobody is going to use is noise."""
        VehicleListing.objects.filter(pk=self.car.pk).update(service_interval_km=None)
        self.run_cron()
        self.assertEqual(self.asked(), 0)

    def test_a_dry_run_asks_nobody(self):
        call_command("send_service_reminders", dry_run=True, verbosity=0)
        self.assertEqual(self.asked(), 0)
