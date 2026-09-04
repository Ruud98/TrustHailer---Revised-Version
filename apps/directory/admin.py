from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

from apps.notifications.models import Notification
from apps.notifications.services import notify

from .models import BusinessListing


@admin.register(BusinessListing)
class BusinessListingAdmin(admin.ModelAdmin):
    """
    The directory moderation queue.

    VERIFYING PHONES THE BUSINESS
    -------------------------------
    "Verified" here means somebody on staff actually rang the number and it
    reached a real business — not that the owner has a verified TrustHailer
    account. Confirm that before you tick it: the badge is the entire product
    of this page, and it stops meaning anything the day it is granted to a
    number nobody checked.
    """

    list_display = ("name", "category", "suburb", "status", "is_verified",
                    "is_paid", "is_hidden", "created_at")
    list_filter = ("status", "category", "is_verified", "is_paid", "is_hidden",
                   "suburb__city", "created_at")
    search_fields = ("name", "description", "phone", "owner_user__full_name",
                     "owner_user__email")
    autocomplete_fields = ("suburb",)
    readonly_fields = ("uuid", "slug", "created_at", "updated_at", "verified_at", "logo_preview")
    actions = ["verify_businesses", "unverify_businesses", "hide_businesses", "unhide_businesses"]
    date_hierarchy = "created_at"

    fieldsets = (
        (None, {"fields": ("uuid", "slug", "owner_user", "status", "is_hidden")}),
        ("Listing", {"fields": ("name", "category", "description", "logo_preview", "logo")}),
        ("Contact", {"fields": ("phone", "whatsapp", "suburb")}),
        ("Verification", {"fields": ("is_verified", "verified_at")}),
        ("Paid tier", {
            "fields": ("is_paid", "paid_until"),
            "description": "Not chargeable while MONETISATION_ENABLED is False. See "
                           "apps/core/pricing.py.",
        }),
        ("Metadata", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Logo")
    def logo_preview(self, obj):
        if not obj.logo:
            return "—"
        return format_html('<img src="{}" style="height:56px;border-radius:6px">', obj.logo.url)

    @admin.action(description="Verify — confirms staff actually checked this business")
    def verify_businesses(self, request, queryset):
        count = 0
        for listing in queryset.filter(is_verified=False):
            listing.is_verified = True
            listing.verified_at = timezone.now()
            listing.save(update_fields=["is_verified", "verified_at", "updated_at"])
            notify(
                recipient=listing.owner_user,
                kind=Notification.Kind.BUSINESS_VERIFIED,
                message=f"{listing.name} is now verified",
                url=listing.get_absolute_url(),
            )
            count += 1
        self.message_user(request, f"{count} business(es) verified.")

    @admin.action(description="Remove verification")
    def unverify_businesses(self, request, queryset):
        count = queryset.update(is_verified=False, verified_at=None)
        self.message_user(request, f"{count} business(es) unverified.")

    @admin.action(description="Hide selected")
    def hide_businesses(self, request, queryset):
        count = queryset.update(is_hidden=True)
        self.message_user(request, f"{count} business(es) hidden.")

    @admin.action(description="Unhide selected")
    def unhide_businesses(self, request, queryset):
        count = queryset.update(is_hidden=False)
        self.message_user(request, f"{count} business(es) unhidden.")
