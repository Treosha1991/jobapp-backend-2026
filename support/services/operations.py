"""Transactional creation and publication of Support operational assignments."""

from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.exceptions import PermissionDenied, ValidationError

from support.models import (
    DriverVehicleAssignment,
    HousingAssignment,
    HousingPlace,
    RouteStop,
    ProjectScheduleTemplate,
    ScheduledWorkShift,
    SupportConnection,
    TransportPassengerAssignment,
    TransportRoute,
    Vehicle,
    WorkerProjectAssignment,
    WorkerProjectScheduleTemplateSelection,
    WorkProject,
)
from support.permission_codes import HOUSING_MANAGE, SCHEDULE_MANAGE, TRANSPORT_MANAGE
from support.permissions import require_permission, require_worker_connection_access

from .audit import record_audit_event
from .notifications import enqueue_support_notification


OPERATIONAL_CONNECTION_STAGES = frozenset(
    {
        SupportConnection.STAGE_COORDINATOR,
        SupportConnection.STAGE_ACTIVE_WORKER,
    }
)
DEFAULT_ROUTE_RESERVATION = timedelta(hours=2)


def _period_overlaps(queryset, *, starts_field, ends_field, starts_at, ends_at):
    """Return records whose half-open period overlaps the candidate period."""

    queryset = queryset.filter(**{f"{starts_field}__lt": ends_at}) if ends_at else queryset
    return queryset.filter(
        Q(**{f"{ends_field}__isnull": True}) | Q(**{f"{ends_field}__gt": starts_at})
    )


def _require_connection_for_operations(*, connection, organization):
    if connection.organization_id != organization.id:
        raise ValidationError({"connection": "connection_not_in_organization"})
    if connection.is_archived or connection.stage not in OPERATIONAL_CONNECTION_STAGES:
        raise ValidationError({"connection": "connection_not_ready_for_operations"})


def _require_same_organization(*, organization, **objects):
    if any(item is None for item in objects.values()):
        raise ValidationError({"operation": "operation_related_record_required"})
    for field, item in objects.items():
        item_organization_id = getattr(item, "organization_id", None)
        if item_organization_id is None and hasattr(item, "site"):
            item_organization_id = item.site.organization_id
        if item_organization_id is None and hasattr(item, "room"):
            item_organization_id = item.room.site.organization_id
        if item_organization_id != organization.id:
            raise ValidationError({field: "operation_related_record_not_in_organization"})


def _publish_project_template_shifts(*, actor, assignment, published_at):
    """Turn selected project templates into immutable worker calendar shifts.

    A ProjectScheduleTemplate is a reusable employer pattern.  A
    ScheduledWorkShift is the actual calendar entry seen by one worker.  The
    copy happens only on publication, so later template edits cannot rewrite
    an already published calendar.
    """

    selections = list(
        WorkerProjectScheduleTemplateSelection.objects.select_for_update()
        .filter(assignment=assignment)
        .select_related("template")
        .order_by("template__name", "template_id")
    )
    if not selections:
        return []

    current_timezone = timezone.get_current_timezone()
    shifts_by_date = {}
    for selection in selections:
        template = selection.template
        for raw_date in template.calendar_dates:
            work_date = parse_date(raw_date) if isinstance(raw_date, str) else None
            if work_date is None:
                raise ValidationError({"schedule": "project_schedule_template_date_invalid"})
            starts_at = timezone.make_aware(
                datetime.combine(work_date, template.starts_at_time),
                current_timezone,
            )
            ends_at = timezone.make_aware(
                datetime.combine(work_date, template.ends_at_time),
                current_timezone,
            )
            if ends_at <= starts_at:
                ends_at += timedelta(days=1)
            if starts_at < assignment.starts_at or (
                assignment.ends_at is not None and ends_at > assignment.ends_at
            ):
                continue
            if work_date in shifts_by_date:
                raise ValidationError(
                    {"schedule": "project_schedule_templates_overlap_on_day"}
                )
            duration_minutes = int((ends_at - starts_at).total_seconds() // 60)
            if template.break_minutes >= duration_minutes:
                raise ValidationError(
                    {"schedule": "project_schedule_template_break_invalid"}
                )
            shifts_by_date[work_date] = {
                "template": template,
                "starts_at": starts_at,
                "ends_at": ends_at,
            }

    if not shifts_by_date:
        return []
    conflicting_dates = set(
        ScheduledWorkShift.objects.select_for_update()
        .filter(
            connection=assignment.connection,
            work_date__in=shifts_by_date,
            state__in=(
                ScheduledWorkShift.STATE_DRAFT,
                ScheduledWorkShift.STATE_PUBLISHED,
            ),
        )
        .values_list("work_date", flat=True)
    )
    if conflicting_dates:
        raise ValidationError({"schedule": "worker_schedule_conflicts_with_existing_shift"})

    shifts = []
    for work_date, values in sorted(shifts_by_date.items()):
        template = values["template"]
        shifts.append(
            ScheduledWorkShift.objects.create(
                organization=assignment.organization,
                connection=assignment.connection,
                work_assignment=assignment,
                work_date=work_date,
                starts_at=values["starts_at"],
                ends_at=values["ends_at"],
                break_minutes=template.break_minutes,
                worker_label=template.worker_label,
                state=ScheduledWorkShift.STATE_PUBLISHED,
                created_by=actor,
                published_by=actor,
                published_at=published_at,
            )
        )
    record_audit_event(
        organization=assignment.organization,
        actor=actor,
        action="schedule.project_templates_published_to_worker",
        target=assignment,
        details={
            "shift_count": len(shifts),
            "work_dates": [item.work_date.isoformat() for item in shifts],
        },
    )
    return shifts


def set_worker_driving_license(*, actor, connection, has_driving_license):
    """Store only the employer's operational licence confirmation, never a scan."""

    organization = connection.organization
    require_permission(user=actor, organization=organization, permission_code=TRANSPORT_MANAGE)
    with transaction.atomic():
        connection = SupportConnection.objects.select_for_update().get(pk=connection.pk)
        require_worker_connection_access(user=actor, organization=organization, connection=connection)
        value = bool(has_driving_license)
        if connection.has_driving_license != value:
            connection.has_driving_license = value
            connection.save(update_fields=["has_driving_license", "updated_at"])
            record_audit_event(
                organization=organization,
                actor=actor,
                action="transport.driving_license_marked",
                target=connection,
                details={"has_driving_license": value},
            )
    return connection


def create_housing_assignment(*, actor, organization, connection, place, check_in_at, check_out_at=None):
    require_permission(user=actor, organization=organization, permission_code=HOUSING_MANAGE)
    with transaction.atomic():
        connection = SupportConnection.objects.select_for_update().get(pk=connection.pk)
        place = HousingPlace.objects.select_for_update().select_related("room__site").get(pk=place.pk)
        _require_connection_for_operations(connection=connection, organization=organization)
        require_worker_connection_access(
            user=actor, organization=organization, connection=connection
        )
        _require_same_organization(organization=organization, place=place)
        if not place.is_active or not place.room.is_active or not place.room.site.is_active:
            raise ValidationError({"place": "housing_place_not_available"})
        assignment = HousingAssignment.objects.create(
            organization=organization,
            connection=connection,
            place=place,
            check_in_at=check_in_at,
            check_out_at=check_out_at,
            created_by=actor,
        )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="housing.assignment_drafted",
            target=assignment,
            details={"connection": str(connection.public_id), "place": str(place.public_id)},
        )
    return assignment


def edit_housing_assignment_draft(*, actor, assignment, check_in_at):
    """Change the check-in date of an unpublished housing draft only."""

    organization = assignment.organization
    require_permission(user=actor, organization=organization, permission_code=HOUSING_MANAGE)
    with transaction.atomic():
        assignment = HousingAssignment.objects.select_for_update().get(pk=assignment.pk)
        if assignment.state != HousingAssignment.STATE_DRAFT:
            raise ValidationError({"assignment": "housing_assignment_not_draft"})
        _require_connection_for_operations(
            connection=assignment.connection,
            organization=organization,
        )
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=assignment.connection,
        )
        assignment.check_in_at = check_in_at
        # A check-out date is intentionally never collected for a housing draft.
        assignment.check_out_at = None
        assignment.save(update_fields=["check_in_at", "check_out_at", "updated_at"])
        record_audit_event(
            organization=organization,
            actor=actor,
            action="housing.assignment_draft_updated",
            target=assignment,
            details={"connection": str(assignment.connection.public_id)},
        )
    return assignment


def schedule_housing_check_out(*, actor, assignment, check_out_at):
    """Set or change the departure date of a published housing assignment."""

    organization = assignment.organization
    require_permission(user=actor, organization=organization, permission_code=HOUSING_MANAGE)
    with transaction.atomic():
        assignment = (
            HousingAssignment.objects.select_for_update()
            .select_related("connection__candidate", "organization")
            .get(pk=assignment.pk)
        )
        if assignment.state != HousingAssignment.STATE_PUBLISHED:
            raise ValidationError({"assignment": "housing_assignment_not_published"})
        _require_connection_for_operations(
            connection=assignment.connection,
            organization=organization,
        )
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=assignment.connection,
        )
        if check_out_at <= assignment.check_in_at:
            raise ValidationError({"check_out_at": "period_end_must_be_after_start"})
        assignment.check_out_at = check_out_at
        assignment.save(update_fields=["check_out_at", "updated_at"])
        record_audit_event(
            organization=organization,
            actor=actor,
            action="housing.assignment_check_out_scheduled",
            target=assignment,
            details={
                "connection": str(assignment.connection.public_id),
                "check_out_at": check_out_at.isoformat(),
            },
        )
        enqueue_support_notification(
            organization=organization,
            recipient=assignment.connection.candidate,
            notification_code="housing.assignment_published",
            target_kind="housing_assignment",
            target_public_id=assignment.public_id,
            target_key=f"support:housing-assignment:{assignment.public_id}",
            collapse_key=f"support:housing:{assignment.connection.public_id}",
            dedupe_key=(
                "housing.assignment.check_out_scheduled:"
                f"{assignment.public_id}:{check_out_at.isoformat()}"
            ),
        )
    return assignment


def publish_housing_assignment(*, actor, assignment):
    organization = assignment.organization
    require_permission(user=actor, organization=organization, permission_code=HOUSING_MANAGE)
    with transaction.atomic():
        assignment = (
            HousingAssignment.objects.select_for_update()
            .select_related("connection__candidate", "place__room__site", "organization")
            .get(pk=assignment.pk)
        )
        if assignment.state != HousingAssignment.STATE_DRAFT:
            raise ValidationError({"assignment": "housing_assignment_not_draft"})
        _require_connection_for_operations(connection=assignment.connection, organization=organization)
        require_worker_connection_access(
            user=actor, organization=organization, connection=assignment.connection
        )
        place = assignment.place
        if not place.is_active or not place.room.is_active or not place.room.site.is_active:
            raise ValidationError({"place": "housing_place_not_available"})
        place_conflict = _period_overlaps(
            HousingAssignment.objects.select_for_update()
            .filter(place=place, state=HousingAssignment.STATE_PUBLISHED)
            .exclude(pk=assignment.pk),
            starts_field="check_in_at",
            ends_field="check_out_at",
            starts_at=assignment.check_in_at,
            ends_at=assignment.check_out_at,
        ).exists()
        worker_conflict = _period_overlaps(
            HousingAssignment.objects.select_for_update()
            .filter(connection=assignment.connection, state=HousingAssignment.STATE_PUBLISHED)
            .exclude(pk=assignment.pk),
            starts_field="check_in_at",
            ends_field="check_out_at",
            starts_at=assignment.check_in_at,
            ends_at=assignment.check_out_at,
        ).exists()
        if place_conflict or worker_conflict:
            raise ValidationError({"assignment": "housing_assignment_conflicts_with_published_assignment"})
        assignment.state = HousingAssignment.STATE_PUBLISHED
        assignment.published_by = actor
        assignment.published_at = timezone.now()
        assignment.save(update_fields=["state", "published_by", "published_at", "updated_at"])
        record_audit_event(
            organization=organization,
            actor=actor,
            action="housing.assignment_published",
            target=assignment,
            details={"connection": str(assignment.connection.public_id), "place": str(place.public_id)},
        )
        enqueue_support_notification(
            organization=organization,
            recipient=assignment.connection.candidate,
            notification_code="housing.assignment_published",
            target_kind="housing_assignment",
            target_public_id=assignment.public_id,
            target_key=f"support:housing-assignment:{assignment.public_id}",
            collapse_key=f"support:housing:{assignment.connection.public_id}",
            dedupe_key=f"housing.assignment.published:{assignment.public_id}:{assignment.published_at.isoformat()}",
        )
    return assignment


def cancel_housing_assignment(*, actor, assignment):
    """Cancel a draft or published housing assignment without erasing history."""

    organization = assignment.organization
    require_permission(user=actor, organization=organization, permission_code=HOUSING_MANAGE)
    with transaction.atomic():
        assignment = (
            HousingAssignment.objects.select_for_update()
            .select_related("connection__candidate", "organization")
            .get(pk=assignment.pk)
        )
        if assignment.state == HousingAssignment.STATE_CANCELLED:
            raise ValidationError({"assignment": "housing_assignment_already_cancelled"})
        was_published = assignment.state == HousingAssignment.STATE_PUBLISHED
        require_worker_connection_access(
            user=actor, organization=organization, connection=assignment.connection
        )
        now = timezone.now()
        assignment.state = HousingAssignment.STATE_CANCELLED
        assignment.cancelled_at = now
        assignment.save(update_fields=["state", "cancelled_at", "updated_at"])
        record_audit_event(
            organization=organization,
            actor=actor,
            action="housing.assignment_cancelled",
            target=assignment,
            details={
                "connection": str(assignment.connection.public_id),
                "was_published": was_published,
            },
        )
        if was_published:
            enqueue_support_notification(
                organization=organization,
                recipient=assignment.connection.candidate,
                notification_code="housing.assignment_published",
                target_kind="housing_assignment",
                target_public_id=assignment.public_id,
                target_key=f"support:housing-assignment:{assignment.public_id}",
                collapse_key=f"support:housing:{assignment.connection.public_id}",
                dedupe_key=f"housing.assignment.cancelled:{assignment.public_id}:{now.isoformat()}",
            )
    return assignment


def delete_housing_assignment_draft(*, actor, assignment):
    """Permanently remove a staff-only housing draft before publication."""

    organization = assignment.organization
    require_permission(user=actor, organization=organization, permission_code=HOUSING_MANAGE)
    with transaction.atomic():
        assignment = HousingAssignment.objects.select_for_update().get(pk=assignment.pk)
        if assignment.state != HousingAssignment.STATE_DRAFT:
            raise ValidationError({"assignment": "housing_assignment_not_draft"})
        require_worker_connection_access(
            user=actor, organization=organization, connection=assignment.connection
        )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="housing.assignment_draft_deleted",
            target=assignment,
            details={"connection": str(assignment.connection.public_id)},
        )
        assignment.delete()


def create_worker_project_assignment(
    *,
    actor,
    organization,
    connection,
    project,
    worker_role,
    starts_at,
    ends_at=None,
    schedule_templates=(),
):
    require_permission(user=actor, organization=organization, permission_code=SCHEDULE_MANAGE)
    with transaction.atomic():
        connection = SupportConnection.objects.select_for_update().get(pk=connection.pk)
        project = WorkProject.objects.select_for_update().select_related("worksite").get(pk=project.pk)
        _require_connection_for_operations(connection=connection, organization=organization)
        require_worker_connection_access(
            user=actor, organization=organization, connection=connection
        )
        _require_same_organization(organization=organization, project=project)
        if not project.is_active or not project.worksite.is_active:
            raise ValidationError({"project": "work_project_not_available"})
        template_ids = {item.pk for item in schedule_templates}
        templates = list(
            ProjectScheduleTemplate.objects.select_for_update()
            .filter(project=project, is_active=True, pk__in=template_ids)
            .order_by("id")
        )
        if len(templates) != len(template_ids):
            raise ValidationError({"schedule_templates": "project_schedule_template_not_available"})
        assignment = WorkerProjectAssignment.objects.create(
            organization=organization,
            connection=connection,
            project=project,
            worker_role=(worker_role or "").strip(),
            starts_at=starts_at,
            ends_at=ends_at,
            created_by=actor,
        )
        WorkerProjectScheduleTemplateSelection.objects.bulk_create(
            [
                WorkerProjectScheduleTemplateSelection(
                    assignment=assignment,
                    template=template,
                )
                for template in templates
            ]
        )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="work.assignment_drafted",
            target=assignment,
            details={
                "connection": str(connection.public_id),
                "project": str(project.public_id),
                "schedule_template_count": len(templates),
            },
        )
    return assignment


def publish_worker_project_assignment(*, actor, assignment):
    organization = assignment.organization
    require_permission(user=actor, organization=organization, permission_code=SCHEDULE_MANAGE)
    with transaction.atomic():
        assignment = (
            WorkerProjectAssignment.objects.select_for_update()
            .select_related("connection__candidate", "project__worksite", "organization")
            .get(pk=assignment.pk)
        )
        if assignment.state != WorkerProjectAssignment.STATE_DRAFT:
            raise ValidationError({"assignment": "work_assignment_not_draft"})
        _require_connection_for_operations(connection=assignment.connection, organization=organization)
        require_worker_connection_access(
            user=actor, organization=organization, connection=assignment.connection
        )
        if not assignment.project.is_active or not assignment.project.worksite.is_active:
            raise ValidationError({"project": "work_project_not_available"})
        conflict = _period_overlaps(
            WorkerProjectAssignment.objects.select_for_update()
            .filter(connection=assignment.connection, state=WorkerProjectAssignment.STATE_PUBLISHED)
            .exclude(pk=assignment.pk),
            starts_field="starts_at",
            ends_field="ends_at",
            starts_at=assignment.starts_at,
            ends_at=assignment.ends_at,
        ).exists()
        if conflict:
            raise ValidationError({"assignment": "work_assignment_conflicts_with_published_assignment"})
        occupied_places = _period_overlaps(
            WorkerProjectAssignment.objects.select_for_update()
            .filter(
                project=assignment.project,
                state=WorkerProjectAssignment.STATE_PUBLISHED,
            )
            .exclude(pk=assignment.pk),
            starts_field="starts_at",
            ends_field="ends_at",
            starts_at=assignment.starts_at,
            ends_at=assignment.ends_at,
        ).count()
        if occupied_places >= assignment.project.worker_capacity:
            raise ValidationError({"project": "work_project_capacity_reached"})
        published_at = timezone.now()
        assignment.state = WorkerProjectAssignment.STATE_PUBLISHED
        assignment.published_by = actor
        assignment.published_at = published_at
        assignment.save(update_fields=["state", "published_by", "published_at", "updated_at"])
        _publish_project_template_shifts(
            actor=actor,
            assignment=assignment,
            published_at=published_at,
        )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="work.assignment_published",
            target=assignment,
            details={"connection": str(assignment.connection.public_id), "project": str(assignment.project.public_id)},
        )
        enqueue_support_notification(
            organization=organization,
            recipient=assignment.connection.candidate,
            notification_code="work.assignment_published",
            target_kind="work_assignment",
            target_public_id=assignment.public_id,
            target_key=f"support:work-assignment:{assignment.public_id}",
            collapse_key=f"support:work:{assignment.connection.public_id}",
            dedupe_key=f"work.assignment.published:{assignment.public_id}:{assignment.published_at.isoformat()}",
        )
    return assignment


def cancel_worker_project_assignment(*, actor, assignment):
    """Cancel a work assignment while preserving its audit trail."""

    organization = assignment.organization
    require_permission(user=actor, organization=organization, permission_code=SCHEDULE_MANAGE)
    with transaction.atomic():
        assignment = (
            WorkerProjectAssignment.objects.select_for_update()
            .select_related("connection__candidate", "organization")
            .get(pk=assignment.pk)
        )
        if assignment.state == WorkerProjectAssignment.STATE_CANCELLED:
            raise ValidationError({"assignment": "work_assignment_already_cancelled"})
        was_published = assignment.state == WorkerProjectAssignment.STATE_PUBLISHED
        require_worker_connection_access(
            user=actor, organization=organization, connection=assignment.connection
        )
        now = timezone.now()
        assignment.state = WorkerProjectAssignment.STATE_CANCELLED
        assignment.cancelled_at = now
        assignment.save(update_fields=["state", "cancelled_at", "updated_at"])
        record_audit_event(
            organization=organization,
            actor=actor,
            action="work.assignment_cancelled",
            target=assignment,
            details={
                "connection": str(assignment.connection.public_id),
                "was_published": was_published,
            },
        )
        if was_published:
            enqueue_support_notification(
                organization=organization,
                recipient=assignment.connection.candidate,
                notification_code="work.assignment_published",
                target_kind="work_assignment",
                target_public_id=assignment.public_id,
                target_key=f"support:work-assignment:{assignment.public_id}",
                collapse_key=f"support:work:{assignment.connection.public_id}",
                dedupe_key=f"work.assignment.cancelled:{assignment.public_id}:{now.isoformat()}",
            )
    return assignment


def delete_worker_project_assignment_draft(*, actor, assignment):
    """Permanently remove a staff-only work draft before publication."""

    organization = assignment.organization
    require_permission(user=actor, organization=organization, permission_code=SCHEDULE_MANAGE)
    with transaction.atomic():
        assignment = WorkerProjectAssignment.objects.select_for_update().get(pk=assignment.pk)
        if assignment.state != WorkerProjectAssignment.STATE_DRAFT:
            raise ValidationError({"assignment": "work_assignment_not_draft"})
        require_worker_connection_access(
            user=actor, organization=organization, connection=assignment.connection
        )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="work.assignment_draft_deleted",
            target=assignment,
            details={"connection": str(assignment.connection.public_id)},
        )
        assignment.delete()


def create_driver_vehicle_assignment(*, actor, organization, driver_connection, vehicle, starts_on, ends_on=None):
    require_permission(user=actor, organization=organization, permission_code=TRANSPORT_MANAGE)
    with transaction.atomic():
        driver_connection = SupportConnection.objects.select_for_update().get(pk=driver_connection.pk)
        vehicle = Vehicle.objects.select_for_update().get(pk=vehicle.pk)
        _require_connection_for_operations(connection=driver_connection, organization=organization)
        require_worker_connection_access(
            user=actor, organization=organization, connection=driver_connection
        )
        _require_same_organization(organization=organization, vehicle=vehicle)
        if not vehicle.is_active:
            raise ValidationError({"vehicle": "vehicle_not_available"})
        if _connection_has_active_passenger_route(
            connection=driver_connection,
            starts_on=starts_on,
            ends_on=ends_on,
        ):
            raise ValidationError({"driver_connection": "driver_already_has_passenger_route"})
        assignment = DriverVehicleAssignment.objects.create(
            organization=organization,
            driver_connection=driver_connection,
            vehicle=vehicle,
            starts_on=starts_on,
            ends_on=ends_on,
            created_by=actor,
        )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="transport.driver_vehicle_drafted",
            target=assignment,
            details={"driver_connection": str(driver_connection.public_id), "vehicle": str(vehicle.public_id)},
        )
    return assignment


def publish_driver_vehicle_assignment(*, actor, assignment):
    """Publish a driver--vehicle assignment even when no route is needed yet.

    A vehicle can be assigned to a driver before the transport coordinator knows
    its stops or passengers.  A route may still be created later from this
    published assignment.
    """

    organization = assignment.organization
    require_permission(user=actor, organization=organization, permission_code=TRANSPORT_MANAGE)
    with transaction.atomic():
        assignment = (
            DriverVehicleAssignment.objects.select_for_update()
            .select_related("vehicle", "driver_connection__candidate")
            .get(pk=assignment.pk)
        )
        _require_same_organization(organization=organization, assignment=assignment)
        _require_connection_for_operations(
            connection=assignment.driver_connection,
            organization=organization,
        )
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=assignment.driver_connection,
        )
        if assignment.state != DriverVehicleAssignment.STATE_DRAFT:
            raise ValidationError({"driver_vehicle_assignment": "driver_vehicle_assignment_not_draft"})
        if not assignment.vehicle.is_active:
            raise ValidationError({"vehicle": "vehicle_not_available"})
        replacement_assignments = list(
            _period_overlaps(
                DriverVehicleAssignment.objects.select_for_update()
                .filter(
                    vehicle=assignment.vehicle,
                    state=DriverVehicleAssignment.STATE_PUBLISHED,
                )
                .exclude(pk=assignment.pk),
                starts_field="starts_on",
                ends_field="ends_on",
                starts_at=assignment.starts_on,
                ends_at=assignment.ends_on,
            )
        )
        driver_conflict = _period_overlaps(
            DriverVehicleAssignment.objects.select_for_update()
            .filter(
                driver_connection=assignment.driver_connection,
                state=DriverVehicleAssignment.STATE_PUBLISHED,
            )
            .exclude(pk=assignment.pk)
            .exclude(pk__in=[item.pk for item in replacement_assignments]),
            starts_field="starts_on",
            ends_field="ends_on",
            starts_at=assignment.starts_on,
            ends_at=assignment.ends_on,
        ).exists()
        if driver_conflict:
            raise ValidationError({"driver_vehicle_assignment": "vehicle_or_driver_has_published_assignment_conflict"})

        now = timezone.now()
        # Publishing a new draft for the same car deliberately replaces its
        # previous driver. Driver appointments are open-ended: their history
        # is defined by the next appointment, not by a manually entered end.
        # Routes belong to the car's crew, so they move with the car instead
        # of disappearing when the driver is changed.
        for previous_assignment in replacement_assignments:
            routes = list(
                TransportRoute.objects.select_for_update()
                .filter(driver_vehicle_assignment=previous_assignment)
                .exclude(state=TransportRoute.STATE_CANCELLED)
            )
            for route in routes:
                route.driver_vehicle_assignment = assignment
                route.save(update_fields=["driver_vehicle_assignment", "updated_at"])
            previous_assignment.state = DriverVehicleAssignment.STATE_CANCELLED
            previous_assignment.cancelled_at = now
            previous_assignment.ends_on = None
            previous_assignment.save(update_fields=["state", "cancelled_at", "ends_on", "updated_at"])
            record_audit_event(
                organization=organization,
                actor=actor,
                action="transport.driver_vehicle_replaced",
                target=previous_assignment,
                details={
                    "replacement_assignment": str(assignment.public_id),
                    "transferred_route_count": len(routes),
                },
            )
        assignment.state = DriverVehicleAssignment.STATE_PUBLISHED
        assignment.published_by = actor
        assignment.published_at = now
        assignment.ends_on = None
        assignment.save(update_fields=["state", "published_by", "published_at", "ends_on", "updated_at"])
        record_audit_event(
            organization=organization,
            actor=actor,
            action="transport.driver_vehicle_published",
            target=assignment,
            details={
                "driver_connection": str(assignment.driver_connection.public_id),
                "vehicle": str(assignment.vehicle.public_id),
            },
        )
        enqueue_support_notification(
            organization=organization,
            recipient=assignment.driver_connection.candidate,
            notification_code="transport.assignment_published",
            target_kind="driver_vehicle_assignment",
            target_public_id=assignment.public_id,
            target_key=f"support:driver-vehicle:{assignment.public_id}",
            collapse_key=f"support:transport:{assignment.driver_connection.candidate_id}",
            dedupe_key=f"transport.driver-vehicle.published:{assignment.public_id}:{now.isoformat()}",
        )
    return assignment


def edit_driver_vehicle_assignment_draft(*, actor, assignment, driver_connection, starts_on, ends_on=None):
    """Edit an unpublished vehicle assignment before it is shared with a worker."""

    organization = assignment.organization
    require_permission(user=actor, organization=organization, permission_code=TRANSPORT_MANAGE)
    with transaction.atomic():
        assignment = DriverVehicleAssignment.objects.select_for_update().get(pk=assignment.pk)
        driver_connection = SupportConnection.objects.select_for_update().get(pk=driver_connection.pk)
        if assignment.state != DriverVehicleAssignment.STATE_DRAFT:
            raise ValidationError({"driver_vehicle_assignment": "driver_vehicle_assignment_not_draft"})
        if TransportRoute.objects.filter(driver_vehicle_assignment=assignment).exists():
            raise ValidationError({"driver_vehicle_assignment": "edit_route_before_driver_vehicle_assignment"})
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=assignment.driver_connection,
        )
        _require_connection_for_operations(connection=driver_connection, organization=organization)
        require_worker_connection_access(
            user=actor, organization=organization, connection=driver_connection
        )
        if _connection_has_active_passenger_route(
            connection=driver_connection, starts_on=starts_on, ends_on=ends_on
        ):
            raise ValidationError({"driver_connection": "driver_already_has_passenger_route"})
        previous_driver_id = assignment.driver_connection_id
        assignment.driver_connection = driver_connection
        assignment.starts_on = starts_on
        assignment.ends_on = ends_on
        assignment.save(update_fields=["driver_connection", "starts_on", "ends_on", "updated_at"])
        record_audit_event(
            organization=organization,
            actor=actor,
            action="transport.driver_vehicle_draft_updated",
            target=assignment,
            details={
                "previous_driver_connection": str(previous_driver_id),
                "driver_connection": str(driver_connection.public_id),
                "vehicle": str(assignment.vehicle.public_id),
            },
        )
    return assignment


def delete_driver_vehicle_assignment_draft(*, actor, assignment):
    """Remove an unshared driver--vehicle draft and its draft route, if any."""

    organization = assignment.organization
    require_permission(user=actor, organization=organization, permission_code=TRANSPORT_MANAGE)
    with transaction.atomic():
        assignment = DriverVehicleAssignment.objects.select_for_update().get(pk=assignment.pk)
        if assignment.state != DriverVehicleAssignment.STATE_DRAFT:
            raise ValidationError({"driver_vehicle_assignment": "driver_vehicle_assignment_not_draft"})
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=assignment.driver_connection,
        )
        routes = list(
            TransportRoute.objects.select_for_update().filter(
                driver_vehicle_assignment=assignment
            )
        )
        for route in routes:
            record_audit_event(
                organization=organization,
                actor=actor,
                action="transport.route_draft_deleted_with_driver_vehicle",
                target=route,
                details={"driver_vehicle_assignment": str(assignment.public_id)},
            )
            route.delete()
        record_audit_event(
            organization=organization,
            actor=actor,
            action="transport.driver_vehicle_draft_deleted",
            target=assignment,
            details={
                "driver_connection": str(assignment.driver_connection.public_id),
                "deleted_draft_route_count": len(routes),
            },
        )
        assignment.delete()


def create_transport_route(*, actor, organization, internal_name, driver_vehicle_assignment, starts_on, ends_on=None, worksite=None, departure_time=None, reservation_expires_at=None):
    require_permission(user=actor, organization=organization, permission_code=TRANSPORT_MANAGE)
    with transaction.atomic():
        driver_assignment = (
            DriverVehicleAssignment.objects.select_for_update()
            .select_related("vehicle", "driver_connection")
            .get(pk=driver_vehicle_assignment.pk)
        )
        _require_same_organization(organization=organization, driver_assignment=driver_assignment)
        _require_connection_for_operations(connection=driver_assignment.driver_connection, organization=organization)
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=driver_assignment.driver_connection,
        )
        if driver_assignment.state == DriverVehicleAssignment.STATE_CANCELLED:
            raise ValidationError({"driver_vehicle_assignment": "driver_vehicle_assignment_cancelled"})
        # A route never outlives the vehicle assignment.  In the employer
        # form the end date is optional, so a finite vehicle assignment is
        # also the natural end date of an otherwise open-ended route.
        if ends_on is None and driver_assignment.ends_on is not None:
            ends_on = driver_assignment.ends_on
        if ends_on is not None and ends_on < starts_on:
            raise ValidationError({"ends_on": "period_end_must_not_be_before_start"})
        if worksite is not None:
            _require_same_organization(organization=organization, worksite=worksite)
            if not worksite.is_active:
                raise ValidationError({"worksite": "worksite_not_available"})
        if starts_on < driver_assignment.starts_on or (
            driver_assignment.ends_on is not None
            and (ends_on is None or ends_on > driver_assignment.ends_on)
        ):
            raise ValidationError({"route": "route_outside_driver_vehicle_assignment_period"})
        now = timezone.now()
        active_route_exists = TransportRoute.objects.select_for_update().filter(
            driver_vehicle_assignment=driver_assignment,
        ).filter(
            Q(state=TransportRoute.STATE_PUBLISHED)
            | Q(
                state=TransportRoute.STATE_DRAFT,
                reservation_expires_at__gt=now,
            )
        ).exists()
        if active_route_exists:
            raise ValidationError({"route": "driver_vehicle_assignment_already_has_active_route"})
        reservation_expiry = reservation_expires_at or (now + DEFAULT_ROUTE_RESERVATION)
        if reservation_expiry <= now:
            raise ValidationError({"reservation_expires_at": "route_reservation_must_be_future"})
        route = TransportRoute.objects.create(
            organization=organization,
            internal_name=(internal_name or "").strip(),
            driver_vehicle_assignment=driver_assignment,
            starts_on=starts_on,
            ends_on=ends_on,
            worksite=worksite,
            departure_time=departure_time,
            reservation_expires_at=reservation_expiry,
            created_by=actor,
        )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="transport.route_drafted",
            target=route,
            details={"driver_vehicle_assignment": str(driver_assignment.public_id)},
        )
    return route


def add_route_stop(*, actor, route, sequence, kind, label, housing_site=None):
    organization = route.organization
    require_permission(user=actor, organization=organization, permission_code=TRANSPORT_MANAGE)
    with transaction.atomic():
        route = (
            TransportRoute.objects.select_for_update()
            .select_related("driver_vehicle_assignment__driver_connection")
            .get(pk=route.pk)
        )
        if route.state != TransportRoute.STATE_DRAFT:
            raise ValidationError({"route": "transport_route_not_draft"})
        if route.reservation_expires_at is None or route.reservation_expires_at <= timezone.now():
            raise ValidationError({"route": "transport_route_reservation_expired"})
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=route.driver_vehicle_assignment.driver_connection,
        )
        normalized_label = (label or "").strip()
        if not normalized_label:
            raise ValidationError({"label": "route_stop_label_required"})
        if housing_site is not None:
            _require_same_organization(organization=organization, housing_site=housing_site)
            if not housing_site.is_active:
                raise ValidationError({"housing_site": "housing_site_not_available"})
        stop = RouteStop.objects.create(
            route=route,
            sequence=sequence,
            kind=kind,
            label=normalized_label,
            housing_site=housing_site,
        )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="transport.stop_added",
            target=stop,
            details={"route": str(route.public_id), "kind": kind, "sequence": sequence},
        )
    return stop


def edit_route_stop(*, actor, stop, kind, label, housing_site=None):
    """Edit a draft stop without breaking the route's ordered itinerary."""

    organization = stop.route.organization
    require_permission(user=actor, organization=organization, permission_code=TRANSPORT_MANAGE)
    with transaction.atomic():
        stop = (
            RouteStop.objects.select_for_update()
            .select_related("route__driver_vehicle_assignment__driver_connection")
            .get(pk=stop.pk)
        )
        route = stop.route
        if route.state != TransportRoute.STATE_DRAFT:
            raise ValidationError({"route": "transport_route_not_draft"})
        if route.reservation_expires_at is None or route.reservation_expires_at <= timezone.now():
            raise ValidationError({"route": "transport_route_reservation_expired"})
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=route.driver_vehicle_assignment.driver_connection,
        )
        normalized_label = (label or "").strip()
        if not normalized_label:
            raise ValidationError({"label": "route_stop_label_required"})
        if housing_site is not None:
            _require_same_organization(organization=organization, housing_site=housing_site)
            if not housing_site.is_active:
                raise ValidationError({"housing_site": "housing_site_not_available"})
        stop.kind = kind
        stop.label = normalized_label
        stop.housing_site = housing_site
        stop.save(update_fields=["kind", "label", "housing_site", "updated_at"])
        record_audit_event(
            organization=organization,
            actor=actor,
            action="transport.stop_edited",
            target=stop,
            details={"route": str(route.public_id), "sequence": stop.sequence},
        )
    return stop


def _route_passenger_conflict(*, connection, route):
    current_time = timezone.now()
    candidates = TransportPassengerAssignment.objects.select_for_update().filter(
        connection=connection,
        route__state__in=[TransportRoute.STATE_DRAFT, TransportRoute.STATE_PUBLISHED],
    ).exclude(route=route)
    candidates = candidates.filter(
        Q(route__state=TransportRoute.STATE_PUBLISHED)
        | Q(route__reservation_expires_at__gt=current_time)
    )
    return _period_overlaps(
        candidates,
        starts_field="route__starts_on",
        ends_field="route__ends_on",
        starts_at=route.starts_on,
        ends_at=route.ends_on,
    ).exists()


def _connection_has_active_passenger_route(*, connection, starts_on, ends_on):
    """A worker may belong to one active transport crew only."""

    current_time = timezone.now()
    routes = TransportPassengerAssignment.objects.select_for_update().filter(
        connection=connection,
        route__state__in=(TransportRoute.STATE_DRAFT, TransportRoute.STATE_PUBLISHED),
    ).filter(
        Q(route__state=TransportRoute.STATE_PUBLISHED)
        | Q(route__reservation_expires_at__gt=current_time)
    )
    return _period_overlaps(
        routes,
        starts_field="route__starts_on",
        ends_field="route__ends_on",
        starts_at=starts_on,
        ends_at=ends_on,
    ).exists()


def _connection_has_driver_assignment_for_route(*, connection, route):
    assignments = DriverVehicleAssignment.objects.select_for_update().filter(
        driver_connection=connection,
        state__in=(
            DriverVehicleAssignment.STATE_DRAFT,
            DriverVehicleAssignment.STATE_PUBLISHED,
        ),
    )
    return _period_overlaps(
        assignments,
        starts_field="starts_on",
        ends_field="ends_on",
        starts_at=route.starts_on,
        ends_at=route.ends_on,
    ).exists()


def add_route_passenger(*, actor, route, connection, pickup_stop, dropoff_stop, boarding_order):
    organization = route.organization
    require_permission(user=actor, organization=organization, permission_code=TRANSPORT_MANAGE)
    with transaction.atomic():
        route = (
            TransportRoute.objects.select_for_update()
            .select_related("driver_vehicle_assignment__driver_connection")
            .get(pk=route.pk)
        )
        connection = SupportConnection.objects.select_for_update().get(pk=connection.pk)
        pickup_stop = RouteStop.objects.select_for_update().get(pk=pickup_stop.pk)
        dropoff_stop = RouteStop.objects.select_for_update().get(pk=dropoff_stop.pk)
        if route.state != TransportRoute.STATE_DRAFT:
            raise ValidationError({"route": "transport_route_not_draft"})
        if route.reservation_expires_at is None or route.reservation_expires_at <= timezone.now():
            raise ValidationError({"route": "transport_route_reservation_expired"})
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=route.driver_vehicle_assignment.driver_connection,
        )
        _require_connection_for_operations(connection=connection, organization=organization)
        require_worker_connection_access(
            user=actor, organization=organization, connection=connection
        )
        if connection.pk == route.driver_vehicle_assignment.driver_connection_id:
            raise ValidationError({"connection": "driver_cannot_be_route_passenger"})
        if _connection_has_driver_assignment_for_route(connection=connection, route=route):
            raise ValidationError({"connection": "driver_already_has_vehicle_assignment"})
        if pickup_stop.route_id != route.id or dropoff_stop.route_id != route.id:
            raise ValidationError({"stop": "transport_stop_not_in_route"})
        if pickup_stop.kind != RouteStop.KIND_PICKUP or dropoff_stop.kind != RouteStop.KIND_DROPOFF:
            raise ValidationError({"stop": "transport_stop_kind_not_valid_for_passenger"})
        if _route_passenger_conflict(connection=connection, route=route):
            raise ValidationError({"connection": "connection_reserved_or_published_on_other_route"})
        if TransportPassengerAssignment.objects.filter(route=route, connection=connection).exists():
            raise ValidationError({"connection": "connection_already_in_route"})
        passenger = TransportPassengerAssignment.objects.create(
            route=route,
            connection=connection,
            pickup_stop=pickup_stop,
            dropoff_stop=dropoff_stop,
            boarding_order=boarding_order,
        )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="transport.passenger_added",
            target=passenger,
            details={"route": str(route.public_id), "connection": str(connection.public_id)},
        )
    return passenger


def _driver_vehicle_conflict(*, assignment):
    vehicle_conflict = _period_overlaps(
        DriverVehicleAssignment.objects.select_for_update()
        .filter(vehicle=assignment.vehicle, state=DriverVehicleAssignment.STATE_PUBLISHED)
        .exclude(pk=assignment.pk),
        starts_field="starts_on",
        ends_field="ends_on",
        starts_at=assignment.starts_on,
        ends_at=assignment.ends_on,
    ).exists()
    driver_conflict = _period_overlaps(
        DriverVehicleAssignment.objects.select_for_update()
        .filter(driver_connection=assignment.driver_connection, state=DriverVehicleAssignment.STATE_PUBLISHED)
        .exclude(pk=assignment.pk),
        starts_field="starts_on",
        ends_field="ends_on",
        starts_at=assignment.starts_on,
        ends_at=assignment.ends_on,
    ).exists()
    return vehicle_conflict or driver_conflict


def publish_transport_route(*, actor, route):
    organization = route.organization
    require_permission(user=actor, organization=organization, permission_code=TRANSPORT_MANAGE)
    with transaction.atomic():
        route = (
            TransportRoute.objects.select_for_update()
            .select_related("organization", "worksite", "driver_vehicle_assignment__vehicle", "driver_vehicle_assignment__driver_connection__candidate")
            .prefetch_related("stops", "passenger_assignments__connection__candidate")
            .get(pk=route.pk)
        )
        if route.state != TransportRoute.STATE_DRAFT:
            raise ValidationError({"route": "transport_route_not_draft"})
        if route.reservation_expires_at is None or route.reservation_expires_at <= timezone.now():
            raise ValidationError({"route": "transport_route_reservation_expired"})
        driver_assignment = DriverVehicleAssignment.objects.select_for_update().select_related("vehicle", "driver_connection__candidate").get(pk=route.driver_vehicle_assignment_id)
        _require_connection_for_operations(connection=driver_assignment.driver_connection, organization=organization)
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=driver_assignment.driver_connection,
        )
        if driver_assignment.state == DriverVehicleAssignment.STATE_CANCELLED:
            raise ValidationError({"driver_vehicle_assignment": "driver_vehicle_assignment_cancelled"})
        if not driver_assignment.vehicle.is_active:
            raise ValidationError({"vehicle": "vehicle_not_available"})
        if _driver_vehicle_conflict(assignment=driver_assignment):
            raise ValidationError({"route": "vehicle_or_driver_has_published_assignment_conflict"})
        if TransportRoute.objects.select_for_update().filter(
            driver_vehicle_assignment=driver_assignment,
            state=TransportRoute.STATE_PUBLISHED,
        ).exclude(pk=route.pk).exists():
            raise ValidationError({"route": "driver_vehicle_assignment_already_has_published_route"})
        stops = list(route.stops.all())
        pickup_stop_ids = {
            item.id for item in stops if item.kind == RouteStop.KIND_PICKUP
        }
        dropoff_stop_ids = {
            item.id for item in stops if item.kind == RouteStop.KIND_DROPOFF
        }
        if not pickup_stop_ids or not dropoff_stop_ids:
            raise ValidationError({"route": "transport_route_requires_pickup_and_dropoff"})
        passengers = list(
            TransportPassengerAssignment.objects.select_for_update()
            .select_related("connection__candidate", "pickup_stop", "dropoff_stop")
            .filter(route=route)
            .order_by("boarding_order", "id")
        )
        if len(passengers) > driver_assignment.vehicle.seat_capacity - 1:
            raise ValidationError({"route": "vehicle_passenger_capacity_exceeded"})
        for passenger in passengers:
            _require_connection_for_operations(connection=passenger.connection, organization=organization)
            require_worker_connection_access(
                user=actor, organization=organization, connection=passenger.connection
            )
            if _connection_has_driver_assignment_for_route(
                connection=passenger.connection,
                route=route,
            ):
                raise ValidationError({"route": "passenger_has_driver_assignment_conflict"})
            if (
                passenger.pickup_stop_id not in pickup_stop_ids
                or passenger.dropoff_stop_id not in dropoff_stop_ids
            ):
                raise ValidationError({"route": "transport_passenger_stop_not_in_route"})
            published_conflict = _period_overlaps(
                TransportPassengerAssignment.objects.select_for_update()
                .filter(connection=passenger.connection, route__state=TransportRoute.STATE_PUBLISHED)
                .exclude(route=route),
                starts_field="route__starts_on",
                ends_field="route__ends_on",
                starts_at=route.starts_on,
                ends_at=route.ends_on,
            ).exists()
            if published_conflict:
                raise ValidationError({"route": "passenger_has_published_route_conflict"})
        now = timezone.now()
        if driver_assignment.state == DriverVehicleAssignment.STATE_DRAFT:
            driver_assignment.state = DriverVehicleAssignment.STATE_PUBLISHED
            driver_assignment.published_by = actor
            driver_assignment.published_at = now
            driver_assignment.save(update_fields=["state", "published_by", "published_at", "updated_at"])
        route.state = TransportRoute.STATE_PUBLISHED
        route.published_by = actor
        route.published_at = now
        route.save(update_fields=["state", "published_by", "published_at", "updated_at"])
        record_audit_event(
            organization=organization,
            actor=actor,
            action="transport.route_published",
            target=route,
            details={
                "driver_vehicle_assignment": str(driver_assignment.public_id),
                "passenger_count": len(passengers),
            },
        )
        recipients = {driver_assignment.driver_connection.candidate_id: driver_assignment.driver_connection.candidate}
        recipients.update({item.connection.candidate_id: item.connection.candidate for item in passengers})
        for recipient in recipients.values():
            enqueue_support_notification(
                organization=organization,
                recipient=recipient,
                notification_code="transport.route_published",
                target_kind="transport_route",
                target_public_id=route.public_id,
                target_key=f"support:transport-route:{route.public_id}",
                collapse_key=f"support:transport:{recipient.id}",
                dedupe_key=f"transport.route.published:{route.public_id}:{recipient.id}:{now.isoformat()}",
            )
    return route


def cancel_transport_route(*, actor, route):
    """Cancel a route and its published driver assignment as one operation."""

    organization = route.organization
    require_permission(user=actor, organization=organization, permission_code=TRANSPORT_MANAGE)
    with transaction.atomic():
        route = (
            TransportRoute.objects.select_for_update()
            .select_related(
                "organization",
                "driver_vehicle_assignment__driver_connection__candidate",
            )
            .prefetch_related("passenger_assignments__connection__candidate")
            .get(pk=route.pk)
        )
        if route.state == TransportRoute.STATE_CANCELLED:
            raise ValidationError({"route": "transport_route_already_cancelled"})
        was_published = route.state == TransportRoute.STATE_PUBLISHED
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=route.driver_vehicle_assignment.driver_connection,
        )
        now = timezone.now()
        route.state = TransportRoute.STATE_CANCELLED
        route.cancelled_at = now
        route.save(update_fields=["state", "cancelled_at", "updated_at"])
        driver_assignment = DriverVehicleAssignment.objects.select_for_update().get(
            pk=route.driver_vehicle_assignment_id
        )
        if was_published and driver_assignment.state == DriverVehicleAssignment.STATE_PUBLISHED:
            driver_assignment.state = DriverVehicleAssignment.STATE_CANCELLED
            driver_assignment.cancelled_at = now
            driver_assignment.save(update_fields=["state", "cancelled_at", "updated_at"])
        record_audit_event(
            organization=organization,
            actor=actor,
            action="transport.route_cancelled",
            target=route,
            details={
                "driver_vehicle_assignment": str(driver_assignment.public_id),
                "was_published": was_published,
            },
        )
        if was_published:
            for passenger in route.passenger_assignments.all():
                require_worker_connection_access(
                    user=actor,
                    organization=organization,
                    connection=passenger.connection,
                )
            recipients = {
                route.driver_vehicle_assignment.driver_connection.candidate_id:
                route.driver_vehicle_assignment.driver_connection.candidate
            }
            recipients.update(
                {
                    item.connection.candidate_id: item.connection.candidate
                    for item in route.passenger_assignments.all()
                }
            )
            for recipient in recipients.values():
                enqueue_support_notification(
                    organization=organization,
                    recipient=recipient,
                    notification_code="transport.route_published",
                    target_kind="transport_route",
                    target_public_id=route.public_id,
                    target_key=f"support:transport-route:{route.public_id}",
                    collapse_key=f"support:transport:{recipient.id}",
                    dedupe_key=f"transport.route.cancelled:{route.public_id}:{recipient.id}:{now.isoformat()}",
                )
    return route


def delete_transport_route_draft(*, actor, route):
    """Remove a transport draft and its draft-only stops and passengers."""

    organization = route.organization
    require_permission(user=actor, organization=organization, permission_code=TRANSPORT_MANAGE)
    with transaction.atomic():
        route = (
            TransportRoute.objects.select_for_update()
            .select_related("driver_vehicle_assignment__driver_connection")
            .prefetch_related("passenger_assignments__connection")
            .get(pk=route.pk)
        )
        if route.state != TransportRoute.STATE_DRAFT:
            raise ValidationError({"route": "transport_route_not_draft"})
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=route.driver_vehicle_assignment.driver_connection,
        )
        for passenger in route.passenger_assignments.all():
            require_worker_connection_access(
                user=actor, organization=organization, connection=passenger.connection
            )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="transport.route_draft_deleted",
            target=route,
            details={"driver_vehicle_assignment": str(route.driver_vehicle_assignment.public_id)},
        )
        route.delete()
