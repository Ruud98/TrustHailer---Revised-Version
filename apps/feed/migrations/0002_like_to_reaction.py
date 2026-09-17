"""
Like -> Reaction.

WRITTEN BY HAND, NOT BY makemigrations
--------------------------------------
The autodetector cannot tell a rename from a drop-and-create, and asks. Left to
answer "no" it would have dropped the `feed_like` table and built an empty
`feed_reaction` beside it — every like on the site, silently gone, with a
migration that runs clean and tests that pass.

`RenameModel` and `RenameField` keep the rows. Everybody who had liked a post
still has a reaction to it, of kind `like`, which is the default on the new
column and the right answer: they pressed a button that meant "like", and that
is still what it means.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("feed", "0001_initial"),
    ]

    operations = [
        # The constraint goes before the rename and comes back after, because
        # its name is part of what is changing and SQLite rebuilds the table
        # for either operation anyway.
        migrations.RemoveConstraint(
            model_name="like",
            name="one_like_per_post_per_user",
        ),
        migrations.RenameModel(old_name="Like", new_name="Reaction"),
        migrations.RenameField(
            model_name="post",
            old_name="like_count",
            new_name="reaction_count",
        ),
        # `related_name` is Python-side only, but it lives in migration state,
        # and state that disagrees with models.py makes the NEXT autodetector
        # run produce a spurious migration.
        migrations.AlterField(
            model_name="reaction",
            name="post",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="reactions",
                to="feed.post",
            ),
        ),
        migrations.AlterField(
            model_name="reaction",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="reactions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # Existing rows take the default, which is what they already meant.
        migrations.AddField(
            model_name="reaction",
            name="kind",
            field=models.CharField(
                choices=[
                    ("like", "Like"),
                    ("love", "Love"),
                    ("care", "Care"),
                    ("haha", "Haha"),
                    ("wow", "Wow"),
                    ("sad", "Sad"),
                    ("angry", "Angry"),
                ],
                default="like",
                max_length=5,
            ),
        ),
        migrations.AddConstraint(
            model_name="reaction",
            constraint=models.UniqueConstraint(
                fields=("post", "user"), name="one_reaction_per_post_per_user"
            ),
        ),
        migrations.AddIndex(
            model_name="reaction",
            index=models.Index(fields=["post", "kind"], name="feed_reacti_post_id_7a35a4_idx"),
        ),
    ]
