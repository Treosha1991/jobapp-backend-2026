from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0028_backfill_housing_room_places"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="projectcrewresourceassignment",
            name="support_pc_one_open_driver",
        ),
        migrations.RemoveConstraint(
            model_name="projectcrewresourceassignment",
            name="support_pc_one_open_vehicle",
        ),
    ]
