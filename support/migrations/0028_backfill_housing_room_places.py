from django.db import migrations


def backfill_housing_room_places(apps, schema_editor):
    HousingRoom = apps.get_model("support", "HousingRoom")
    HousingPlace = apps.get_model("support", "HousingPlace")

    for room in HousingRoom.objects.filter(is_active=True).iterator():
        existing_count = HousingPlace.objects.filter(room_id=room.id).count()
        missing_count = max(0, room.capacity - existing_count)
        if not missing_count:
            continue

        used_labels = set(
            HousingPlace.objects.filter(room_id=room.id).values_list("label", flat=True)
        )
        labels = []
        candidate = 1
        while len(labels) < missing_count:
            label = str(candidate)
            if label not in used_labels:
                labels.append(label)
                used_labels.add(label)
            candidate += 1
        HousingPlace.objects.bulk_create(
            [HousingPlace(room_id=room.id, label=label) for label in labels]
        )


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0027_projectcrewdriversubstitution_passenger_state"),
    ]

    operations = [
        migrations.RunPython(backfill_housing_room_places, migrations.RunPython.noop),
    ]
