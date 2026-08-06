from django.db import migrations, models


def reset_schedule_templates(apps, schema_editor):
    """Start the staging schedule-template catalogue from a clean state."""

    ProjectScheduleTemplate = apps.get_model("support", "ProjectScheduleTemplate")
    ProjectScheduleTemplate.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0018_remove_legacy_project_schedule_templates"),
    ]

    operations = [
        migrations.RunPython(
            reset_schedule_templates,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="projectscheduletemplate",
            name="name",
            field=models.CharField(max_length=30),
        ),
    ]
