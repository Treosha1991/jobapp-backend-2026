# Generated manually for JobHub Support Package 4.

import django.db.models.deletion
import uuid

from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0005_alter_notificationoutbox_target_kind"),
    ]

    operations = [
        migrations.CreateModel(
            name="WorkerAccessScope",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "public_id",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "connection",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="staff_access_scopes",
                        to="support.supportconnection",
                    ),
                ),
                (
                    "granted_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="granted_support_worker_scopes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "membership",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="worker_access_scopes",
                        to="support.organizationmembership",
                    ),
                ),
                (
                    "revoked_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="revoked_support_worker_scopes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("membership_id", "connection_id", "id"),
                "indexes": [
                    models.Index(
                        fields=["membership", "is_active"],
                        name="support_was_membership_active",
                    ),
                    models.Index(
                        fields=["connection", "is_active"],
                        name="support_was_connection_active",
                    ),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="workeraccessscope",
            constraint=models.UniqueConstraint(
                condition=Q(("is_active", True)),
                fields=("membership", "connection"),
                name="support_unique_active_worker_access_scope",
            ),
        ),
    ]
