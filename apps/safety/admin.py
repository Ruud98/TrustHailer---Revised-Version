from django.contrib import admin
from django.utils.html import format_html

from .models import Block, Report


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    """
    The moderation queue.

    THE ONLY PLACE USER ACCUSATIONS LIVE
    ------------------------------------
    Nothing here is ever published. A user saying somebody is a scammer is an
    allegation, and republishing an allegation is publication — under SA
    defamation law, truth alone is not a complete defence, and the platform can
    be joined to the claim. Act on reports by suspending accounts and removing
    listings, with the evidence on file and the person given a chance to
    answer. Do not build a public board out of this table.

    Nothing here identifies the reporter to the person reported, either. That
    promise is made on the report form, and it is only worth making if it is
    kept everywhere.
    """

    list_display = ("created_at", "reason", "target_link", "reporter", "status",
                    "handled_by", "handled_at")
    list_filter = ("status", "reason", "content_type", "created_at")
    search_fields = ("detail", "staff_note", "reporter__full_name", "reporter__email")
    readonly_fields = ("reporter", "content_type", "object_id", "target_link",
                       "reason", "detail", "created_at", "updated_at",
                       "handled_by", "handled_at")
    fields = ("target_link", "reason", "detail", "reporter", "status", "staff_note",
              "handled_by", "handled_at", "created_at", "updated_at")
    actions = ["mark_actioned", "mark_dismissed"]
    date_hierarchy = "created_at"

    @admin.display(description="Reported thing")
    def target_link(self, obj):
        target = obj.target
        if target is None:
            return "Deleted"
        url = getattr(target, "get_absolute_url", None)
        if url is None:
            return str(target)
        return format_html('<a href="{}" target="_blank">{}</a>', url(), str(target))

    @admin.action(description="Mark actioned — you have done something about it")
    def mark_actioned(self, request, queryset):
        count = 0
        for report in queryset.open():
            report.resolve(by=request.user, actioned=True)
            count += 1
        self.message_user(request, f"{count} report(s) marked actioned.")

    @admin.action(description="Dismiss — nothing to do here")
    def mark_dismissed(self, request, queryset):
        count = 0
        for report in queryset.open():
            report.resolve(by=request.user, actioned=False)
            count += 1
        self.message_user(request, f"{count} report(s) dismissed.")


@admin.register(Block)
class BlockAdmin(admin.ModelAdmin):
    """
    Read-only. Blocks are a user's own decision, and staff undoing one would
    put somebody back in front of a person they chose not to deal with.

    Worth watching as a signal: one account collecting blocks from many
    unrelated people is usually worth a look before the first report arrives.
    """

    list_display = ("user", "blocked_user", "reason", "created_at")
    list_filter = ("created_at",)
    search_fields = ("user__full_name", "user__email", "blocked_user__full_name",
                     "blocked_user__email", "reason")
    readonly_fields = ("user", "blocked_user", "reason", "created_at", "updated_at")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
