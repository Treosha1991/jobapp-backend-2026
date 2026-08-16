from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("support", "0032_support_chat_parity"),
    ]

    operations = [
        migrations.AddField(
            model_name="supportmessage",
            name="forwarded_from",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="forwards",
                to="support.supportmessage",
            ),
        ),
    ]
