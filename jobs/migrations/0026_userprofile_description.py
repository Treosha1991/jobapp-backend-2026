from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0025_push_notifications_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="description",
            field=models.CharField(blank=True, default="", max_length=160),
        ),
    ]

