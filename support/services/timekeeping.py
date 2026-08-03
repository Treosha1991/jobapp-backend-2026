"""Business rules for scheduled shifts and factual work-time entries."""

from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from support.models import (
    CalendarMarkBatch,
    CalendarMarkBatchItem,
    CalendarMarkTemplate,
    ScheduledShiftBatch,
    ScheduledWorkShift,
    ShiftTemplate,
    SupportConnection,
    WorkTimeEntry,
    WorkTimeEntryRevision,
    WorkerRequest,
)
from support.permission_codes import SCHEDULE_MANAGE, TIME_EDIT, TIME_REVIEW
from support.permissions import require_permission, require_worker_connection_access

from .audit import record_audit_event
from .notifications import enqueue_support_notification


_WORKING_STAGES = frozenset(
    {
        SupportConnection.STAGE_COORDINATOR,
        SupportConnection.STAGE_ACTIVE_WORKER,
    }
)
_MAX_SHIFT_MINUTES = 24 * 60
_MAX_BATCH_DAYS = 31
_MAX_BATCH_WORKERS = 50
_MAX_BATCH_SHIFTS = 300
_MAX_CALENDAR_MARK_BATCH_ITEMS = 100
_CALENDAR_MARK_REQUEST_TYPES = frozenset(
    {
        WorkerRequest.TYPE_DAY_OFF,
        WorkerRequest.TYPE_VACATION,
        WorkerRequest.TYPE_UNPAID_ABSENCE,
        WorkerRequest.TYPE_UNABLE_TODAY,
    }
)


def _require_operational_connection(*, connection, organization):
    if connection.organization_id != organization.id or connection.is_archived:
        raise ValidationError({"connection": "connection_not_in_organization"})
    if connection.stage not in _WORKING_STAGES:
        raise ValidationError({"connection": "connection_not_in_working_stage"})


def _worked_minutes(*, started_at, ended_at, break_minutes, reject_future=False):
    if timezone.is_naive(started_at) or timezone.is_naive(ended_at):
        raise ValidationError({"time": "timezone_aware_time_required"})
    if started_at.second or started_at.microsecond or ended_at.second or ended_at.microsecond:
        raise ValidationError({"time": "minute_precision_required"})
    if ended_at <= started_at:
        raise ValidationError({"ended_at": "end_must_be_after_start"})
    if reject_future and ended_at > timezone.now():
        raise ValidationError({"ended_at": "actual_end_cannot_be_in_future"})
    total_minutes = int((ended_at - started_at).total_seconds() // 60)
    if total_minutes > _MAX_SHIFT_MINUTES:
        raise ValidationError({"time": "shift_cannot_exceed_24_hours"})
    if break_minutes < 0 or break_minutes >= total_minutes:
        raise ValidationError({"break_minutes": "break_must_be_less_than_shift"})
    return total_minutes - break_minutes


def _record_revision(*, entry, action, actor, note=""):
    return WorkTimeEntryRevision.objects.create(
        entry=entry,
        revision=entry.revision,
        action=action,
        status_after=entry.status,
        started_at=entry.started_at,
        ended_at=entry.ended_at,
        break_minutes=entry.break_minutes,
        worked_minutes=entry.worked_minutes,
        note=(note or "").strip(),
        actor=actor,
    )


def _published_shift_for_day(*, connection, work_date):
    return (
        ScheduledWorkShift.objects.filter(
            connection=connection,
            work_date=work_date,
            state=ScheduledWorkShift.STATE_PUBLISHED,
        )
        .select_related("work_assignment")
        .first()
    )


def _template_shift_datetimes(*, work_date, template):
    """Build a timezone-aware shift from a reusable local-time template."""

    starts_at = timezone.make_aware(
        datetime.combine(work_date, template.starts_at_time),
        timezone.get_current_timezone(),
    )
    ends_at = timezone.make_aware(
        datetime.combine(work_date, template.ends_at_time),
        timezone.get_current_timezone(),
    )
    if ends_at <= starts_at:
        ends_at += timedelta(days=1)
    return starts_at, ends_at


def _batch_dates(*, starts_on, ends_on, weekdays):
    normalized_weekdays = sorted({int(item) for item in weekdays})
    if not normalized_weekdays or any(item < 0 or item > 6 for item in normalized_weekdays):
        raise ValidationError({"weekdays": "weekdays_must_contain_values_from_zero_to_six"})
    if ends_on < starts_on:
        raise ValidationError({"ends_on": "period_end_must_not_be_before_start"})
    day_count = (ends_on - starts_on).days + 1
    if day_count > _MAX_BATCH_DAYS:
        raise ValidationError({"ends_on": "scheduled_shift_batch_period_too_long"})
    dates = []
    current = starts_on
    while current <= ends_on:
        if current.weekday() in normalized_weekdays:
            dates.append(current)
        current += timedelta(days=1)
    if not dates:
        raise ValidationError({"weekdays": "scheduled_shift_batch_has_no_matching_dates"})
    return normalized_weekdays, dates


def create_shift_template(
    *,
    actor,
    organization,
    name,
    starts_at_time,
    ends_at_time,
    break_minutes,
    worker_label="",
):
    """Create a reusable planned-time pattern after the same duration checks."""

    require_permission(
        user=actor,
        organization=organization,
        permission_code=SCHEDULE_MANAGE,
    )
    normalized_name = (name or "").strip()
    if not normalized_name:
        raise ValidationError({"name": "shift_template_name_required"})
    with transaction.atomic():
        if ShiftTemplate.objects.select_for_update().filter(
            organization=organization,
            name=normalized_name,
        ).exists():
            raise ValidationError({"name": "shift_template_name_already_exists"})
        template = ShiftTemplate(
            organization=organization,
            name=normalized_name,
            starts_at_time=starts_at_time,
            ends_at_time=ends_at_time,
            break_minutes=break_minutes,
            worker_label=(worker_label or "").strip(),
            created_by=actor,
        )
        starts_at, ends_at = _template_shift_datetimes(
            work_date=timezone.localdate(),
            template=template,
        )
        _worked_minutes(
            started_at=starts_at,
            ended_at=ends_at,
            break_minutes=break_minutes,
        )
        template.save()
        record_audit_event(
            organization=organization,
            actor=actor,
            action="schedule.template_created",
            target=template,
            details={"name": template.name},
        )
    return template


def create_calendar_mark_template(*, actor, organization, name, request_type):
    """Create an internal preset for a group of already approved requests."""

    require_permission(
        user=actor,
        organization=organization,
        permission_code=SCHEDULE_MANAGE,
    )
    normalized_name = (name or "").strip()
    if not normalized_name:
        raise ValidationError({"name": "calendar_mark_template_name_required"})
    if request_type not in _CALENDAR_MARK_REQUEST_TYPES:
        raise ValidationError({"request_type": "calendar_mark_template_type_not_available"})
    with transaction.atomic():
        if CalendarMarkTemplate.objects.select_for_update().filter(
            organization=organization,
            name=normalized_name,
        ).exists():
            raise ValidationError({"name": "calendar_mark_template_name_already_exists"})
        template = CalendarMarkTemplate.objects.create(
            organization=organization,
            name=normalized_name,
            request_type=request_type,
            created_by=actor,
        )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="calendar_mark.template_created",
            target=template,
            details={"request_type": template.request_type},
        )
    return template


def _validate_calendar_mark_requests(*, actor, organization, template, requests):
    request_ids = {item.pk for item in requests}
    if not request_ids:
        raise ValidationError({"worker_request_ids": "calendar_mark_batch_requests_required"})
    if len(request_ids) > _MAX_CALENDAR_MARK_BATCH_ITEMS:
        raise ValidationError({"worker_request_ids": "calendar_mark_batch_too_many_requests"})
    locked_requests = list(
        WorkerRequest.objects.select_for_update()
        .filter(pk__in=request_ids)
        .select_related("connection__candidate")
        .order_by("pk")
    )
    if len(locked_requests) != len(request_ids):
        raise ValidationError({"worker_request_ids": "calendar_mark_request_not_available"})
    for item in locked_requests:
        if (
            item.organization_id != organization.id
            or item.status != WorkerRequest.STATUS_APPROVED
            or item.request_type != template.request_type
        ):
            raise ValidationError({"worker_request_ids": "calendar_mark_request_not_approved"})
        _require_operational_connection(
            connection=item.connection,
            organization=organization,
        )
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=item.connection,
        )
    return locked_requests


def create_calendar_mark_batch(*, actor, organization, template, requests):
    """Prepare a reversible internal grouping of approved absence requests."""

    require_permission(
        user=actor,
        organization=organization,
        permission_code=SCHEDULE_MANAGE,
    )
    with transaction.atomic():
        template = CalendarMarkTemplate.objects.select_for_update().get(pk=template.pk)
        if template.organization_id != organization.id or not template.is_active:
            raise ValidationError({"template": "calendar_mark_template_not_available"})
        locked_requests = _validate_calendar_mark_requests(
            actor=actor,
            organization=organization,
            template=template,
            requests=requests,
        )
        active_items = CalendarMarkBatchItem.objects.select_for_update().filter(
            request_id__in={item.id for item in locked_requests},
            batch__state__in=(
                CalendarMarkBatch.STATE_DRAFT,
                CalendarMarkBatch.STATE_PUBLISHED,
            ),
        )
        if active_items.exists():
            raise ValidationError({"worker_request_ids": "calendar_mark_request_already_batched"})
        batch = CalendarMarkBatch.objects.create(
            organization=organization,
            template=template,
            created_by=actor,
        )
        CalendarMarkBatchItem.objects.bulk_create(
            [CalendarMarkBatchItem(batch=batch, request=item) for item in locked_requests]
        )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="calendar_mark.batch_drafted",
            target=batch,
            details={
                "template": str(template.public_id),
                "request_count": len(locked_requests),
            },
        )
    return batch


def publish_calendar_mark_batch(*, actor, batch):
    """Publish a group only if every request is still approved and in scope."""

    organization = batch.organization
    require_permission(
        user=actor,
        organization=organization,
        permission_code=SCHEDULE_MANAGE,
    )
    with transaction.atomic():
        batch = CalendarMarkBatch.objects.select_for_update().select_related("template").get(
            pk=batch.pk
        )
        if batch.state != CalendarMarkBatch.STATE_DRAFT:
            raise ValidationError({"batch": "calendar_mark_batch_not_draft"})
        if batch.template_id is None or not batch.template.is_active:
            raise ValidationError({"template": "calendar_mark_template_not_available"})
        items = list(
            CalendarMarkBatchItem.objects.select_for_update()
            .filter(batch=batch)
            .select_related("request__connection__candidate")
            .order_by("request__starts_on", "request__ends_on", "id")
        )
        if not items:
            raise ValidationError({"batch": "calendar_mark_batch_empty"})
        locked_requests = _validate_calendar_mark_requests(
            actor=actor,
            organization=organization,
            template=batch.template,
            requests=[item.request for item in items],
        )
        other_active_items = CalendarMarkBatchItem.objects.select_for_update().filter(
            request_id__in={item.id for item in locked_requests},
            batch__state__in=(
                CalendarMarkBatch.STATE_DRAFT,
                CalendarMarkBatch.STATE_PUBLISHED,
            ),
        ).exclude(batch=batch)
        if other_active_items.exists():
            raise ValidationError({"batch": "calendar_mark_batch_conflicts_with_current_batch"})
        now = timezone.now()
        batch.state = CalendarMarkBatch.STATE_PUBLISHED
        batch.published_by = actor
        batch.published_at = now
        batch.save(update_fields=["state", "published_by", "published_at", "updated_at"])
        record_audit_event(
            organization=organization,
            actor=actor,
            action="calendar_mark.batch_published",
            target=batch,
            details={"request_count": len(items)},
        )
    return batch


def cancel_calendar_mark_batch(*, actor, batch):
    """Cancel only the grouping; it never revokes the individual approval."""

    organization = batch.organization
    require_permission(
        user=actor,
        organization=organization,
        permission_code=SCHEDULE_MANAGE,
    )
    with transaction.atomic():
        batch = CalendarMarkBatch.objects.select_for_update().get(pk=batch.pk)
        if batch.state == CalendarMarkBatch.STATE_CANCELLED:
            raise ValidationError({"batch": "calendar_mark_batch_already_cancelled"})
        items = list(
            CalendarMarkBatchItem.objects.select_related("request__connection").filter(batch=batch)
        )
        for item in items:
            require_worker_connection_access(
                user=actor,
                organization=organization,
                connection=item.request.connection,
            )
        batch.state = CalendarMarkBatch.STATE_CANCELLED
        batch.cancelled_at = timezone.now()
        batch.save(update_fields=["state", "cancelled_at", "updated_at"])
        record_audit_event(
            organization=organization,
            actor=actor,
            action="calendar_mark.batch_cancelled",
            target=batch,
            details={"request_count": len(items)},
        )
    return batch


def create_scheduled_shift_batch(
    *,
    actor,
    organization,
    template,
    connections,
    starts_on,
    ends_on,
    weekdays,
):
    """Create all selected planned shifts as one unpublished, reversible batch."""

    require_permission(
        user=actor,
        organization=organization,
        permission_code=SCHEDULE_MANAGE,
    )
    connection_ids = {item.pk for item in connections}
    if not connection_ids:
        raise ValidationError({"connection_ids": "scheduled_shift_batch_workers_required"})
    if len(connection_ids) > _MAX_BATCH_WORKERS:
        raise ValidationError({"connection_ids": "scheduled_shift_batch_too_many_workers"})
    normalized_weekdays, dates = _batch_dates(
        starts_on=starts_on,
        ends_on=ends_on,
        weekdays=weekdays,
    )
    if len(connection_ids) * len(dates) > _MAX_BATCH_SHIFTS:
        raise ValidationError({"connection_ids": "scheduled_shift_batch_too_many_shifts"})
    with transaction.atomic():
        template = ShiftTemplate.objects.select_for_update().get(pk=template.pk)
        if template.organization_id != organization.id or not template.is_active:
            raise ValidationError({"template": "shift_template_not_available"})
        locked_connections = list(
            SupportConnection.objects.select_for_update()
            .filter(pk__in=connection_ids)
            .select_related("candidate")
            .order_by("pk")
        )
        if len(locked_connections) != len(connection_ids):
            raise ValidationError({"connection_ids": "connection_not_in_organization"})
        for connection in locked_connections:
            _require_operational_connection(connection=connection, organization=organization)
            require_worker_connection_access(
                user=actor,
                organization=organization,
                connection=connection,
            )
        starts_at, ends_at = _template_shift_datetimes(
            work_date=dates[0],
            template=template,
        )
        _worked_minutes(
            started_at=starts_at,
            ended_at=ends_at,
            break_minutes=template.break_minutes,
        )
        occupied = ScheduledWorkShift.objects.select_for_update().filter(
            connection__in=locked_connections,
            work_date__in=dates,
            state__in=(
                ScheduledWorkShift.STATE_DRAFT,
                ScheduledWorkShift.STATE_PUBLISHED,
            ),
        )
        if occupied.exists():
            raise ValidationError({"schedule": "scheduled_shift_batch_conflicts_with_current_shift"})
        batch = ScheduledShiftBatch.objects.create(
            organization=organization,
            template=template,
            starts_on=starts_on,
            ends_on=ends_on,
            weekdays=normalized_weekdays,
            created_by=actor,
        )
        shifts = []
        for connection in locked_connections:
            for work_date in dates:
                shift_starts_at, shift_ends_at = _template_shift_datetimes(
                    work_date=work_date,
                    template=template,
                )
                shifts.append(
                    ScheduledWorkShift(
                        organization=organization,
                        connection=connection,
                        batch=batch,
                        work_date=work_date,
                        starts_at=shift_starts_at,
                        ends_at=shift_ends_at,
                        break_minutes=template.break_minutes,
                        worker_label=template.worker_label,
                        created_by=actor,
                    )
                )
        ScheduledWorkShift.objects.bulk_create(shifts)
        record_audit_event(
            organization=organization,
            actor=actor,
            action="schedule.batch_drafted",
            target=batch,
            details={
                "template": str(template.public_id),
                "worker_count": len(locked_connections),
                "shift_count": len(shifts),
                "starts_on": starts_on.isoformat(),
                "ends_on": ends_on.isoformat(),
                "weekdays": normalized_weekdays,
            },
        )
    return batch


def publish_scheduled_shift_batch(*, actor, batch):
    """Publish every shift together, or publish none if any recheck fails."""

    organization = batch.organization
    require_permission(
        user=actor,
        organization=organization,
        permission_code=SCHEDULE_MANAGE,
    )
    with transaction.atomic():
        batch = ScheduledShiftBatch.objects.select_for_update().get(pk=batch.pk)
        if batch.state != ScheduledShiftBatch.STATE_DRAFT:
            raise ValidationError({"batch": "scheduled_shift_batch_not_draft"})
        shifts = list(
            ScheduledWorkShift.objects.select_for_update()
            .filter(batch=batch)
            .select_related("connection__candidate")
            .order_by("connection_id", "work_date", "id")
        )
        if not shifts:
            raise ValidationError({"batch": "scheduled_shift_batch_empty"})
        for shift in shifts:
            _require_operational_connection(
                connection=shift.connection,
                organization=organization,
            )
            require_worker_connection_access(
                user=actor,
                organization=organization,
                connection=shift.connection,
            )
            if shift.state != ScheduledWorkShift.STATE_DRAFT:
                raise ValidationError({"batch": "scheduled_shift_batch_contains_non_draft"})
        occupied = ScheduledWorkShift.objects.select_for_update().filter(
            connection_id__in={item.connection_id for item in shifts},
            work_date__in={item.work_date for item in shifts},
            state__in=(
                ScheduledWorkShift.STATE_DRAFT,
                ScheduledWorkShift.STATE_PUBLISHED,
            ),
        ).exclude(batch=batch)
        if occupied.exists():
            raise ValidationError({"batch": "scheduled_shift_batch_conflicts_with_current_shift"})
        now = timezone.now()
        for shift in shifts:
            shift.state = ScheduledWorkShift.STATE_PUBLISHED
            shift.published_by = actor
            shift.published_at = now
            shift.save(
                update_fields=["state", "published_by", "published_at", "updated_at"]
            )
            enqueue_support_notification(
                organization=organization,
                recipient=shift.connection.candidate,
                notification_code="schedule.shift_published",
                target_kind="scheduled_shift",
                target_public_id=shift.public_id,
                target_key=f"support:scheduled-shift:{shift.public_id}",
                collapse_key=(
                    f"support:schedule:{shift.connection.public_id}:"
                    f"{shift.work_date.isoformat()}"
                ),
                dedupe_key=(
                    f"schedule.shift.published:{shift.public_id}:{now.isoformat()}"
                ),
            )
        batch.state = ScheduledShiftBatch.STATE_PUBLISHED
        batch.published_by = actor
        batch.published_at = now
        batch.save(
            update_fields=["state", "published_by", "published_at", "updated_at"]
        )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="schedule.batch_published",
            target=batch,
            details={"shift_count": len(shifts)},
        )
    return batch


def cancel_scheduled_shift_batch(*, actor, batch):
    """Cancel a complete batch and notify only workers with published shifts."""

    organization = batch.organization
    require_permission(
        user=actor,
        organization=organization,
        permission_code=SCHEDULE_MANAGE,
    )
    with transaction.atomic():
        batch = ScheduledShiftBatch.objects.select_for_update().get(pk=batch.pk)
        if batch.state == ScheduledShiftBatch.STATE_CANCELLED:
            raise ValidationError({"batch": "scheduled_shift_batch_already_cancelled"})
        shifts = list(
            ScheduledWorkShift.objects.select_for_update()
            .filter(batch=batch)
            .select_related("connection__candidate")
            .order_by("connection_id", "work_date", "id")
        )
        for shift in shifts:
            require_worker_connection_access(
                user=actor,
                organization=organization,
                connection=shift.connection,
            )
        now = timezone.now()
        for shift in shifts:
            was_published = shift.state == ScheduledWorkShift.STATE_PUBLISHED
            if shift.state != ScheduledWorkShift.STATE_CANCELLED:
                shift.state = ScheduledWorkShift.STATE_CANCELLED
                shift.cancelled_at = now
                shift.save(update_fields=["state", "cancelled_at", "updated_at"])
            if was_published:
                enqueue_support_notification(
                    organization=organization,
                    recipient=shift.connection.candidate,
                    notification_code="schedule.shift_published",
                    target_kind="scheduled_shift",
                    target_public_id=shift.public_id,
                    target_key=f"support:scheduled-shift:{shift.public_id}",
                    collapse_key=(
                        f"support:schedule:{shift.connection.public_id}:"
                        f"{shift.work_date.isoformat()}"
                    ),
                    dedupe_key=(
                        f"schedule.shift.cancelled:{shift.public_id}:{now.isoformat()}"
                    ),
                )
        batch.state = ScheduledShiftBatch.STATE_CANCELLED
        batch.cancelled_at = now
        batch.save(update_fields=["state", "cancelled_at", "updated_at"])
        record_audit_event(
            organization=organization,
            actor=actor,
            action="schedule.batch_cancelled",
            target=batch,
            details={"shift_count": len(shifts)},
        )
    return batch


def create_scheduled_shift(
    *,
    actor,
    organization,
    connection,
    work_date,
    starts_at,
    ends_at,
    break_minutes,
    worker_label="",
    work_assignment=None,
):
    """Create a staff-only draft.  The worker sees it only after publication."""

    require_permission(
        user=actor,
        organization=organization,
        permission_code=SCHEDULE_MANAGE,
    )
    with transaction.atomic():
        connection = SupportConnection.objects.select_for_update().get(pk=connection.pk)
        _require_operational_connection(connection=connection, organization=organization)
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=connection,
        )
        if work_assignment is not None:
            if (
                work_assignment.organization_id != organization.id
                or work_assignment.connection_id != connection.id
                or work_assignment.state != work_assignment.STATE_PUBLISHED
            ):
                raise ValidationError({"work_assignment": "published_assignment_for_worker_required"})
        _worked_minutes(
            started_at=starts_at,
            ended_at=ends_at,
            break_minutes=break_minutes,
        )
        current = ScheduledWorkShift.objects.select_for_update().filter(
            connection=connection,
            work_date=work_date,
            state__in=(
                ScheduledWorkShift.STATE_DRAFT,
                ScheduledWorkShift.STATE_PUBLISHED,
            ),
        )
        if current.exists():
            raise ValidationError({"work_date": "current_scheduled_shift_already_exists"})
        shift = ScheduledWorkShift.objects.create(
            organization=organization,
            connection=connection,
            work_assignment=work_assignment,
            work_date=work_date,
            starts_at=starts_at,
            ends_at=ends_at,
            break_minutes=break_minutes,
            worker_label=(worker_label or "").strip(),
            created_by=actor,
        )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="schedule.shift_drafted",
            target=shift,
            details={
                "connection": str(connection.public_id),
                "work_date": work_date.isoformat(),
            },
        )
    return shift


def publish_scheduled_shift(*, actor, shift):
    organization = shift.organization
    require_permission(
        user=actor,
        organization=organization,
        permission_code=SCHEDULE_MANAGE,
    )
    with transaction.atomic():
        shift = (
            ScheduledWorkShift.objects.select_for_update()
            .select_related("organization", "connection__candidate")
            .get(pk=shift.pk)
        )
        _require_operational_connection(connection=shift.connection, organization=organization)
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=shift.connection,
        )
        if shift.state != ScheduledWorkShift.STATE_DRAFT:
            raise ValidationError({"shift": "scheduled_shift_not_draft"})
        shift.state = ScheduledWorkShift.STATE_PUBLISHED
        shift.published_by = actor
        shift.published_at = timezone.now()
        shift.save(update_fields=["state", "published_by", "published_at", "updated_at"])
        record_audit_event(
            organization=organization,
            actor=actor,
            action="schedule.shift_published",
            target=shift,
            details={
                "connection": str(shift.connection.public_id),
                "work_date": shift.work_date.isoformat(),
            },
        )
        enqueue_support_notification(
            organization=organization,
            recipient=shift.connection.candidate,
            notification_code="schedule.shift_published",
            target_kind="scheduled_shift",
            target_public_id=shift.public_id,
            target_key=f"support:scheduled-shift:{shift.public_id}",
            collapse_key=f"support:schedule:{shift.connection.public_id}:{shift.work_date.isoformat()}",
            dedupe_key=f"schedule.shift.published:{shift.public_id}:{shift.published_at.isoformat()}",
        )
    return shift


def cancel_scheduled_shift(*, actor, shift):
    organization = shift.organization
    require_permission(
        user=actor,
        organization=organization,
        permission_code=SCHEDULE_MANAGE,
    )
    with transaction.atomic():
        shift = (
            ScheduledWorkShift.objects.select_for_update()
            .select_related("organization", "connection__candidate")
            .get(pk=shift.pk)
        )
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=shift.connection,
        )
        if shift.state == ScheduledWorkShift.STATE_CANCELLED:
            raise ValidationError({"shift": "scheduled_shift_already_cancelled"})
        was_published = shift.state == ScheduledWorkShift.STATE_PUBLISHED
        shift.state = ScheduledWorkShift.STATE_CANCELLED
        shift.cancelled_at = timezone.now()
        shift.save(update_fields=["state", "cancelled_at", "updated_at"])
        record_audit_event(
            organization=organization,
            actor=actor,
            action="schedule.shift_cancelled",
            target=shift,
            details={
                "connection": str(shift.connection.public_id),
                "work_date": shift.work_date.isoformat(),
            },
        )
        if was_published:
            enqueue_support_notification(
                organization=organization,
                recipient=shift.connection.candidate,
                notification_code="schedule.shift_published",
                target_kind="scheduled_shift",
                target_public_id=shift.public_id,
                target_key=f"support:scheduled-shift:{shift.public_id}",
                collapse_key=f"support:schedule:{shift.connection.public_id}:{shift.work_date.isoformat()}",
                dedupe_key=f"schedule.shift.cancelled:{shift.public_id}:{shift.cancelled_at.isoformat()}",
            )
    return shift


def replace_scheduled_shift(
    *,
    actor,
    shift,
    work_date,
    starts_at,
    ends_at,
    break_minutes,
    worker_label="",
):
    """Replace one planned shift while preserving the old audit record.

    A published shift must not be mutated in place: the previous version stays
    in history as cancelled, while the replacement keeps its published state
    and sends one consolidated update to the worker.
    """

    organization = shift.organization
    require_permission(
        user=actor,
        organization=organization,
        permission_code=SCHEDULE_MANAGE,
    )
    with transaction.atomic():
        shift = (
            ScheduledWorkShift.objects.select_for_update()
            .select_related("organization", "connection__candidate", "work_assignment")
            .get(pk=shift.pk)
        )
        if shift.state == ScheduledWorkShift.STATE_CANCELLED:
            raise ValidationError({"shift": "scheduled_shift_already_cancelled"})
        _require_operational_connection(connection=shift.connection, organization=organization)
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=shift.connection,
        )
        _worked_minutes(
            started_at=starts_at,
            ended_at=ends_at,
            break_minutes=break_minutes,
        )
        conflict_exists = (
            ScheduledWorkShift.objects.select_for_update()
            .filter(
                connection=shift.connection,
                work_date=work_date,
                state__in=(
                    ScheduledWorkShift.STATE_DRAFT,
                    ScheduledWorkShift.STATE_PUBLISHED,
                ),
            )
            .exclude(pk=shift.pk)
            .exists()
        )
        if conflict_exists:
            raise ValidationError({"work_date": "current_scheduled_shift_already_exists"})

        previous_state = shift.state
        now = timezone.now()
        shift.state = ScheduledWorkShift.STATE_CANCELLED
        shift.cancelled_at = now
        shift.save(update_fields=["state", "cancelled_at", "updated_at"])
        replacement = ScheduledWorkShift.objects.create(
            organization=organization,
            connection=shift.connection,
            work_assignment=shift.work_assignment,
            work_date=work_date,
            starts_at=starts_at,
            ends_at=ends_at,
            break_minutes=break_minutes,
            worker_label=(worker_label or "").strip(),
            state=previous_state,
            created_by=actor,
            published_by=actor if previous_state == ScheduledWorkShift.STATE_PUBLISHED else None,
            published_at=now if previous_state == ScheduledWorkShift.STATE_PUBLISHED else None,
        )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="schedule.shift_replaced",
            target=replacement,
            details={
                "previous_shift": str(shift.public_id),
                "connection": str(shift.connection.public_id),
                "work_date": work_date.isoformat(),
            },
        )
        if previous_state == ScheduledWorkShift.STATE_PUBLISHED:
            enqueue_support_notification(
                organization=organization,
                recipient=shift.connection.candidate,
                notification_code="schedule.shift_published",
                target_kind="scheduled_shift",
                target_public_id=replacement.public_id,
                target_key=f"support:scheduled-shift:{replacement.public_id}",
                collapse_key=(
                    f"support:schedule:{shift.connection.public_id}:{work_date.isoformat()}"
                ),
                dedupe_key=(
                    f"schedule.shift.replaced:{replacement.public_id}:{now.isoformat()}"
                ),
            )
    return replacement


def submit_work_time_entry(
    *,
    worker,
    connection,
    work_date,
    started_at,
    ended_at,
    break_minutes,
):
    """Create or re-submit the worker's daily record after a correction request."""

    with transaction.atomic():
        connection = (
            SupportConnection.objects.select_for_update()
            .select_related("organization")
            .get(pk=connection.pk)
        )
        if connection.candidate_id != worker.id:
            raise PermissionDenied("support_time_entry_not_owned")
        _require_operational_connection(
            connection=connection,
            organization=connection.organization,
        )
        worked_minutes = _worked_minutes(
            started_at=started_at,
            ended_at=ended_at,
            break_minutes=break_minutes,
            reject_future=True,
        )
        entry = (
            WorkTimeEntry.objects.select_for_update()
            .filter(connection=connection, work_date=work_date)
            .first()
        )
        today = timezone.localdate()
        if entry is None and work_date not in {today, today - timedelta(days=1)}:
            raise ValidationError({"work_date": "worker_may_submit_only_today_or_yesterday"})
        now = timezone.now()
        if entry is None:
            entry = WorkTimeEntry.objects.create(
                organization=connection.organization,
                connection=connection,
                scheduled_shift=_published_shift_for_day(
                    connection=connection,
                    work_date=work_date,
                ),
                work_date=work_date,
                started_at=started_at,
                ended_at=ended_at,
                break_minutes=break_minutes,
                worked_minutes=worked_minutes,
                status=WorkTimeEntry.STATUS_SUBMITTED,
                revision=1,
                submitted_at=now,
                last_changed_by=worker,
            )
        else:
            if entry.status != WorkTimeEntry.STATUS_CORRECTION_REQUESTED:
                raise ValidationError({"entry": "time_entry_is_not_open_for_worker_change"})
            entry.started_at = started_at
            entry.ended_at = ended_at
            entry.break_minutes = break_minutes
            entry.worked_minutes = worked_minutes
            entry.status = WorkTimeEntry.STATUS_SUBMITTED
            entry.revision += 1
            entry.manager_note = ""
            entry.submitted_at = now
            entry.confirmed_at = None
            entry.confirmed_by = None
            entry.worker_acknowledged_at = None
            entry.last_changed_by = worker
            entry.save(
                update_fields=[
                    "started_at",
                    "ended_at",
                    "break_minutes",
                    "worked_minutes",
                    "status",
                    "revision",
                    "manager_note",
                    "submitted_at",
                    "confirmed_at",
                    "confirmed_by",
                    "worker_acknowledged_at",
                    "last_changed_by",
                    "updated_at",
                ]
            )
        _record_revision(
            entry=entry,
            action=WorkTimeEntryRevision.ACTION_SUBMITTED,
            actor=worker,
        )
        record_audit_event(
            organization=connection.organization,
            actor=worker,
            action="time.entry_submitted",
            target=entry,
            details={
                "connection": str(connection.public_id),
                "work_date": work_date.isoformat(),
                "revision": entry.revision,
                "worked_minutes": worked_minutes,
            },
        )
    return entry


def request_work_time_correction(*, actor, entry, reason):
    organization = entry.organization
    require_permission(user=actor, organization=organization, permission_code=TIME_REVIEW)
    with transaction.atomic():
        entry = (
            WorkTimeEntry.objects.select_for_update()
            .select_related("connection", "organization")
            .get(pk=entry.pk)
        )
        require_worker_connection_access(
            user=actor, organization=organization, connection=entry.connection
        )
        if entry.status != WorkTimeEntry.STATUS_SUBMITTED:
            raise ValidationError({"entry": "only_submitted_entry_can_request_correction"})
        entry.status = WorkTimeEntry.STATUS_CORRECTION_REQUESTED
        entry.manager_note = (reason or "").strip()
        entry.last_changed_by = actor
        entry.save(update_fields=["status", "manager_note", "last_changed_by", "updated_at"])
        _record_revision(
            entry=entry,
            action=WorkTimeEntryRevision.ACTION_CORRECTION_REQUESTED,
            actor=actor,
            note=entry.manager_note,
        )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="time.entry_correction_requested",
            target=entry,
            details={"connection": str(entry.connection.public_id), "revision": entry.revision},
        )
    return entry


def confirm_work_time_entry(*, actor, entry):
    organization = entry.organization
    require_permission(user=actor, organization=organization, permission_code=TIME_REVIEW)
    with transaction.atomic():
        entry = (
            WorkTimeEntry.objects.select_for_update()
            .select_related("connection", "organization")
            .get(pk=entry.pk)
        )
        require_worker_connection_access(
            user=actor, organization=organization, connection=entry.connection
        )
        if entry.status != WorkTimeEntry.STATUS_SUBMITTED:
            raise ValidationError({"entry": "only_submitted_entry_can_be_confirmed"})
        now = timezone.now()
        entry.status = WorkTimeEntry.STATUS_CONFIRMED
        entry.confirmed_by = actor
        entry.confirmed_at = now
        entry.manager_note = ""
        entry.last_changed_by = actor
        entry.save(
            update_fields=[
                "status",
                "confirmed_by",
                "confirmed_at",
                "manager_note",
                "last_changed_by",
                "updated_at",
            ]
        )
        _record_revision(
            entry=entry,
            action=WorkTimeEntryRevision.ACTION_CONFIRMED,
            actor=actor,
        )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="time.entry_confirmed",
            target=entry,
            details={"connection": str(entry.connection.public_id), "revision": entry.revision},
        )
    return entry


def edit_work_time_entry(
    *,
    actor,
    entry,
    started_at,
    ended_at,
    break_minutes,
    reason,
):
    """Staff correction changes the factual values and asks the worker to acknowledge."""

    organization = entry.organization
    require_permission(user=actor, organization=organization, permission_code=TIME_EDIT)
    with transaction.atomic():
        entry = (
            WorkTimeEntry.objects.select_for_update()
            .select_related("connection__candidate", "organization")
            .get(pk=entry.pk)
        )
        require_worker_connection_access(
            user=actor, organization=organization, connection=entry.connection
        )
        if entry.status not in {
            WorkTimeEntry.STATUS_SUBMITTED,
            WorkTimeEntry.STATUS_CONFIRMED,
            WorkTimeEntry.STATUS_CORRECTION_REQUESTED,
        }:
            raise ValidationError({"entry": "time_entry_cannot_be_staff_edited_now"})
        worked_minutes = _worked_minutes(
            started_at=started_at,
            ended_at=ended_at,
            break_minutes=break_minutes,
        )
        entry.started_at = started_at
        entry.ended_at = ended_at
        entry.break_minutes = break_minutes
        entry.worked_minutes = worked_minutes
        entry.status = WorkTimeEntry.STATUS_MANAGER_ADJUSTED
        entry.revision += 1
        entry.manager_note = (reason or "").strip()
        entry.confirmed_by = None
        entry.confirmed_at = None
        entry.worker_acknowledged_at = None
        entry.last_changed_by = actor
        entry.save(
            update_fields=[
                "started_at",
                "ended_at",
                "break_minutes",
                "worked_minutes",
                "status",
                "revision",
                "manager_note",
                "confirmed_by",
                "confirmed_at",
                "worker_acknowledged_at",
                "last_changed_by",
                "updated_at",
            ]
        )
        _record_revision(
            entry=entry,
            action=WorkTimeEntryRevision.ACTION_MANAGER_ADJUSTED,
            actor=actor,
            note=entry.manager_note,
        )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="time.entry_staff_edited",
            target=entry,
            details={
                "connection": str(entry.connection.public_id),
                "revision": entry.revision,
                "worked_minutes": worked_minutes,
            },
        )
        enqueue_support_notification(
            organization=organization,
            recipient=entry.connection.candidate,
            notification_code="time.entry_changed",
            target_kind="time_entry",
            target_public_id=entry.public_id,
            target_key=f"support:time-entry:{entry.public_id}",
            collapse_key=f"support:time:{entry.connection.public_id}:{entry.work_date.isoformat()}",
            dedupe_key=f"time.entry.staff_edited:{entry.public_id}:{entry.revision}",
        )
    return entry


def acknowledge_staff_time_adjustment(*, worker, entry):
    with transaction.atomic():
        entry = (
            WorkTimeEntry.objects.select_for_update()
            .select_related("connection", "organization")
            .get(pk=entry.pk)
        )
        if entry.connection.candidate_id != worker.id:
            raise PermissionDenied("support_time_entry_not_owned")
        if entry.status != WorkTimeEntry.STATUS_MANAGER_ADJUSTED:
            raise ValidationError({"entry": "manager_adjustment_not_pending"})
        now = timezone.now()
        entry.status = WorkTimeEntry.STATUS_CONFIRMED
        entry.confirmed_at = now
        entry.worker_acknowledged_at = now
        entry.last_changed_by = worker
        entry.save(
            update_fields=[
                "status",
                "confirmed_at",
                "worker_acknowledged_at",
                "last_changed_by",
                "updated_at",
            ]
        )
        _record_revision(
            entry=entry,
            action=WorkTimeEntryRevision.ACTION_WORKER_ACKNOWLEDGED,
            actor=worker,
        )
        record_audit_event(
            organization=entry.organization,
            actor=worker,
            action="time.entry_staff_adjustment_acknowledged",
            target=entry,
            details={"connection": str(entry.connection.public_id), "revision": entry.revision},
        )
    return entry
