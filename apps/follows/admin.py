from django.contrib import admin

from .models import Follow


@admin.register(Follow)
class FollowAdmin(admin.ModelAdmin):
    """
    Read-only. Follows are created by members, and a staff-made one would be a
    row nobody can account for.
    """

    list_display = ("follower", "following", "created_at")
    search_fields = ("follower__full_name", "follower__email",
                     "following__full_name", "following__email")
    readonly_fields = ("follower", "following", "created_at", "updated_at")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False
