from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0036_private_manager_worker_conversations"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="supportmessage",
            name="kind",
            field=models.CharField(
                choices=[("text", "Text"), ("contact", "Contact")],
                default="text",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="supportmessage",
            name="shared_contact_connection",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="shared_in_support_messages",
                to="support.supportconnection",
            ),
        ),
        migrations.AddField(
            model_name="supportmessage",
            name="shared_contact_membership",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="shared_in_support_messages",
                to="support.organizationmembership",
            ),
        ),
        migrations.AddField(
            model_name="supportmessage",
            name="shared_contact_user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="shared_in_support_messages",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
