"""
One image per post becomes several.

The order of the three operations matters. The table is created first, the
existing `Post.image` values are copied into it, and only then is the old
column dropped — so no post loses the photo it already had, and the reverse
runs the same steps backwards.

The data step moves the NAME, not the file. A `PostImage.image` holding
`posts/2026/09/post_abc.webp` points at exactly the file `Post.image` pointed
at a moment earlier; copying bytes would double the storage to change which row
owns them. New uploads land under `post_image_path` instead, and the old paths
stay valid forever.
"""
from django.db import migrations, models
import django.db.models.deletion

import apps.feed.models


def carry_images_forward(apps, schema_editor):
    Post = apps.get_model("feed", "Post")
    PostImage = apps.get_model("feed", "PostImage")
    PostImage.objects.bulk_create(
        [
            PostImage(post_id=pk, image=name, position=0)
            for pk, name in Post.objects.exclude(image="").values_list("pk", "image")
        ]
    )


def carry_images_back(apps, schema_editor):
    """
    Put the first image of each post back on the post.

    Anything past the first is dropped, because there is nowhere to put it —
    that is the honest cost of reversing this, and it is why the files
    themselves are left on disk rather than deleted here.
    """
    Post = apps.get_model("feed", "Post")
    PostImage = apps.get_model("feed", "PostImage")

    # Walked in Python rather than with DISTINCT ON, which is Postgres-only:
    # this project runs SQLite locally and Postgres in production, and a
    # migration that only reverses on one of them is not a reversible one.
    seen = set()
    for image in PostImage.objects.order_by("post_id", "position", "id").iterator():
        if image.post_id in seen:
            continue
        seen.add(image.post_id)
        Post.objects.filter(pk=image.post_id).update(image=image.image)


class Migration(migrations.Migration):

    dependencies = [
        ("feed", "0004_post_title"),
    ]

    operations = [
        migrations.CreateModel(
            name="PostImage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("image", models.ImageField(upload_to=apps.feed.models.post_image_path)),
                ("position", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("post", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                           related_name="images", to="feed.post")),
            ],
            options={
                "ordering": ["position", "id"],
            },
        ),
        migrations.AddIndex(
            model_name="postimage",
            index=models.Index(fields=["post", "position"],
                               name="feed_postim_post_id_672527_idx"),
        ),
        migrations.RunPython(carry_images_forward, carry_images_back),
        migrations.RemoveField(
            model_name="post",
            name="image",
        ),
    ]
