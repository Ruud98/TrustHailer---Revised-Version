from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils import timezone
from django.utils.html import format_html

from .models import OTPChallenge, Profile, User, Verification, VerificationDocument


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    fields = (
        ("is_owner", "is_driver", "is_business"),
        "suburb",
        "bio",
        ("whatsapp_ok", "hide_from_search"),
        "onboarding_completed_at",
    )
    autocomplete_fields = ("suburb",)


class VerificationInline(admin.StackedInline):
    model = Verification
    can_delete = False
    # Verification has two FKs to User (the subject, and the staff member who
    # confirmed a number manually). Django needs to be told which one is the
    # inline's parent.
    fk_name = "user"
    fields = (
        "email_verified_at",
        ("phone_verified_at", "phone_verified_manually_by"),
        "id_verified_at",
        ("licence_verified_at", "licence_expires_on"),
        ("prdp_verified_at", "prdp_expires_on"),
        "references_checked_at",
    )


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("full_name", "email", "phone", "handle", "roles", "verification_level",
                    "is_suspended", "date_joined")
    list_filter = ("is_suspended", "is_staff", "is_active", "profile__is_owner",
                   "profile__is_driver", "date_joined")
    search_fields = ("full_name", "email", "phone", "handle")
    ordering = ("-date_joined",)
    inlines = [ProfileInline, VerificationInline]

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Identity", {"fields": ("full_name", "handle", "phone")}),
        ("Status", {"fields": ("is_active", "is_suspended", "suspension_reason")}),
        ("Permissions", {"fields": ("is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined", "last_seen")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "full_name", "password1", "password2")}),
    )
    readonly_fields = ("last_login", "last_seen")

    @admin.display(description="Roles")
    def roles(self, obj):
        return obj.profile.roles_display if hasattr(obj, "profile") else "—"

    @admin.display(description="Verified")
    def verification_level(self, obj):
        if not hasattr(obj, "verification"):
            return "—"
        return obj.verification.label


@admin.register(VerificationDocument)
class VerificationDocumentAdmin(admin.ModelAdmin):
    """
    The moderation queue for identity documents.

    Approving or rejecting DELETES the uploaded file. That is intentional. Read
    it, decide, and the image is gone — what remains is the flag on
    Verification and this audit row.
    """

    list_display = ("user", "kind", "status", "created_at", "has_file", "purge_after")
    list_filter = ("status", "kind", "created_at")
    search_fields = ("user__full_name", "user__phone", "user__handle")
    readonly_fields = ("user", "kind", "file", "created_at", "reviewed_by", "reviewed_at")
    actions = ["approve_documents", "reject_documents"]
    date_hierarchy = "created_at"

    @admin.display(boolean=True, description="File held")
    def has_file(self, obj):
        return bool(obj.file)

    def _finish(self, request, queryset, status):
        now = timezone.now()
        count = 0
        for doc in queryset:
            doc.status = status
            doc.reviewed_by = request.user
            doc.reviewed_at = now
            doc.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
            if status == VerificationDocument.Status.APPROVED:
                self._apply_to_verification(doc, now)
            doc.purge_file()
            count += 1
        return count

    @staticmethod
    def _apply_to_verification(doc, now):
        v = doc.user.verification
        fields = []
        if doc.kind == VerificationDocument.Kind.ID:
            v.id_verified_at = now
            fields.append("id_verified_at")
        elif doc.kind == VerificationDocument.Kind.LICENCE:
            v.licence_verified_at = now
            v.licence_expires_on = doc.expires_on
            fields += ["licence_verified_at", "licence_expires_on"]
        elif doc.kind == VerificationDocument.Kind.PRDP:
            v.prdp_verified_at = now
            v.prdp_expires_on = doc.expires_on
            fields += ["prdp_verified_at", "prdp_expires_on"]
        if fields:
            v.save(update_fields=fields + ["updated_at"])

    @admin.action(description="Approve selected (deletes the uploaded file)")
    def approve_documents(self, request, queryset):
        n = self._finish(request, queryset, VerificationDocument.Status.APPROVED)
        self.message_user(request, f"{n} approved. Files deleted.")

    @admin.action(description="Reject selected (deletes the uploaded file)")
    def reject_documents(self, request, queryset):
        n = self._finish(request, queryset, VerificationDocument.Status.REJECTED)
        self.message_user(request, f"{n} rejected. Files deleted.")


@admin.register(OTPChallenge)
class OTPChallengeAdmin(admin.ModelAdmin):
    """
    Read-only. Two uses: answering "the code never arrived", and watching the
    SMS bill. Filter channel=sms and the row count IS the bill.
    """

    list_display = ("destination", "channel", "purpose", "attempts",
                    "is_used", "created_at", "expires_at")
    list_filter = ("channel", "purpose", "is_used", "created_at")
    search_fields = ("destination",)
    readonly_fields = [f.name for f in OTPChallenge._meta.fields]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        from .notify import sms_spend_estimate

        spend = sms_spend_estimate(30)
        self.message_user(
            request,
            f"SMS in the last 30 days: {spend['messages']} messages, "
            f"about R{spend['estimated_cost']}.",
            level=messages.INFO,
        )
        return super().changelist_view(request, extra_context)


@admin.action(description="Mark phone number as verified (confirmed over WhatsApp)")
def mark_phone_verified(modeladmin, request, queryset):
    """
    The free alternative to SMS verification.

    While PHONE_VERIFICATION_CHANNEL is "manual", a user WhatsApps the support
    number, you recognise them, and you tick them off here. At seed scale this
    costs nothing and is a stronger signal than an automated SMS — you have
    actually spoken to the person.
    """
    now = timezone.now()
    count = 0
    for user in queryset.select_related("verification"):
        if not user.phone:
            continue
        verification = user.verification
        verification.phone_verified_at = now
        verification.phone_verified_manually_by = request.user
        verification.save(
            update_fields=["phone_verified_at", "phone_verified_manually_by", "updated_at"]
        )
        count += 1
    modeladmin.message_user(request, f"{count} number(s) marked verified.")


UserAdmin.actions = [mark_phone_verified]
