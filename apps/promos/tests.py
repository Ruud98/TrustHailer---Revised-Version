from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from .models import Promo
from . import services


class RailAssemblyTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_empty_table_renders_no_rails(self):
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, "siderail--left")

    def test_scheduling_window_is_respected(self):
        past = Promo.objects.create(
            title="Over", slot=Promo.Slot.RAIL,
            ends_at=timezone.now() - timedelta(days=1),
        )
        future = Promo.objects.create(
            title="Not yet", slot=Promo.Slot.RAIL,
            starts_at=timezone.now() + timedelta(days=1),
        )
        now = Promo.objects.create(title="Live now", slot=Promo.Slot.RAIL)

        live = list(Promo.objects.live())
        self.assertIn(now, live)
        self.assertNotIn(past, live)
        self.assertNotIn(future, live)

    def test_inactive_is_excluded(self):
        Promo.objects.create(title="Off", slot=Promo.Slot.RAIL, is_active=False)
        self.assertEqual(Promo.objects.live().count(), 0)

    def test_sort_order_decides_the_carousel_order(self):
        Promo.objects.create(title="Second", slot=Promo.Slot.RAIL, sort_order=2)
        Promo.objects.create(title="First", slot=Promo.Slot.RAIL, sort_order=1)

        slides = services.rail(include_businesses=False)["slides"]
        self.assertEqual([p.title for p in slides], ["First", "Second"])

    def test_slots_do_not_mix(self):
        Promo.objects.create(title="An ad", slot=Promo.Slot.AD)
        Promo.objects.create(title="A slide", slot=Promo.Slot.RAIL)

        rail = services.rail(include_businesses=False)
        self.assertEqual([p.title for p in rail["ads"]], ["An ad"])
        self.assertEqual([p.title for p in rail["slides"]], ["A slide"])

    def test_landing_page_renders_the_rails(self):
        Promo.objects.create(title="Reviews you cannot buy", slot=Promo.Slot.RAIL)
        response = self.client.get(reverse("home"))
        self.assertContains(response, "siderail--left")
        self.assertContains(response, "Reviews you cannot buy")

    def test_join_page_renders_the_rails(self):
        Promo.objects.create(title="Free while we build it out", slot=Promo.Slot.AD)
        response = self.client.get(reverse("accounts:join"))
        self.assertContains(response, "promo-ad")

    def test_promos_stay_on_the_left(self):
        """
        Adverts down both sides is what this looked like first and why it
        changed. If a right-hand promo rail ever comes back it is a decision,
        not an accident.
        """
        Promo.objects.create(title="Anything", slot=Promo.Slot.RAIL)
        response = self.client.get(reverse("home"))
        self.assertContains(response, "siderail--left")
        self.assertNotContains(response, "siderail--right siderail--left")

    def test_car_detail_does_not(self):
        """
        A rail beside somebody's listing is the case this feature opts out of.
        If this ever fails, {% promo_rails %} has leaked into base.html.
        """
        Promo.objects.create(title="Anything", slot=Promo.Slot.RAIL)
        response = self.client.get(reverse("listings:browse"))
        self.assertNotContains(response, "siderail--left")
