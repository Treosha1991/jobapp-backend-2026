from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0026_userprofile_description"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EmployerSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "employer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="employer_followers",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "subscriber",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="employer_subscriptions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="employersubscription",
            constraint=models.UniqueConstraint(
                fields=("subscriber", "employer"),
                name="jobs_unique_employer_subscription",
            ),
        ),
        migrations.AddIndex(
            model_name="employersubscription",
            index=models.Index(fields=["subscriber", "created_at"], name="jobs_employ_subscri_90bbda_idx"),
        ),
        migrations.AddIndex(
            model_name="employersubscription",
            index=models.Index(fields=["employer", "created_at"], name="jobs_employ_employe_7eb371_idx"),
        ),
    ]
