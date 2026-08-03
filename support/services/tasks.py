"""Scoped task and announcement workflows for JobHub Support.

The service keeps publication separate from editing, creates an individual task
state for every worker and produces only neutral in-app notifications. Neither
workflow accepts uploads, document numbers or other sensitive files.
"""

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from support.models import (
    Announcement,
    AnnouncementAcknowledgement,
    ContentTemplate,
    OrganizationMembership,
    SupportConnection,
    TaskAssignment,
    WorkerTask,
)
from support.permission_codes import ANNOUNCEMENT_MANAGE, TASK_MANAGE
from support.permissions import require_permission, require_worker_connection_access

from .audit import record_audit_event
from .notifications import enqueue_support_notification


def _connections_for_ids(*, organization, connection_ids):
    ids = list(connection_ids)
    connections = list(
        SupportConnection.objects.select_related("candidate")
        .filter(organization=organization, public_id__in=ids, is_archived=False)
        .order_by("id")
    )
    if len(connections) != len(ids):
        raise ValidationError({"connection_ids": "task_or_announcement_connection_not_available"})
    return connections


def _require_connection_scope(*, actor, organization, connections):
    for connection in connections:
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=connection,
        )


def _responsible_membership(*, organization, membership_public_id):
    if membership_public_id is None:
        return None
    membership = (
        OrganizationMembership.objects.filter(
            organization=organization,
            public_id=membership_public_id,
            state=OrganizationMembership.STATE_ACTIVE,
        )
        .select_related("user")
        .first()
    )
    if membership is None:
        raise ValidationError({"responsible_membership_id": "responsible_membership_not_available"})
    return membership


def _task_notification(*, assignment, notification_code, dedupe_suffix):
    enqueue_support_notification(
        organization=assignment.task.organization,
        recipient=assignment.connection.candidate,
        notification_code=notification_code,
        target_kind="task_assignment",
        target_public_id=assignment.public_id,
        target_key=f"support:task:{assignment.public_id}",
        collapse_key=f"support:tasks:{assignment.connection.public_id}",
        dedupe_key=f"task:{dedupe_suffix}:{assignment.public_id}",
        push_requested=False,
    )


def create_content_template(
    *,
    actor,
    organization,
    name,
    kind,
    source_language,
    translations,
):
    """Save reusable multilingual wording without recipients or publication.

    The same permission that is required to create the eventual task or
    announcement is required here.  This deliberately avoids creating a
    broader, template-only permission surface for the MVP.
    """

    required_permission = (
        TASK_MANAGE if kind == ContentTemplate.KIND_TASK else ANNOUNCEMENT_MANAGE
    )
    require_permission(
        user=actor,
        organization=organization,
        permission_code=required_permission,
    )
    try:
        with transaction.atomic():
            item = ContentTemplate.objects.create(
                organization=organization,
                name=name,
                kind=kind,
                source_language=source_language,
                translations=translations,
                created_by=actor,
            )
            record_audit_event(
                organization=organization,
                actor=actor,
                action="content_template.created",
                target=item,
                details={"kind": kind},
            )
    except IntegrityError as error:
        # A duplicate internal name is a normal user-facing validation error,
        # not an unhandled database error.
        raise ValidationError({"name": "content_template_name_already_used"}) from error
    return item


def create_worker_task(
    *,
    actor,
    organization,
    source_language,
    translations,
    priority,
    context_kind,
    due_at,
    connection_ids,
    responsible_membership_id=None,
):
    """Create a draft task and one draft-safe assignment per selected worker."""

    require_permission(user=actor, organization=organization, permission_code=TASK_MANAGE)
    with transaction.atomic():
        connections = _connections_for_ids(
            organization=organization,
            connection_ids=connection_ids,
        )
        _require_connection_scope(
            actor=actor,
            organization=organization,
            connections=connections,
        )
        responsible = _responsible_membership(
            organization=organization,
            membership_public_id=responsible_membership_id,
        )
        source = translations[source_language]
        task = WorkerTask.objects.create(
            organization=organization,
            title=source["title"],
            instructions=source["instructions"],
            translations=translations,
            original_language=source_language,
            priority=priority,
            context_kind=context_kind,
            due_at=due_at,
            responsible_membership=responsible,
            created_by=actor,
        )
        TaskAssignment.objects.bulk_create(
            [
                TaskAssignment(task=task, connection=connection, last_changed_by=actor)
                for connection in connections
            ]
        )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="worker_task.created",
            target=task,
            details={"recipient_count": len(connections), "context_kind": context_kind},
        )
    return task


def publish_worker_task(*, actor, task):
    require_permission(user=actor, organization=task.organization, permission_code=TASK_MANAGE)
    with transaction.atomic():
        item = (
            WorkerTask.objects.select_for_update()
            .select_related("organization")
            .prefetch_related("assignments__connection__candidate")
            .get(pk=task.pk)
        )
        if item.state != WorkerTask.STATE_DRAFT:
            raise ValidationError({"task": "worker_task_not_draft"})
        assignments = list(item.assignments.all())
        if not assignments:
            raise ValidationError({"task": "worker_task_requires_recipient"})
        _require_connection_scope(
            actor=actor,
            organization=item.organization,
            connections=[assignment.connection for assignment in assignments],
        )
        item.state = WorkerTask.STATE_PUBLISHED
        item.published_at = timezone.now()
        item.published_by = actor
        item.save(update_fields=["state", "published_at", "published_by", "updated_at"])
        for assignment in assignments:
            _task_notification(
                assignment=assignment,
                notification_code="worker_task.published",
                dedupe_suffix="published",
            )
        record_audit_event(
            organization=item.organization,
            actor=actor,
            action="worker_task.published",
            target=item,
            details={"recipient_count": len(assignments)},
        )
    return item


def worker_change_task_assignment(*, worker, assignment, action, worker_note=""):
    """Start or complete the worker's own published task."""

    with transaction.atomic():
        item = (
            TaskAssignment.objects.select_for_update()
            .select_related("task__organization", "connection__candidate")
            .get(pk=assignment.pk)
        )
        if item.connection.candidate_id != worker.id:
            raise PermissionDenied("support_task_not_owned")
        if item.task.state != WorkerTask.STATE_PUBLISHED:
            raise ValidationError({"task": "worker_task_not_published"})
        now = timezone.now()
        if action == "start":
            if item.status not in {TaskAssignment.STATUS_NEW, TaskAssignment.STATUS_RETURNED}:
                raise ValidationError({"task": "worker_task_cannot_be_started"})
            item.status = TaskAssignment.STATUS_IN_PROGRESS
            item.manager_note = ""
        elif action == "complete":
            if item.status not in {
                TaskAssignment.STATUS_NEW,
                TaskAssignment.STATUS_IN_PROGRESS,
                TaskAssignment.STATUS_RETURNED,
            }:
                raise ValidationError({"task": "worker_task_cannot_be_completed"})
            item.status = TaskAssignment.STATUS_COMPLETED_BY_WORKER
            item.worker_note = (worker_note or "").strip()
            item.completed_at = now
        else:
            raise ValidationError({"action": "unsupported_worker_task_action"})
        item.last_changed_by = worker
        item.save(
            update_fields=[
                "status",
                "worker_note",
                "manager_note",
                "completed_at",
                "last_changed_by",
                "updated_at",
            ]
        )
        record_audit_event(
            organization=item.task.organization,
            actor=worker,
            action=f"worker_task.worker_{action}",
            target=item,
            details={"task": str(item.task.public_id), "status": item.status},
        )
    return item


def staff_change_task_assignment(*, actor, assignment, action, manager_note=""):
    """Confirm, return or cancel a scoped worker task assignment."""

    organization = assignment.task.organization
    require_permission(user=actor, organization=organization, permission_code=TASK_MANAGE)
    with transaction.atomic():
        item = (
            TaskAssignment.objects.select_for_update()
            .select_related("task__organization", "connection__candidate")
            .get(pk=assignment.pk)
        )
        require_worker_connection_access(
            user=actor,
            organization=item.task.organization,
            connection=item.connection,
        )
        if item.task.state != WorkerTask.STATE_PUBLISHED:
            raise ValidationError({"task": "worker_task_not_published"})
        now = timezone.now()
        note = (manager_note or "").strip()
        if action == "confirm":
            if item.status != TaskAssignment.STATUS_COMPLETED_BY_WORKER:
                raise ValidationError({"task": "worker_task_not_ready_for_confirmation"})
            item.status = TaskAssignment.STATUS_CONFIRMED
            item.confirmed_at = now
        elif action == "return":
            if item.status in {TaskAssignment.STATUS_CONFIRMED, TaskAssignment.STATUS_CANCELLED}:
                raise ValidationError({"task": "worker_task_is_final"})
            if not note:
                raise ValidationError({"manager_note": "task_return_reason_required"})
            item.status = TaskAssignment.STATUS_RETURNED
            item.returned_at = now
        elif action == "cancel":
            if item.status in {TaskAssignment.STATUS_CONFIRMED, TaskAssignment.STATUS_CANCELLED}:
                raise ValidationError({"task": "worker_task_is_final"})
            item.status = TaskAssignment.STATUS_CANCELLED
            item.cancelled_at = now
        else:
            raise ValidationError({"action": "unsupported_staff_task_action"})
        item.manager_note = note
        item.last_changed_by = actor
        item.save(
            update_fields=[
                "status",
                "manager_note",
                "confirmed_at",
                "returned_at",
                "cancelled_at",
                "last_changed_by",
                "updated_at",
            ]
        )
        _task_notification(
            assignment=item,
            notification_code="worker_task.status_changed",
            dedupe_suffix=f"{action}:{item.updated_at.isoformat()}",
        )
        record_audit_event(
            organization=item.task.organization,
            actor=actor,
            action=f"worker_task.staff_{action}",
            target=item,
            details={"task": str(item.task.public_id), "status": item.status},
        )
    return item


def create_announcement(
    *,
    actor,
    organization,
    source_language,
    translations,
    importance,
    requires_acknowledgement,
    expires_at,
    connection_ids,
):
    require_permission(
        user=actor,
        organization=organization,
        permission_code=ANNOUNCEMENT_MANAGE,
    )
    if expires_at is not None and expires_at <= timezone.now():
        raise ValidationError({"expires_at": "announcement_expiry_must_be_future"})
    with transaction.atomic():
        connections = _connections_for_ids(
            organization=organization,
            connection_ids=connection_ids,
        )
        _require_connection_scope(
            actor=actor,
            organization=organization,
            connections=connections,
        )
        source = translations[source_language]
        announcement = Announcement.objects.create(
            organization=organization,
            title=source["title"],
            body=source["body"],
            translations=translations,
            original_language=source_language,
            importance=importance,
            requires_acknowledgement=requires_acknowledgement,
            expires_at=expires_at,
            created_by=actor,
        )
        AnnouncementAcknowledgement.objects.bulk_create(
            [
                AnnouncementAcknowledgement(announcement=announcement, connection=connection)
                for connection in connections
            ]
        )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="announcement.created",
            target=announcement,
            details={"recipient_count": len(connections), "important": importance == "important"},
        )
    return announcement


def publish_announcement(*, actor, announcement):
    require_permission(
        user=actor,
        organization=announcement.organization,
        permission_code=ANNOUNCEMENT_MANAGE,
    )
    with transaction.atomic():
        item = (
            Announcement.objects.select_for_update()
            .select_related("organization")
            .prefetch_related("acknowledgements__connection__candidate")
            .get(pk=announcement.pk)
        )
        if item.state != Announcement.STATE_DRAFT:
            raise ValidationError({"announcement": "announcement_not_draft"})
        if item.expires_at is not None and item.expires_at <= timezone.now():
            raise ValidationError({"expires_at": "announcement_expiry_must_be_future"})
        recipients = list(item.acknowledgements.all())
        if not recipients:
            raise ValidationError({"announcement": "announcement_requires_recipient"})
        _require_connection_scope(
            actor=actor,
            organization=item.organization,
            connections=[recipient.connection for recipient in recipients],
        )
        item.state = Announcement.STATE_PUBLISHED
        item.published_at = timezone.now()
        item.published_by = actor
        item.save(update_fields=["state", "published_at", "published_by", "updated_at"])
        for recipient in recipients:
            enqueue_support_notification(
                organization=item.organization,
                recipient=recipient.connection.candidate,
                notification_code="announcement.published",
                target_kind="announcement",
                target_public_id=item.public_id,
                target_key=f"support:announcement:{item.public_id}",
                collapse_key=f"support:announcements:{recipient.connection.public_id}",
                dedupe_key=f"announcement:published:{item.public_id}:{recipient.connection.public_id}",
                push_requested=False,
            )
        record_audit_event(
            organization=item.organization,
            actor=actor,
            action="announcement.published",
            target=item,
            details={"recipient_count": len(recipients), "important": item.importance == "important"},
        )
    return item


def acknowledge_announcement(*, worker, acknowledgement):
    with transaction.atomic():
        item = (
            AnnouncementAcknowledgement.objects.select_for_update()
            .select_related("announcement__organization", "connection__candidate")
            .get(pk=acknowledgement.pk)
        )
        if item.connection.candidate_id != worker.id:
            raise PermissionDenied("support_announcement_not_owned")
        announcement = item.announcement
        if announcement.state != Announcement.STATE_PUBLISHED:
            raise ValidationError({"announcement": "announcement_not_published"})
        if announcement.expires_at is not None and announcement.expires_at <= timezone.now():
            raise ValidationError({"announcement": "announcement_expired"})
        if not announcement.requires_acknowledgement:
            raise ValidationError({"announcement": "announcement_acknowledgement_not_required"})
        if item.acknowledged_at is None:
            item.acknowledged_at = timezone.now()
            item.acknowledged_by = worker
            item.save(update_fields=["acknowledged_at", "acknowledged_by", "updated_at"])
            record_audit_event(
                organization=announcement.organization,
                actor=worker,
                action="announcement.acknowledged",
                target=item,
                details={"announcement": str(announcement.public_id)},
            )
    return item
