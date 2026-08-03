import uuid

from django.conf import settings
from django.db import models

from .organization import SupportOrganization


class NotificationOutbox(models.Model):
    """Durable, safe notification event created with the business action.

    It stores a code and a technical target, never a chat body, address,
    document value, financial value, or other user-visible sensitive content.
    The delivery service turns the code into neutral, localized push text only
    for the recipient's registered device language.
    """

    STATUS_PENDING = "pending"
    STATUS_DELIVERING = "delivering"
    STATUS_DELIVERED = "delivered"
    STATUS_SKIPPED = "skipped"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_DELIVERING, "Delivering"),
        (STATUS_DELIVERED, "Delivered"),
        (STATUS_SKIPPED, "Skipped"),
        (STATUS_FAILED, "Failed"),
    ]

    TARGET_CONNECTION = "connection"
    TARGET_CONVERSATION = "conversation"
    TARGET_SUPPORT_ACCESS = "support_access"
    TARGET_HOUSING_ASSIGNMENT = "housing_assignment"
    TARGET_WORK_ASSIGNMENT = "work_assignment"
    TARGET_TRANSPORT_ROUTE = "transport_route"
    TARGET_SCHEDULED_SHIFT = "scheduled_shift"
    TARGET_TIME_ENTRY = "time_entry"
    TARGET_WORKER_REQUEST = "worker_request"
    TARGET_TASK_ASSIGNMENT = "task_assignment"
    TARGET_ANNOUNCEMENT = "announcement"
    TARGET_CHOICES = [
        (TARGET_CONNECTION, "Connection"),
        (TARGET_CONVERSATION, "Conversation"),
        (TARGET_SUPPORT_ACCESS, "Support access"),
        (TARGET_HOUSING_ASSIGNMENT, "Housing assignment"),
        (TARGET_WORK_ASSIGNMENT, "Work assignment"),
        (TARGET_TRANSPORT_ROUTE, "Transport route"),
        (TARGET_SCHEDULED_SHIFT, "Scheduled shift"),
        (TARGET_TIME_ENTRY, "Time entry"),
        (TARGET_WORKER_REQUEST, "Worker request"),
        (TARGET_TASK_ASSIGNMENT, "Task assignment"),
        (TARGET_ANNOUNCEMENT, "Announcement"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notification_outbox_entries",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_notification_outbox_entries",
    )
    notification_code = models.CharField(max_length=64)
    target_kind = models.CharField(max_length=32, choices=TARGET_CHOICES)
    target_public_id = models.UUIDField()
    target_key = models.CharField(max_length=160)
    notification_namespace = models.CharField(max_length=40, default="support")
    collapse_key = models.CharField(max_length=160)
    dedupe_key = models.CharField(max_length=160, unique=True)
    push_requested = models.BooleanField(default=True)
    safe_context = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    attempt_count = models.PositiveSmallIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=("status", "created_at")),
            models.Index(fields=("recipient", "created_at")),
            models.Index(fields=("target_key", "created_at")),
        ]


class InAppNotification(models.Model):
    """The notification center item, independent from the phone's tray."""

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    outbox = models.OneToOneField(
        NotificationOutbox,
        on_delete=models.CASCADE,
        related_name="in_app_notification",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_in_app_notifications",
    )
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=("recipient", "read_at", "created_at")),
        ]


class PushDelivery(models.Model):
    STATUS_PENDING = "pending"
    STATUS_SENT = "sent"
    STATUS_SKIPPED = "skipped"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_SENT, "Sent"),
        (STATUS_SKIPPED, "Skipped"),
        (STATUS_FAILED, "Failed"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    outbox = models.ForeignKey(
        NotificationOutbox,
        on_delete=models.CASCADE,
        related_name="push_deliveries",
    )
    device = models.ForeignKey(
        "jobs.PushDevice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_push_deliveries",
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    device_platform = models.CharField(max_length=20, blank=True, default="")
    device_token_tail = models.CharField(max_length=12, blank=True, default="")
    provider_message_id = models.CharField(max_length=255, blank=True, default="")
    native_notification_tag = models.CharField(max_length=180, blank=True, default="")
    error_code = models.CharField(max_length=120, blank=True, default="")
    attempted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("outbox", "device"),
                name="support_unique_push_delivery_per_device",
            ),
        ]
        indexes = [
            models.Index(fields=("outbox", "status")),
            models.Index(fields=("device", "status")),
        ]
