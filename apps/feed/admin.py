from django.contrib import admin

from .models import Comment, Post, PostImage, Reaction


class PostImageInline(admin.TabularInline):
    model = PostImage
    extra = 0
    fields = ("image", "position", "created_at")
    readonly_fields = ("created_at",)


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

    list_display = ("author", "topic", "city", "body_preview", "reaction_count",
                    "comment_count", "is_hidden", "created_at")
    list_filter = ("topic", "is_hidden", "city", "created_at")
    search_fields = ("body", "author__full_name", "author__email")
    readonly_fields = ("uuid", "author", "reaction_count", "comment_count",
                       "created_at", "updated_at")
    fields = ("uuid", "author", "topic", "city", "body",
              "is_hidden", "reaction_count", "comment_count", "created_at", "updated_at")
    inlines = [PostImageInline, CommentInline]
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


@admin.register(Reaction)
class ReactionAdmin(admin.ModelAdmin):
    """
    Read-only, like the old like admin was.

    Filterable by kind because the one question staff will actually ask of this
    table is "who is going round leaving angry faces", and that is a moderation
    question with an answer here.
    """

    list_display = ("user", "post", "kind", "created_at")
    list_filter = ("kind", "created_at")
    search_fields = ("user__full_name", "user__email")
    readonly_fields = ("post", "user", "kind", "created_at")

    def has_add_permission(self, request):
        return False
