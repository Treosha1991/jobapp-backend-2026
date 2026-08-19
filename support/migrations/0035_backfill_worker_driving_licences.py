from django.db import migrations


DEMO_DRIVER_EMAILS = {
    "support-demo-worker-01@jobhub.test",
    "support-demo-worker-04@jobhub.test",
    "support-demo-worker-05@jobhub.test",
    "support-demo-worker-07@jobhub.test",
    "support-demo-worker-08@jobhub.test",
}


def backfill_worker_driving_licences(apps, schema_editor):
    SupportConnection = apps.get_model("support", "SupportConnection")
    DriverVehicleAssignment = apps.get_model("support", "DriverVehicleAssignment")
    ProjectCrewResourceAssignment = apps.get_model(
        "support", "ProjectCrewResourceAssignment"
    )
    ProjectCrewDriverSubstitution = apps.get_model(
        "support", "ProjectCrewDriverSubstitution"
    )

    connection_ids = set(
        DriverVehicleAssignment.objects.values_list("driver_connection_id", flat=True)
    )
    connection_ids.update(
        ProjectCrewResourceAssignment.objects.exclude(driver_connection_id=None).values_list(
            "driver_connection_id", flat=True
        )
    )
    connection_ids.update(
        ProjectCrewDriverSubstitution.objects.values_list(
            "primary_driver_connection_id", flat=True
        )
    )
    connection_ids.update(
        ProjectCrewDriverSubstitution.objects.values_list(
            "substitute_driver_connection_id", flat=True
        )
    )

    for connection in SupportConnection.objects.select_related("application", "candidate"):
        answers = connection.application.questionnaire_answers or {}
        if answers.get("has_driving_license") is True:
            connection_ids.add(connection.pk)
        if connection.candidate.email.lower() in DEMO_DRIVER_EMAILS:
            connection_ids.add(connection.pk)

    if connection_ids:
        SupportConnection.objects.filter(pk__in=connection_ids).update(
            has_driving_license=True
        )


class Migration(migrations.Migration):
    dependencies = [("support", "0034_auditevent_idempotency_constraint")]

    operations = [
        migrations.RunPython(backfill_worker_driving_licences, migrations.RunPython.noop),
    ]
