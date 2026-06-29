from django.db import migrations


def make_ad_forever_contacts_free(apps, schema_editor):
    VacancyContactAccessPolicy = apps.get_model("jobs", "VacancyContactAccessPolicy")
    VacancyContactAccessPolicy.objects.filter(
        contact_unlock_mode="ad_forever",
        contact_unlock_price_credits__gt=0,
    ).update(
        contact_unlock_price_credits=0,
        contact_unlock_timer_hours=None,
        contact_unlock_paid_click_limit=None,
        paid_window_started_at=None,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("jobs", "0041_moderator_notification_delivery"),
    ]

    operations = [
        migrations.RunPython(make_ad_forever_contacts_free, migrations.RunPython.noop),
    ]
