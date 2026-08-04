from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("support", "0013_contenttemplate")]

    operations = [
        migrations.AddField(
            model_name="supportconnection",
            name="has_driving_license",
            field=models.BooleanField(default=False),
        ),
    ]
