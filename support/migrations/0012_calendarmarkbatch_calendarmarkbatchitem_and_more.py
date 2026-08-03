# Generated manually for the approved-calendar-mark template slice.

import django.db.models.deletion
import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0011_scheduledshiftbatch_shifttemplate_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CalendarMarkTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("request_type", models.CharField(choices=[("day_off", "Day off"), ("vacation", "Vacation"), ("unpaid_absence", "Unpaid absence"), ("unable_today", "Unable to work today")], max_length=32)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_support_calendar_mark_templates", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="calendar_mark_templates", to="support.supportorganization")),
            ],
            options={"ordering": ("request_type", "name", "id")},
        ),
        migrations.CreateModel(
            name="CalendarMarkBatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("state", models.CharField(choices=[("draft", "Draft"), ("published", "Published"), ("cancelled", "Cancelled")], default="draft", max_length=16)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_support_calendar_mark_batches", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="calendar_mark_batches", to="support.supportorganization")),
                ("published_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="published_support_calendar_mark_batches", to=settings.AUTH_USER_MODEL)),
                ("template", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="batches", to="support.calendarmarktemplate")),
            ],
            options={"ordering": ("-created_at", "-id")},
        ),
        migrations.CreateModel(
            name="CalendarMarkBatchItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="support.calendarmarkbatch")),
                ("request", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="calendar_mark_batch_items", to="support.workerrequest")),
            ],
            options={"ordering": ("request__starts_on", "request__ends_on", "id")},
        ),
        migrations.AddConstraint(
            model_name="calendarmarktemplate",
            constraint=models.UniqueConstraint(fields=("organization", "name"), name="support_unique_calendar_mark_template_name_per_org"),
        ),
        migrations.AddConstraint(
            model_name="calendarmarkbatchitem",
            constraint=models.UniqueConstraint(fields=("batch", "request"), name="support_unique_calendar_mark_batch_item"),
        ),
        migrations.AddIndex(
            model_name="calendarmarktemplate",
            index=models.Index(fields=["organization", "is_active", "request_type", "name"], name="support_cmt_org_type_nm_idx"),
        ),
        migrations.AddIndex(
            model_name="calendarmarkbatch",
            index=models.Index(fields=["organization", "state", "created_at"], name="support_cmb_org_state_idx"),
        ),
        migrations.AddIndex(
            model_name="calendarmarkbatchitem",
            index=models.Index(fields=["request", "batch"], name="support_cmbi_req_batch_idx"),
        ),
    ]
