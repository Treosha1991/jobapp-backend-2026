"""Business rules for worker absence, vacation and exit requests."""

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from support.models import (
    OrganizationMembership,
    SupportConnection,
    WorkerRequest,
    WorkerRequestEvent,
)
from support.permission_codes import REQUEST_DECIDE
from support.permissions import require_permission, require_worker_connection_access

from .audit import record_audit_event
from .notifications import enqueue_support_notification


_OPERATIONAL_STAGES = frozenset(
    {
        SupportConnection.STAGE_COORDINATOR,
        SupportConnection.STAGE_ACTIVE_WORKER,
    }
)
_WORKER_CANCELLABLE_STATUSES = frozenset(
    {
        WorkerRequest.STATUS_DRAFT,
        WorkerRequest.STATUS_SUBMITTED,
        WorkerRequest.STATUS_NEEDS_CLARIFICATION,
    }
)


def _require_operational_connection(*, connection, organization):
    if connection.organization_id != organization.id or connection.is_archived:
        raise ValidationError({"connection": "connection_not_in_organization"})
    if connection.stage not in _OPERATIONAL_STAGES:
        raise ValidationError({"connection": "connection_not_in_working_stage"})


def _record_event(*, request, action, actor):
    return WorkerRequestEvent.objects.create(
        request=request,
        action=action,
        status_after=request.status,
        actor=actor,
    )


def _urgent_recipient(*, connection):
    """Use the assigned manager, otherwise the single active organization owner."""

    membership = connection.assigned_manager
    if (
        membership is not None
        and membership.organization_id == connection.organization_id
        and membership.state == OrganizationMembership.STATE_ACTIVE
    ):
        return membership.user
    owner = (
        OrganizationMembership.objects.filter(
            organization=connection.organization,
            is_owner=True,
            state=OrganizationMembership.STATE_ACTIVE,
        )
        .select_related("user")
        .first()
    )
    return owner.user if owner else None


def submit_worker_request(
    *,
    worker,
    connection,
    request_type,
    starts_on,
    ends_on,
    worker_note="",
):
    """Submit a request; it never makes an employment or schedule decision."""

    with transaction.atomic():
        connection = (
            SupportConnection.objects.select_for_update()
            .select_related("organization", "assigned_manager__user")
            .get(pk=connection.pk)
        )
        if connection.candidate_id != worker.id:
            raise PermissionDenied("support_worker_request_not_owned")
        _require_operational_connection(
            connection=connection,
            organization=connection.organization,
        )
        today = timezone.localdate()
        if request_type == WorkerRequest.TYPE_UNABLE_TODAY:
            starts_on = today
            ends_on = today
        elif starts_on < today:
            raise ValidationError({"starts_on": "worker_request_cannot_start_in_past"})
        if ends_on < starts_on:
            raise ValidationError({"ends_on": "worker_request_end_before_start"})
        item = WorkerRequest.objects.create(
            organization=connection.organization,
            connection=connection,
            request_type=request_type,
            status=WorkerRequest.STATUS_SUBMITTED,
            starts_on=starts_on,
            ends_on=ends_on,
            worker_note=(worker_note or "").strip(),
            submitted_at=timezone.now(),
            last_changed_by=worker,
        )
        _record_event(
            request=item,
            action=WorkerRequestEvent.ACTION_SUBMITTED,
            actor=worker,
        )
        record_audit_event(
            organization=connection.organization,
            actor=worker,
            action="worker_request.submitted",
            target=item,
            details={
                "connection": str(connection.public_id),
                "request_type": item.request_type,
                "urgent": item.is_urgent,
            },
        )
        if item.is_urgent:
            recipient = _urgent_recipient(connection=connection)
            if recipient is not None:
                enqueue_support_notification(
                    organization=connection.organization,
                    recipient=recipient,
                    notification_code="worker_request.urgent_submitted",
                    target_kind="worker_request",
                    target_public_id=item.public_id,
                    target_key=f"support:worker-request:{item.public_id}",
                    collapse_key=f"support:urgent-request:{connection.public_id}",
                    dedupe_key=f"worker_request.urgent_submitted:{item.public_id}",
                )
    return item


def decide_worker_request(*, actor, request, action, manager_note):
    """Let scoped staff clarify, approve or decline without altering a shift."""

    organization = request.organization
    require_permission(user=actor, organization=organization, permission_code=REQUEST_DECIDE)
    with transaction.atomic():
        item = (
            WorkerRequest.objects.select_for_update()
            .select_related("organization", "connection")
            .get(pk=request.pk)
        )
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=item.connection,
        )
        if item.status not in {
            WorkerRequest.STATUS_SUBMITTED,
            WorkerRequest.STATUS_NEEDS_CLARIFICATION,
        }:
            raise ValidationError({"request": "worker_request_not_open_for_review"})
        mapping = {
            "clarify": (
                WorkerRequest.STATUS_NEEDS_CLARIFICATION,
                WorkerRequestEvent.ACTION_CLARIFICATION_REQUESTED,
                "worker_request.clarification_requested",
            ),
            "approve": (
                WorkerRequest.STATUS_APPROVED,
                WorkerRequestEvent.ACTION_APPROVED,
                "worker_request.approved",
            ),
            "decline": (
                WorkerRequest.STATUS_DECLINED,
                WorkerRequestEvent.ACTION_DECLINED,
                "worker_request.declined",
            ),
        }
        try:
            next_status, event_action, audit_action = mapping[action]
        except KeyError as error:
            raise ValidationError({"action": "unsupported_worker_request_decision"}) from error
        if action in {"clarify", "decline"} and not (manager_note or "").strip():
            raise ValidationError({"manager_note": "manager_note_required_for_request_decision"})
        now = timezone.now()
        item.status = next_status
        item.manager_note = (manager_note or "").strip()
        item.reviewed_at = now
        item.reviewed_by = actor
        item.last_changed_by = actor
        item.save(
            update_fields=[
                "status",
                "manager_note",
                "reviewed_at",
                "reviewed_by",
                "last_changed_by",
                "updated_at",
            ]
        )
        _record_event(request=item, action=event_action, actor=actor)
        record_audit_event(
            organization=organization,
            actor=actor,
            action=audit_action,
            target=item,
            details={
                "connection": str(item.connection.public_id),
                "request_type": item.request_type,
            },
        )
    return item


def cancel_worker_request(*, worker, request):
    """Workers can cancel an unresolved request; approval requires a new request."""

    with transaction.atomic():
        item = (
            WorkerRequest.objects.select_for_update()
            .select_related("organization", "connection")
            .get(pk=request.pk)
        )
        if item.connection.candidate_id != worker.id:
            raise PermissionDenied("support_worker_request_not_owned")
        if item.status not in _WORKER_CANCELLABLE_STATUSES:
            raise ValidationError({"request": "worker_request_not_open_for_cancellation"})
        now = timezone.now()
        item.status = WorkerRequest.STATUS_CANCELLED
        item.cancelled_at = now
        item.last_changed_by = worker
        item.save(update_fields=["status", "cancelled_at", "last_changed_by", "updated_at"])
        _record_event(
            request=item,
            action=WorkerRequestEvent.ACTION_CANCELLED,
            actor=worker,
        )
        record_audit_event(
            organization=item.organization,
            actor=worker,
            action="worker_request.cancelled",
            target=item,
            details={
                "connection": str(item.connection.public_id),
                "request_type": item.request_type,
            },
        )
    return item
