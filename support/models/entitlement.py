import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from .organization import OrganizationMembership, SupportOrganization


class SupportAccessGrant(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_REVOKED = "revoked"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_REVOKED, "Revoked"),
        (STATUS_EXPIRED, "Expired"),
    ]

    REASON_CONNECTION = "continue_connection"
    REASON_TRANSITION = "transition_period"
    REASON_TECHNICAL = "technical_help"
    REASON_CHOICES = [
        (REASON_CONNECTION, "Continue connection"),
        (REASON_TRANSITION, "Transition period"),
        (REASON_TECHNICAL, "Technical help"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_access_grants",
    )
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_access_grants",
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_support_access_grants",
    )
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField()
    reason = models.CharField(max_length=32, choices=REASON_CHOICES)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="revoked_support_access_grants",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-ends_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=Q(starts_at__lt=F("ends_at")),
                name="support_access_grant_valid_period",
            ),
        ]
        indexes = [
            models.Index(fields=("user", "status", "ends_at")),
            models.Index(fields=("organization", "status", "ends_at")),
        ]


class SupportAccessExtensionRequest(models.Model):
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_DECLINED = "declined"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_DECLINED, "Declined"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    DURATION_CHOICES = [(7, "7 days"), (14, "14 days"), (30, "30 days")]

    REASON_CONNECTION = SupportAccessGrant.REASON_CONNECTION
    REASON_TRANSITION = SupportAccessGrant.REASON_TRANSITION
    REASON_TECHNICAL = SupportAccessGrant.REASON_TECHNICAL
    REASON_CHOICES = SupportAccessGrant.REASON_CHOICES

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.CASCADE,
        related_name="support_extension_requests",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_extension_requests",
    )
    requested_by = models.ForeignKey(
        OrganizationMembership,
        on_delete=models.SET_NULL,
        null=True,
        related_name="requested_support_extensions",
    )
    duration_days = models.PositiveSmallIntegerField(choices=DURATION_CHOICES)
    reason = models.CharField(max_length=32, choices=REASON_CHOICES)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decided_support_extension_requests",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("user",),
                condition=Q(status="pending"),
                name="support_unique_pending_extension_request_per_user",
            ),
        ]
        indexes = [
            models.Index(fields=("organization", "status", "created_at")),
            models.Index(fields=("user", "status", "created_at")),
        ]
