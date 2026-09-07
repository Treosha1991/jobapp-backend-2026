from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from support.models import SupportAccessExtensionRequest, SupportAccessGrant, SupportConnection
from support.permission_codes import SUPPORT_EXTENSION_REQUEST
from support.permissions import (
    active_membership_for,
    require_permission,
    require_worker_connection_access,
)

from .audit import record_audit_event
from .notifications import enqueue_support_notification


def _valid_extension_duration(value):
    try:
        duration_days = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"duration_days": "support_extension_duration_invalid"}) from exc
    allowed_values = {choice[0] for choice in SupportAccessExtensionRequest.DURATION_CHOICES}
    if duration_days not in allowed_values:
        raise ValidationError({"duration_days": "support_extension_duration_invalid"})
    return duration_days


def _valid_extension_reason(value):
    reason = (value or "").strip()
    allowed_values = {choice[0] for choice in SupportAccessExtensionRequest.REASON_CHOICES}
    if reason not in allowed_values:
        raise ValidationError({"reason": "support_extension_reason_invalid"})
    return reason


def _require_extendable_connection(*, actor, organization, connection):
    if connection.organization_id != organization.id or connection.is_archived:
        raise ValidationError({"connection": "support_extension_connection_not_available"})
    if connection.stage == SupportConnection.STAGE_CLOSED:
        raise ValidationError({"connection": "support_extension_connection_not_available"})
    require_worker_connection_access(
        user=actor,
        organization=organization,
        connection=connection,
    )
    return connection


def _create_extension_grant(*, actor, organization, user, duration_days, reason, now):
    """Add a consecutive access period without shortening active access."""

    active_grants = list(
        SupportAccessGrant.objects.select_for_update()
        .filter(
            user=user,
            status=SupportAccessGrant.STATUS_ACTIVE,
            ends_at__gt=now,
        )
        .only("ends_at")
    )
    starts_at = max([now, *(grant.ends_at for grant in active_grants)])
    return SupportAccessGrant.objects.create(
        user=user,
        organization=organization,
        granted_by=actor,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=duration_days),
        reason=reason,
    )


def grant_support_access_extension_as_owner(
    *,
    actor,
    organization,
    connection,
    duration_days,
    reason,
):
    """Let the organization owner extend a worker's Support access directly."""

    owner_membership = active_membership_for(user=actor, organization=organization)
    if owner_membership is None or not owner_membership.is_owner:
        raise PermissionDenied("support_extension_owner_required")
    _require_extendable_connection(
        actor=actor,
        organization=organization,
        connection=connection,
    )
    normalized_duration_days = _valid_extension_duration(duration_days)
    normalized_reason = _valid_extension_reason(reason)
    with transaction.atomic():
        now = timezone.now()
        grant = _create_extension_grant(
            actor=actor,
            organization=organization,
            user=connection.candidate,
            duration_days=normalized_duration_days,
            reason=normalized_reason,
            now=now,
        )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="support_extension.granted",
            target=grant,
            details={
                "connection": str(connection.public_id),
                "duration_days": normalized_duration_days,
                "reason": normalized_reason,
                "owner_direct": True,
            },
        )
    return grant


def request_support_access_extension(
    *,
    actor,
    organization,
    connection,
    duration_days,
    reason,
):
    """Create one owner-reviewable Support extension request for a worker.

    This is deliberately not a payment flow and does not change access on its
    own. The organization owner is the sole final decision maker below.
    """

    membership = require_permission(
        user=actor,
        organization=organization,
        permission_code=SUPPORT_EXTENSION_REQUEST,
    )
    if membership.is_owner:
        raise PermissionDenied("support_extension_owner_decides")
    _require_extendable_connection(
        actor=actor,
        organization=organization,
        connection=connection,
    )
    normalized_duration_days = _valid_extension_duration(duration_days)
    normalized_reason = _valid_extension_reason(reason)

    try:
        with transaction.atomic():
            existing_request = (
                SupportAccessExtensionRequest.objects.select_for_update()
                .filter(
                    user=connection.candidate,
                    status=SupportAccessExtensionRequest.STATUS_PENDING,
                )
                .first()
            )
            if existing_request is not None:
                raise ValidationError({"connection": "support_extension_already_pending"})
            extension_request = SupportAccessExtensionRequest.objects.create(
                organization=organization,
                user=connection.candidate,
                requested_by=membership,
                duration_days=normalized_duration_days,
                reason=normalized_reason,
            )
            record_audit_event(
                organization=organization,
                actor=actor,
                action="support_extension.requested",
                target=extension_request,
                details={
                    "connection": str(connection.public_id),
                    "duration_days": normalized_duration_days,
                    "reason": normalized_reason,
                },
            )
    except IntegrityError as exc:
        # The database constraint covers concurrent requests, including a
        # pending request that may have been made in another organization.
        raise ValidationError({"connection": "support_extension_already_pending"}) from exc
    return extension_request


def decide_support_access_extension(*, actor, extension_request, decision, decision_note=""):
    """Approve or decline a pending request; only the firm owner may decide."""

    normalized_decision = (decision or "").strip().lower()
    if normalized_decision not in {"approve", "decline"}:
        raise ValidationError({"decision": "support_extension_decision_invalid"})
    note = (decision_note or "").strip()
    if len(note) > 255:
        raise ValidationError({"decision_note": "support_extension_note_too_long"})

    with transaction.atomic():
        locked_request = (
            SupportAccessExtensionRequest.objects.select_for_update()
            .select_related("organization", "user")
            .filter(pk=extension_request.pk)
            .first()
        )
        if locked_request is None:
            raise ValidationError({"request": "support_extension_not_found"})
        owner_membership = active_membership_for(
            user=actor,
            organization=locked_request.organization,
        )
        if owner_membership is None or not owner_membership.is_owner:
            raise PermissionDenied("support_extension_owner_required")
        if locked_request.status != SupportAccessExtensionRequest.STATUS_PENDING:
            raise ValidationError({"request": "support_extension_not_pending"})

        now = timezone.now()
        locked_request.decided_by = actor
        locked_request.decided_at = now
        locked_request.decision_note = note
        if normalized_decision == "approve":
            grant = _create_extension_grant(
                actor=actor,
                organization=locked_request.organization,
                user=locked_request.user,
                duration_days=locked_request.duration_days,
                reason=locked_request.reason,
                now=now,
            )
            locked_request.status = SupportAccessExtensionRequest.STATUS_APPROVED
            audit_action = "support_extension.approved"
            audit_details = {
                "duration_days": locked_request.duration_days,
                "grant": str(grant.public_id),
            }
        else:
            locked_request.status = SupportAccessExtensionRequest.STATUS_DECLINED
            audit_action = "support_extension.declined"
            audit_details = {}
        locked_request.save(
            update_fields=[
                "status",
                "decided_by",
                "decided_at",
                "decision_note",
                "updated_at",
            ]
        )
        record_audit_event(
            organization=locked_request.organization,
            actor=actor,
            action=audit_action,
            target=locked_request,
            details=audit_details,
        )
    return locked_request


def active_temporary_grant_for(user, *, at_time=None):
    current_time = at_time or timezone.now()
    return (
        SupportAccessGrant.objects.filter(
            user=user,
            status=SupportAccessGrant.STATUS_ACTIVE,
            starts_at__lte=current_time,
            ends_at__gt=current_time,
        )
        .order_by("-ends_at", "-id")
        .first()
    )


def support_access_snapshot_for(user, *, at_time=None):
    """Return the Package-1 portion of a future effective Support entitlement.

    Store subscriptions are deliberately not consulted yet.  A later package
    adds verified Apple/Google subscription records to this one service rather
    than making screens inspect payment models on their own.
    """

    grant = active_temporary_grant_for(user, at_time=at_time)
    if grant is None:
        return {"state": "not_configured", "source": "none", "ends_at": None}
    return {
        "state": "active",
        "source": "temporary_grant",
        "ends_at": grant.ends_at,
    }


def users_with_active_support_access(user_ids, *, at_time=None):
    """Resolve active Support access for a bounded set without per-user queries."""

    ids = {int(user_id) for user_id in user_ids}
    if not ids:
        return set()
    current_time = at_time or timezone.now()
    return set(
        SupportAccessGrant.objects.filter(
            user_id__in=ids,
            status=SupportAccessGrant.STATUS_ACTIVE,
            starts_at__lte=current_time,
            ends_at__gt=current_time,
        ).values_list("user_id", flat=True)
    )


def expire_elapsed_temporary_access_grants(*, limit=100, at_time=None):
    """Mark elapsed temporary grants expired and notify the affected user.

    Access evaluation already checks ``ends_at``.  This job adds the durable
    audit-friendly state transition and the neutral notification-center event;
    it never prolongs or recreates access.
    """

    current_time = at_time or timezone.now()
    candidate_ids = list(
        SupportAccessGrant.objects.filter(
            status=SupportAccessGrant.STATUS_ACTIVE,
            ends_at__lte=current_time,
        )
        .order_by("ends_at", "id")
        .values_list("id", flat=True)[:limit]
    )
    expired = 0
    for grant_id in candidate_ids:
        with transaction.atomic():
            grant = (
                SupportAccessGrant.objects.select_for_update()
                .select_related("user", "organization")
                .filter(
                    pk=grant_id,
                    status=SupportAccessGrant.STATUS_ACTIVE,
                    ends_at__lte=current_time,
                )
                .first()
            )
            if grant is None:
                continue
            grant.status = SupportAccessGrant.STATUS_EXPIRED
            grant.save(update_fields=["status", "updated_at"])
            enqueue_support_notification(
                organization=grant.organization,
                recipient=grant.user,
                notification_code="support.access_changed",
                target_kind="support_access",
                target_public_id=grant.public_id,
                target_key=f"support:access:{grant.public_id}",
                collapse_key=f"support:access:{grant.user_id}",
                dedupe_key=f"support.access.expired:{grant.public_id}",
            )
            expired += 1
    return expired
