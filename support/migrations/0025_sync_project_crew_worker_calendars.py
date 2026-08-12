from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone


def backfill_worker_calendars(apps, schema_editor):
    ProjectCrewShiftMember = apps.get_model("support", "ProjectCrewShiftMember")
    ScheduledWorkShift = apps.get_model("support", "ScheduledWorkShift")
    now = timezone.now()
    members = ProjectCrewShiftMember.objects.select_related(
        "shift__crew__organization",
    ).all()
    for member in members.iterator():
        shift = member.shift
        published = shift.state == "published"
        ScheduledWorkShift.objects.update_or_create(
            project_crew_member_id=member.pk,
            defaults={
                "organization_id": shift.crew.organization_id,
                "connection_id": member.connection_id,
                "work_date": shift.work_date,
                "starts_at": shift.starts_at,
                "ends_at": shift.ends_at,
                "break_minutes": shift.break_minutes,
                "worker_label": shift.crew.internal_name,
                "state": "published" if published else "cancelled",
                "created_by_id": member.created_by_id,
                "published_by_id": member.created_by_id if published else None,
                "published_at": now if published else None,
                "cancelled_at": None if published else now,
            },
        )


def remove_backfilled_worker_calendars(apps, schema_editor):
    ScheduledWorkShift = apps.get_model("support", "ScheduledWorkShift")
    ScheduledWorkShift.objects.filter(project_crew_member__isnull=False).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0024_add_project_first_crews"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="scheduledworkshift",
            name="support_one_legacy_shift_day",
        ),
        migrations.AddField(
            model_name="scheduledworkshift",
            name="project_crew_member",
            field=models.OneToOneField(
                blank=True,
                help_text="Source crew-day membership in the project-first workspace.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="worker_calendar_shift",
                to="support.projectcrewshiftmember",
            ),
        ),
        migrations.AddConstraint(
            model_name="scheduledworkshift",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("crew__isnull", True),
                    ("project_crew_member__isnull", True),
                    ("state__in", ("draft", "published")),
                ),
                fields=("connection", "work_date"),
                name="support_one_legacy_shift_day",
            ),
        ),
        migrations.RunPython(
            backfill_worker_calendars,
            remove_backfilled_worker_calendars,
        ),
    ]
