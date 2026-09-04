"""
Tests for imported Facebook adverts and the claim flow.

The ones that matter most here are the negative ones: that a phone number
cannot reach a page, that a stranger cannot take over a listing, and that an
import that has gone stale takes itself down. Those are the three ways this
feature could do real damage, so they get the most tests.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.management import call_command
from django.db.utils import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.redact import contains_contact_details, redact_contacts

from .importer import parse_advert
from .models import Arrangement, ListingClaim, Transmission, VehicleListing
from .tests import ListingTestCase

FB_POST = "https://www.facebook.com/groups/123456/posts/7890/"

ADVERT = """
2019 Toyota Corolla Quest 1.6 manual for rent.
R2 500 p/w, deposit R5000. Uber and Bolt approved.
Tracker fitted. PrDP required. Driver pays own fuel.
Call 082 123 4567 or WhatsApp me.
"""


class RedactionTests(TestCase):
    """`apps.core.redact` — the belt to the summarise-it-yourself braces."""

    def test_south_african_numbers_go(self):
        for raw in ["082 123 4567", "0821234567", "+27 82 123 4567", "27-82-123-4567"]:
            with self.subTest(raw=raw):
                self.assertNotIn("123", redact_contacts(f"Call {raw} today"))

    def test_zimbabwean_numbers_go(self):
        self.assertNotIn("77", redact_contacts("Ring +263 77 123 4567"))

    def test_prices_survive(self):
        """
        The whole reason the pattern is anchored on a dialling prefix rather
        than on a run of digits. A redactor that eats the rent is useless.
        """
        text = "R2 500 per week, deposit R5000, 2019 model, 1400 km a week"
        self.assertEqual(redact_contacts(text), text)

    def test_emails_and_chat_links_go(self):
        cleaned = redact_contacts("mail joe@example.co.za or wa.me/27821234567")
        self.assertNotIn("joe@example.co.za", cleaned)
        self.assertNotIn("wa.me", cleaned)

    def test_detector_agrees_with_the_redactor(self):
        self.assertTrue(contains_contact_details("call 082 123 4567"))
        self.assertFalse(contains_contact_details("R2 500 per week"))


class AdvertParserTests(TestCase):
    """Guesses only. Everything here is corrected by a human before it saves."""

    def test_reads_the_common_shape(self):
        guess = parse_advert(ADVERT)
        self.assertEqual(guess["year"], 2019)
        self.assertEqual(guess["make"], "Toyota")
        self.assertEqual(guess["model"], "Corolla Quest")
        self.assertEqual(guess["transmission"], Transmission.MANUAL)
        self.assertEqual(guess["weekly_rate"], 2500)
        self.assertEqual(guess["deposit_amount"], 5000)
        self.assertEqual(guess["arrangement"], Arrangement.WEEKLY)
        self.assertTrue(guess["requires_prdp"])
        self.assertTrue(guess["has_tracker"])
        self.assertEqual(guess["platform_slugs"], ["bolt", "uber"])

    def test_vw_becomes_volkswagen(self):
        self.assertEqual(parse_advert("VW Polo Vivo 2021 auto")["make"], "Volkswagen")

    def test_daily_and_no_prdp(self):
        guess = parse_advert("Rent R450 per day, no PDP needed, fully insured")
        self.assertEqual(guess["daily_rate"], 450)
        self.assertEqual(guess["arrangement"], Arrangement.DAILY)
        self.assertFalse(guess["requires_prdp"])
        self.assertTrue(guess["has_insurance"])

    def test_rent_to_own_wins_over_the_weekly_rate(self):
        guess = parse_advert("Rent to own Suzuki Swift 2022, R2800 pw over 36 months")
        self.assertEqual(guess["arrangement"], Arrangement.RENT2OWN)
        self.assertEqual(guess["weekly_rate"], 2800)

    def test_never_prefills_the_description(self):
        """
        The one field a guess must not touch. Pre-filling it would put somebody
        else's words in the box a tired admin is about to submit.
        """
        self.assertNotIn("description", parse_advert(ADVERT))

    def test_nonsense_yields_nothing_rather_than_a_wrong_guess(self):
        self.assertEqual(parse_advert("hi is the car still available"), {})


class ImportFlowTests(ListingTestCase):
    def setUp(self):
        super().setUp()
        self.staff = self._make_user("staff@example.com", "Staff Member", verified=True)
        self.staff.is_staff = True
        self.staff.save()

    def _import_payload(self, **overrides):
        payload = {
            "step": "review",
            "make": "Toyota", "model": "Corolla Quest", "year": "2019",
            "transmission": Transmission.MANUAL, "fuel_type": "petrol",
            "arrangement": Arrangement.WEEKLY, "weekly_rate": "2500",
            "deposit_amount": "5000",
            "fuel_paid_by": "driver", "maintenance_paid_by": "owner",
            "insurance_paid_by": "owner", "licensing_paid_by": "owner",
            "tracker_paid_by": "owner",
            "min_experience_years": "0",
            "city": str(self.city.pk), "suburb": str(self.soweto.pk),
            "description": "Corolla Quest on weekly rental, deposit up front.",
            "source_url": FB_POST,
            "source_author_name": "Group Poster",
            "summarised": "on",
        }
        payload.update(overrides)
        return payload

    # ----------------------------------------------------------- access

    def test_import_is_staff_only(self):
        self.login(self.owner)
        response = self.client.get(reverse("listings:import"))
        self.assertNotEqual(response.status_code, 200)

    def test_staff_can_open_the_paste_screen(self):
        self.login(self.staff)
        response = self.client.get(reverse("listings:import"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Paste the advert")

    def test_the_queue_is_staff_only_and_lists_what_is_waiting(self):
        listing = self.make_import()
        ListingClaim.objects.create(listing=listing, claimant=self.owner)

        self.login(self.owner)
        self.assertNotEqual(self.client.get(reverse("listings:import_queue")).status_code, 200)

        self.login(self.staff)
        response = self.client.get(reverse("listings:import_queue"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nissan Almera")
        self.assertContains(response, "Owner One")

    def test_the_claim_page_renders_for_a_verified_member(self):
        listing = self.make_import()
        self.login(self.owner)
        response = self.client.get(reverse("listings:claim", args=[listing.uuid]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Is this your car?")

    # ----------------------------------------------------------- step one

    def test_paste_prefills_the_review_form(self):
        self.login(self.staff)
        response = self.client.post(
            reverse("listings:import"), {"source_url": FB_POST, "raw_text": ADVERT}
        )
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form.initial["make"], "Toyota")
        self.assertEqual(form.initial["weekly_rate"], 2500)
        self.assertEqual(form.initial["source_url"], FB_POST)
        # Nothing is written until the human submits the second screen.
        self.assertFalse(VehicleListing.objects.exists())

    def test_the_raw_paste_is_shown_but_never_stored(self):
        self.login(self.staff)
        response = self.client.post(
            reverse("listings:import"), {"source_url": FB_POST, "raw_text": ADVERT}
        )
        self.assertContains(response, "Corolla Quest 1.6 manual")
        self.assertNotIn("raw_text", [f.name for f in VehicleListing._meta.get_fields()])

    def test_a_link_that_is_not_facebook_is_refused(self):
        self.login(self.staff)
        response = self.client.post(
            reverse("listings:import"),
            {"source_url": "https://example.com/post/1", "raw_text": ADVERT},
        )
        self.assertContains(response, "not a Facebook post link")

    def test_the_same_post_cannot_be_imported_twice(self):
        self.login(self.staff)
        self.client.post(reverse("listings:import"), self._import_payload())
        response = self.client.post(
            reverse("listings:import"), {"source_url": FB_POST, "raw_text": ADVERT}
        )
        self.assertContains(response, "already on the site")

    # ----------------------------------------------------------- step two

    def test_publishing_an_import_creates_an_unowned_live_listing(self):
        self.login(self.staff)
        response = self.client.post(reverse("listings:import"), self._import_payload())
        listing = VehicleListing.objects.get()
        self.assertRedirects(response, listing.get_absolute_url())
        self.assertEqual(listing.source, VehicleListing.Source.FACEBOOK)
        self.assertEqual(listing.status, VehicleListing.Status.ACTIVE)
        self.assertIsNone(listing.owner_id)
        self.assertEqual(listing.imported_by, self.staff)
        self.assertEqual(listing.source_url, FB_POST)
        self.assertTrue(listing.is_claimable)

    def test_an_import_publishes_without_a_photo(self):
        """
        The owner-side rule is that a car needs a picture before it goes live.
        We deliberately do not copy the photos off the original post, so the
        same rule here would mean no imports at all.
        """
        self.login(self.staff)
        self.client.post(reverse("listings:import"), self._import_payload())
        listing = VehicleListing.objects.get()
        self.assertFalse(listing.photos.exists())
        self.assertTrue(listing.is_live)

    def test_a_phone_number_in_the_summary_is_stripped_before_it_saves(self):
        self.login(self.staff)
        self.client.post(
            reverse("listings:import"),
            self._import_payload(description="Corolla Quest. Call 082 123 4567."),
        )
        listing = VehicleListing.objects.get()
        self.assertNotIn("082", listing.description)
        self.assertNotIn("123 4567", listing.description)
        self.assertIn("Corolla Quest", listing.description)

    def test_the_number_never_reaches_the_page(self):
        self.login(self.staff)
        self.client.post(
            reverse("listings:import"),
            self._import_payload(description="Ask for Sipho on 0821234567."),
        )
        listing = VehicleListing.objects.get()
        self.client.logout()
        response = self.client.get(listing.get_absolute_url())
        self.assertNotContains(response, "0821234567")

    def test_a_pasted_essay_is_refused(self):
        """Summarise, do not republish. The cap is what makes that real."""
        self.login(self.staff)
        response = self.client.post(
            reverse("listings:import"), self._import_payload(description="x" * 900)
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(VehicleListing.objects.exists())

    def test_the_confirmation_is_required(self):
        self.login(self.staff)
        payload = self._import_payload()
        del payload["summarised"]
        response = self.client.post(reverse("listings:import"), payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(VehicleListing.objects.exists())

    # ----------------------------------------------------------- the page

    def test_detail_links_to_the_original_and_offers_no_introduction(self):
        listing = self.make_import()
        self.login(self.driver)
        response = self.client.get(listing.get_absolute_url())
        self.assertContains(response, FB_POST)
        self.assertContains(response, "Open the original post")
        self.assertNotContains(response, "Request an introduction")

    def test_the_card_says_where_it_came_from(self):
        self.make_import()
        response = self.client.get(reverse("listings:browse"))
        self.assertContains(response, "From Facebook")

    def test_members_own_listings_rank_above_imports(self):
        """
        Imports are scaffolding. The moment there is a real listing to show,
        it goes first — an import cannot be introduced through, which is the
        thing drivers are actually here for.
        """
        self.make_import()
        mine = self.make_listing()
        ranked = list(VehicleListing.objects.live().ranked())
        self.assertEqual(ranked[0].pk, mine.pk)

    # ------------------------------------------------------------- claims

    def test_a_driver_can_claim_and_it_only_opens_a_request(self):
        listing = self.make_import()
        self.login(self.owner)
        response = self.client.post(
            reverse("listings:claim", args=[listing.uuid]), {"message": "I posted it."}
        )
        self.assertRedirects(response, listing.get_absolute_url())
        claim = ListingClaim.objects.get()
        self.assertTrue(claim.is_pending)
        listing.refresh_from_db()
        self.assertIsNone(listing.owner_id)

    def test_claiming_needs_no_phone_verification(self):
        """
        What stops a stranger taking somebody's advert is not a check at the
        door but the staff decision at the end — see `ListingClaim`.
        """
        listing = self.make_import()
        unverified = self._make_user("new@example.com", "New Person", verified=False)
        self.login(unverified)
        self.client.post(
            reverse("listings:claim", args=[listing.uuid]), {"message": "mine"}
        )
        self.assertTrue(ListingClaim.objects.filter(claimant=unverified).exists())

    def test_you_cannot_claim_the_same_listing_twice(self):
        listing = self.make_import()
        self.login(self.owner)
        url = reverse("listings:claim", args=[listing.uuid])
        self.client.post(url, {"message": "mine"})
        response = self.client.post(url, {"message": "mine again"})
        self.assertEqual(ListingClaim.objects.count(), 1)
        self.assertContains(response, "already claimed")

    def test_approval_hands_the_listing_over_and_keeps_the_provenance(self):
        listing = self.make_import()
        claim = ListingClaim.objects.create(listing=listing, claimant=self.owner)
        claim.approve(by=self.staff)

        listing.refresh_from_db()
        self.assertEqual(listing.owner, self.owner)
        self.assertIsNotNone(listing.claimed_at)
        self.assertFalse(listing.is_claimable)
        # Where it came from is not rewritten. The link on the page stays true.
        self.assertEqual(listing.source, VehicleListing.Source.FACEBOOK)
        self.assertEqual(listing.source_url, FB_POST)

    def test_approving_one_claim_closes_the_others(self):
        listing = self.make_import()
        mine = ListingClaim.objects.create(listing=listing, claimant=self.owner)
        theirs = ListingClaim.objects.create(listing=listing, claimant=self.driver)
        mine.approve(by=self.staff)
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, ListingClaim.Status.REJECTED)

    def test_a_claimed_listing_cannot_be_claimed_again(self):
        listing = self.make_import()
        ListingClaim.objects.create(listing=listing, claimant=self.owner).approve(by=self.staff)
        self.login(self.driver)
        response = self.client.post(
            reverse("listings:claim", args=[listing.uuid]), {"message": "actually mine"}
        )
        self.assertRedirects(response, listing.get_absolute_url())
        self.assertEqual(ListingClaim.objects.filter(status="pending").count(), 0)

    def test_the_new_owner_can_edit_what_they_claimed(self):
        listing = self.make_import()
        ListingClaim.objects.create(listing=listing, claimant=self.owner).approve(by=self.staff)
        self.login(self.owner)
        response = self.client.get(reverse("listings:edit", args=[listing.uuid]))
        self.assertEqual(response.status_code, 200)

    def test_nobody_can_edit_an_unclaimed_import(self):
        listing = self.make_import()
        self.login(self.owner)
        response = self.client.get(reverse("listings:edit", args=[listing.uuid]))
        self.assertEqual(response.status_code, 404)

    # ------------------------------------------------------------ expiry

    def test_stale_imports_archive_themselves(self):
        fresh = self.make_import()
        stale = self.make_import(source_url=FB_POST + "2")
        VehicleListing.objects.filter(pk=stale.pk).update(
            published_at=timezone.now()
            - timedelta(days=VehicleListing.IMPORT_STALE_DAYS + 1)
        )

        call_command("expire_imports", verbosity=0)

        stale.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual(stale.status, VehicleListing.Status.ARCHIVED)
        self.assertEqual(fresh.status, VehicleListing.Status.ACTIVE)

    def test_a_claimed_import_is_left_alone_however_old(self):
        """There is a person behind it now, and they can pause it themselves."""
        listing = self.make_import()
        ListingClaim.objects.create(listing=listing, claimant=self.owner).approve(by=self.staff)
        VehicleListing.objects.filter(pk=listing.pk).update(
            published_at=timezone.now() - timedelta(days=365)
        )
        call_command("expire_imports", verbosity=0)
        listing.refresh_from_db()
        self.assertEqual(listing.status, VehicleListing.Status.ACTIVE)

    def test_a_members_own_listing_is_never_touched(self):
        mine = self.make_listing()
        VehicleListing.objects.filter(pk=mine.pk).update(
            published_at=timezone.now() - timedelta(days=365)
        )
        call_command("expire_imports", verbosity=0)
        mine.refresh_from_db()
        self.assertEqual(mine.status, VehicleListing.Status.ACTIVE)

    # ------------------------------------------------------------- model

    def test_a_listing_with_no_owner_and_no_source_is_refused_by_the_database(self):
        """
        The invariant lives in a check constraint, so it holds against a stray
        `update()` or a shell session as well as against the forms.
        """
        with self.assertRaises(IntegrityError):
            VehicleListing.objects.create(
                owner=None,
                make="Toyota", model="Corolla", year=2019,
                transmission=Transmission.MANUAL, arrangement=Arrangement.WEEKLY,
                weekly_rate=Decimal("2500"), suburb=self.soweto,
            )

    # ------------------------------------------------------------ helper

    def make_import(self, **overrides):
        defaults = dict(
            make="Nissan", model="Almera", year=2018,
            transmission=Transmission.MANUAL,
            arrangement=Arrangement.WEEKLY, weekly_rate=Decimal("2300"),
            deposit_amount=Decimal("4000"), suburb=self.soweto,
            status=VehicleListing.Status.ACTIVE,
            source=VehicleListing.Source.FACEBOOK,
            source_url=FB_POST,
            source_author_name="Group Poster",
            imported_by=self.staff,
            description="Almera on weekly rental in Soweto.",
        )
        defaults.update(overrides)
        return VehicleListing.objects.create(owner=None, **defaults)
