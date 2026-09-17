from django.contrib import admin

from .models import Message, Thread


@admin.register(Thread)
class ThreadAdmin(admin.ModelAdmin):
    """
    Read-only, and it deliberately does not show message bodies.

    A thread is two people's private conversation. Staff can see that one
    exists — which is what a harassment report needs to be actionable — without
    a screen that reads everybody's mail.
    """

    list_display = ("user_a", "user_b", "last_message_at")
    search_fields = ("user_a__email", "user_b__email")
    readonly_fields = ("user_a", "user_b", "last_message_at", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
