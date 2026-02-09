from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0010_emailverification_pending_password_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="vacancy",
            name="editing_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="vacancy",
            name="is_editing",
            field=models.BooleanField(default=False),
        ),
    ]
