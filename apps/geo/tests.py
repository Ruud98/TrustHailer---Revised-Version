from django.test import TestCase

from .models import City, Country, Province, Suburb
from .resolve import clean_place_name, resolve, resolve_city, resolve_suburb


class PlaceNameTests(TestCase):
    def test_spacing_is_collapsed_and_trimmed(self):
        self.assertEqual(clean_place_name("  Ivory   Park \n"), "Ivory Park")

    def test_empty_input_stays_empty(self):
        self.assertEqual(clean_place_name(None), "")
        self.assertEqual(clean_place_name("   "), "")


class ResolveTests(TestCase):
    def setUp(self):
        self.gauteng = Province.objects.create(
            country=Country.ZA, name="Gauteng", slug="za-gauteng"
        )
        # Seeded with a hand-written slug that slugify() would never produce
        # from the name — the case that split a city in two before matching
        # looked at the name as well.
        self.jhb = City.objects.create(
            province=self.gauteng, name="Johannesburg", slug="jhb"
        )
        self.soweto = Suburb.objects.create(city=self.jhb, name="Soweto", slug="soweto")

    def test_an_existing_city_is_matched_despite_a_hand_written_slug(self):
        self.assertEqual(resolve_city("Johannesburg", Country.ZA), self.jhb)
        self.assertEqual(City.objects.count(), 1)

    def test_matching_ignores_case_and_spacing(self):
        self.assertEqual(resolve_suburb("  soweto ", self.jhb), self.soweto)
        self.assertEqual(resolve_suburb("SOWETO", self.jhb), self.soweto)
        self.assertEqual(Suburb.objects.count(), 1)

    def test_punctuation_differences_land_on_the_same_row(self):
        park = Suburb.objects.create(city=self.jhb, name="Ivory Park", slug="ivory-park")
        self.assertEqual(resolve_suburb("Ivory-Park", self.jhb), park)
        self.assertEqual(resolve_suburb("ivory park", self.jhb), park)

    def test_a_new_city_is_created_under_the_right_country(self):
        harare = resolve_city("Harare", Country.ZW)
        self.assertEqual(harare.name, "Harare")
        self.assertEqual(harare.province.country, Country.ZW)

    def test_the_same_name_in_two_countries_stays_two_rows(self):
        first = resolve_city("Springs", Country.ZA)
        second = resolve_city("Springs", Country.ZW)
        self.assertNotEqual(first, second)

    def test_the_first_spelling_is_the_one_that_sticks(self):
        """A later arrival joins the row rather than renaming it under everyone
        already filtering on that label."""
        resolve_suburb("Ivory Park", self.jhb)
        resolve_suburb("ivory park", self.jhb)
        self.assertEqual(
            list(Suburb.objects.filter(slug="ivory-park").values_list("name", flat=True)),
            ["Ivory Park"],
        )

    def test_resolving_both_halves_creates_the_city_first(self):
        suburb = resolve(" polokwane ", "Seshego", Country.ZA)
        self.assertEqual(suburb.name, "Seshego")
        self.assertEqual(suburb.city.name, "polokwane")

    def test_input_with_nothing_sluggable_resolves_to_nothing(self):
        self.assertIsNone(resolve_city("!!!", Country.ZA))
        self.assertIsNone(resolve_suburb("###", self.jhb))
        self.assertIsNone(resolve("", "Seshego", Country.ZA))

    def test_a_new_suburb_has_no_coordinates_yet(self):
        """Radius search falls back to the city centroid for these, which is the
        documented behaviour rather than an error."""
        suburb = resolve_suburb("Seshego", self.jhb)
        self.assertIsNone(suburb.latitude)
        self.assertIsNone(suburb.longitude)
