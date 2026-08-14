from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0030_application_clarification_answer"),
    ]

    operations = [
        migrations.AddField(
            model_name="supportapplication",
            name="questionnaire_version",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="supportapplication",
            name="questionnaire_answers",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
