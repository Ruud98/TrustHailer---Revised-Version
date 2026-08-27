"""
Seed provinces, cities and suburbs.

Idempotent — safe to re-run after adding entries below.

    python manage.py seed_geo
    python manage.py seed_geo --launch-market johannesburg

NOTE ON COORDINATES
-------------------
Only a handful of suburbs below carry lat/lng, and those are approximate
(suburb centroid, good to a kilometre or two). That is accurate enough for a
"within 20km" filter but not for anything finer. Everything else is null.

Before you switch on radius search, run a geocoding pass over the null rows and
verify a sample by hand. Do not guess coordinates — a wrong one silently puts a
car in the wrong part of the city, and the user has no way to tell.
Suburbs with null coordinates fall back to their city's centroid.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from apps.geo.models import City, Country, Province, Suburb

# (name, latitude, longitude) — coordinates optional
GAUTENG = {
    "Johannesburg": [
        ("Johannesburg CBD", -26.2041, 28.0473),
        ("Braamfontein", -26.1929, 28.0305),
        ("Hillbrow", -26.1875, 28.0489),
        ("Berea", -26.1874, 28.0555),
        ("Yeoville", -26.1836, 28.0631),
        ("Bez Valley", None, None),
        ("Observatory", None, None),
        ("Troyeville", None, None),
        ("Kensington", None, None),
        ("Malvern", None, None),
        ("Rosettenville", None, None),
        ("Turffontein", None, None),
        ("La Rochelle", None, None),
        ("Mondeor", None, None),
        ("Glenvista", None, None),
        ("Bassonia", None, None),
        ("Kibler Park", None, None),
        ("Southgate", None, None),
        ("Parktown", None, None),
        ("Melville", None, None),
        ("Greenside", None, None),
        ("Emmarentia", None, None),
        ("Linden", None, None),
        ("Northcliff", None, None),
        ("Rosebank", -26.1467, 28.0436),
        ("Houghton", None, None),
        ("Norwood", None, None),
        ("Orange Grove", None, None),
        ("Alexandra", -26.1044, 28.0975),
        ("Sandton", -26.1076, 28.0567),
        ("Bryanston", None, None),
        ("Rivonia", None, None),
        ("Morningside", None, None),
        ("Fourways", None, None),
        ("Sunninghill", None, None),
        ("Douglasdale", None, None),
        ("Randburg", -26.0936, 28.0064),
        ("Ferndale", None, None),
        ("Cresta", None, None),
        ("Northgate", None, None),
        ("Midrand", -25.9992, 28.1264),
        ("Halfway House", None, None),
        ("Ivory Park", None, None),
        ("Rabie Ridge", None, None),
        ("Roodepoort", -26.1625, 27.8725),
        ("Florida", None, None),
        ("Discovery", None, None),
        ("Constantia Kloof", None, None),
        ("Honeydew", None, None),
        ("Soweto", -26.2678, 27.8586),
        ("Orlando East", None, None),
        ("Orlando West", None, None),
        ("Diepkloof", None, None),
        ("Pimville", None, None),
        ("Meadowlands", None, None),
        ("Dobsonville", None, None),
        ("Protea Glen", None, None),
        ("Naledi", None, None),
        ("Jabulani", None, None),
        ("Zola", None, None),
        ("Chiawelo", None, None),
        ("Braamfischerville", None, None),
        ("Lenasia", -26.3333, 27.8333),
        ("Eldorado Park", None, None),
        ("Ennerdale", None, None),
        ("Orange Farm", -26.4750, 27.8583),
        ("Devland", None, None),
    ],
    "Ekurhuleni": [
        ("Tembisa", -25.9964, 28.2264),
        ("Kempton Park", -26.1000, 28.2333),
        ("Birchleigh", None, None),
        ("Norkem Park", None, None),
        ("Edenvale", -26.1417, 28.1542),
        ("Bedfordview", None, None),
        ("Primrose", None, None),
        ("Germiston", -26.2178, 28.1672),
        ("Isando", None, None),
        ("Elandsfontein", None, None),
        ("Katlehong", -26.3333, 28.1500),
        ("Vosloorus", -26.3583, 28.2000),
        ("Thokoza", None, None),
        ("Alberton", -26.2672, 28.1219),
        ("Brackendowns", None, None),
        ("Boksburg", -26.2125, 28.2597),
        ("Reiger Park", None, None),
        ("Benoni", -26.1885, 28.3208),
        ("Daveyton", -26.1500, 28.4000),
        ("Etwatwa", None, None),
        ("Wattville", None, None),
        ("Springs", -26.2500, 28.4000),
        ("Kwa-Thema", None, None),
        ("Brakpan", -26.2353, 28.3700),
        ("Tsakane", None, None),
        ("Duduza", None, None),
        ("Nigel", None, None),
        ("Bapsfontein", None, None),
    ],
    "Tshwane": [
        ("Pretoria Central", -25.7479, 28.2293),
        ("Arcadia", None, None),
        ("Sunnyside", None, None),
        ("Hatfield", -25.7487, 28.2380),
        ("Brooklyn", None, None),
        ("Menlyn", None, None),
        ("Lynnwood", None, None),
        ("Waterkloof", None, None),
        ("Silverton", None, None),
        ("Mamelodi", -25.7167, 28.3833),
        ("Eersterust", None, None),
        ("Centurion", -25.8603, 28.1894),
        ("Lyttelton", None, None),
        ("Olievenhoutbosch", None, None),
        ("Laudium", None, None),
        ("Atteridgeville", -25.7667, 28.0667),
        ("Saulsville", None, None),
        ("Pretoria North", None, None),
        ("Akasia", None, None),
        ("Montana", None, None),
        ("Wonderboom", None, None),
        ("Soshanguve", -25.5167, 28.1000),
        ("Ga-Rankuwa", None, None),
        ("Mabopane", None, None),
        ("Hammanskraal", None, None),
        ("Rosslyn", None, None),
    ],
    "West Rand": [
        ("Krugersdorp", -26.1000, 27.7667),
        ("Kagiso", None, None),
        ("Randfontein", -26.1833, 27.7000),
        ("Mohlakeng", None, None),
        ("Westonaria", None, None),
        ("Bekkersdal", None, None),
        ("Carletonville", None, None),
        ("Khutsong", None, None),
        ("Muldersdrift", None, None),
        ("Magaliesburg", None, None),
    ],
    "Sedibeng": [
        ("Vereeniging", -26.6731, 27.9319),
        ("Vanderbijlpark", -26.7100, 27.8378),
        ("Sebokeng", None, None),
        ("Evaton", None, None),
        ("Sharpeville", None, None),
        ("Boipatong", None, None),
        ("Meyerton", None, None),
        ("Heidelberg", None, None),
        ("Ratanda", None, None),
    ],
}

HARARE = [
    ("Harare CBD", -17.8292, 31.0522),
    ("Avondale", None, None),
    ("Belvedere", None, None),
    ("Mount Pleasant", None, None),
    ("Borrowdale", None, None),
    ("Highlands", None, None),
    ("Msasa", None, None),
    ("Waterfalls", None, None),
    ("Hatfield", None, None),
    ("Mbare", None, None),
    ("Highfield", None, None),
    ("Glen View", None, None),
    ("Glen Norah", None, None),
    ("Budiriro", None, None),
    ("Kuwadzana", None, None),
    ("Warren Park", None, None),
    ("Dzivarasekwa", None, None),
    ("Mufakose", None, None),
    ("Mabvuku", None, None),
    ("Tafara", None, None),
    ("Epworth", None, None),
    ("Chitungwiza", -18.0128, 31.0756),
    ("Norton", None, None),
    ("Ruwa", None, None),
]

BULAWAYO = [
    ("Bulawayo CBD", -20.1500, 28.5833),
    ("Hillside", None, None),
    ("Suburbs", None, None),
    ("Mzilikazi", None, None),
    ("Njube", None, None),
    ("Luveve", None, None),
    ("Nkulumane", None, None),
    ("Pumula", None, None),
    ("Cowdray Park", None, None),
    ("Emganwini", None, None),
]


class Command(BaseCommand):
    help = "Seed provinces, cities and suburbs for the launch markets."

    def add_arguments(self, parser):
        parser.add_argument(
            "--launch-market",
            action="append",
            default=[],
            help="City slug to flag as a launch market (repeatable).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        created_suburbs = 0

        gauteng, _ = Province.objects.get_or_create(
            slug="za-gauteng", defaults={"country": Country.ZA, "name": "Gauteng"}
        )
        for city_name, suburbs in GAUTENG.items():
            created_suburbs += self._seed_city(gauteng, city_name, suburbs)

        harare_prov, _ = Province.objects.get_or_create(
            slug="zw-harare", defaults={"country": Country.ZW, "name": "Harare Province"}
        )
        created_suburbs += self._seed_city(harare_prov, "Harare", HARARE)

        byo_prov, _ = Province.objects.get_or_create(
            slug="zw-bulawayo", defaults={"country": Country.ZW, "name": "Bulawayo Province"}
        )
        created_suburbs += self._seed_city(byo_prov, "Bulawayo", BULAWAYO)

        for slug in options["launch_market"]:
            updated = City.objects.filter(slug=slug).update(is_launch_market=True)
            if updated:
                self.stdout.write(self.style.SUCCESS(f"Launch market enabled: {slug}"))
            else:
                self.stdout.write(self.style.WARNING(f"No city with slug '{slug}'"))

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. {Province.objects.count()} provinces, "
                f"{City.objects.count()} cities, "
                f"{Suburb.objects.count()} suburbs "
                f"({created_suburbs} new)."
            )
        )
        missing = Suburb.objects.filter(latitude__isnull=True).count()
        if missing:
            self.stdout.write(
                self.style.WARNING(
                    f"{missing} suburbs have no coordinates. Radius search will fall back "
                    f"to the city centroid for these. Run a geocoding pass before relying on it."
                )
            )

    def _seed_city(self, province, city_name, suburbs):
        city, _ = City.objects.get_or_create(
            province=province,
            slug=slugify(city_name),
            defaults={"name": city_name},
        )
        created = 0
        for name, lat, lng in suburbs:
            _, was_created = Suburb.objects.get_or_create(
                city=city,
                slug=slugify(name),
                defaults={"name": name, "latitude": lat, "longitude": lng},
            )
            created += int(was_created)
        return created
