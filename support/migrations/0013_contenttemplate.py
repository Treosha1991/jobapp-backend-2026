# Generated manually for the reusable task/announcement wording slice.

import django.db.models.deletion
import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0012_calendarmarkbatch_calendarmarkbatchitem_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ContentTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("name", models.CharField(max_length=120)),
                ("kind", models.CharField(choices=[("task", "Task"), ("announcement", "Announcement")], max_length=16)),
                ("translations", models.JSONField(default=dict)),
                ("source_language", models.CharField(choices=[("ru", "Russian"), ("en", "English"), ("pl", "Polish"), ("uk", "Ukrainian")], max_length=2)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_support_content_templates", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="content_templates", to="support.supportorganization")),
            ],
            options={"ordering": ("kind", "name", "id")},
        ),
        migrations.AddConstraint(
            model_name="contenttemplate",
            constraint=models.UniqueConstraint(fields=("organization", "name"), name="support_unique_content_template_name_per_org"),
        ),
        migrations.AddIndex(
            model_name="contenttemplate",
            index=models.Index(fields=["organization", "kind", "is_active", "name"], name="support_ct_org_kind_active_idx"),
        ),
    ]
