from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("jobs", "0038_alter_unlockedcontact_charged_credits_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="vacancy",
            name="additional_phone_2",
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name="vacancy",
            name="additional_phone_3",
            field=models.CharField(blank=True, max_length=30),
        ),
    ]
