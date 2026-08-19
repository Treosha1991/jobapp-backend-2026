from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0033_supportmessage_forwarded_from"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="auditevent",
            constraint=models.UniqueConstraint(
                fields=("organization", "actor", "action", "request_id"),
                condition=models.Q(request_id__isnull=False),
                name="support_unique_audit_request_action",
            ),
        ),
    ]
