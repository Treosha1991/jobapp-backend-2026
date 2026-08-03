"""E-mail document request cards without document files.

JobHub Support records only what the employer asked for and the progress of
the e-mail hand-off.  It deliberately does not contain uploads, document
numbers, scans, bank details or e-mail contents.
"""

import uuid

from django.conf import settings
from django.db import models

from .organization import SupportOrganization
from .pipeline import SupportConnection


class SupportWorkerDocumentReference(models.Model):
    """A stable account code used by the employer to find an incoming e-mail."""

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_document_reference",
    )
    reference_code = models.CharField(max_length=24, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=("reference_code",))]


class DocumentRequestPackage(models.Model):
    """A manager-selected request to use the employer's external e-mail flow."""

    STATUS_REQUESTED = "requested"
    STATUS_SENT_TO_EMPLOYER = "sent_to_employer"
    STATUS_NEEDS_CORRECTION = "needs_correction"
    STATUS_COMPLETED = "completed"
    STATUS_NOT_REQUIRED = "not_required"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_REQUESTED, "Requested"),
        (STATUS_SENT_TO_EMPLOYER, "Sent to employer"),
        (STATUS_NEEDS_CORRECTION, "Needs correction"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_NOT_REQUIRED, "Not required"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.CASCADE,
        related_name="document_request_packages",
    )
    connection = models.ForeignKey(
        SupportConnection,
        on_delete=models.CASCADE,
        related_name="document_request_packages",
    )
    recipient_email = models.EmailField()
    account_reference = models.ForeignKey(
        SupportWorkerDocumentReference,
        on_delete=models.PROTECT,
        related_name="document_request_packages",
    )
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_REQUESTED)
    # A non-sensitive list of document type keys plus an optional short custom
    # label. It never contains a number, scan, URL, file name or document value.
    requested_items = models.JSONField(default=list)
    additional_instructions = models.CharField(max_length=500, blank=True, default="")
    manager_note = models.CharField(max_length=500, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_support_document_request_packages",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_support_document_request_packages",
    )
    sent_marked_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at", "-id")
        indexes = [
            models.Index(fields=("connection", "status", "updated_at")),
            models.Index(fields=("organization", "status", "updated_at")),
        ]


class DocumentRequestPackageEvent(models.Model):
    """Append-only history without the requested document contents."""

    ACTION_CREATED = "created"
    ACTION_WORKER_MARKED_SENT = "worker_marked_sent"
    ACTION_NEEDS_CORRECTION = "needs_correction"
    ACTION_COMPLETED = "completed"
    ACTION_NOT_REQUIRED = "not_required"
    ACTION_CANCELLED = "cancelled"
    ACTION_CHOICES = [
        (ACTION_CREATED, "Created"),
        (ACTION_WORKER_MARKED_SENT, "Worker marked sent"),
        (ACTION_NEEDS_CORRECTION, "Needs correction"),
        (ACTION_COMPLETED, "Completed"),
        (ACTION_NOT_REQUIRED, "Not required"),
        (ACTION_CANCELLED, "Cancelled"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    package = models.ForeignKey(
        DocumentRequestPackage,
        on_delete=models.CASCADE,
        related_name="events",
    )
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    status_after = models.CharField(max_length=32, choices=DocumentRequestPackage.STATUS_CHOICES)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_document_request_package_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("package_id", "created_at", "id")
        indexes = [models.Index(fields=("package", "created_at"))]
