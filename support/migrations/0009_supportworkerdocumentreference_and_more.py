# Generated manually for JobHub Support Package 5.

import django.db.models.deletion
import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0008_workerrequest_workerrequestevent_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SupportWorkerDocumentReference",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("reference_code", models.CharField(max_length=24, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="support_document_reference", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="DocumentRequestPackage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("recipient_email", models.EmailField(max_length=254)),
                ("status", models.CharField(choices=[("requested", "Requested"), ("sent_to_employer", "Sent to employer"), ("needs_correction", "Needs correction"), ("completed", "Completed"), ("not_required", "Not required"), ("cancelled", "Cancelled")], default="requested", max_length=32)),
                ("requested_items", models.JSONField(default=list)),
                ("additional_instructions", models.CharField(blank=True, default="", max_length=500)),
                ("manager_note", models.CharField(blank=True, default="", max_length=500)),
                ("sent_marked_at", models.DateTimeField(blank=True, null=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("connection", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="document_request_packages", to="support.supportconnection")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_support_document_request_packages", to=settings.AUTH_USER_MODEL)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="document_request_packages", to="support.supportorganization")),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_support_document_request_packages", to=settings.AUTH_USER_MODEL)),
                ("account_reference", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="document_request_packages", to="support.supportworkerdocumentreference")),
            ],
            options={"ordering": ("-updated_at", "-id")},
        ),
        migrations.CreateModel(
            name="DocumentRequestPackageEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("action", models.CharField(choices=[("created", "Created"), ("worker_marked_sent", "Worker marked sent"), ("needs_correction", "Needs correction"), ("completed", "Completed"), ("not_required", "Not required"), ("cancelled", "Cancelled")], max_length=32)),
                ("status_after", models.CharField(choices=[("requested", "Requested"), ("sent_to_employer", "Sent to employer"), ("needs_correction", "Needs correction"), ("completed", "Completed"), ("not_required", "Not required"), ("cancelled", "Cancelled")], max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="support_document_request_package_events", to=settings.AUTH_USER_MODEL)),
                ("package", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="support.documentrequestpackage")),
            ],
            options={
                "ordering": ("package_id", "created_at", "id"),
                "indexes": [models.Index(fields=["package", "created_at"], name="support_doc_package_586a8b_idx")],
            },
        ),
        migrations.AddIndex(model_name="supportworkerdocumentreference", index=models.Index(fields=["reference_code"], name="support_sup_referen_dafef6_idx")),
        migrations.AddIndex(model_name="documentrequestpackage", index=models.Index(fields=["connection", "status", "updated_at"], name="support_doc_connect_8cad85_idx")),
        migrations.AddIndex(model_name="documentrequestpackage", index=models.Index(fields=["organization", "status", "updated_at"], name="support_doc_organiz_496624_idx")),
    ]
