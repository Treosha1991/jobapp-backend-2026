"""Transactional creation and publication of Support operational assignments."""

import uuid
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from support.models import (
    DriverVehicleAssignment,
    HousingAssignment,
    HousingPlace,
    ProjectScheduleTemplate,
    RouteStop,
    ScheduledWorkShift,
    SupportConnection,
    TransportCrew,
    TransportCrewDriver,
    TransportCrewMember,
    TransportCrewScheduleOverride,
    TransportCrewVehicle,
    TransportPassengerAssignment,
    TransportRoute,
    Vehicle,
    WorkerProjectAssignment,
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


def _close_open_crew_membership(*, crew, connection, effective_on):
    """Close an effective-dated membership without creating an invalid period."""

    previous_date = effective_on - timedelta(days=1)
    rows = list(
        TransportCrewMember.objects.select_for_update().filter(
            crew=crew,
            connection=connection,
            ends_on__isnull=True,
        )
    )
    for row in rows:
        if row.starts_on >= effective_on:
            row.delete()
        else:
            row.ends_on = previous_date
            row.save(update_fields=["ends_on", "updated_at"])


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
    starts_at=None,
    ends_at=None,
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
        starts_at = starts_at or timezone.now()
        assignment = WorkerProjectAssignment.objects.create(
            organization=organization,
            connection=connection,
            project=project,
            worker_role=(worker_role or "").strip(),
            starts_at=starts_at,
            ends_at=ends_at,
            created_by=actor,
        )
        record_audit_event(
            organization=organization,
            actor=actor,
            action="work.assignment_drafted",
            target=assignment,
            details={
                "connection": str(connection.public_id),
                "project": str(project.public_id),
            },
        )
    return assignment


def publish_worker_project_assignment(*, actor, assignment, replace_conflicting_assignments=False):
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
        conflicting_assignments = _period_overlaps(
            WorkerProjectAssignment.objects.select_for_update()
            .filter(
                connection=assignment.connection,
                state=WorkerProjectAssignment.STATE_PUBLISHED,
            )
            .exclude(pk=assignment.pk),
            starts_field="starts_at",
            ends_field="ends_at",
            starts_at=assignment.starts_at,
            ends_at=assignment.ends_at,
        )
        if conflicting_assignments.exists() and not replace_conflicting_assignments:
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
        if replace_conflicting_assignments:
            for previous_assignment in conflicting_assignments:
                previous_assignment.state = WorkerProjectAssignment.STATE_CANCELLED
                previous_assignment.cancelled_at = published_at
                previous_assignment.save(update_fields=["state", "cancelled_at", "updated_at"])
                record_audit_event(
                    organization=organization,
                    actor=actor,
                    action="work.assignment_replaced",
                    target=previous_assignment,
                    details={"replacement_assignment": str(assignment.public_id)},
                )
        assignment.state = WorkerProjectAssignment.STATE_PUBLISHED
        assignment.published_by = actor
        assignment.published_at = published_at
        assignment.save(update_fields=["state", "published_by", "published_at", "updated_at"])
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


def publish_driver_vehicle_assignment(
    *, actor, assignment, excluded_passenger_public_ids=()
):
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
        driver_replacement_assignments = list(
            _period_overlaps(
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
            )
        )
        driver_routes_to_transfer = list(
            _period_overlaps(
                TransportRoute.objects.select_for_update()
                .filter(
                    driver_vehicle_assignment__driver_connection=assignment.driver_connection,
                    state__in=(TransportRoute.STATE_DRAFT, TransportRoute.STATE_PUBLISHED),
                )
                .exclude(driver_vehicle_assignment=assignment),
                starts_field="starts_on",
                ends_field="ends_on",
                starts_at=assignment.starts_on,
                ends_at=assignment.ends_on,
            )
        )
        excluded_ids = {str(item) for item in excluded_passenger_public_ids if item}
        transferable_passengers = list(
            TransportPassengerAssignment.objects.select_for_update()
            .select_related("connection__candidate", "route")
            .filter(route__in=driver_routes_to_transfer)
            .order_by("route_id", "boarding_order", "id")
        )
        transferable_ids = {str(item.public_id) for item in transferable_passengers}
        if excluded_ids - transferable_ids:
            raise ValidationError(
                {"passengers": "driver_crew_excluded_passenger_not_found"}
            )
        passengers_to_exclude = [
            item for item in transferable_passengers if str(item.public_id) in excluded_ids
        ]
        available_passenger_seats = assignment.vehicle.seat_capacity - 1
        remaining_by_route = {
            route.id: sum(
                1
                for passenger in transferable_passengers
                if passenger.route_id == route.id
                and str(passenger.public_id) not in excluded_ids
            )
            for route in driver_routes_to_transfer
        }
        required_exclusions = max(
            (
                max(0, passenger_count - available_passenger_seats)
                for passenger_count in remaining_by_route.values()
            ),
            default=0,
        )
        if required_exclusions:
            raise ValidationError(
                {
                    "passengers": "driver_crew_capacity_exceeded",
                    "required_exclusions": required_exclusions,
                }
            )
        now = timezone.now()
        for passenger in passengers_to_exclude:
            route_public_id = str(passenger.route.public_id)
            connection_public_id = str(passenger.connection.public_id)
            record_audit_event(
                organization=organization,
                actor=actor,
                action="transport.passenger_excluded_for_vehicle_change",
                target=passenger,
                details={
                    "route": route_public_id,
                    "connection": connection_public_id,
                    "replacement_assignment": str(assignment.public_id),
                    "new_vehicle": str(assignment.vehicle.public_id),
                },
            )
            passenger.delete()

        # The crew belongs to the driver: active routes, stops and remaining
        # passengers move together to the driver's new vehicle assignment.
        for route in driver_routes_to_transfer:
            route.driver_vehicle_assignment = assignment
            route.save(update_fields=["driver_vehicle_assignment", "updated_at"])

        for previous_assignment in driver_replacement_assignments:
            previous_assignment.state = DriverVehicleAssignment.STATE_CANCELLED
            previous_assignment.cancelled_at = now
            previous_assignment.ends_on = None
            previous_assignment.save(
                update_fields=["state", "cancelled_at", "ends_on", "updated_at"]
            )
            record_audit_event(
                organization=organization,
                actor=actor,
                action="transport.driver_moved_to_another_vehicle",
                target=previous_assignment,
                details={
                    "replacement_assignment": str(assignment.public_id),
                    "previous_vehicle": str(previous_assignment.vehicle.public_id),
                    "new_vehicle": str(assignment.vehicle.public_id),
                    "transferred_route_count": len(driver_routes_to_transfer),
                    "excluded_passenger_count": len(passengers_to_exclude),
                },
            )
        # Publishing a new draft for the same car deliberately replaces its
        # previous driver. Driver appointments are open-ended: their history
        # is defined by the next appointment, not by a manually entered end.
        for previous_assignment in replacement_assignments:
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
                    "crew_retained_with_previous_driver": True,
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


def ensure_transport_crew_for_route(*, actor, route):
    """Mirror a legacy route into the stable crew records exactly once."""

    organization = route.organization
    require_permission(
        user=actor,
        organization=organization,
        permission_code=TRANSPORT_MANAGE,
    )
    with transaction.atomic():
        route = (
            TransportRoute.objects.select_for_update()
            .select_related(
                "schedule_template__project",
                "driver_vehicle_assignment__driver_connection",
                "driver_vehicle_assignment__vehicle",
                "crew",
            )
            .prefetch_related("passenger_assignments")
            .get(pk=route.pk)
        )
        if route.schedule_template is None:
            raise ValidationError({"route": "transport_schedule_template_required"})
        if route.crew_id is not None:
            return route.crew

        assignment = route.driver_vehicle_assignment
        crew = TransportCrew.objects.create(
            organization=organization,
            project=route.schedule_template.project,
            schedule_template=route.schedule_template,
            internal_name=route.internal_name,
            starts_on=route.starts_on,
            ends_on=route.ends_on,
            state=(
                TransportCrew.STATE_ARCHIVED
                if route.state == TransportRoute.STATE_CANCELLED
                else TransportCrew.STATE_ACTIVE
            ),
            created_by=actor,
        )
        TransportCrewDriver.objects.create(
            crew=crew,
            driver_connection=assignment.driver_connection,
            starts_on=route.starts_on,
            ends_on=route.ends_on,
            created_by=actor,
        )
        TransportCrewVehicle.objects.create(
            crew=crew,
            vehicle=assignment.vehicle,
            starts_on=route.starts_on,
            ends_on=route.ends_on,
            created_by=actor,
        )
        members = [
            TransportCrewMember(
                crew=crew,
                connection=passenger.connection,
                starts_on=route.starts_on,
                ends_on=route.ends_on,
                boarding_order=passenger.boarding_order,
                created_by=actor,
            )
            for passenger in route.passenger_assignments.all()
        ]
        TransportCrewMember.objects.bulk_create(members)
        route.crew = crew
        route.save(update_fields=["crew", "updated_at"])

        participant_ids = [assignment.driver_connection_id] + [
            member.connection_id for member in members
        ]
        ScheduledWorkShift.objects.filter(
            organization=organization,
            connection_id__in=participant_ids,
            schedule_template=route.schedule_template,
            crew__isnull=True,
            work_date__gte=route.starts_on,
            state__in=(
                ScheduledWorkShift.STATE_DRAFT,
                ScheduledWorkShift.STATE_PUBLISHED,
            ),
        ).filter(
            Q(work_date__lte=route.ends_on)
            if route.ends_on is not None
            else Q()
        ).update(crew=crew)
        record_audit_event(
            organization=organization,
            actor=actor,
            action="transport.crew_materialized",
            target=crew,
            details={"route": str(route.public_id)},
        )
    return crew


def replace_transport_crew_driver(*, actor, route, replacement_connection):
    """Replace a selected crew's driver without rebuilding its route or passengers.

    The legacy route still points at a driver/vehicle assignment, while the
    stable crew stores effective-dated driver history.  Both records are
    updated in one transaction so the employer never sees a half-switched
    crew.  A passenger promoted to driver swaps places with the previous
    driver; otherwise the existing passenger list is left untouched.
    """

    organization = route.organization
    require_permission(
        user=actor,
        organization=organization,
        permission_code=TRANSPORT_MANAGE,
    )
    require_permission(
        user=actor,
        organization=organization,
        permission_code=SCHEDULE_MANAGE,
    )
    require_worker_connection_access(
        user=actor,
        organization=organization,
        connection=replacement_connection,
    )
    _require_connection_for_operations(
        connection=replacement_connection,
        organization=organization,
    )
    if not replacement_connection.has_driving_license:
        raise ValidationError({"driver": "driver_license_required"})

    crew = ensure_transport_crew_for_route(actor=actor, route=route)
    effective_on = max(timezone.localdate(), route.starts_on)
    with transaction.atomic():
        route = (
            TransportRoute.objects.select_for_update()
            .select_related(
                "driver_vehicle_assignment__driver_connection",
                "driver_vehicle_assignment__vehicle",
                "schedule_template__project",
                "crew",
            )
            .prefetch_related("passenger_assignments")
            .get(pk=route.pk)
        )
        crew = TransportCrew.objects.select_for_update().get(pk=crew.pk)
        old_assignment = DriverVehicleAssignment.objects.select_for_update().get(
            pk=route.driver_vehicle_assignment_id
        )
        old_driver = old_assignment.driver_connection
        if old_driver.id == replacement_connection.id:
            raise ValidationError({"driver": "transport_crew_driver_unchanged"})

        replacement_is_passenger = (
            TransportPassengerAssignment.objects.select_for_update()
            .filter(route=route, connection=replacement_connection)
            .first()
        )
        if replacement_is_passenger is None:
            replacement_busy = (
                DriverVehicleAssignment.objects.select_for_update()
                .filter(
                    organization=organization,
                    driver_connection=replacement_connection,
                    state=DriverVehicleAssignment.STATE_PUBLISHED,
                    starts_on__lte=effective_on,
                )
                .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=effective_on))
                .exists()
            )
            if replacement_busy:
                raise ValidationError({"driver": "transport_crew_driver_busy"})

        new_assignment = DriverVehicleAssignment.objects.create(
            organization=organization,
            driver_connection=replacement_connection,
            vehicle=old_assignment.vehicle,
            starts_on=effective_on,
            ends_on=route.ends_on,
            state=DriverVehicleAssignment.STATE_PUBLISHED,
            created_by=actor,
            published_by=actor,
            published_at=timezone.now(),
        )
        route.driver_vehicle_assignment = new_assignment
        route.save(update_fields=["driver_vehicle_assignment", "updated_at"])

        open_driver_rows = list(
            TransportCrewDriver.objects.select_for_update().filter(
                crew=crew,
                ends_on__isnull=True,
            )
        )
        previous_date = effective_on - timedelta(days=1)
        for row in open_driver_rows:
            if row.starts_on >= effective_on:
                row.delete()
            else:
                row.ends_on = previous_date
                row.save(update_fields=["ends_on", "updated_at"])
        TransportCrewDriver.objects.create(
            crew=crew,
            driver_connection=replacement_connection,
            starts_on=effective_on,
            ends_on=crew.ends_on,
            created_by=actor,
        )

        old_driver_shift_rows = list(
            ScheduledWorkShift.objects.select_for_update()
            .filter(
                organization=organization,
                connection=old_driver,
                crew=crew,
                work_date__gte=effective_on,
                state__in=(
                    ScheduledWorkShift.STATE_DRAFT,
                    ScheduledWorkShift.STATE_PUBLISHED,
                ),
            )
            .order_by("work_date", "starts_at", "id")
        )
        if replacement_is_passenger is not None:
            replacement_is_passenger.connection = old_driver
            replacement_is_passenger.save(update_fields=["connection", "updated_at"])
            _close_open_crew_membership(
                crew=crew,
                connection=replacement_connection,
                effective_on=effective_on,
            )
            if not TransportCrewMember.objects.filter(
                crew=crew,
                connection=old_driver,
                ends_on__isnull=True,
            ).exists():
                TransportCrewMember.objects.create(
                    crew=crew,
                    connection=old_driver,
                    starts_on=effective_on,
                    boarding_order=replacement_is_passenger.boarding_order,
                    created_by=actor,
                )
        else:
            from .timekeeping import create_scheduled_shift, publish_scheduled_shift

            work_assignment = _published_assignment_for_crew_member(
                actor=actor,
                crew=crew,
                connection=replacement_connection,
                starts_at=(
                    old_driver_shift_rows[0].starts_at
                    if old_driver_shift_rows
                    else timezone.now()
                ),
            )
            for source_shift in old_driver_shift_rows:
                existing = (
                    ScheduledWorkShift.objects.select_for_update()
                    .filter(
                        organization=organization,
                        connection=replacement_connection,
                        crew=crew,
                        work_date=source_shift.work_date,
                        state__in=(
                            ScheduledWorkShift.STATE_DRAFT,
                            ScheduledWorkShift.STATE_PUBLISHED,
                        ),
                    )
                    .first()
                )
                if existing is not None:
                    continue
                cloned = create_scheduled_shift(
                    actor=actor,
                    organization=organization,
                    connection=replacement_connection,
                    work_date=source_shift.work_date,
                    starts_at=source_shift.starts_at,
                    ends_at=source_shift.ends_at,
                    break_minutes=source_shift.break_minutes,
                    worker_label=source_shift.worker_label,
                    work_assignment=work_assignment,
                    schedule_template=route.schedule_template,
                    crew=crew,
                )
                publish_scheduled_shift(actor=actor, shift=cloned)
            ScheduledWorkShift.objects.filter(
                pk__in=[item.pk for item in old_driver_shift_rows]
            ).update(crew=None)

        if not TransportRoute.objects.filter(
            driver_vehicle_assignment=old_assignment,
            state__in=(TransportRoute.STATE_DRAFT, TransportRoute.STATE_PUBLISHED),
        ).exists():
            old_assignment.state = DriverVehicleAssignment.STATE_CANCELLED
            old_assignment.cancelled_at = timezone.now()
            if old_assignment.ends_on is None or old_assignment.ends_on >= effective_on:
                old_assignment.ends_on = max(old_assignment.starts_on, previous_date)
            old_assignment.save(
                update_fields=["state", "cancelled_at", "ends_on", "updated_at"]
            )

        record_audit_event(
            organization=organization,
            actor=actor,
            action="transport.crew_driver_replaced",
            target=crew,
            details={
                "route": str(route.public_id),
                "previous_driver": str(old_driver.public_id),
                "replacement_driver": str(replacement_connection.public_id),
                "vehicle": str(old_assignment.vehicle.public_id),
                "passenger_swap": replacement_is_passenger is not None,
            },
        )
    return route


def _published_assignment_for_crew_member(
    *,
    actor,
    crew,
    connection,
    starts_at,
):
    assignment = (
        WorkerProjectAssignment.objects.select_for_update()
        .filter(
            organization=crew.organization,
            connection=connection,
            project=crew.project,
            state=WorkerProjectAssignment.STATE_PUBLISHED,
        )
        .order_by("-starts_at", "-id")
        .first()
    )
    if assignment is not None:
        return assignment
    draft = (
        WorkerProjectAssignment.objects.select_for_update()
        .filter(
            organization=crew.organization,
            connection=connection,
            project=crew.project,
            state=WorkerProjectAssignment.STATE_DRAFT,
        )
        .order_by("-starts_at", "-id")
        .first()
    )
    if draft is None:
        draft = create_worker_project_assignment(
            actor=actor,
            organization=crew.organization,
            connection=connection,
            project=crew.project,
            worker_role="",
            starts_at=starts_at,
        )
    return publish_worker_project_assignment(
        actor=actor,
        assignment=draft,
        replace_conflicting_assignments=True,
    )


def apply_transport_crew_schedule_override(
    *,
    actor,
    crew,
    work_dates,
    kind,
    starts_at_time=None,
    ends_at_time=None,
    break_minutes=0,
    note="",
):
    """Publish one schedule exception to the driver and current passengers.

    Another crew's shift is intentionally not removed. Both records remain
    visible as a conflict until a manager chooses which crew membership to
    keep for that worker.
    """

    organization = crew.organization
    require_permission(
        user=actor,
        organization=organization,
        permission_code=TRANSPORT_MANAGE,
    )
    require_permission(
        user=actor,
        organization=organization,
        permission_code=SCHEDULE_MANAGE,
    )
    allowed_kinds = {
        TransportCrewScheduleOverride.KIND_SHIFT,
        TransportCrewScheduleOverride.KIND_DAY_OFF,
        TransportCrewScheduleOverride.KIND_CANCELLED,
    }
    if kind not in allowed_kinds:
        raise ValidationError({"kind": "transport_crew_override_kind_invalid"})
    if kind == TransportCrewScheduleOverride.KIND_SHIFT:
        if starts_at_time is None or ends_at_time is None:
            raise ValidationError({"time": "transport_crew_override_time_required"})
    elif starts_at_time is not None or ends_at_time is not None:
        raise ValidationError({"time": "transport_crew_override_time_not_allowed"})
    work_dates = sorted(set(work_dates))
    if not work_dates:
        raise ValidationError({"work_dates": "schedule_dates_required"})

    from .timekeeping import (
        cancel_scheduled_shift,
        create_scheduled_shift,
        delete_scheduled_shift_draft,
        publish_scheduled_shift,
        replace_scheduled_shift,
    )

    conflict_rows = []
    with transaction.atomic():
        crew = (
            TransportCrew.objects.select_for_update()
            .select_related("organization", "project", "schedule_template")
            .get(pk=crew.pk)
        )
        if crew.state != TransportCrew.STATE_ACTIVE:
            raise ValidationError({"crew": "transport_crew_not_active"})
        current_timezone = timezone.get_current_timezone()
        for work_date in work_dates:
            driver_rows = list(
                TransportCrewDriver.objects.select_for_update().filter(
                    crew=crew,
                    starts_on__lte=work_date,
                ).filter(Q(ends_on__isnull=True) | Q(ends_on__gte=work_date))
            )
            member_rows = list(
                TransportCrewMember.objects.select_for_update().filter(
                    crew=crew,
                    starts_on__lte=work_date,
                ).filter(Q(ends_on__isnull=True) | Q(ends_on__gte=work_date))
            )
            participants_by_id = {
                row.driver_connection_id: row.driver_connection for row in driver_rows
            }
            participants_by_id.update(
                {row.connection_id: row.connection for row in member_rows}
            )
            participants = list(participants_by_id.values())
            if not participants:
                raise ValidationError({"crew": "transport_crew_has_no_participants"})

            override_defaults = {
                "kind": kind,
                "starts_at_time": (
                    starts_at_time
                    if kind == TransportCrewScheduleOverride.KIND_SHIFT
                    else None
                ),
                "ends_at_time": (
                    ends_at_time
                    if kind == TransportCrewScheduleOverride.KIND_SHIFT
                    else None
                ),
                "break_minutes": (
                    break_minutes
                    if kind == TransportCrewScheduleOverride.KIND_SHIFT
                    else 0
                ),
                "note": (note or "").strip(),
                "updated_by": actor,
            }
            override, created = TransportCrewScheduleOverride.objects.update_or_create(
                crew=crew,
                work_date=work_date,
                defaults=override_defaults,
            )
            if created and override.created_by_id is None:
                override.created_by = actor
                override.save(update_fields=["created_by", "updated_at"])

            for connection in participants:
                require_worker_connection_access(
                    user=actor,
                    organization=organization,
                    connection=connection,
                )
                current_shift = (
                    ScheduledWorkShift.objects.select_for_update()
                    .filter(
                        organization=organization,
                        connection=connection,
                        crew=crew,
                        work_date=work_date,
                        state__in=(
                            ScheduledWorkShift.STATE_DRAFT,
                            ScheduledWorkShift.STATE_PUBLISHED,
                        ),
                    )
                    .order_by("-published_at", "-created_at", "-id")
                    .first()
                )
                if kind != TransportCrewScheduleOverride.KIND_SHIFT:
                    if current_shift is not None:
                        if current_shift.state == ScheduledWorkShift.STATE_DRAFT:
                            delete_scheduled_shift_draft(actor=actor, shift=current_shift)
                        else:
                            cancel_scheduled_shift(actor=actor, shift=current_shift)
                    continue

                starts_at = timezone.make_aware(
                    datetime.combine(work_date, starts_at_time),
                    current_timezone,
                )
                ends_at = timezone.make_aware(
                    datetime.combine(work_date, ends_at_time),
                    current_timezone,
                )
                if ends_at <= starts_at:
                    ends_at += timedelta(days=1)
                work_assignment = _published_assignment_for_crew_member(
                    actor=actor,
                    crew=crew,
                    connection=connection,
                    starts_at=starts_at,
                )
                if current_shift is not None:
                    replace_scheduled_shift(
                        actor=actor,
                        shift=current_shift,
                        work_date=work_date,
                        starts_at=starts_at,
                        ends_at=ends_at,
                        break_minutes=break_minutes,
                        worker_label=(note or "").strip(),
                        work_assignment=work_assignment,
                        replacement_state=ScheduledWorkShift.STATE_PUBLISHED,
                        schedule_template=crew.schedule_template,
                        crew=crew,
                    )
                else:
                    shift = create_scheduled_shift(
                        actor=actor,
                        organization=organization,
                        connection=connection,
                        work_date=work_date,
                        starts_at=starts_at,
                        ends_at=ends_at,
                        break_minutes=break_minutes,
                        worker_label=(note or "").strip(),
                        work_assignment=work_assignment,
                        schedule_template=crew.schedule_template,
                        crew=crew,
                    )
                    publish_scheduled_shift(actor=actor, shift=shift)

                overlapping = ScheduledWorkShift.objects.filter(
                    organization=organization,
                    connection=connection,
                    work_date=work_date,
                    state__in=(
                        ScheduledWorkShift.STATE_DRAFT,
                        ScheduledWorkShift.STATE_PUBLISHED,
                    ),
                ).exclude(crew=crew).filter(
                    starts_at__lt=ends_at,
                    ends_at__gt=starts_at,
                )
                if overlapping.exists():
                    conflict_rows.append(
                        {
                            "connection": connection,
                            "work_date": work_date,
                            "other_shift_count": overlapping.count(),
                        }
                    )

        record_audit_event(
            organization=organization,
            actor=actor,
            action="transport.crew_schedule_overridden",
            target=crew,
            details={
                "kind": kind,
                "work_dates": [item.isoformat() for item in work_dates],
                "participant_count": len(participants),
                "conflict_count": len(conflict_rows),
            },
        )
    return conflict_rows


def resolve_transport_crew_schedule_conflict(*, actor, keep_shift):
    """Keep one shift and detach the worker from every conflicting crew."""

    organization = keep_shift.organization
    require_permission(
        user=actor,
        organization=organization,
        permission_code=TRANSPORT_MANAGE,
    )
    require_permission(
        user=actor,
        organization=organization,
        permission_code=SCHEDULE_MANAGE,
    )
    from .timekeeping import cancel_scheduled_shift, delete_scheduled_shift_draft

    with transaction.atomic():
        keep_shift = (
            ScheduledWorkShift.objects.select_for_update()
            .select_related("connection", "crew")
            .get(pk=keep_shift.pk)
        )
        if keep_shift.state not in {
            ScheduledWorkShift.STATE_DRAFT,
            ScheduledWorkShift.STATE_PUBLISHED,
        }:
            raise ValidationError({"shift": "scheduled_shift_not_current"})
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=keep_shift.connection,
        )
        conflicts = list(
            ScheduledWorkShift.objects.select_for_update()
            .select_related("crew")
            .filter(
                organization=organization,
                connection=keep_shift.connection,
                work_date=keep_shift.work_date,
                state__in=(
                    ScheduledWorkShift.STATE_DRAFT,
                    ScheduledWorkShift.STATE_PUBLISHED,
                ),
                starts_at__lt=keep_shift.ends_at,
                ends_at__gt=keep_shift.starts_at,
            )
            .exclude(pk=keep_shift.pk)
        )
        if not conflicts:
            raise ValidationError({"shift": "scheduled_shift_conflict_not_found"})

        detached_crews = []
        for conflict in conflicts:
            if conflict.crew_id is not None:
                driver_rows = TransportCrewDriver.objects.select_for_update().filter(
                    crew=conflict.crew,
                    driver_connection=keep_shift.connection,
                    starts_on__lte=keep_shift.work_date,
                ).filter(
                    Q(ends_on__isnull=True) | Q(ends_on__gte=keep_shift.work_date)
                )
                member_rows = TransportCrewMember.objects.select_for_update().filter(
                    crew=conflict.crew,
                    connection=keep_shift.connection,
                    starts_on__lte=keep_shift.work_date,
                ).filter(
                    Q(ends_on__isnull=True) | Q(ends_on__gte=keep_shift.work_date)
                )
                previous_date = keep_shift.work_date - timedelta(days=1)
                for row in driver_rows:
                    if row.starts_on >= keep_shift.work_date:
                        row.delete()
                    else:
                        row.ends_on = previous_date
                        row.save(update_fields=["ends_on", "updated_at"])
                for row in member_rows:
                    if row.starts_on >= keep_shift.work_date:
                        row.delete()
                    else:
                        row.ends_on = previous_date
                        row.save(update_fields=["ends_on", "updated_at"])
                detached_crews.append(str(conflict.crew.public_id))
            if conflict.state == ScheduledWorkShift.STATE_DRAFT:
                delete_scheduled_shift_draft(actor=actor, shift=conflict)
            else:
                cancel_scheduled_shift(actor=actor, shift=conflict)

        record_audit_event(
            organization=organization,
            actor=actor,
            action="transport.crew_schedule_conflict_resolved",
            target=keep_shift,
            details={
                "cancelled_shift_count": len(conflicts),
                "detached_crews": detached_crews,
            },
        )
    return keep_shift


def create_transport_crew_for_schedule(
    *,
    actor,
    organization,
    driver_vehicle_assignment,
    schedule_template,
):
    """Create and publish an empty crew for one driver, vehicle and template.

    The driver may travel alone.  Stops and passengers are generated later,
    when a passenger is added from the worker or project workspace.
    """

    require_permission(
        user=actor,
        organization=organization,
        permission_code=TRANSPORT_MANAGE,
    )
    require_permission(
        user=actor,
        organization=organization,
        permission_code=SCHEDULE_MANAGE,
    )
    with transaction.atomic():
        assignment = (
            DriverVehicleAssignment.objects.select_for_update()
            .select_related("driver_connection__candidate", "vehicle")
            .get(pk=driver_vehicle_assignment.pk)
        )
        template = (
            ProjectScheduleTemplate.objects.select_for_update()
            .select_related("project__worksite")
            .get(pk=schedule_template.pk)
        )
        _require_same_organization(
            organization=organization,
            driver_assignment=assignment,
        )
        _require_connection_for_operations(
            connection=assignment.driver_connection,
            organization=organization,
        )
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=assignment.driver_connection,
        )
        if not assignment.driver_connection.has_driving_license:
            raise ValidationError({"driver": "driver_license_required"})
        if assignment.state != DriverVehicleAssignment.STATE_PUBLISHED:
            raise ValidationError(
                {"driver_vehicle_assignment": "driver_vehicle_assignment_required"}
            )
        if (
            template.project.organization_id != organization.id
            or not template.is_active
            or not template.project.is_active
            or not template.project.worksite.is_active
        ):
            raise ValidationError(
                {"schedule_template": "project_schedule_template_not_available"}
            )

        today = timezone.localdate()
        if assignment.starts_on > today or (
            assignment.ends_on is not None and assignment.ends_on < today
        ):
            raise ValidationError(
                {"driver_vehicle_assignment": "driver_vehicle_assignment_required"}
            )
        if TransportRoute.objects.select_for_update().filter(
            driver_vehicle_assignment=assignment,
            schedule_template=template,
            state__in=(
                TransportRoute.STATE_DRAFT,
                TransportRoute.STATE_PUBLISHED,
            ),
        ).exists():
            raise ValidationError({"crew": "transport_schedule_crew_already_exists"})

        driver_shifts = list(
            ScheduledWorkShift.objects.select_for_update()
            .filter(
                organization=organization,
                connection=assignment.driver_connection,
                schedule_template=template,
                state=ScheduledWorkShift.STATE_PUBLISHED,
                work_date__gte=today,
            )
            .order_by("work_date", "starts_at", "id")
        )
        if not driver_shifts:
            raise ValidationError(
                {"schedule_template": "driver_template_schedule_required"}
            )

        first_date = driver_shifts[0].work_date
        driver_name = (
            assignment.driver_connection.candidate.get_full_name().strip()
            or assignment.driver_connection.candidate.username
        )
        route = TransportRoute.objects.create(
            organization=organization,
            internal_name=(
                f"{template.project.internal_name} · {driver_name} · "
                f"{assignment.vehicle.registration_identifier} · {uuid.uuid4().hex[:6]}"
            )[:160],
            worksite=template.project.worksite,
            schedule_template=template,
            driver_vehicle_assignment=assignment,
            starts_on=first_date,
            ends_on=None,
            departure_time=template.starts_at_time,
            state=TransportRoute.STATE_PUBLISHED,
            published_by=actor,
            published_at=timezone.now(),
            created_by=actor,
        )
        crew = ensure_transport_crew_for_route(actor=actor, route=route)
        route.crew = crew
        record_audit_event(
            organization=organization,
            actor=actor,
            action="transport.schedule_empty_crew_created",
            target=crew,
            details={
                "route": str(route.public_id),
                "schedule_template": str(template.public_id),
                "driver_vehicle_assignment": str(assignment.public_id),
            },
        )
    return route


def add_passenger_to_driver_schedule(
    *,
    actor,
    driver_connection,
    schedule_template,
    passenger_connection,
):
    """Add a passenger to one driver's schedule and publish all related data.

    The crew, route, project assignment and copied shifts are one atomic
    operation.  A validation error rolls every write back, so the employer
    never gets a passenger without the matching work schedule.
    """

    organization = driver_connection.organization
    require_permission(
        user=actor,
        organization=organization,
        permission_code=TRANSPORT_MANAGE,
    )
    require_permission(
        user=actor,
        organization=organization,
        permission_code=SCHEDULE_MANAGE,
    )
    from .timekeeping import (
        cancel_scheduled_shift,
        create_scheduled_shift,
        delete_scheduled_shift_draft,
        publish_scheduled_shift,
        replace_scheduled_shift,
    )

    with transaction.atomic():
        driver_connection = SupportConnection.objects.select_for_update().get(
            pk=driver_connection.pk
        )
        passenger_connection = SupportConnection.objects.select_for_update().get(
            pk=passenger_connection.pk
        )
        schedule_template = (
            ProjectScheduleTemplate.objects.select_for_update()
            .select_related("project__worksite")
            .get(pk=schedule_template.pk)
        )
        project = schedule_template.project
        _require_connection_for_operations(
            connection=driver_connection,
            organization=organization,
        )
        _require_connection_for_operations(
            connection=passenger_connection,
            organization=organization,
        )
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=driver_connection,
        )
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=passenger_connection,
        )
        if passenger_connection.pk == driver_connection.pk:
            raise ValidationError({"connection": "driver_cannot_be_route_passenger"})
        if (
            project.organization_id != organization.id
            or not project.is_active
            or not project.worksite.is_active
            or not schedule_template.is_active
        ):
            raise ValidationError(
                {"schedule_template": "project_schedule_template_not_available"}
            )

        today = timezone.localdate()
        driver_assignment = (
            DriverVehicleAssignment.objects.select_for_update()
            .select_related("vehicle", "driver_connection__candidate")
            .filter(
                organization=organization,
                driver_connection=driver_connection,
                state=DriverVehicleAssignment.STATE_PUBLISHED,
                starts_on__lte=today,
            )
            .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=today))
            .order_by("-starts_on", "-id")
            .first()
        )
        if driver_assignment is None:
            raise ValidationError(
                {"driver_vehicle_assignment": "driver_vehicle_assignment_required"}
            )

        driver_shifts = list(
            ScheduledWorkShift.objects.select_for_update()
            .filter(
                organization=organization,
                connection=driver_connection,
                schedule_template=schedule_template,
                state=ScheduledWorkShift.STATE_PUBLISHED,
                work_date__gte=today,
            )
            # Do not join the nullable work_assignment while locking rows.
            # PostgreSQL rejects FOR UPDATE on the nullable side of an outer
            # join; the assignment is not used while building this crew.
            .order_by("work_date", "starts_at", "id")
        )
        if not driver_shifts:
            raise ValidationError(
                {"schedule_template": "driver_template_schedule_required"}
            )

        now = timezone.now()
        housing = (
            HousingAssignment.objects.select_for_update()
            .select_related("place__room__site")
            .filter(
                organization=organization,
                connection=passenger_connection,
                state=HousingAssignment.STATE_PUBLISHED,
                check_in_at__lte=now,
            )
            .filter(Q(check_out_at__isnull=True) | Q(check_out_at__gt=now))
            .order_by("-check_in_at", "-id")
            .first()
        )
        if housing is None:
            raise ValidationError({"housing": "passenger_housing_required"})

        route = (
            TransportRoute.objects.select_for_update()
            .filter(
                driver_vehicle_assignment=driver_assignment,
                schedule_template=schedule_template,
                state__in=(TransportRoute.STATE_DRAFT, TransportRoute.STATE_PUBLISHED),
            )
            .first()
        )
        first_date = driver_shifts[0].work_date
        last_date = driver_shifts[-1].work_date
        if route is None:
            route = TransportRoute.objects.create(
                organization=organization,
                internal_name=(
                    f"Crew {str(driver_connection.public_id)[:8]} "
                    f"{str(schedule_template.public_id)[:8]} {uuid.uuid4().hex[:6]}"
                ),
                worksite=project.worksite,
                schedule_template=schedule_template,
                driver_vehicle_assignment=driver_assignment,
                starts_on=first_date,
                ends_on=last_date,
                departure_time=schedule_template.starts_at_time,
                state=TransportRoute.STATE_PUBLISHED,
                published_by=actor,
                published_at=now,
                created_by=actor,
            )
            record_audit_event(
                organization=organization,
                actor=actor,
                action="transport.schedule_crew_created",
                target=route,
                details={"schedule_template": str(schedule_template.public_id)},
            )
        else:
            update_fields = []
            if route.state != TransportRoute.STATE_PUBLISHED:
                route.state = TransportRoute.STATE_PUBLISHED
                route.published_by = actor
                route.published_at = now
                update_fields.extend(("state", "published_by", "published_at"))
            if route.starts_on != first_date:
                route.starts_on = first_date
                update_fields.append("starts_on")
            if route.ends_on != last_date:
                route.ends_on = last_date
                update_fields.append("ends_on")
            if route.worksite_id != project.worksite_id:
                route.worksite = project.worksite
                update_fields.append("worksite")
            if update_fields:
                route.save(update_fields=[*update_fields, "updated_at"])

        crew = ensure_transport_crew_for_route(actor=actor, route=route)
        route.crew = crew

        passenger_count = TransportPassengerAssignment.objects.select_for_update().filter(
            route=route
        ).count()
        if passenger_count >= driver_assignment.vehicle.seat_capacity - 1:
            raise ValidationError({"route": "transport_crew_full"})
        if TransportPassengerAssignment.objects.filter(
            route=route,
            connection=passenger_connection,
        ).exists():
            raise ValidationError({"connection": "connection_already_in_route"})
        if DriverVehicleAssignment.objects.select_for_update().filter(
            organization=organization,
            driver_connection=passenger_connection,
            state=DriverVehicleAssignment.STATE_PUBLISHED,
            starts_on__lte=today,
        ).filter(Q(ends_on__isnull=True) | Q(ends_on__gte=today)).exists():
            raise ValidationError({"connection": "driver_already_has_vehicle_assignment"})

        pickup_stop, _ = RouteStop.objects.get_or_create(
            route=route,
            kind=RouteStop.KIND_PICKUP,
            housing_site=housing.place.room.site,
            defaults={
                "sequence": RouteStop.objects.filter(
                    route=route,
                    kind=RouteStop.KIND_PICKUP,
                ).count()
                + 1,
                "label": (
                    f"{housing.place.room.site.internal_name} · "
                    f"{housing.place.room.label} · {housing.place.label}"
                ),
            },
        )
        dropoff_stop, _ = RouteStop.objects.get_or_create(
            route=route,
            kind=RouteStop.KIND_DROPOFF,
            housing_site=None,
            defaults={
                "sequence": 500,
                "label": (
                    f"{project.worker_visible_name} · {project.worksite.city}, "
                    f"{project.worksite.street} {project.worksite.building}"
                ),
            },
        )
        passenger = TransportPassengerAssignment.objects.create(
            route=route,
            connection=passenger_connection,
            pickup_stop=pickup_stop,
            dropoff_stop=dropoff_stop,
            boarding_order=passenger_count + 1,
        )
        current_member = TransportCrewMember.objects.filter(
            crew=crew,
            connection=passenger_connection,
            ends_on__isnull=True,
        ).first()
        if current_member is None:
            TransportCrewMember.objects.create(
                crew=crew,
                connection=passenger_connection,
                starts_on=first_date,
                boarding_order=passenger_count + 1,
                created_by=actor,
            )

        work_assignment = (
            WorkerProjectAssignment.objects.select_for_update()
            .filter(
                organization=organization,
                connection=passenger_connection,
                project=project,
                state=WorkerProjectAssignment.STATE_PUBLISHED,
            )
            .order_by("-starts_at", "-id")
            .first()
        )
        if work_assignment is None:
            work_assignment = create_worker_project_assignment(
                actor=actor,
                organization=organization,
                connection=passenger_connection,
                project=project,
                worker_role="",
                starts_at=driver_shifts[0].starts_at,
            )
            work_assignment = publish_worker_project_assignment(
                actor=actor,
                assignment=work_assignment,
                replace_conflicting_assignments=True,
            )

        for driver_shift in driver_shifts:
            day_shifts = list(
                ScheduledWorkShift.objects.select_for_update().filter(
                    organization=organization,
                    connection=passenger_connection,
                    crew=crew,
                    work_date=driver_shift.work_date,
                    state__in=(
                        ScheduledWorkShift.STATE_DRAFT,
                        ScheduledWorkShift.STATE_PUBLISHED,
                    ),
                )
            )
            published_shift = next(
                (
                    item
                    for item in day_shifts
                    if item.state == ScheduledWorkShift.STATE_PUBLISHED
                ),
                None,
            )
            for item in day_shifts:
                if item.state == ScheduledWorkShift.STATE_DRAFT:
                    delete_scheduled_shift_draft(actor=actor, shift=item)
                elif item.pk != getattr(published_shift, "pk", None):
                    cancel_scheduled_shift(actor=actor, shift=item)
            if published_shift is not None:
                replace_scheduled_shift(
                    actor=actor,
                    shift=published_shift,
                    work_date=driver_shift.work_date,
                    starts_at=driver_shift.starts_at,
                    ends_at=driver_shift.ends_at,
                    break_minutes=driver_shift.break_minutes,
                    worker_label="",
                    work_assignment=work_assignment,
                    replacement_state=ScheduledWorkShift.STATE_PUBLISHED,
                    schedule_template=schedule_template,
                    crew=crew,
                )
            else:
                shift = create_scheduled_shift(
                    actor=actor,
                    organization=organization,
                    connection=passenger_connection,
                    work_date=driver_shift.work_date,
                    starts_at=driver_shift.starts_at,
                    ends_at=driver_shift.ends_at,
                    break_minutes=driver_shift.break_minutes,
                    worker_label="",
                    work_assignment=work_assignment,
                    schedule_template=schedule_template,
                    crew=crew,
                )
                publish_scheduled_shift(actor=actor, shift=shift)

        record_audit_event(
            organization=organization,
            actor=actor,
            action="transport.schedule_passenger_added",
            target=passenger,
            details={
                "route": str(route.public_id),
                "schedule_template": str(schedule_template.public_id),
                "connection": str(passenger_connection.public_id),
                "copied_shift_count": len(driver_shifts),
            },
        )
        enqueue_support_notification(
            organization=organization,
            recipient=passenger_connection.candidate,
            notification_code="transport.route_published",
            target_kind="transport_route",
            target_public_id=route.public_id,
            target_key=f"support:transport-route:{route.public_id}",
            collapse_key=f"support:transport:{passenger_connection.public_id}",
            dedupe_key=(
                f"transport.schedule-passenger:{passenger.public_id}:{now.isoformat()}"
            ),
        )
    return passenger


def remove_passenger_from_driver_schedule(*, actor, passenger_assignment):
    """Remove only the transport seat, preserving the worker's job and shifts."""

    organization = passenger_assignment.route.organization
    require_permission(
        user=actor,
        organization=organization,
        permission_code=TRANSPORT_MANAGE,
    )
    require_permission(
        user=actor,
        organization=organization,
        permission_code=SCHEDULE_MANAGE,
    )
    with transaction.atomic():
        passenger = (
            TransportPassengerAssignment.objects.select_for_update()
            .select_related(
                "route__driver_vehicle_assignment__driver_connection",
                "route__crew",
                "connection__candidate",
            )
            .get(pk=passenger_assignment.pk)
        )
        route = passenger.route
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=route.driver_vehicle_assignment.driver_connection,
        )
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=passenger.connection,
        )
        connection = passenger.connection
        removed_passenger_id = passenger.public_id
        passenger.delete()
        if route.crew_id is not None:
            _close_open_crew_membership(
                crew=route.crew,
                connection=connection,
                effective_on=timezone.localdate(),
            )
        for order, remaining in enumerate(
            TransportPassengerAssignment.objects.select_for_update()
            .filter(route=route)
            .order_by("boarding_order", "id"),
            start=1,
        ):
            if remaining.boarding_order != order:
                remaining.boarding_order = order
                remaining.save(update_fields=["boarding_order", "updated_at"])
        record_audit_event(
            organization=organization,
            actor=actor,
            action="transport.schedule_passenger_removed",
            target=route,
            details={
                "passenger_assignment": str(removed_passenger_id),
                "connection": str(connection.public_id),
            },
        )
        now = timezone.now()
        enqueue_support_notification(
            organization=organization,
            recipient=connection.candidate,
            notification_code="transport.route_published",
            target_kind="transport_route",
            target_public_id=route.public_id,
            target_key=f"support:transport-route:{route.public_id}",
            collapse_key=f"support:transport:{connection.public_id}",
            dedupe_key=(
                f"transport.schedule-passenger-removed:"
                f"{removed_passenger_id}:{now.isoformat()}"
            ),
        )
    return route


def replace_passenger_in_driver_schedule(
    *,
    actor,
    passenger_assignment,
    replacement_connection,
):
    """Move a worker into the selected crew and replace their future schedule.

    A replacement may already be a passenger of another crew or have another
    published schedule.  Those future memberships and shifts are closed in
    the same transaction before the selected driver's schedule is copied.
    Any validation error rolls the entire operation back, including the
    original passenger seat.
    """

    organization = passenger_assignment.route.organization
    require_permission(
        user=actor,
        organization=organization,
        permission_code=TRANSPORT_MANAGE,
    )
    require_permission(
        user=actor,
        organization=organization,
        permission_code=SCHEDULE_MANAGE,
    )
    with transaction.atomic():
        current = (
            TransportPassengerAssignment.objects.select_for_update()
            .select_related(
                "route__driver_vehicle_assignment__driver_connection",
                "route__schedule_template",
                "route__crew",
                "connection__candidate",
            )
            .get(pk=passenger_assignment.pk)
        )
        route = current.route
        if route.schedule_template is None:
            raise ValidationError({"route": "transport_schedule_template_required"})
        if current.connection_id == replacement_connection.id:
            raise ValidationError({"connection": "transport_passenger_unchanged"})
        old_connection = current.connection
        old_assignment_id = current.public_id
        original_boarding_order = current.boarding_order
        today = timezone.localdate()

        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=old_connection,
        )
        require_worker_connection_access(
            user=actor,
            organization=organization,
            connection=replacement_connection,
        )
        _require_connection_for_operations(
            connection=replacement_connection,
            organization=organization,
        )

        previous_passenger_rows = list(
            TransportPassengerAssignment.objects.select_for_update()
            .select_related("route__crew")
            .filter(
                connection=replacement_connection,
                route__organization=organization,
                route__state__in=(
                    TransportRoute.STATE_DRAFT,
                    TransportRoute.STATE_PUBLISHED,
                ),
            )
            .filter(Q(route__ends_on__isnull=True) | Q(route__ends_on__gte=today))
            .exclude(route=route)
        )
        previous_routes = []
        for passenger_row in previous_passenger_rows:
            previous_route = passenger_row.route
            passenger_row.delete()
            previous_routes.append(previous_route)
            if previous_route.crew_id is not None:
                _close_open_crew_membership(
                    crew=previous_route.crew,
                    connection=replacement_connection,
                    effective_on=today,
                )
        for previous_route in previous_routes:
            for order, remaining in enumerate(
                TransportPassengerAssignment.objects.select_for_update()
                .filter(route=previous_route)
                .order_by("boarding_order", "id"),
                start=1,
            ):
                if remaining.boarding_order != order:
                    remaining.boarding_order = order
                    remaining.save(update_fields=["boarding_order", "updated_at"])

        from .timekeeping import cancel_scheduled_shift, delete_scheduled_shift_draft

        previous_future_shifts = list(
            ScheduledWorkShift.objects.select_for_update()
            .filter(
                organization=organization,
                connection=replacement_connection,
                work_date__gte=today,
                state__in=(
                    ScheduledWorkShift.STATE_DRAFT,
                    ScheduledWorkShift.STATE_PUBLISHED,
                ),
            )
            .order_by("work_date", "starts_at", "id")
        )
        for shift in previous_future_shifts:
            if shift.state == ScheduledWorkShift.STATE_DRAFT:
                delete_scheduled_shift_draft(actor=actor, shift=shift)
            else:
                cancel_scheduled_shift(actor=actor, shift=shift)

        current.delete()
        if route.crew_id is not None:
            _close_open_crew_membership(
                crew=route.crew,
                connection=old_connection,
                effective_on=timezone.localdate(),
            )
        replacement = add_passenger_to_driver_schedule(
            actor=actor,
            driver_connection=route.driver_vehicle_assignment.driver_connection,
            schedule_template=route.schedule_template,
            passenger_connection=replacement_connection,
        )
        if replacement.boarding_order != original_boarding_order:
            replacement.boarding_order = original_boarding_order
            replacement.save(update_fields=["boarding_order", "updated_at"])
        for order, remaining in enumerate(
            TransportPassengerAssignment.objects.select_for_update()
            .filter(route=route)
            .order_by("boarding_order", "id"),
            start=1,
        ):
            if remaining.boarding_order != order:
                remaining.boarding_order = order
                remaining.save(update_fields=["boarding_order", "updated_at"])
        record_audit_event(
            organization=organization,
            actor=actor,
            action="transport.schedule_passenger_replaced",
            target=replacement,
            details={
                "route": str(route.public_id),
                "previous_passenger_assignment": str(old_assignment_id),
                "previous_connection": str(old_connection.public_id),
                "replacement_connection": str(replacement_connection.public_id),
                "detached_route_count": len(previous_routes),
                "replaced_shift_count": len(previous_future_shifts),
            },
        )
        now = timezone.now()
        enqueue_support_notification(
            organization=organization,
            recipient=old_connection.candidate,
            notification_code="transport.route_published",
            target_kind="transport_route",
            target_public_id=route.public_id,
            target_key=f"support:transport-route:{route.public_id}",
            collapse_key=f"support:transport:{old_connection.public_id}",
            dedupe_key=(
                f"transport.schedule-passenger-replaced:"
                f"{old_assignment_id}:{now.isoformat()}"
            ),
        )
    return replacement


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
