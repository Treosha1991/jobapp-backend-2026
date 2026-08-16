import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from .organization import OrganizationMembership, SupportOrganization
from .pipeline import SupportConnection


class SupportConversation(models.Model):
    """A Support-only text conversation.

    There are no attachment, file, image, or document fields in this model.
    Keeping it independent from the legacy JobHub chat prevents a public chat
    from accidentally becoming a Support channel.
    """

    KIND_MANAGER = "manager"
    KIND_COORDINATOR = "coordinator"
    KIND_DRIVER = "driver"
    KIND_GROUP = "group"
    KIND_JOBHUB = "jobhub"
    KIND_CHOICES = [
        (KIND_MANAGER, "Manager"),
        (KIND_COORDINATOR, "Coordinator"),
        (KIND_DRIVER, "Driver"),
        (KIND_GROUP, "Group"),
        (KIND_JOBHUB, "JobHub"),
    ]
    STATE_ACTIVE = "active"
    STATE_ARCHIVED = "archived"
    STATE_CHOICES = [(STATE_ACTIVE, "Active"), (STATE_ARCHIVED, "Archived")]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.CASCADE,
        related_name="support_conversations",
    )
    connection = models.ForeignKey(
        SupportConnection,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="conversations",
    )
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    title = models.CharField(max_length=160, blank=True, default="")
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=STATE_ACTIVE)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_support_conversations",
    )
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("connection", "kind"),
                condition=Q(kind="manager"),
                name="support_one_manager_conversation_per_connection",
            ),
        ]
        indexes = [
            models.Index(fields=("organization", "state", "updated_at")),
            models.Index(fields=("connection", "kind", "state")),
        ]


class SupportConversationMember(models.Model):
    ROLE_WORKER = "worker"
    ROLE_STAFF = "staff"
    ROLE_CHOICES = [(ROLE_WORKER, "Worker"), (ROLE_STAFF, "Staff")]

    conversation = models.ForeignKey(
        SupportConversation,
        on_delete=models.CASCADE,
        related_name="members",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_conversation_memberships",
    )
    organization_membership = models.ForeignKey(
        OrganizationMembership,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_conversation_memberships",
    )
    role = models.CharField(max_length=16, choices=ROLE_CHOICES)
    can_send = models.BooleanField(default=True)
    group_push_enabled = models.BooleanField(default=True)
    last_read_at = models.DateTimeField(null=True, blank=True)
    left_at = models.DateTimeField(null=True, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("conversation", "user"),
                name="support_unique_conversation_member",
            ),
        ]
        indexes = [
            models.Index(fields=("user", "left_at")),
            models.Index(fields=("conversation", "left_at")),
        ]


class SupportMessage(models.Model):
    LANGUAGE_RU = "ru"
    LANGUAGE_EN = "en"
    LANGUAGE_PL = "pl"
    LANGUAGE_UK = "uk"
    LANGUAGE_CHOICES = [
        (LANGUAGE_RU, "Russian"),
        (LANGUAGE_EN, "English"),
        (LANGUAGE_PL, "Polish"),
        (LANGUAGE_UK, "Ukrainian"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    conversation = models.ForeignKey(
        SupportConversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="sent_support_messages",
    )
    reply_to = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replies",
    )
    body = models.TextField(max_length=1500)
    original_language = models.CharField(max_length=2, choices=LANGUAGE_CHOICES)
    client_message_id = models.UUIDField(default=uuid.uuid4)
    edited_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("conversation", "client_message_id"),
                name="support_unique_conversation_client_message",
            ),
        ]
        indexes = [models.Index(fields=("conversation", "created_at"))]


class SupportConversationReport(models.Model):
    """A moderation report created from a Support conversation."""

    REASON_CHOICES = [
        ("spam", "Spam or advertising"),
        ("scam", "Scam or fraud"),
        ("abuse", "Abuse or harassment"),
        ("inappropriate", "Inappropriate content"),
        ("other", "Other"),
    ]
    STATUS_CHOICES = [
        ("new", "New"),
        ("in_review", "In review"),
        ("resolved", "Resolved"),
        ("rejected", "Rejected"),
    ]

    conversation = models.ForeignKey(
        SupportConversation,
        on_delete=models.CASCADE,
        related_name="reports",
    )
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="filed_support_conversation_reports",
    )
    reported_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="received_support_conversation_reports",
    )
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    message = models.TextField(blank=True, max_length=1000)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="new")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [models.Index(fields=("status", "created_at"))]


class SupportMessageTranslation(models.Model):
    """A requested translation; the original Support message is never replaced."""

    STATUS_READY = "ready"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [(STATUS_READY, "Ready"), (STATUS_FAILED, "Failed")]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    message = models.ForeignKey(
        SupportMessage,
        on_delete=models.CASCADE,
        related_name="translations",
    )
    target_language = models.CharField(max_length=2, choices=SupportMessage.LANGUAGE_CHOICES)
    translated_text = models.TextField(max_length=3000, blank=True, default="")
    provider = models.CharField(max_length=64, blank=True, default="")
    provider_version = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES)
    error_code = models.CharField(max_length=120, blank=True, default="")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_support_message_translations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("message", "target_language"),
                name="support_unique_message_translation_language",
            ),
        ]
        indexes = [models.Index(fields=("message", "target_language", "status"))]
