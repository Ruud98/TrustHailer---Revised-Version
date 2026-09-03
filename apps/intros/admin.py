from django.contrib import admin

from .models import IntroRequest


@admin.register(IntroRequest)
class IntroRequestAdmin(admin.ModelAdmin):
    """
    Read-only, on purpose.

    An introduction is an agreement between two people about their own phone
    numbers. Staff need to be able to see that one happened — for a dispute, a
    scam report, a "they never called me" — and staff have no business editing
    one after the fact. Approving on somebody's behalf would release a number
    they did not agree to release, which is the one thing this whole flow
    exists to prevent, and there is no support question that needs it.

    The message body is visible because moderating abuse means reading what was
    sent. Everything else here is a timestamp.
    """

    list_display = ("uuid", "from_user", "to_user", "subject", "status",
                    "created_at", "responded_at", "expires_at")
    list_filter = ("status", "created_at")
    search_fields = ("uuid", "from_user__full_name", "from_user__email",
                     "to_user__full_name", "to_user__email", "message")
    date_hierarchy = "created_at"
    readonly_fields = ("uuid", "from_user", "to_user", "vehicle_listing", "driver_listing",
                       "message", "status", "credits_charged", "contacts_released_at",
                       "responded_at", "expires_at", "created_at", "updated_at")

    @admin.display(description="About")
    def subject(self, obj):
        listing = obj.listing
        return str(listing) if listing else "—"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
