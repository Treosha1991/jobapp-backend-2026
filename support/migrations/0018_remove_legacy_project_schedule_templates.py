from django.db import migrations


def remove_legacy_project_schedule_templates(apps, schema_editor):
    """Clear the former date-bound templates before changing the data model.

    Earlier staging builds attached selected dates to each project template and
    copied those dates through worker-assignment selections.  The new design
    assigns a date only in the worker calendar, so retaining the old data
    would be misleading.  It is intentionally not recreated on rollback.
    """

    Selection = apps.get_model("support", "WorkerProjectScheduleTemplateSelection")
    Template = apps.get_model("support", "ProjectScheduleTemplate")
    Selection.objects.all().delete()
    Template.objects.all().delete()


class Migration(migrations.Migration):

    # PostgreSQL cannot alter this table in the same transaction that deletes
    # rows referenced by its foreign-key triggers.  Running the operations in
    # separate transactions lets the cleanup finish before the schema change.
    atomic = False

    dependencies = [
        ("support", "0017_backfill_legacy_project_capacity"),
    ]

    operations = [
        migrations.RunPython(
            remove_legacy_project_schedule_templates,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="projectscheduletemplate",
            name="calendar_dates",
        ),
        migrations.DeleteModel(
            name="WorkerProjectScheduleTemplateSelection",
        ),
    ]
