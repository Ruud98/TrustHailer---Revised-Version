"""
Tests for the business directory.

The rule most worth defending here runs the opposite direction from every
other listing on the site: the phone number is NOT masked, and it has to
render in full on the detail page and the card from the moment the listing
goes live — that is the entire point of a directory entry.
"""
from django.urls import reverse

from apps.listings.tests import ListingTestCase
from apps.notifications.models import Notification

from .models import BusinessListing


class BusinessTestCase(ListingTestCase):
    def make_business(self, owner=None, **overrides):
        defaults = dict(
            name="Speedy Tyres", category=BusinessListing.Category.TYRES,
            description="Fast fitting, fair prices.", phone="+27821234567",
            suburb=self.soweto, status=BusinessListing.Status.ACTIVE,
        )
        defaults.update(overrides)
        return BusinessListing.objects.create(owner_user=owner, **defaults)


class CreateTests(BusinessTestCase):
    def test_a_verified_phone_can_list_a_business(self):
        self.login(self.owner)
        response = self.client.post(
            reverse("directory:create"),
            {
                "name": "Speedy Tyres", "category": BusinessListing.Category.TYRES,
                "description": "Fast fitting, fair prices, open weekends.",
                "phone": "082 123 4567", "city": self.city.pk, "suburb": self.soweto.pk,
            },
        )
        listing = BusinessListing.objects.get()
        self.assertRedirects(response, listing.get_absolute_url())
        self.assertEqual(listing.owner_user, self.owner)
        self.assertEqual(listing.status, BusinessListing.Status.ACTIVE)
        # Stored in E.164, same as every other phone number on the site.
        self.assertEqual(listing.phone, "+27821234567")

    def test_listing_a_business_needs_no_phone_verification(self):
        unverified = self._make_user("new@example.com", "New Person", verified=False)
        self.login(unverified)
        response = self.client.post(
            reverse("directory:create"),
            {
                "name": "Speedy Tyres", "category": BusinessListing.Category.TYRES,
                "description": "Fast fitting, fair prices, open weekends.",
                "phone": "082 123 4567", "city": self.city.pk, "suburb": self.soweto.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(BusinessListing.objects.filter(name="Speedy Tyres").exists())

    def test_a_landline_is_accepted(self):
        """Businesses call from landlines all the time; this is the one place
        on the site a mobile-only number is the wrong rule."""
        self.login(self.owner)
        self.client.post(
            reverse("directory:create"),
            {
                "name": "Speedy Tyres", "category": BusinessListing.Category.TYRES,
                "description": "Fast fitting, fair prices, open weekends.",
                "phone": "011 123 4567", "city": self.city.pk, "suburb": self.soweto.pk,
            },
        )
        self.assertTrue(BusinessListing.objects.exists())

    def test_a_slug_is_generated_and_unique(self):
        self.make_business(name="Speedy Tyres")
        second = self.make_business(name="Speedy Tyres")
        self.assertEqual(second.slug, "speedy-tyres-2")


class DetailPageTests(BusinessTestCase):
    def test_the_number_shows_in_full_immediately(self):
        """
        The opposite rule from every other listing on the site — see the
        module docstring. There is no introduction step to gate it behind.
        """
        listing = self.make_business(owner=self.owner)
        response = self.client.get(listing.get_absolute_url())
        self.assertContains(response, "082 123 4567")
        self.assertNotContains(response, "Request an introduction")

    def test_a_stale_slug_redirects_to_the_current_one(self):
        listing = self.make_business(owner=self.owner)
        stale_url = f"/directory/{listing.pk}-not-the-real-slug/"
        response = self.client.get(stale_url)
        self.assertRedirects(response, listing.get_absolute_url())

    def test_a_paused_listing_is_hidden_from_the_public(self):
        listing = self.make_business(owner=self.owner, status=BusinessListing.Status.PAUSED)
        response = self.client.get(listing.get_absolute_url())
        self.assertEqual(response.status_code, 404)

    def test_the_owner_can_still_see_a_paused_listing(self):
        listing = self.make_business(owner=self.owner, status=BusinessListing.Status.PAUSED)
        self.login(self.owner)
        response = self.client.get(listing.get_absolute_url())
        self.assertEqual(response.status_code, 200)

    def test_a_hidden_business_gives_everyone_but_staff_a_404(self):
        listing = self.make_business(owner=self.owner)
        BusinessListing.objects.filter(pk=listing.pk).update(is_hidden=True)
        self.assertEqual(self.client.get(listing.get_absolute_url()).status_code, 404)


class BrowseTests(BusinessTestCase):
    def test_the_directory_lists_visible_businesses(self):
        self.make_business(owner=self.owner)
        response = self.client.get(reverse("directory:browse"))
        self.assertEqual(response.context["total"], 1)

    def test_a_paused_or_hidden_business_does_not_appear(self):
        self.make_business(owner=self.owner, status=BusinessListing.Status.PAUSED)
        hidden = self.make_business(owner=self.owner, phone="+27821234568")
        BusinessListing.objects.filter(pk=hidden.pk).update(is_hidden=True)
        response = self.client.get(reverse("directory:browse"))
        self.assertEqual(response.context["total"], 0)

    def test_the_category_filter_narrows_results(self):
        self.make_business(owner=self.owner, category=BusinessListing.Category.TYRES)
        self.make_business(
            owner=self.owner, category=BusinessListing.Category.MECHANIC,
            phone="+27821234569",
        )
        response = self.client.get(
            reverse("directory:browse") + f"?category={BusinessListing.Category.MECHANIC}"
        )
        self.assertEqual(response.context["total"], 1)

    def test_the_category_path_works_too(self):
        self.make_business(owner=self.owner, category=BusinessListing.Category.TYRES)
        response = self.client.get(
            reverse("directory:browse_category", args=[BusinessListing.Category.TYRES])
        )
        self.assertEqual(response.context["total"], 1)

    def test_an_unknown_category_in_the_path_is_a_404(self):
        response = self.client.get(reverse("directory:browse_category", args=["not-a-category"]))
        self.assertEqual(response.status_code, 404)


class OwnershipTests(BusinessTestCase):
    def test_the_owner_can_edit_their_listing(self):
        listing = self.make_business(owner=self.owner)
        self.login(self.owner)
        response = self.client.post(
            reverse("directory:edit", args=[listing.pk]),
            {
                "name": "Speedy Tyres and Exhausts", "category": listing.category,
                "description": listing.description, "phone": "082 123 4567",
                "city": self.city.pk, "suburb": self.soweto.pk,
            },
        )
        listing.refresh_from_db()
        self.assertEqual(listing.name, "Speedy Tyres and Exhausts")
        self.assertRedirects(response, listing.get_absolute_url())

    def test_nobody_else_can_edit_it(self):
        listing = self.make_business(owner=self.owner)
        self.login(self.driver)
        response = self.client.get(reverse("directory:edit", args=[listing.pk]))
        self.assertEqual(response.status_code, 404)

    def test_my_businesses_lists_only_your_own(self):
        self.make_business(owner=self.owner)
        self.make_business(owner=self.driver, phone="+27821234570")
        self.login(self.owner)
        response = self.client.get(reverse("directory:mine"))
        self.assertEqual(len(response.context["listings"]), 1)

    def test_pausing_takes_it_off_the_directory(self):
        listing = self.make_business(owner=self.owner)
        self.login(self.owner)
        self.client.post(
            reverse("directory:set_status", args=[listing.pk]), {"status": "paused"}
        )
        listing.refresh_from_db()
        self.assertEqual(listing.status, BusinessListing.Status.PAUSED)


class VerificationTests(BusinessTestCase):
    def test_staff_verifying_notifies_the_owner(self):
        listing = self.make_business(owner=self.driver)
        staff = self._make_user("staff9@example.com", "Staff Nine", verified=True)
        staff.is_staff = True
        staff.is_superuser = True
        staff.save()

        self.login(staff)
        self.client.post(
            reverse("admin:directory_businesslisting_changelist"),
            {"action": "verify_businesses", "_selected_action": [listing.pk]},
        )

        listing.refresh_from_db()
        self.assertTrue(listing.is_verified)
        self.assertIsNotNone(listing.verified_at)
        n = Notification.objects.get(kind=Notification.Kind.BUSINESS_VERIFIED)
        self.assertEqual(n.recipient, self.driver)

    def test_a_business_with_no_linked_account_is_not_a_crash_to_verify(self):
        """
        Staff can hand-enter a business with nobody signed up yet — the same
        cold-start move the Facebook advert importer makes for cars. Verifying
        one has nobody to notify, and that has to be a normal outcome.
        """
        listing = self.make_business(owner=None)
        staff = self._make_user("staff10@example.com", "Staff Ten", verified=True)
        staff.is_staff = True
        staff.is_superuser = True
        staff.save()

        self.login(staff)
        response = self.client.post(
            reverse("admin:directory_businesslisting_changelist"),
            {"action": "verify_businesses", "_selected_action": [listing.pk]},
        )
        self.assertEqual(response.status_code, 302)
        listing.refresh_from_db()
        self.assertTrue(listing.is_verified)
        self.assertFalse(Notification.objects.exists())


class ReportingTests(BusinessTestCase):
    def test_a_business_can_be_reported_as_itself(self):
        listing = self.make_business(owner=self.owner)
        self.login(self.driver)
        response = self.client.post(
            reverse("safety:report") + f"?business={listing.pk}",
            {"reason": "scam", "detail": "Charged double the quoted price."},
        )
        self.assertRedirects(response, listing.get_absolute_url())
        from apps.safety.models import Report

        self.assertEqual(Report.objects.get().target, listing)

    def test_the_owner_cannot_report_their_own_business(self):
        listing = self.make_business(owner=self.owner)
        self.login(self.owner)
        self.client.post(
            reverse("safety:report") + f"?business={listing.pk}",
            {"reason": "scam", "detail": "Testing."},
        )
        from apps.safety.models import Report

        self.assertFalse(Report.objects.exists())
