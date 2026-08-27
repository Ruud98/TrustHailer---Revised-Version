from django.db import models
from django.utils.text import slugify


class Country(models.TextChoices):
    ZA = "ZA", "South Africa"
    ZW = "ZW", "Zimbabwe"


CURRENCY_FOR_COUNTRY = {
    Country.ZA: "ZAR",
    Country.ZW: "USD",  # Zimbabwe rentals are quoted in USD in practice
}


class Province(models.Model):
    country = models.CharField(max_length=2, choices=Country.choices, default=Country.ZA)
    name = models.CharField(max_length=80)
    slug = models.SlugField(unique=True)

    class Meta:
        ordering = ["country", "name"]

    def __str__(self):
        return f"{self.name}, {self.get_country_display()}"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(f"{self.country}-{self.name}")
        super().save(*args, **kwargs)


class City(models.Model):
    province = models.ForeignKey(Province, on_delete=models.PROTECT, related_name="cities")
    name = models.CharField(max_length=80)
    slug = models.SlugField()
    is_launch_market = models.BooleanField(
        default=False,
        help_text="Only launch markets are shown in signup. Seed one metro at a time.",
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["province", "slug"], name="uniq_city_slug_per_province")
        ]
        verbose_name_plural = "cities"

    def __str__(self):
        return self.name

    @property
    def country(self):
        return self.province.country


class Suburb(models.Model):
    city = models.ForeignKey(City, on_delete=models.PROTECT, related_name="suburbs")
    name = models.CharField(max_length=100)
    slug = models.SlugField()
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["city", "slug"], name="uniq_suburb_slug_per_city")
        ]
        indexes = [
            models.Index(fields=["latitude", "longitude"]),
            models.Index(fields=["city", "name"]),
        ]

    def __str__(self):
        return f"{self.name}, {self.city.name}"

    @property
    def full_name(self):
        return f"{self.name}, {self.city.name}"
