import re

from django.db import migrations


def refresh_generated_public_nicknames(apps, schema_editor):
    UserProfile = apps.get_model("jobs", "UserProfile")
    generated_pattern = re.compile(r"^(?:JobHub User|User)\s+\d+$", re.IGNORECASE)
    for profile in UserProfile.objects.all().only("id", "user_id", "nickname").iterator():
        nickname = (profile.nickname or "").strip()
        if not nickname or generated_pattern.fullmatch(nickname):
            profile.nickname = f"User {1000 + int(profile.user_id)}"
            profile.save(update_fields=["nickname"])


class Migration(migrations.Migration):
    dependencies = [("jobs", "0045_chat_message_actions_and_public_nicknames")]

    operations = [migrations.RunPython(refresh_generated_public_nicknames, migrations.RunPython.noop)]
