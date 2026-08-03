# Generated manually for the JobHub Support schedule-template slice.

import django.db.models.deletion
import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0010_announcement_taskassignment_workertask_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ShiftTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("starts_at_time", models.TimeField()),
                ("ends_at_time", models.TimeField()),
                ("break_minutes", models.PositiveSmallIntegerField(default=0)),
                ("worker_label", models.CharField(blank=True, default="", max_length=160)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_support_shift_templates", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="shift_templates", to="support.supportorganization")),
            ],
            options={"ordering": ("name", "id")},
        ),
        migrations.CreateModel(
            name="ScheduledShiftBatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("starts_on", models.DateField()),
                ("ends_on", models.DateField()),
                ("weekdays", models.JSONField(default=list)),
                ("state", models.CharField(choices=[("draft", "Draft"), ("published", "Published"), ("cancelled", "Cancelled")], default="draft", max_length=16)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_support_scheduled_shift_batches", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="scheduled_shift_batches", to="support.supportorganization")),
                ("published_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="published_support_scheduled_shift_batches", to=settings.AUTH_USER_MODEL)),
                ("template", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="batches", to="support.shifttemplate")),
            ],
            options={"ordering": ("-starts_on", "-created_at", "-id")},
        ),
        migrations.AddField(
            model_name="scheduledworkshift",
            name="batch",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="shifts", to="support.scheduledshiftbatch"),
        ),
        migrations.AddConstraint(
            model_name="shifttemplate",
            constraint=models.UniqueConstraint(fields=("organization", "name"), name="support_unique_shift_template_name_per_organization"),
        ),
        migrations.AddConstraint(
            model_name="scheduledshiftbatch",
            constraint=models.CheckConstraint(condition=models.Q(starts_on__lte=models.F("ends_on")), name="support_scheduled_shift_batch_valid_period"),
        ),
        migrations.AddIndex(
            model_name="shifttemplate",
            index=models.Index(fields=["organization", "is_active", "name"], name="support_sht_orgact_name_idx"),
        ),
        migrations.AddIndex(
            model_name="scheduledshiftbatch",
            index=models.Index(fields=["organization", "state", "starts_on"], name="support_shb_orgstate_start_idx"),
        ),
    ]
