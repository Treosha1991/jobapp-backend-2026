from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0020_userblock"),
    ]

    operations = [
        migrations.AddField(
            model_name="vacancy",
            name="is_paused_by_owner",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="vacancy",
            name="paused_by_owner_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

