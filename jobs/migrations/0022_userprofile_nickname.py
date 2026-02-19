from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0021_vacancy_pause_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="nickname",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
    ]
