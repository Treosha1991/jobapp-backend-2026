# Generated manually for JobHub Support Package 4 task and announcement slice.

import django.db.models.deletion
import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0009_supportworkerdocumentreference_and_more"),
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
                    ("task_assignment", "Task assignment"),
                    ("announcement", "Announcement"),
                ],
                max_length=32,
            ),
        ),
        migrations.CreateModel(
            name="Announcement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("title", models.CharField(max_length=180)),
                ("body", models.TextField(max_length=8000)),
                ("translations", models.JSONField(default=dict)),
                ("original_language", models.CharField(choices=[("ru", "Russian"), ("en", "English"), ("pl", "Polish"), ("uk", "Ukrainian")], default="en", max_length=2)),
                ("importance", models.CharField(choices=[("normal", "Normal"), ("important", "Important")], default="normal", max_length=16)),
                ("requires_acknowledgement", models.BooleanField(default=False)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("state", models.CharField(choices=[("draft", "Draft"), ("published", "Published"), ("archived", "Archived")], default="draft", max_length=16)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("archived_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_support_announcements", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="announcements", to="support.supportorganization")),
                ("published_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="published_support_announcements", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("-published_at", "-created_at", "-id")},
        ),
        migrations.CreateModel(
            name="WorkerTask",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("title", models.CharField(max_length=180)),
                ("instructions", models.TextField(max_length=5000)),
                ("translations", models.JSONField(default=dict)),
                ("original_language", models.CharField(choices=[("ru", "Russian"), ("en", "English"), ("pl", "Polish"), ("uk", "Ukrainian")], default="en", max_length=2)),
                ("priority", models.CharField(choices=[("normal", "Normal"), ("important", "Important")], default="normal", max_length=16)),
                ("context_kind", models.CharField(choices=[("general", "General"), ("arrival", "Arrival"), ("housing", "Housing"), ("transport", "Transport"), ("work", "Work"), ("finance", "Finance")], default="general", max_length=24)),
                ("due_at", models.DateTimeField(blank=True, null=True)),
                ("state", models.CharField(choices=[("draft", "Draft"), ("published", "Published"), ("cancelled", "Cancelled")], default="draft", max_length=16)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_support_worker_tasks", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="worker_tasks", to="support.supportorganization")),
                ("published_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="published_support_worker_tasks", to=settings.AUTH_USER_MODEL)),
                ("responsible_membership", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="responsible_worker_tasks", to="support.organizationmembership")),
            ],
            options={"ordering": ("due_at", "-created_at", "-id")},
        ),
        migrations.CreateModel(
            name="TaskAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("status", models.CharField(choices=[("new", "New"), ("in_progress", "In progress"), ("completed_by_worker", "Completed by worker"), ("confirmed", "Confirmed"), ("returned", "Returned"), ("cancelled", "Cancelled")], default="new", max_length=32)),
                ("worker_note", models.CharField(blank=True, default="", max_length=500)),
                ("manager_note", models.CharField(blank=True, default="", max_length=500)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("returned_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("connection", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="task_assignments", to="support.supportconnection")),
                ("last_changed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="changed_support_task_assignments", to=settings.AUTH_USER_MODEL)),
                ("task", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="assignments", to="support.workertask")),
            ],
            options={"ordering": ("task__due_at", "-created_at", "-id")},
        ),
        migrations.CreateModel(
            name="AnnouncementAcknowledgement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("acknowledged_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("acknowledged_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="acknowledged_support_announcements", to=settings.AUTH_USER_MODEL)),
                ("announcement", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="acknowledgements", to="support.announcement")),
                ("connection", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="announcement_acknowledgements", to="support.supportconnection")),
            ],
            options={"ordering": ("-announcement__published_at", "-id")},
        ),
        migrations.AddConstraint(
            model_name="taskassignment",
            constraint=models.UniqueConstraint(fields=("task", "connection"), name="support_unique_task_assignment_per_connection"),
        ),
        migrations.AddConstraint(
            model_name="announcementacknowledgement",
            constraint=models.UniqueConstraint(fields=("announcement", "connection"), name="support_unique_announcement_recipient_per_connection"),
        ),
        migrations.AddIndex(model_name="announcement", index=models.Index(fields=["organization", "state", "published_at"], name="support_ann_organiz_6e972d_idx")),
        migrations.AddIndex(model_name="announcement", index=models.Index(fields=["organization", "expires_at"], name="support_ann_organiz_3ea622_idx")),
        migrations.AddIndex(model_name="workertask", index=models.Index(fields=["organization", "state", "due_at"], name="support_wor_organiz_60be04_idx")),
        migrations.AddIndex(model_name="workertask", index=models.Index(fields=["responsible_membership", "state", "due_at"], name="support_wor_respons_59144d_idx")),
        migrations.AddIndex(model_name="taskassignment", index=models.Index(fields=["connection", "status", "updated_at"], name="support_tas_connect_dc6745_idx")),
        migrations.AddIndex(model_name="taskassignment", index=models.Index(fields=["task", "status"], name="support_tas_task_id_625be7_idx")),
        migrations.AddIndex(model_name="announcementacknowledgement", index=models.Index(fields=["connection", "acknowledged_at"], name="support_ann_connect_9fcd2f_idx")),
        migrations.AddIndex(model_name="announcementacknowledgement", index=models.Index(fields=["announcement", "acknowledged_at"], name="support_ann_announc_8a5e71_idx")),
    ]
