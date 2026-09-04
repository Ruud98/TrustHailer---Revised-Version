from django.contrib import admin
from django.utils.html import format_html

from .models import (
    DriverListing,
    ListingClaim,
    ListingPhoto,
    Platform,
    PlatformRatingProof,
    SavedSearch,
    VehicleListing,
)


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
    list_display = ("title", "owner", "source", "suburb", "arrangement", "weekly_rate",
                    "status", "is_boosted", "view_count", "created_at")
    list_filter = ("status", "source", "arrangement", "requires_prdp", "has_tracker",
                   "suburb__city", "created_at")
    search_fields = ("make", "model", "owner__full_name", "owner__email", "suburb__name")
    autocomplete_fields = ("suburb",)
    filter_horizontal = ("platforms",)
    inlines = [ListingPhotoInline]
    date_hierarchy = "created_at"
    readonly_fields = ("uuid", "view_count", "published_at", "created_at", "updated_at")

    fieldsets = (
        (None, {"fields": ("uuid", "owner", "status", "boost_expires_at")}),
        ("Where it came from", {
            "fields": ("source", "source_url", "source_author_name", "source_posted_at",
                       "imported_by", "claimed_at"),
            "description": (
                "Imported adverts keep their source for good. A claim gives a listing an "
                "owner; it never rewrites where the listing came from, because the link on "
                "the page has to stay true."
            ),
        }),
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


@admin.register(DriverListing)
class DriverListingAdmin(admin.ModelAdmin):
    list_display = ("driver", "headline", "home_suburb", "years_experience",
                    "has_prdp", "status", "view_count", "created_at")
    list_filter = ("status", "has_prdp", "licence_code", "preferred_arrangement",
                   "home_suburb__city", "created_at")
    search_fields = ("headline", "about", "driver__full_name", "driver__email",
                     "home_suburb__name")
    autocomplete_fields = ("home_suburb",)
    filter_horizontal = ("platforms_experience", "work_suburbs")
    date_hierarchy = "created_at"
    readonly_fields = ("uuid", "view_count", "published_at", "created_at", "updated_at")

    fieldsets = (
        (None, {"fields": ("uuid", "driver", "status", "headline")}),
        ("Experience", {"fields": ("years_experience", "licence_code", "has_prdp",
                                   "platforms_experience")}),
        ("What they want", {"fields": ("preferred_arrangement", "max_weekly_rate",
                                       "currency", "available_from")}),
        ("Where", {"fields": ("home_suburb", "work_suburbs")}),
        ("About", {"fields": ("about",)}),
        ("Metadata", {"fields": ("view_count", "published_at", "created_at", "updated_at")}),
    )


@admin.register(PlatformRatingProof)
class PlatformRatingProofAdmin(admin.ModelAdmin):
    """
    The review queue for platform ratings.

    APPROVING DELETES THE SCREENSHOT. THAT IS THE WHOLE DESIGN.
    -----------------------------------------------------------
    A driver-app screenshot carries a photo, a legal name and a trip history —
    ID-document-grade personal information. The two actions below record the
    decision and destroy the file in the same step, exactly as the KYC queue
    does, so there is no path through this admin that leaves a reviewed
    screenshot lying in storage.

    Save the model form after looking at the image and you get the same result:
    `review()` is the only way to set the status from here, because `status`
    is read-only on the form.
    """

    list_display = ("driver", "platform", "rating", "trips", "status",
                    "has_screenshot", "created_at", "purge_after")
    list_filter = ("status", "platform", "created_at")
    search_fields = ("driver__full_name", "driver__email", "driver__handle")
    readonly_fields = ("driver", "platform", "rating", "trips", "preview", "status",
                       "reviewed_by", "reviewed_at", "created_at", "updated_at")
    fields = ("driver", "platform", "rating", "trips", "preview", "status",
              "reject_reason", "reviewed_by", "reviewed_at", "purge_after",
              "created_at", "updated_at")
    actions = ["approve_ratings", "reject_ratings"]
    date_hierarchy = "created_at"

    @admin.display(boolean=True, description="Screenshot held")
    def has_screenshot(self, obj):
        return bool(obj.screenshot)

    @admin.display(description="Screenshot")
    def preview(self, obj):
        if not obj.screenshot:
            return "Deleted on review — nothing held."
        return format_html(
            '<img src="{}" style="max-height:420px;border-radius:6px">', obj.screenshot.url
        )

    @admin.action(description="Approve — records the rating, deletes the screenshot")
    def approve_ratings(self, request, queryset):
        count = 0
        for proof in queryset:
            proof.review(approved=True, by=request.user)
            count += 1
        self.message_user(request, f"{count} rating(s) verified and screenshot(s) deleted.")

    @admin.action(description="Reject — deletes the screenshot")
    def reject_ratings(self, request, queryset):
        count = 0
        for proof in queryset:
            proof.review(
                approved=False,
                by=request.user,
                reason=proof.reject_reason or "Screenshot didn't support the rating.",
            )
            count += 1
        self.message_user(request, f"{count} rating(s) rejected and screenshot(s) deleted.")


@admin.register(ListingClaim)
class ListingClaimAdmin(admin.ModelAdmin):
    """
    The queue for "that is my car".

    APPROVING HANDS OVER A LIVE LISTING. CHECK THE POST FIRST.
    ----------------------------------------------------------
    Open `source_post` in the column below, next to the claimant profile, and
    satisfy yourself they are the same person before you approve. Approval
    makes them the owner of the listing — their number is what gets released on
    an introduction, and their name is what carries the reviews. Getting this
    wrong hands a stranger somebody else's advert, complete with the photo of a
    car they do not have, which is precisely the scam this site exists to make
    harder. When in doubt, reject with a reason and ask them to post the car
    themselves.
    """

    list_display = ("listing", "claimant", "status", "source_post", "created_at",
                    "reviewed_by", "reviewed_at")
    list_filter = ("status", "created_at")
    search_fields = ("listing__make", "listing__model", "claimant__full_name",
                     "claimant__email", "claimant__handle", "message")
    readonly_fields = ("listing", "claimant", "message", "source_post", "status",
                       "reviewed_by", "reviewed_at", "created_at", "updated_at")
    fields = ("listing", "source_post", "claimant", "message", "status",
              "reject_reason", "reviewed_by", "reviewed_at", "created_at", "updated_at")
    actions = ["approve_claims", "reject_claims"]
    date_hierarchy = "created_at"

    @admin.display(description="Original post")
    def source_post(self, obj):
        if not obj.listing.source_url:
            return "—"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">Open the post</a>',
            obj.listing.source_url,
        )

    @admin.action(description="Approve — hands the listing to the claimant")
    def approve_claims(self, request, queryset):
        count = 0
        for claim in queryset.filter(status=ListingClaim.Status.PENDING):
            claim.approve(by=request.user)
            count += 1
        self.message_user(request, f"{count} claim(s) approved and listing(s) handed over.")

    @admin.action(description="Reject")
    def reject_claims(self, request, queryset):
        count = 0
        for claim in queryset.filter(status=ListingClaim.Status.PENDING):
            claim.reject(
                by=request.user,
                reason=claim.reject_reason or "We could not match you to the original post.",
            )
            count += 1
        self.message_user(request, f"{count} claim(s) rejected.")


@admin.register(SavedSearch)
class SavedSearchAdmin(admin.ModelAdmin):
    list_display = ("label", "user", "kind", "frequency", "last_sent_at", "created_at")
    list_filter = ("kind", "frequency", "created_at")
    search_fields = ("label", "user__full_name", "user__email")
    readonly_fields = ("params", "created_at", "updated_at")
