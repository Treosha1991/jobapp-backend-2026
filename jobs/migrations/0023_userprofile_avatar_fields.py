from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0022_userprofile_nickname"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="avatar_key",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="avatar_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
