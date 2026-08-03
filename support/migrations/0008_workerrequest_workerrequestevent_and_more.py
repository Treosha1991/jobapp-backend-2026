# Generated manually for JobHub Support Package 6.

import django.db.models.deletion
import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0007_alter_notificationoutbox_target_kind_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="notificationoutbox",
            name="target_kind",
            field=models.CharField(
                choices=[
                    ("connection", "Connection"),
                    ("conversation", "Conversation"),
                    ("support_access", "Support access"),
                    ("housing_assignment", "Housing assignment"),
                    ("work_assignment", "Work assignment"),
                    ("transport_route", "Transport route"),
                    ("scheduled_shift", "Scheduled shift"),
                    ("time_entry", "Time entry"),
                    ("worker_request", "Worker request"),
                ],
                max_length=32,
            ),
        ),
        migrations.CreateModel(
            name="WorkerRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("request_type", models.CharField(choices=[("day_off", "Day off"), ("vacation", "Vacation"), ("unpaid_absence", "Unpaid absence"), ("unable_today", "Unable to work today"), ("exit_request", "Exit request")], max_length=32)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("submitted", "Submitted"), ("needs_clarification", "Needs clarification"), ("approved", "Approved"), ("declined", "Declined"), ("cancelled", "Cancelled")], default="draft", max_length=32)),
                ("starts_on", models.DateField()),
                ("ends_on", models.DateField()),
                ("worker_note", models.CharField(blank=True, default="", max_length=500)),
                ("manager_note", models.CharField(blank=True, default="", max_length=500)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("connection", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="worker_requests", to="support.supportconnection")),
                ("last_changed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="last_changed_support_worker_requests", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="worker_requests", to="support.supportorganization")),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_support_worker_requests", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("-submitted_at", "-created_at", "-id")},
        ),
        migrations.CreateModel(
            name="WorkerRequestEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("action", models.CharField(choices=[("submitted", "Submitted"), ("clarification_requested", "Clarification requested"), ("approved", "Approved"), ("declined", "Declined"), ("cancelled", "Cancelled")], max_length=32)),
                ("status_after", models.CharField(choices=[("draft", "Draft"), ("submitted", "Submitted"), ("needs_clarification", "Needs clarification"), ("approved", "Approved"), ("declined", "Declined"), ("cancelled", "Cancelled")], max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="support_worker_request_events", to=settings.AUTH_USER_MODEL)),
                ("request", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="support.workerrequest")),
            ],
            options={"ordering": ("request_id", "created_at", "id")},
        ),
        migrations.AddIndex(model_name="workerrequest", index=models.Index(fields=["organization", "status", "starts_on"], name="support_wor_organiz_db5f8a_idx")),
        migrations.AddIndex(model_name="workerrequest", index=models.Index(fields=["connection", "status", "starts_on"], name="support_wor_connect_8758a1_idx")),
        migrations.AddConstraint(model_name="workerrequest", constraint=models.CheckConstraint(condition=models.Q(("starts_on__lte", models.F("ends_on"))), name="support_worker_request_valid_dates")),
        migrations.AddIndex(model_name="workerrequestevent", index=models.Index(fields=["request", "created_at"], name="support_wor_request_f29bb2_idx")),
    ]
