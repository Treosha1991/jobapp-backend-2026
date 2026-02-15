from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0019_accountdeletionrequest"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserBlock",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "blocked_user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="incoming_blocks",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "blocker",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="outgoing_blocks",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="userblock",
            constraint=models.UniqueConstraint(fields=("blocker", "blocked_user"), name="jobs_unique_user_block"),
        ),
        migrations.AddIndex(
            model_name="userblock",
            index=models.Index(fields=["blocker", "created_at"], name="jobs_userbl_blocker_bac549_idx"),
        ),
        migrations.AddIndex(
            model_name="userblock",
            index=models.Index(fields=["blocked_user", "created_at"], name="jobs_userbl_blocked_791a78_idx"),
        ),
    ]
