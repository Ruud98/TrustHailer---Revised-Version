from django.contrib import admin

from .models import Comment, Like, Post


class CommentInline(admin.TabularInline):
    model = Comment
    extra = 0
    fields = ("author", "body", "is_hidden", "created_at")
    readonly_fields = ("author", "body", "created_at")


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    """
    Moderation for the feed. Hiding rather than deleting is the default action
    here — see `Post.is_hidden` for why the row has to stay.
    """

    list_display = ("author", "topic", "city", "body_preview", "like_count",
                    "comment_count", "is_hidden", "created_at")
    list_filter = ("topic", "is_hidden", "city", "created_at")
    search_fields = ("body", "author__full_name", "author__email")
    readonly_fields = ("uuid", "author", "like_count", "comment_count",
                       "created_at", "updated_at")
    fields = ("uuid", "author", "topic", "city", "body", "image",
              "is_hidden", "like_count", "comment_count", "created_at", "updated_at")
    inlines = [CommentInline]
    actions = ["hide_posts", "unhide_posts"]
    date_hierarchy = "created_at"

    @admin.display(description="Post")
    def body_preview(self, obj):
        return obj.body[:60] + ("…" if len(obj.body) > 60 else "")

    @admin.action(description="Hide selected")
    def hide_posts(self, request, queryset):
        count = queryset.update(is_hidden=True)
        self.message_user(request, f"{count} post(s) hidden.")

    @admin.action(description="Unhide selected")
    def unhide_posts(self, request, queryset):
        count = queryset.update(is_hidden=False)
        self.message_user(request, f"{count} post(s) unhidden.")


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("author", "post", "body_preview", "is_hidden", "created_at")
    list_filter = ("is_hidden", "created_at")
    search_fields = ("body", "author__full_name", "author__email")
    readonly_fields = ("post", "author", "parent", "body", "created_at", "updated_at")
    actions = ["hide_comments", "unhide_comments"]
    date_hierarchy = "created_at"

    @admin.display(description="Comment")
    def body_preview(self, obj):
        return obj.body[:60] + ("…" if len(obj.body) > 60 else "")

    @admin.action(description="Hide selected")
    def hide_comments(self, request, queryset):
        count = 0
        for comment in queryset:
            comment.is_hidden = True
            comment.save(update_fields=["is_hidden", "updated_at"])
            comment.post.recount()
            count += 1
        self.message_user(request, f"{count} comment(s) hidden.")

    @admin.action(description="Unhide selected")
    def unhide_comments(self, request, queryset):
        count = 0
        for comment in queryset:
            comment.is_hidden = False
            comment.save(update_fields=["is_hidden", "updated_at"])
            comment.post.recount()
            count += 1
        self.message_user(request, f"{count} comment(s) unhidden.")


@admin.register(Like)
class LikeAdmin(admin.ModelAdmin):
    list_display = ("user", "post", "created_at")
    search_fields = ("user__full_name", "user__email")
    readonly_fields = ("post", "user", "created_at")

    def has_add_permission(self, request):
        return False
