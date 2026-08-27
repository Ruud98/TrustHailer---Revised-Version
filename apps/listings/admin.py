from django.contrib import admin
from django.utils.html import format_html

from .models import ListingPhoto, Platform, VehicleListing


@admin.register(Platform)
class PlatformAdmin(admin.ModelAdmin):
    list_display = ("name", "country", "max_vehicle_age_years", "is_active", "order")
    list_filter = ("country", "is_active")
    list_editable = ("max_vehicle_age_years", "is_active", "order")
    prepopulated_fields = {"slug": ("name",)}


class ListingPhotoInline(admin.TabularInline):
    model = ListingPhoto
    extra = 0
    fields = ("preview", "image", "is_primary", "order")
    readonly_fields = ("preview",)

    @admin.display(description="Preview")
    def preview(self, obj):
        if obj.thumbnail:
            return format_html('<img src="{}" style="height:56px;border-radius:6px">',
                               obj.thumbnail.url)
        return "—"


@admin.register(VehicleListing)
class VehicleListingAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "suburb", "arrangement", "weekly_rate",
                    "status", "is_boosted", "view_count", "created_at")
    list_filter = ("status", "arrangement", "requires_prdp", "has_tracker",
                   "suburb__city", "created_at")
    search_fields = ("make", "model", "owner__full_name", "owner__email", "suburb__name")
    autocomplete_fields = ("suburb",)
    filter_horizontal = ("platforms",)
    inlines = [ListingPhotoInline]
    date_hierarchy = "created_at"
    readonly_fields = ("uuid", "view_count", "published_at", "created_at", "updated_at")

    fieldsets = (
        (None, {"fields": ("uuid", "owner", "status", "boost_expires_at")}),
        ("Vehicle", {"fields": ("make", "model", "year", "transmission",
                                "fuel_type", "colour", "platforms")}),
        ("Terms", {"fields": ("arrangement", "currency", "weekly_rate", "daily_rate",
                              "deposit_amount", "earnings_share_pct", "rent_to_own_months")}),
        ("Who pays what", {"fields": ("fuel_paid_by", "maintenance_paid_by",
                                      "insurance_paid_by", "licensing_paid_by",
                                      "tracker_paid_by")}),
        ("Requirements", {"fields": ("has_tracker", "has_insurance", "weekly_km_limit",
                                     "min_experience_years", "requires_prdp")}),
        ("Placement", {"fields": ("suburb", "available_from", "description")}),
        ("Metadata", {"fields": ("view_count", "published_at", "created_at", "updated_at")}),
    )

    @admin.display(boolean=True, description="Boosted")
    def is_boosted(self, obj):
        return obj.is_boosted
