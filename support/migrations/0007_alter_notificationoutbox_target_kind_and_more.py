# Generated manually for JobHub Support Package 5.

import django.db.models.deletion
import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0006_workeraccessscope"),
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
                ],
                max_length=32,
            ),
        ),
        migrations.CreateModel(
            name="ScheduledWorkShift",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("work_date", models.DateField()),
                ("starts_at", models.DateTimeField()),
                ("ends_at", models.DateTimeField()),
                ("break_minutes", models.PositiveSmallIntegerField(default=0)),
                ("worker_label", models.CharField(blank=True, default="", max_length=160)),
                ("state", models.CharField(choices=[("draft", "Draft"), ("published", "Published"), ("cancelled", "Cancelled")], default="draft", max_length=16)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("connection", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="scheduled_work_shifts", to="support.supportconnection")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_support_scheduled_work_shifts", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="scheduled_work_shifts", to="support.supportorganization")),
                ("published_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="published_support_scheduled_work_shifts", to=settings.AUTH_USER_MODEL)),
                ("work_assignment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="scheduled_shifts", to="support.workerprojectassignment")),
            ],
            options={"ordering": ("work_date", "starts_at", "id")},
        ),
        migrations.CreateModel(
            name="WorkTimeEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("work_date", models.DateField()),
                ("started_at", models.DateTimeField()),
                ("ended_at", models.DateTimeField()),
                ("break_minutes", models.PositiveSmallIntegerField(default=0)),
                ("worked_minutes", models.PositiveSmallIntegerField()),
                ("status", models.CharField(choices=[("submitted", "Submitted"), ("correction_requested", "Correction requested"), ("confirmed", "Confirmed"), ("manager_adjusted", "Manager adjustment awaiting worker acknowledgement")], default="submitted", max_length=32)),
                ("revision", models.PositiveSmallIntegerField(default=1)),
                ("manager_note", models.CharField(blank=True, default="", max_length=500)),
                ("submitted_at", models.DateTimeField()),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("worker_acknowledged_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("confirmed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="confirmed_support_time_entries", to=settings.AUTH_USER_MODEL)),
                ("connection", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="work_time_entries", to="support.supportconnection")),
                ("last_changed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="last_changed_support_time_entries", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="work_time_entries", to="support.supportorganization")),
                ("scheduled_shift", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="time_entries", to="support.scheduledworkshift")),
            ],
            options={"ordering": ("-work_date", "-updated_at", "-id")},
        ),
        migrations.CreateModel(
            name="WorkTimeEntryRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("revision", models.PositiveSmallIntegerField()),
                ("action", models.CharField(choices=[("submitted", "Submitted"), ("correction_requested", "Correction requested"), ("confirmed", "Confirmed"), ("manager_adjusted", "Manager adjusted"), ("worker_acknowledged", "Worker acknowledged")], max_length=32)),
                ("status_after", models.CharField(choices=[("submitted", "Submitted"), ("correction_requested", "Correction requested"), ("confirmed", "Confirmed"), ("manager_adjusted", "Manager adjustment awaiting worker acknowledgement")], max_length=32)),
                ("started_at", models.DateTimeField()),
                ("ended_at", models.DateTimeField()),
                ("break_minutes", models.PositiveSmallIntegerField()),
                ("worked_minutes", models.PositiveSmallIntegerField()),
                ("note", models.CharField(blank=True, default="", max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="support_time_entry_revisions", to=settings.AUTH_USER_MODEL)),
                ("entry", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="revisions", to="support.worktimeentry")),
            ],
            options={"ordering": ("entry_id", "created_at", "id")},
        ),
        migrations.AddIndex(model_name="scheduledworkshift", index=models.Index(fields=["organization", "state", "work_date"], name="support_sch_organiz_c7f429_idx")),
        migrations.AddIndex(model_name="scheduledworkshift", index=models.Index(fields=["connection", "state", "work_date"], name="support_sch_connect_a3a779_idx")),
        migrations.AddConstraint(model_name="scheduledworkshift", constraint=models.CheckConstraint(condition=models.Q(("starts_at__lt", models.F("ends_at"))), name="support_scheduled_shift_valid_period")),
        migrations.AddConstraint(model_name="scheduledworkshift", constraint=models.UniqueConstraint(condition=models.Q(("state__in", ("draft", "published"))), fields=("connection", "work_date"), name="support_one_current_scheduled_shift_per_day")),
        migrations.AddIndex(model_name="worktimeentry", index=models.Index(fields=["organization", "work_date", "status"], name="support_wor_organiz_b39dc8_idx")),
        migrations.AddIndex(model_name="worktimeentry", index=models.Index(fields=["connection", "work_date"], name="support_wor_connect_44177a_idx")),
        migrations.AddConstraint(model_name="worktimeentry", constraint=models.UniqueConstraint(fields=("connection", "work_date"), name="support_one_work_time_entry_per_day")),
        migrations.AddConstraint(model_name="worktimeentry", constraint=models.CheckConstraint(condition=models.Q(("started_at__lt", models.F("ended_at"))), name="support_time_entry_valid_period")),
        migrations.AddConstraint(model_name="worktimeentry", constraint=models.CheckConstraint(condition=models.Q(("worked_minutes__gte", 0)), name="support_time_entry_nonnegative_minutes")),
        migrations.AddIndex(model_name="worktimeentryrevision", index=models.Index(fields=["entry", "revision", "created_at"], name="support_wor_entry_i_09e8e3_idx")),
    ]
