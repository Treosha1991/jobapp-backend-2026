from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Q
from django.utils import timezone


def backfill_shift_templates(apps, schema_editor):
    ScheduledWorkShift = apps.get_model("support", "ScheduledWorkShift")
    ProjectScheduleTemplate = apps.get_model("support", "ProjectScheduleTemplate")

    templates_by_project = {}
    for template in ProjectScheduleTemplate.objects.filter(is_active=True).iterator():
        templates_by_project.setdefault(template.project_id, []).append(template)

    shifts = ScheduledWorkShift.objects.select_related("work_assignment").filter(
        work_assignment__isnull=False,
        schedule_template__isnull=True,
    )
    for shift in shifts.iterator():
        project_id = shift.work_assignment.project_id
        matches = [
            template
            for template in templates_by_project.get(project_id, [])
            if template.starts_at_time
            == timezone.localtime(shift.starts_at).time().replace(tzinfo=None)
            and template.ends_at_time
            == timezone.localtime(shift.ends_at).time().replace(tzinfo=None)
            and template.break_minutes == shift.break_minutes
        ]
        if len(matches) == 1:
            shift.schedule_template_id = matches[0].id
            shift.save(update_fields=["schedule_template"])


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0019_reset_schedule_templates_and_limit_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="scheduledworkshift",
            name="schedule_template",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="scheduled_shifts",
                to="support.projectscheduletemplate",
            ),
        ),
        migrations.AddField(
            model_name="transportroute",
            name="schedule_template",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="transport_routes",
                to="support.projectscheduletemplate",
            ),
        ),
        migrations.RunPython(backfill_shift_templates, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="transportroute",
            constraint=models.UniqueConstraint(
                condition=Q(
                    schedule_template__isnull=False,
                    state__in=("draft", "published"),
                ),
                fields=("driver_vehicle_assignment", "schedule_template"),
                name="support_one_active_crew_per_driver_template",
            ),
        ),
    ]
