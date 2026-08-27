from django.contrib import admin

from .models import City, Province, Suburb


@admin.register(Province)
class ProvinceAdmin(admin.ModelAdmin):
    list_display = ("name", "country")
    list_filter = ("country",)
    search_fields = ("name",)


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ("name", "province", "is_launch_market", "suburb_count")
    list_filter = ("province__country", "is_launch_market", "province")
    search_fields = ("name",)
    list_editable = ("is_launch_market",)

    @admin.display(description="Suburbs")
    def suburb_count(self, obj):
        return obj.suburbs.count()


@admin.register(Suburb)
class SuburbAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "latitude", "longitude")
    list_filter = ("city__province", "city")
    search_fields = ("name", "city__name")
    autocomplete_fields = ()
