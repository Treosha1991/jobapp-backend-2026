from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0023_userprofile_avatar_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="vacancy",
            name="additional_phone",
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name="vacancy",
            name="hide_primary_phone",
            field=models.BooleanField(default=False),
        ),
    ]

