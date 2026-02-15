from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0018_alter_complaintactionlog_action"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AccountDeletionRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("user_id_snapshot", models.PositiveIntegerField(db_index=True)),
                ("email_snapshot", models.EmailField(blank=True, max_length=254)),
                (
                    "status",
                    models.CharField(
                        choices=[("pending", "Pending"), ("completed", "Completed"), ("cancelled", "Cancelled")],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("confirmed_via", models.CharField(blank=True, max_length=20)),
                ("requested_at", models.DateTimeField(auto_now_add=True)),
                ("execute_after", models.DateTimeField(db_index=True)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                ("note", models.TextField(blank=True)),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="account_deletion_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-requested_at"],
            },
        ),
        migrations.AddIndex(
            model_name="accountdeletionrequest",
            index=models.Index(fields=["status", "execute_after"], name="jobs_accoun_status_e656b4_idx"),
        ),
    ]
