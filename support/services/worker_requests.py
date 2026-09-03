"""Business rules for worker absence, availability and exit requests."""

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from support.models import (
    OrganizationMembership,
    ProjectCrewShift,
    ProjectCrewShiftMember,
    SupportConnection,
    WorkerRequest,
    WorkerRequestDate,
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


def refresh_extra_shift_requests(requests):
    """Synchronize requested dates with the authoritative published schedule.

    A date is assigned only when a real shift is published.  An assigned date
    reopens if that shift is later cancelled, while untouched dates expire
    after the day passes.  The parent request stays open as long as at least
    one date still needs a manager action.
    """

    items = [item for item in requests if item.is_extra_shift]
    if not items:
        return requests
    rows = list(
        WorkerRequestDate.objects.filter(request_id__in=[item.id for item in items])
        .select_related("decided_by")
        .order_by("work_date", "id")
    )
    item_by_id = {item.id: item for item in items}
    rows_by_request = {item.id: [] for item in items}
    for row in rows:
        rows_by_request[row.request_id].append(row)

    connection_ids = {item.connection_id for item in items}
    work_dates = {row.work_date for row in rows}
    published_pairs = set()
    if connection_ids and work_dates:
        published_pairs = set(
            ProjectCrewShiftMember.objects.filter(
                connection_id__in=connection_ids,
                shift__work_date__in=work_dates,
                shift__state=ProjectCrewShift.STATE_PUBLISHED,
            ).values_list("connection_id", "shift__work_date")
        )

    today = timezone.localdate()
    now = timezone.now()
    changed_rows = []
    for row in rows:
        item = item_by_id[row.request_id]
        pair = (item.connection_id, row.work_date)
        desired_status = row.status
        if row.status != WorkerRequestDate.STATUS_CANCELLED:
            if pair in published_pairs:
                desired_status = WorkerRequestDate.STATUS_ASSIGNED
            elif row.status == WorkerRequestDate.STATUS_ASSIGNED:
                desired_status = (
                    WorkerRequestDate.STATUS_EXPIRED
                    if row.work_date < today
                    else WorkerRequestDate.STATUS_REQUESTED
                )
            elif (
                row.status == WorkerRequestDate.STATUS_REQUESTED
                and row.work_date < today
            ):
                desired_status = WorkerRequestDate.STATUS_EXPIRED
        if desired_status == row.status:
            continue
        row.status = desired_status
        row.updated_at = now
        if desired_status in {
            WorkerRequestDate.STATUS_ASSIGNED,
            WorkerRequestDate.STATUS_REQUESTED,
            WorkerRequestDate.STATUS_EXPIRED,
        }:
            row.manager_note = ""
            row.decided_by = None
            row.decided_at = None
        changed_rows.append(row)
    if changed_rows:
        WorkerRequestDate.objects.bulk_update(
            changed_rows,
            (
                "status",
                "manager_note",
                "decided_by",
                "decided_at",
                "updated_at",
            ),
        )

    changed_items = []
    for item in items:
        item_rows = rows_by_request[item.id]
        item.extra_shift_dates_for_payload = item_rows
        if item.status == WorkerRequest.STATUS_CANCELLED or not item_rows:
            continue
        statuses = {row.status for row in item_rows}
        if WorkerRequestDate.STATUS_REQUESTED in statuses:
            desired_status = WorkerRequest.STATUS_SUBMITTED
        elif WorkerRequestDate.STATUS_ASSIGNED in statuses:
            desired_status = WorkerRequest.STATUS_APPROVED
        else:
            desired_status = WorkerRequest.STATUS_DECLINED
        if desired_status != item.status:
            item.status = desired_status
            item.updated_at = now
            changed_items.append(item)
    if changed_items:
        WorkerRequest.objects.bulk_update(changed_items, ("status", "updated_at"))
    return requests


def submit_worker_request(
    *,
    worker,
    connection,
    request_type,
    starts_on,
    ends_on,
    requested_dates=None,
    worker_note="",
):
    """Submit a request; it never makes an employment or schedule decision."""

    with transaction.atomic():
        # Lock only the connection row. ``assigned_manager`` is nullable, and
        # joining it here makes PostgreSQL reject ``FOR UPDATE`` because the
        # nullable side of an outer join cannot be locked. Related objects are
        # loaded lazily below while this transaction remains open.
        connection = SupportConnection.objects.select_for_update().get(
            pk=connection.pk
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
        elif request_type == WorkerRequest.TYPE_EXTRA_SHIFT:
            requested_dates = sorted(set(requested_dates or ()))
            if not requested_dates:
                raise ValidationError(
                    {"requested_dates": "extra_shift_dates_required"}
                )
            if requested_dates[0] < today:
                raise ValidationError(
                    {"requested_dates": "extra_shift_dates_cannot_be_in_past"}
                )
            published_dates = list(
                ProjectCrewShiftMember.objects.filter(
                    connection=connection,
                    shift__work_date__in=requested_dates,
                    shift__state=ProjectCrewShift.STATE_PUBLISHED,
                )
                .order_by("shift__work_date")
                .values_list("shift__work_date", flat=True)
            )
            if published_dates:
                raise ValidationError(
                    {
                        "requested_dates": {
                            "code": "extra_shift_dates_already_scheduled",
                            "dates": [item.isoformat() for item in published_dates],
                        }
                    }
                )
            duplicate_dates = list(
                WorkerRequestDate.objects.filter(
                    request__connection=connection,
                    request__status__in=(
                        WorkerRequest.STATUS_SUBMITTED,
                        WorkerRequest.STATUS_NEEDS_CLARIFICATION,
                    ),
                    work_date__in=requested_dates,
                    status=WorkerRequestDate.STATUS_REQUESTED,
                )
                .order_by("work_date")
                .values_list("work_date", flat=True)
            )
            if duplicate_dates:
                raise ValidationError(
                    {
                        "requested_dates": {
                            "code": "extra_shift_dates_already_requested",
                            "dates": [item.isoformat() for item in duplicate_dates],
                        }
                    }
                )
            starts_on = requested_dates[0]
            ends_on = requested_dates[-1]
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
        if item.is_extra_shift:
            WorkerRequestDate.objects.bulk_create(
                [
                    WorkerRequestDate(request=item, work_date=work_date)
                    for work_date in requested_dates
                ]
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
                "requested_date_count": len(requested_dates or ()),
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
        elif item.is_extra_shift:
            recipient = _urgent_recipient(connection=connection)
            if recipient is not None:
                enqueue_support_notification(
                    organization=connection.organization,
                    recipient=recipient,
                    notification_code="worker_request.extra_shift_submitted",
                    target_kind="worker_request",
                    target_public_id=item.public_id,
                    target_key=f"support:worker-request:{item.public_id}",
                    collapse_key=f"support:extra-shift-request:{connection.public_id}",
                    dedupe_key=f"worker_request.extra_shift_submitted:{item.public_id}",
                )
    return item


def decide_worker_request(*, actor, request, action, manager_note):
    """Let scoped staff approve or decline without altering a shift."""

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
        if item.is_extra_shift:
            if action != "decline":
                raise ValidationError(
                    {"action": "extra_shift_request_requires_date_actions"}
                )
            normalized_note = (manager_note or "").strip()
            if not normalized_note:
                raise ValidationError(
                    {"manager_note": "manager_note_required_for_request_decision"}
                )
            now = timezone.now()
            pending_dates = WorkerRequestDate.objects.select_for_update().filter(
                request=item,
                status=WorkerRequestDate.STATUS_REQUESTED,
            )
            if not pending_dates.exists():
                raise ValidationError(
                    {"request": "extra_shift_request_has_no_pending_dates"}
                )
            pending_dates.update(
                status=WorkerRequestDate.STATUS_DECLINED,
                manager_note=normalized_note,
                decided_by=actor,
                decided_at=now,
                updated_at=now,
            )
            refresh_extra_shift_requests([item])
            item.manager_note = normalized_note
            item.reviewed_at = now
            item.reviewed_by = actor
            item.last_changed_by = actor
            item.save(
                update_fields=(
                    "manager_note",
                    "reviewed_at",
                    "reviewed_by",
                    "last_changed_by",
                    "updated_at",
                )
            )
            _record_event(
                request=item,
                action=WorkerRequestEvent.ACTION_DECLINED,
                actor=actor,
            )
            record_audit_event(
                organization=organization,
                actor=actor,
                action="worker_request.extra_shift_declined",
                target=item,
                details={"connection": str(item.connection.public_id)},
            )
            enqueue_support_notification(
                organization=organization,
                recipient=item.connection.candidate,
                notification_code="worker_request.extra_shift_changed",
                target_kind="worker_request",
                target_public_id=item.public_id,
                target_key=f"support:worker-request:{item.public_id}",
                collapse_key=f"support:extra-shift-request:{item.connection.public_id}",
                dedupe_key=f"worker_request.extra_shift_declined:{item.public_id}",
            )
            return item
        mapping = {
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
        if action == "decline" and not (manager_note or "").strip():
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


def decline_extra_shift_date(*, actor, request, request_date, manager_note):
    """Decline one offered date without closing the remaining dates."""

    organization = request.organization
    require_permission(
        user=actor,
        organization=organization,
        permission_code=REQUEST_DECIDE,
    )
    normalized_note = (manager_note or "").strip()
    if not normalized_note:
        raise ValidationError(
            {"manager_note": "manager_note_required_for_request_decision"}
        )
    with transaction.atomic():
        item = (
            WorkerRequest.objects.select_for_update()
            .select_related("organization", "connection__candidate")
            .get(pk=request.pk)
        )
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=item.connection,
        )
        if not item.is_extra_shift:
            raise ValidationError({"request": "worker_request_not_extra_shift"})
        row = WorkerRequestDate.objects.select_for_update().get(
            pk=request_date.pk,
            request=item,
        )
        if row.status != WorkerRequestDate.STATUS_REQUESTED:
            raise ValidationError(
                {"request_date": "extra_shift_date_not_open_for_review"}
            )
        now = timezone.now()
        row.status = WorkerRequestDate.STATUS_DECLINED
        row.manager_note = normalized_note
        row.decided_by = actor
        row.decided_at = now
        row.save(
            update_fields=(
                "status",
                "manager_note",
                "decided_by",
                "decided_at",
                "updated_at",
            )
        )
        item.last_changed_by = actor
        item.save(update_fields=("last_changed_by", "updated_at"))
        refresh_extra_shift_requests([item])
        _record_event(
            request=item,
            action=WorkerRequestEvent.ACTION_EXTRA_DATE_DECLINED,
            actor=actor,
        )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="worker_request.extra_shift_date_declined",
            target=item,
            details={
                "connection": str(item.connection.public_id),
                "request_date": str(row.public_id),
            },
        )
        enqueue_support_notification(
            organization=organization,
            recipient=item.connection.candidate,
            notification_code="worker_request.extra_shift_changed",
            target_kind="worker_request",
            target_public_id=item.public_id,
            target_key=f"support:worker-request:{item.public_id}",
            collapse_key=f"support:extra-shift-request:{item.connection.public_id}",
            dedupe_key=(
                f"worker_request.extra_shift_date_declined:"
                f"{item.public_id}:{row.public_id}"
            ),
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
        if item.is_extra_shift:
            WorkerRequestDate.objects.filter(
                request=item,
                status=WorkerRequestDate.STATUS_REQUESTED,
            ).update(
                status=WorkerRequestDate.STATUS_CANCELLED,
                decided_at=now,
                updated_at=now,
            )
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
