from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0027_employer_subscription"),
    ]

    operations = [
        migrations.AlterField(
            model_name="vacancy",
            name="category",
            field=models.CharField(
                choices=[
                    ("construction", "Construction"),
                    ("agriculture", "Agriculture"),
                    ("warehouse", "Warehouse"),
                    ("logistics", "Logistics"),
                    ("manufacturing", "Manufacturing"),
                    ("hospitality", "Hospitality"),
                    ("cleaning", "Cleaning"),
                    ("retail", "Retail"),
                    ("transport", "Transport"),
                    ("healthcare", "Healthcare"),
                    ("it", "IT"),
                    ("freelance", "Freelance"),
                    ("service", "Service"),
                    ("other", "Other"),
                ],
                max_length=30,
            ),
        ),
    ]
