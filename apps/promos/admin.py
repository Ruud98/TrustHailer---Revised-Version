from django.contrib import admin
from django.utils.html import format_html

from . import services
from .models import Promo


@admin.register(Promo)
class PromoAdmin(admin.ModelAdmin):
    """
    The rail editor.

    `rail()` is cached for a minute, so every write here busts it — staff who
    have just changed a headline should see the headline, not wonder whether
    they saved it.
    """

    list_display = ("title", "slot", "kind", "is_active",
                    "sort_order", "starts_at", "ends_at")
    list_filter = ("slot", "kind", "is_active")
    list_editable = ("sort_order", "is_active")
    search_fields = ("title", "body")
    readonly_fields = ("image_preview", "created_at", "updated_at")

    fieldsets = (
        ("Where it goes", {
            "fields": ("slot", "sort_order"),
            "description": "Promos run down the left rail. The ad box sits at the top and "
                           "shows one card at a time; the carousel crawls underneath it.",
        }),
        ("The card", {"fields": ("kind", "title", "body", "image_preview", "image",
                                 "image_alt", "url", "cta_label")}),
        ("Animation", {
            "fields": ("animation",),
            "description": "Ad box only. Ignored on carousel slides.",
        }),
        ("Scheduling", {
            "fields": ("is_active", "starts_at", "ends_at"),
            "description": "Leave the dates blank for an evergreen promo.",
        }),
        ("Metadata", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Current image")
    def image_preview(self, obj):
        if not obj.image:
            return "—"
        return format_html(
            '<img src="{}" style="max-height:120px;border-radius:8px">', obj.image.url
        )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        services.clear_cache()

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        services.clear_cache()

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset)
        services.clear_cache()
