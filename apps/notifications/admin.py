from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    """
    Read-only. There is no support workflow that needs to send someone a
    notification by hand, and editing one after the fact would be rewriting
    something the recipient may have already read.
    """

    list_display = ("recipient", "kind", "message", "is_read", "created_at")
    list_filter = ("kind", "is_read", "created_at")
    search_fields = ("recipient__full_name", "recipient__email", "message")
    readonly_fields = ("recipient", "actor", "kind", "message", "url",
                       "is_read", "read_at", "created_at", "updated_at")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
