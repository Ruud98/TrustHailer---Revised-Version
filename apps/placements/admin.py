from django.contrib import admin

from .models import Placement, Review


class ReviewInline(admin.TabularInline):
    model = Review
    extra = 0
    fields = ("author", "subject", "overall", "is_published", "published_at")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Placement)
class PlacementAdmin(admin.ModelAdmin):
    """
    Read-only. A placement is two people's agreement about a fact, and staff
    editing one would silently change who is allowed to review whom.
    """

    list_display = ("uuid", "vehicle_listing", "owner", "driver", "started_on",
                    "ended_on", "is_confirmed")
    list_filter = ("confirmed_by_owner", "confirmed_by_driver", "end_reason", "started_on")
    search_fields = ("uuid", "owner__full_name", "owner__email",
                     "driver__full_name", "driver__email")
    readonly_fields = [f.name for f in Placement._meta.fields]
    inlines = [ReviewInline]
    date_hierarchy = "started_on"

    @admin.display(boolean=True, description="Confirmed")
    def is_confirmed(self, obj):
        return obj.is_confirmed

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    """
    Visible to staff for moderation, and editable in exactly one respect:
    a review can be taken down.

    UNPUBLISHED REVIEWS ARE VISIBLE HERE, AND THAT IS A REAL COST
    -------------------------------------------------------------
    The double blind protects people from each other, not from us — abuse
    reported in a review has to be readable before the timer runs out, or the
    report button is useless for the fortnight it matters most. Staff can see
    an unpublished review; nobody can edit the words in one. Removing it
    entirely is the only remedy, which keeps the temptation to quietly soften
    somebody's review off the table.
    """

    list_display = ("created_at", "author", "subject", "overall", "is_published",
                    "published_at")
    list_filter = ("is_published", "overall", "created_at")
    search_fields = ("body", "author__full_name", "author__email",
                     "subject__full_name", "subject__email")
    readonly_fields = ("placement", "author", "subject", "overall", "communication",
                       "payment_reliability", "vehicle_care", "fairness",
                       "maintenance_response", "deposit_returned", "body",
                       "published_at", "created_at", "updated_at")
    fields = readonly_fields
    actions = ["remove_reviews"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    @admin.action(description="Remove — deletes the review and re-averages the subject")
    def remove_reviews(self, request, queryset):
        from .models import recalculate_rating

        subjects = {review.subject for review in queryset}
        count = queryset.count()
        queryset.delete()
        for subject in subjects:
            recalculate_rating(subject)
        self.message_user(request, f"{count} review(s) removed and ratings recalculated.")
