from django.db import migrations


def backfill_legacy_project_capacity(apps, schema_editor):
    """Keep existing projects from displaying an impossible occupied count."""

    WorkProject = apps.get_model("support", "WorkProject")
    WorkerProjectAssignment = apps.get_model("support", "WorkerProjectAssignment")
    for project in WorkProject.objects.all().iterator():
        published_count = WorkerProjectAssignment.objects.filter(
            project_id=project.pk,
            state="published",
        ).count()
        if published_count > project.worker_capacity:
            WorkProject.objects.filter(pk=project.pk).update(
                worker_capacity=published_count,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0016_add_project_workspace_and_schedules"),
    ]

    operations = [
        migrations.RunPython(
            backfill_legacy_project_capacity,
            migrations.RunPython.noop,
        ),
    ]
