# Generated manually for the separate public Telegram username field.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0046_refresh_generated_public_nicknames"),
    ]

    operations = [
        migrations.AddField(
            model_name="vacancy",
            name="telegram_username",
            field=models.CharField(blank=True, max_length=32),
        ),
    ]
