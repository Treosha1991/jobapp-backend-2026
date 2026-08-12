from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0026_projectcrewdriversubstitution_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="projectcrewdriversubstitution",
            name="substitute_was_passenger",
            field=models.BooleanField(
                default=False,
                help_text="Whether the substitute must return to the passenger role when replaced.",
            ),
        ),
    ]
