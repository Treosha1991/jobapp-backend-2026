from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0024_vacancy_additional_phone_fields"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PushDevice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.CharField(db_index=True, max_length=512)),
                (
                    "platform",
                    models.CharField(
                        choices=[("android", "Android"), ("ios", "iOS"), ("web", "Web"), ("other", "Other")],
                        default="android",
                        max_length=20,
                    ),
                ),
                ("app_language", models.CharField(blank=True, default="", max_length=10)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_seen_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="push_devices",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-last_seen_at"],
                "indexes": [
                    models.Index(fields=["user", "is_active"], name="jobs_pushde_user_id_3b633f_idx"),
                    models.Index(fields=["token", "is_active"], name="jobs_pushde_token_056e5e_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("user", "token"), name="jobs_unique_push_device_per_user")
                ],
            },
        ),
        migrations.CreateModel(
            name="VacancyAlertSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("enabled", models.BooleanField(default=False)),
                ("country", models.CharField(blank=True, default="", max_length=10)),
                ("city", models.CharField(blank=True, default="", max_length=80)),
                ("category", models.CharField(blank=True, default="", max_length=30)),
                ("employment_type", models.CharField(blank=True, default="", max_length=20)),
                ("housing_type", models.CharField(blank=True, default="", max_length=10)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="vacancy_alert_subscription",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-updated_at"],
                "indexes": [
                    models.Index(fields=["enabled", "updated_at"], name="jobs_vacanc_enabled_ecda88_idx")
                ],
            },
        ),
        migrations.CreateModel(
            name="VacancyAlertDelivery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("sent", "Sent"),
                            ("failed", "Failed"),
                            ("skipped_no_device", "Skipped (no device)"),
                            ("skipped_not_configured", "Skipped (provider not configured)"),
                        ],
                        max_length=40,
                    ),
                ),
                ("device_platform", models.CharField(blank=True, default="", max_length=20)),
                ("device_token_tail", models.CharField(blank=True, default="", max_length=12)),
                ("provider_message_id", models.CharField(blank=True, default="", max_length=255)),
                ("error_text", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "subscription",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="deliveries",
                        to="jobs.vacancyalertsubscription",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="vacancy_alert_deliveries",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "vacancy",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="alert_deliveries",
                        to="jobs.vacancy",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["status", "created_at"], name="jobs_vacanc_status_408155_idx"),
                    models.Index(fields=["vacancy", "created_at"], name="jobs_vacanc_vacancy_9c3a53_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("user", "vacancy"), name="jobs_unique_alert_delivery_user_vacancy")
                ],
            },
        ),
    ]
