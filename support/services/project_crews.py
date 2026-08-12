"""Transactional operations for the project-first crew architecture.

The current employer screens still use the legacy transport services.  These
operations are intentionally isolated until the controlled UI cutover.
"""

from datetime import datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from support.models import (
    ProjectCrew,
    ProjectCrewPassenger,
    ProjectCrewResourceAssignment,
    ProjectCrewShift,
    ProjectCrewShiftMember,
    ScheduledWorkShift,
    SupportConnection,
    Vehicle,
    WorkProject,
)
from support.permission_codes import SCHEDULE_MANAGE, TRANSPORT_MANAGE
from support.permissions import require_permission, require_worker_connection_access

from .audit import record_audit_event


PASSENGER_SCOPE_FUTURE = "future"
PASSENGER_SCOPE_SELECTED = "selected"
PASSENGER_SCOPES = frozenset({PASSENGER_SCOPE_FUTURE, PASSENGER_SCOPE_SELECTED})


def _operation_error(code, message, **details):
    payload = {"code": code, "message": message}
    payload.update(details)
    raise ValidationError(payload)


def _require_permissions(*, actor, organization):
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


def _require_connection(*, actor, organization, connection):
    if connection.organization_id != organization.id:
        _operation_error(
            "worker_not_in_organization",
            "The selected worker does not belong to this organization.",
        )
    if connection.is_archived:
        _operation_error("worker_archived", "The selected worker is archived.")
    require_worker_connection_access(
        user=actor,
        organization=organization,
        connection=connection,
    )


def _normalize_dates(work_dates):
    dates = sorted(set(work_dates or ()))
    if not dates:
        _operation_error("work_dates_required", "Select at least one calendar date.")
    return dates


def _local_shift_period(*, work_date, starts_at_time, ends_at_time):
    if not isinstance(starts_at_time, time) or not isinstance(ends_at_time, time):
        _operation_error(
            "shift_time_required",
            "Shift start and end times are required.",
        )
    current_timezone = timezone.get_current_timezone()
    starts_at = timezone.make_aware(
        datetime.combine(work_date, starts_at_time),
        current_timezone,
    )
    end_date = work_date if ends_at_time > starts_at_time else work_date + timedelta(days=1)
    ends_at = timezone.make_aware(
        datetime.combine(end_date, ends_at_time),
        current_timezone,
    )
    return starts_at, ends_at


def _resource_for_date(*, crew, work_date, lock=False):
    queryset = ProjectCrewResourceAssignment.objects.filter(
        crew=crew,
        starts_on__lte=work_date,
    ).filter(Q(ends_on__isnull=True) | Q(ends_on__gte=work_date))
    if lock:
        queryset = queryset.select_for_update()
    return queryset.select_related("driver_connection", "vehicle").order_by("-starts_on", "-id").first()


def _active_roster_for_date(*, crew, work_date, lock=False):
    queryset = ProjectCrewPassenger.objects.filter(
        crew=crew,
        starts_on__lte=work_date,
    ).filter(Q(ends_on__isnull=True) | Q(ends_on__gte=work_date))
    if lock:
        queryset = queryset.select_for_update()
    return queryset.select_related("connection").order_by("id")


def _overlapping_memberships(*, connection, starts_at, ends_at, exclude_shift=None):
    queryset = (
        ProjectCrewShiftMember.objects.select_for_update()
        .select_related("shift__crew__project", "connection")
        .filter(
            connection=connection,
            shift__state=ProjectCrewShift.STATE_PUBLISHED,
            shift__starts_at__lt=ends_at,
            shift__ends_at__gt=starts_at,
        )
    )
    if exclude_shift is not None:
        queryset = queryset.exclude(shift=exclude_shift)
    return list(queryset.order_by("shift__work_date", "id"))


def _ensure_shift_capacity(*, shift, additional_members=0):
    driver = shift.members.select_related("vehicle").filter(
        role=ProjectCrewShiftMember.ROLE_DRIVER
    ).first()
    if driver is None or driver.vehicle_id is None:
        _operation_error(
            "crew_driver_missing",
            f"Crew has no driver and vehicle on {shift.work_date.isoformat()}.",
            work_date=shift.work_date.isoformat(),
        )
    occupied = shift.members.count()
    if occupied + additional_members > driver.vehicle.seat_capacity:
        _operation_error(
            "crew_capacity_exceeded",
            (
                f"Crew capacity is exceeded on {shift.work_date.isoformat()}: "
                f"{occupied + additional_members}/{driver.vehicle.seat_capacity} seats."
            ),
            work_date=shift.work_date.isoformat(),
            occupied=occupied,
            capacity=driver.vehicle.seat_capacity,
        )


def _sync_member_worker_calendar(*, member, actor):
    """Mirror one project-first crew-day member into the worker calendar."""

    shift = member.shift
    scheduled_state = (
        ScheduledWorkShift.STATE_PUBLISHED
        if shift.state == ProjectCrewShift.STATE_PUBLISHED
        else ScheduledWorkShift.STATE_CANCELLED
    )
    published_at = timezone.now() if scheduled_state == ScheduledWorkShift.STATE_PUBLISHED else None
    cancelled_at = timezone.now() if scheduled_state == ScheduledWorkShift.STATE_CANCELLED else None
    calendar_shift, _ = ScheduledWorkShift.objects.update_or_create(
        project_crew_member=member,
        defaults={
            "organization": shift.crew.organization,
            "connection": member.connection,
            "work_date": shift.work_date,
            "starts_at": shift.starts_at,
            "ends_at": shift.ends_at,
            "break_minutes": shift.break_minutes,
            "worker_label": shift.crew.internal_name,
            "state": scheduled_state,
            "created_by": actor,
            "published_by": actor if scheduled_state == ScheduledWorkShift.STATE_PUBLISHED else None,
            "published_at": published_at,
            "cancelled_at": cancelled_at,
        },
    )
    return calendar_shift


def _sync_shift_worker_calendars(*, shift, actor):
    for member in shift.members.select_related("connection", "shift__crew__organization"):
        _sync_member_worker_calendar(member=member, actor=actor)


def _resolve_passenger_conflicts(*, connection, shift):
    conflicts = _overlapping_memberships(
        connection=connection,
        starts_at=shift.starts_at,
        ends_at=shift.ends_at,
        exclude_shift=shift,
    )
    driver_conflicts = [item for item in conflicts if item.role == ProjectCrewShiftMember.ROLE_DRIVER]
    if driver_conflicts:
        dates = sorted({item.shift.work_date.isoformat() for item in driver_conflicts})
        _operation_error(
            "worker_drives_other_crew",
            (
                "The selected worker is a driver in another crew on: "
                + ", ".join(dates)
                + ". A driver cannot be assigned as a passenger for overlapping shifts."
            ),
            work_dates=dates,
            worker=str(connection.public_id),
        )
    # Ordinary passenger conflicts are deliberately replaced for the selected
    # dates.  The append-only audit event on the parent operation records it.
    for conflict in conflicts:
        conflict.delete()


def _add_passenger_to_shift(*, actor, shift, connection):
    existing = shift.members.filter(connection=connection).first()
    if existing:
        if existing.role == ProjectCrewShiftMember.ROLE_DRIVER:
            _operation_error(
                "worker_is_crew_driver",
                f"The selected worker is already the crew driver on {shift.work_date.isoformat()}.",
                work_date=shift.work_date.isoformat(),
            )
        _sync_member_worker_calendar(member=existing, actor=actor)
        return existing, False
    _resolve_passenger_conflicts(connection=connection, shift=shift)
    _ensure_shift_capacity(shift=shift, additional_members=1)
    member = ProjectCrewShiftMember(
        shift=shift,
        connection=connection,
        role=ProjectCrewShiftMember.ROLE_PASSENGER,
        created_by=actor,
    )
    member.full_clean()
    member.save()
    _sync_member_worker_calendar(member=member, actor=actor)
    return member, True


def _sync_shift_members_for_new_shift(*, actor, shift, resource):
    if resource is None:
        _operation_error(
            "crew_resource_missing",
            f"Crew has no active driver and vehicle on {shift.work_date.isoformat()}.",
            work_date=shift.work_date.isoformat(),
        )
    if not resource.driver_connection.has_driving_license:
        _operation_error(
            "driver_licence_not_confirmed",
            "The crew driver does not have a confirmed driving licence.",
            worker=str(resource.driver_connection.public_id),
        )
    roster = list(_active_roster_for_date(crew=shift.crew, work_date=shift.work_date, lock=True))
    if len(roster) + 1 > resource.vehicle.seat_capacity:
        _operation_error(
            "crew_capacity_exceeded",
            (
                f"Crew capacity is exceeded on {shift.work_date.isoformat()}: "
                f"{len(roster) + 1}/{resource.vehicle.seat_capacity} seats."
            ),
            work_date=shift.work_date.isoformat(),
            occupied=len(roster) + 1,
            capacity=resource.vehicle.seat_capacity,
        )
    driver = ProjectCrewShiftMember(
        shift=shift,
        connection=resource.driver_connection,
        role=ProjectCrewShiftMember.ROLE_DRIVER,
        vehicle=resource.vehicle,
        created_by=actor,
    )
    driver.full_clean()
    driver.save()
    _sync_member_worker_calendar(member=driver, actor=actor)
    for roster_entry in roster:
        _add_passenger_to_shift(actor=actor, shift=shift, connection=roster_entry.connection)


def _validate_existing_shift_conflicts(*, shift):
    for member in list(shift.members.select_related("connection")):
        conflicts = _overlapping_memberships(
            connection=member.connection,
            starts_at=shift.starts_at,
            ends_at=shift.ends_at,
            exclude_shift=shift,
        )
        if not conflicts:
            continue
        if member.role == ProjectCrewShiftMember.ROLE_DRIVER or any(
            conflict.role == ProjectCrewShiftMember.ROLE_DRIVER for conflict in conflicts
        ):
            dates = sorted({conflict.shift.work_date.isoformat() for conflict in conflicts})
            _operation_error(
                "driver_shift_conflict",
                (
                    f"Driver {member.connection.candidate.get_full_name() or member.connection.candidate.email} "
                    "has an overlapping crew shift on: " + ", ".join(dates) + "."
                ),
                work_dates=dates,
                worker=str(member.connection.public_id),
            )
        for conflict in conflicts:
            conflict.delete()


@transaction.atomic
def create_project_crew(
    *,
    actor,
    organization,
    project,
    driver_connection,
    vehicle,
    internal_name="",
    starts_on=None,
):
    """Create a crew and its first effective driver/vehicle pair atomically."""

    _require_permissions(actor=actor, organization=organization)
    project = WorkProject.objects.select_for_update().get(pk=project.pk)
    driver_connection = SupportConnection.objects.select_for_update().get(pk=driver_connection.pk)
    vehicle = Vehicle.objects.select_for_update().get(pk=vehicle.pk)
    if project.organization_id != organization.id:
        _operation_error("project_not_in_organization", "Project does not belong to this organization.")
    _require_connection(
        actor=actor,
        organization=organization,
        connection=driver_connection,
    )
    if not driver_connection.has_driving_license:
        _operation_error(
            "driver_licence_not_confirmed",
            "The selected worker does not have a confirmed driving licence.",
        )
    if vehicle.organization_id != organization.id or not vehicle.is_active:
        _operation_error("vehicle_not_available", "The selected vehicle is not available in this organization.")

    crew = ProjectCrew(
        organization=organization,
        project=project,
        internal_name=internal_name,
        created_by=actor,
    )
    crew.full_clean()
    crew.save()
    resource = ProjectCrewResourceAssignment(
        crew=crew,
        driver_connection=driver_connection,
        vehicle=vehicle,
        starts_on=starts_on or timezone.localdate(),
        created_by=actor,
    )
    resource.full_clean()
    try:
        resource.save()
    except IntegrityError:
        _operation_error(
            "driver_or_vehicle_already_assigned",
            "The selected driver or vehicle is already assigned to another active crew.",
        )
    record_audit_event(
        organization=organization,
        actor=actor,
        action="project_crew.created",
        target=crew,
        details={
            "project": str(project.public_id),
            "driver": str(driver_connection.public_id),
            "vehicle": str(vehicle.public_id),
            "starts_on": resource.starts_on.isoformat(),
        },
    )
    return crew


@transaction.atomic
def publish_project_crew_shifts(
    *,
    actor,
    crew,
    work_dates,
    starts_at_time,
    ends_at_time,
    break_minutes=0,
):
    """Create or replace directly-entered published shifts for selected days."""

    crew = (
        ProjectCrew.objects.select_for_update()
        .select_related("organization", "project")
        .get(pk=crew.pk)
    )
    organization = crew.organization
    _require_permissions(actor=actor, organization=organization)
    dates = _normalize_dates(work_dates)
    try:
        break_minutes = int(break_minutes)
    except (TypeError, ValueError):
        _operation_error("break_minutes_invalid", "Break must be a whole number of minutes.")
    if break_minutes < 0:
        _operation_error("break_minutes_invalid", "Break cannot be negative.")

    shifts = []
    created_count = 0
    replaced_count = 0
    for work_date in dates:
        starts_at, ends_at = _local_shift_period(
            work_date=work_date,
            starts_at_time=starts_at_time,
            ends_at_time=ends_at_time,
        )
        shift = ProjectCrewShift.objects.select_for_update().filter(
            crew=crew,
            work_date=work_date,
        ).first()
        if shift is None:
            shift = ProjectCrewShift(
                crew=crew,
                work_date=work_date,
                starts_at=starts_at,
                ends_at=ends_at,
                break_minutes=break_minutes,
                state=ProjectCrewShift.STATE_PUBLISHED,
                created_by=actor,
                updated_by=actor,
            )
            shift.full_clean()
            shift.save()
            resource = _resource_for_date(crew=crew, work_date=work_date, lock=True)
            _sync_shift_members_for_new_shift(actor=actor, shift=shift, resource=resource)
            created_count += 1
        else:
            shift.starts_at = starts_at
            shift.ends_at = ends_at
            shift.break_minutes = break_minutes
            shift.state = ProjectCrewShift.STATE_PUBLISHED
            shift.updated_by = actor
            shift.full_clean()
            shift.save(
                update_fields=(
                    "starts_at",
                    "ends_at",
                    "break_minutes",
                    "state",
                    "updated_by",
                    "updated_at",
                )
            )
            if not shift.members.exists():
                resource = _resource_for_date(crew=crew, work_date=work_date, lock=True)
                _sync_shift_members_for_new_shift(actor=actor, shift=shift, resource=resource)
            else:
                _validate_existing_shift_conflicts(shift=shift)
                _sync_shift_worker_calendars(shift=shift, actor=actor)
            replaced_count += 1
        shifts.append(shift)

    record_audit_event(
        organization=organization,
        actor=actor,
        action="project_crew.shifts_published",
        target=crew,
        details={
            "work_dates": [item.isoformat() for item in dates],
            "created": created_count,
            "replaced": replaced_count,
            "starts_at_time": starts_at_time.isoformat(),
            "ends_at_time": ends_at_time.isoformat(),
            "break_minutes": break_minutes,
        },
    )
    return shifts


@transaction.atomic
def release_project_crew_shifts(*, actor, crew, work_dates):
    """Cancel selected crew days without deleting their audit/history context."""

    crew = ProjectCrew.objects.select_for_update().select_related("organization").get(pk=crew.pk)
    organization = crew.organization
    _require_permissions(actor=actor, organization=organization)
    dates = _normalize_dates(work_dates)
    shifts = list(
        ProjectCrewShift.objects.select_for_update()
        .filter(crew=crew, work_date__in=dates)
        .order_by("work_date")
    )
    changed = []
    for shift in shifts:
        if shift.state == ProjectCrewShift.STATE_CANCELLED:
            continue
        shift.state = ProjectCrewShift.STATE_CANCELLED
        shift.updated_by = actor
        shift.save(update_fields=("state", "updated_by", "updated_at"))
        _sync_shift_worker_calendars(shift=shift, actor=actor)
        changed.append(shift)
    record_audit_event(
        organization=organization,
        actor=actor,
        action="project_crew.shifts_released",
        target=crew,
        details={
            "work_dates": [item.isoformat() for item in dates],
            "days_released": len(changed),
        },
    )
    return changed


@transaction.atomic
def assign_project_crew_passenger(
    *,
    actor,
    crew,
    connection,
    scope,
    selected_dates=None,
    effective_on=None,
):
    """Add a passenger to all future or explicitly selected crew days."""

    crew = ProjectCrew.objects.select_for_update().select_related("organization").get(pk=crew.pk)
    organization = crew.organization
    _require_permissions(actor=actor, organization=organization)
    connection = SupportConnection.objects.select_for_update().get(pk=connection.pk)
    _require_connection(actor=actor, organization=organization, connection=connection)
    if scope not in PASSENGER_SCOPES:
        _operation_error("passenger_scope_invalid", "Passenger scope must be future or selected.")

    roster_entry = None
    if scope == PASSENGER_SCOPE_FUTURE:
        effective_on = effective_on or timezone.localdate()
        roster_entry = ProjectCrewPassenger.objects.select_for_update().filter(
            crew=crew,
            connection=connection,
            ends_on__isnull=True,
        ).first()
        if roster_entry is None:
            roster_entry = ProjectCrewPassenger(
                crew=crew,
                connection=connection,
                starts_on=effective_on,
                created_by=actor,
            )
            roster_entry.full_clean()
            roster_entry.save()
        shifts = list(
            ProjectCrewShift.objects.select_for_update()
            .filter(
                crew=crew,
                state=ProjectCrewShift.STATE_PUBLISHED,
                work_date__gte=effective_on,
            )
            .order_by("work_date")
        )
        dates = [shift.work_date for shift in shifts]
    else:
        dates = _normalize_dates(selected_dates)
        shifts = list(
            ProjectCrewShift.objects.select_for_update()
            .filter(
                crew=crew,
                state=ProjectCrewShift.STATE_PUBLISHED,
                work_date__in=dates,
            )
            .order_by("work_date")
        )
        found_dates = {shift.work_date for shift in shifts}
        missing_dates = [item.isoformat() for item in dates if item not in found_dates]
        if missing_dates:
            _operation_error(
                "crew_shift_missing",
                "No published crew shift exists on: " + ", ".join(missing_dates) + ".",
                work_dates=missing_dates,
            )

    added_count = 0
    for shift in shifts:
        _, created = _add_passenger_to_shift(actor=actor, shift=shift, connection=connection)
        added_count += int(created)
    record_audit_event(
        organization=organization,
        actor=actor,
        action="project_crew.passenger_assigned",
        target=crew,
        details={
            "worker": str(connection.public_id),
            "scope": scope,
            "work_dates": [item.isoformat() for item in dates],
            "days_added": added_count,
        },
    )
    return roster_entry, shifts


def _close_roster_entry(*, entry, effective_on):
    if entry.starts_on >= effective_on:
        entry.delete()
        return
    entry.ends_on = effective_on - timedelta(days=1)
    entry.save(update_fields=("ends_on", "updated_at"))


@transaction.atomic
def remove_project_crew_passenger(
    *,
    actor,
    crew,
    connection,
    scope,
    selected_dates=None,
    effective_on=None,
):
    """Remove a passenger from future or selected crew days and roster state."""

    crew = ProjectCrew.objects.select_for_update().select_related("organization").get(pk=crew.pk)
    organization = crew.organization
    _require_permissions(actor=actor, organization=organization)
    connection = SupportConnection.objects.select_for_update().get(pk=connection.pk)
    _require_connection(actor=actor, organization=organization, connection=connection)
    if scope not in PASSENGER_SCOPES:
        _operation_error("passenger_scope_invalid", "Passenger scope must be future or selected.")

    if scope == PASSENGER_SCOPE_FUTURE:
        effective_on = effective_on or timezone.localdate()
        for entry in ProjectCrewPassenger.objects.select_for_update().filter(
            crew=crew,
            connection=connection,
            ends_on__isnull=True,
        ):
            _close_roster_entry(entry=entry, effective_on=effective_on)
        shifts = ProjectCrewShift.objects.select_for_update().filter(
            crew=crew,
            work_date__gte=effective_on,
        )
        dates = list(shifts.values_list("work_date", flat=True))
    else:
        dates = _normalize_dates(selected_dates)
        shifts = ProjectCrewShift.objects.select_for_update().filter(
            crew=crew,
            work_date__in=dates,
        )
    removed_count, _ = ProjectCrewShiftMember.objects.filter(
        shift__in=shifts,
        connection=connection,
        role=ProjectCrewShiftMember.ROLE_PASSENGER,
    ).delete()
    record_audit_event(
        organization=organization,
        actor=actor,
        action="project_crew.passenger_removed",
        target=crew,
        details={
            "worker": str(connection.public_id),
            "scope": scope,
            "work_dates": [item.isoformat() for item in sorted(set(dates))],
            "records_removed": removed_count,
        },
    )
    return removed_count


@transaction.atomic
def release_project_crew_member_days(*, actor, connection, work_dates):
    """Remove one worker from concrete project-first crew days.

    The project shift itself remains published for the rest of the crew.  For
    a permanent passenger this deliberately leaves the roster entry open: the
    missing day is an explicit absence, while later crew days still inherit
    the passenger as usual.
    """

    connection = (
        SupportConnection.objects.select_for_update()
        .select_related("organization")
        .get(pk=connection.pk)
    )
    organization = connection.organization
    _require_permissions(actor=actor, organization=organization)
    _require_connection(
        actor=actor,
        organization=organization,
        connection=connection,
    )
    dates = _normalize_dates(work_dates)
    memberships = list(
        ProjectCrewShiftMember.objects.select_for_update()
        .select_related("shift__crew")
        .filter(
            connection=connection,
            shift__work_date__in=dates,
            shift__state=ProjectCrewShift.STATE_PUBLISHED,
        )
        .order_by("shift__work_date", "id")
    )
    if not memberships:
        _operation_error(
            "selected_schedule_days_have_no_shifts",
            "The selected project crew days are already free for this worker.",
        )
    released_dates = sorted({item.shift.work_date for item in memberships})
    crew_ids = sorted({item.shift.crew_id for item in memberships})
    ProjectCrewShiftMember.objects.filter(
        pk__in=[item.pk for item in memberships]
    ).delete()
    record_audit_event(
        organization=organization,
        actor=actor,
        action="project_crew.worker_days_released",
        target=connection,
        details={
            "crews": crew_ids,
            "work_dates": [item.isoformat() for item in released_dates],
        },
    )
    return released_dates


@transaction.atomic
def replace_project_crew_driver(
    *,
    actor,
    crew,
    new_driver_connection,
    effective_on=None,
):
    """Permanently replace the driver from a date, keeping the crew vehicle."""

    crew = ProjectCrew.objects.select_for_update().select_related("organization").get(pk=crew.pk)
    organization = crew.organization
    _require_permissions(actor=actor, organization=organization)
    new_driver = SupportConnection.objects.select_for_update().get(pk=new_driver_connection.pk)
    _require_connection(actor=actor, organization=organization, connection=new_driver)
    if not new_driver.has_driving_license:
        _operation_error(
            "driver_licence_not_confirmed",
            "The selected passenger does not have a confirmed driving licence.",
        )
    effective_on = effective_on or timezone.localdate()
    current = _resource_for_date(crew=crew, work_date=effective_on, lock=True)
    if current is None:
        _operation_error(
            "crew_resource_missing",
            "The crew has no active driver and vehicle to replace on this date.",
        )
    if current.driver_connection_id == new_driver.id:
        return current

    is_passenger = ProjectCrewPassenger.objects.filter(
        crew=crew,
        connection=new_driver,
        starts_on__lte=effective_on,
    ).filter(Q(ends_on__isnull=True) | Q(ends_on__gte=effective_on)).exists()
    if not is_passenger:
        is_passenger = ProjectCrewShiftMember.objects.filter(
            shift__crew=crew,
            shift__work_date__gte=effective_on,
            connection=new_driver,
            role=ProjectCrewShiftMember.ROLE_PASSENGER,
        ).exists()
    if not is_passenger:
        _operation_error(
            "replacement_driver_not_in_crew",
            "A replacement driver must be selected from this crew's passengers.",
        )

    other_open_resource = ProjectCrewResourceAssignment.objects.select_for_update().filter(
        driver_connection=new_driver,
        ends_on__isnull=True,
    ).exclude(crew=crew).first()

    released_other_crew = None
    released_other_vehicle = None
    if other_open_resource:
        released_other_crew = other_open_resource.crew
        released_other_vehicle = other_open_resource.vehicle
        if other_open_resource.starts_on >= effective_on:
            other_open_resource.delete()
        else:
            other_open_resource.ends_on = effective_on - timedelta(days=1)
            other_open_resource.save(update_fields=("ends_on", "updated_at"))
        # The previous crew and its passengers remain intact.  Only the driver
        # and vehicle snapshots are removed for future days, which makes the
        # missing-driver state explicit for the next planning step.
        ProjectCrewShiftMember.objects.filter(
            shift__crew=released_other_crew,
            shift__work_date__gte=effective_on,
            connection=new_driver,
            role=ProjectCrewShiftMember.ROLE_DRIVER,
        ).delete()

    old_driver = current.driver_connection
    vehicle = current.vehicle
    if current.starts_on >= effective_on:
        current.delete()
    else:
        current.ends_on = effective_on - timedelta(days=1)
        current.save(update_fields=("ends_on", "updated_at"))
    replacement = ProjectCrewResourceAssignment(
        crew=crew,
        driver_connection=new_driver,
        vehicle=vehicle,
        starts_on=effective_on,
        created_by=actor,
    )
    replacement.full_clean()
    replacement.save()

    for entry in ProjectCrewPassenger.objects.select_for_update().filter(
        crew=crew,
        connection=new_driver,
        ends_on__isnull=True,
    ):
        _close_roster_entry(entry=entry, effective_on=effective_on)
    old_driver_roster = ProjectCrewPassenger.objects.select_for_update().filter(
        crew=crew,
        connection=old_driver,
        ends_on__isnull=True,
    ).first()
    if old_driver_roster is None:
        old_driver_roster = ProjectCrewPassenger(
            crew=crew,
            connection=old_driver,
            starts_on=effective_on,
            created_by=actor,
        )
        old_driver_roster.full_clean()
        old_driver_roster.save()

    shifts = list(
        ProjectCrewShift.objects.select_for_update()
        .filter(
            crew=crew,
            state=ProjectCrewShift.STATE_PUBLISHED,
            work_date__gte=effective_on,
        )
        .order_by("work_date")
    )
    for shift in shifts:
        new_driver_conflicts = _overlapping_memberships(
            connection=new_driver,
            starts_at=shift.starts_at,
            ends_at=shift.ends_at,
            exclude_shift=shift,
        )
        if new_driver_conflicts:
            _operation_error(
                "replacement_driver_shift_conflict",
                (
                    "The replacement driver has an overlapping crew shift on "
                    f"{shift.work_date.isoformat()}."
                ),
                work_date=shift.work_date.isoformat(),
            )
        shift.members.filter(
            connection=old_driver,
            role=ProjectCrewShiftMember.ROLE_DRIVER,
        ).delete()
        shift.members.filter(connection=new_driver).delete()
        new_driver_member = ProjectCrewShiftMember(
            shift=shift,
            connection=new_driver,
            role=ProjectCrewShiftMember.ROLE_DRIVER,
            vehicle=vehicle,
            created_by=actor,
        )
        new_driver_member.full_clean()
        new_driver_member.save()
        _sync_member_worker_calendar(member=new_driver_member, actor=actor)
        _add_passenger_to_shift(actor=actor, shift=shift, connection=old_driver)

    record_audit_event(
        organization=organization,
        actor=actor,
        action="project_crew.driver_replaced",
        target=crew,
        details={
            "previous_driver": str(old_driver.public_id),
            "new_driver": str(new_driver.public_id),
            "vehicle": str(vehicle.public_id),
            "effective_on": effective_on.isoformat(),
            "future_days_updated": len(shifts),
            "released_other_crew": (
                str(released_other_crew.public_id) if released_other_crew else ""
            ),
            "released_other_vehicle": (
                str(released_other_vehicle.public_id) if released_other_vehicle else ""
            ),
        },
    )
    return replacement
