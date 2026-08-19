import uuid

from django.conf import settings
from django.db import models

from .organization import SupportOrganization


class AuditEvent(models.Model):
    """Append-only audit record for sensitive Support actions.

    The service layer owns the metadata whitelist.  Files, message bodies,
    document values, bank information, and push contents must never be written
    into ``details``.
    """

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_audit_events",
    )
    action = models.CharField(max_length=96, db_index=True)
    target_type = models.CharField(max_length=64, blank=True, default="")
    target_public_id = models.UUIDField(null=True, blank=True)
    details = models.JSONField(default=dict, blank=True)
    request_id = models.UUIDField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "actor", "action", "request_id"),
                condition=models.Q(request_id__isnull=False),
                name="support_unique_audit_request_action",
            ),
        ]
        indexes = [
            models.Index(fields=("organization", "created_at")),
            models.Index(fields=("actor", "created_at")),
            models.Index(fields=("target_type", "target_public_id")),
        ]
