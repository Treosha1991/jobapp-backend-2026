from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("support", "0031_supportapplication_questionnaire"),
    ]

    operations = [
        migrations.AddField(
            model_name="supportmessage",
            name="reply_to",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="replies",
                to="support.supportmessage",
            ),
        ),
        migrations.CreateModel(
            name="SupportConversationReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reason", models.CharField(choices=[("spam", "Spam or advertising"), ("scam", "Scam or fraud"), ("abuse", "Abuse or harassment"), ("inappropriate", "Inappropriate content"), ("other", "Other")], max_length=20)),
                ("message", models.TextField(blank=True, max_length=1000)),
                ("status", models.CharField(choices=[("new", "New"), ("in_review", "In review"), ("resolved", "Resolved"), ("rejected", "Rejected")], default="new", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("conversation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reports", to="support.supportconversation")),
                ("reported_user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="received_support_conversation_reports", to=settings.AUTH_USER_MODEL)),
                ("reporter", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="filed_support_conversation_reports", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ("-created_at", "-id"),
            },
        ),
        migrations.AddIndex(
            model_name="supportconversationreport",
            index=models.Index(fields=["status", "created_at"], name="support_sup_status_b3b37d_idx"),
        ),
    ]
