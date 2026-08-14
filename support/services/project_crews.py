"""Transactional operations for the project-first crew architecture.

The current employer screens still use the legacy transport services.  These
operations are intentionally isolated until the controlled UI cutover.
"""

from datetime import datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from support.models import (
    DriverVehicleAssignment,
    ProjectCrew,
    ProjectCrewDriverSubstitution,
    ProjectCrewMemberAbsence,
    ProjectCrewPassenger,
    ProjectCrewResourceAssignment,
    ProjectCrewShift,
    ProjectCrewShiftMember,
    ProjectScheduleTemplate,
    ScheduledWorkShift,
    SupportConnection,
    TransportCrew,
    Vehicle,
    WorkerProjectAssignment,
    WorkerScheduleDayOff,
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
    resource = None
    vehicle = driver.vehicle if driver and driver.vehicle_id else None
    if vehicle is None:
        resource = _resource_for_date(crew=shift.crew, work_date=shift.work_date)
        vehicle = resource.vehicle if resource else None
    if vehicle is None:
        _operation_error(
            "crew_resource_missing",
            f"Crew has no assigned vehicle on {shift.work_date.isoformat()}.",
            work_date=shift.work_date.isoformat(),
        )
    occupied = shift.members.count()
    # A temporary missing driver must not block passenger planning.  The car
    # still has one seat reserved for the primary or substitute driver.
    reserved_driver_seat = 0 if driver else 1
    requested_occupied = occupied + additional_members + reserved_driver_seat
    if requested_occupied > vehicle.seat_capacity:
        _operation_error(
            "crew_capacity_exceeded",
            (
                f"Crew capacity is exceeded on {shift.work_date.isoformat()}: "
                f"{requested_occupied}/{vehicle.seat_capacity} seats."
            ),
            work_date=shift.work_date.isoformat(),
            occupied=occupied,
            capacity=vehicle.seat_capacity,
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
    if WorkerScheduleDayOff.objects.filter(
        connection=connection,
        work_date=shift.work_date,
    ).exists():
        _operation_error(
            "worker_day_off",
            f"The selected worker has a day off on {shift.work_date.isoformat()}.",
            work_date=shift.work_date.isoformat(),
            worker=str(connection.public_id),
        )
    if ProjectCrewMemberAbsence.objects.filter(
        crew=shift.crew,
        connection=connection,
        work_date=shift.work_date,
    ).exists():
        _operation_error(
            "worker_absent_from_crew",
            f"The selected worker is absent from this crew on {shift.work_date.isoformat()}.",
            work_date=shift.work_date.isoformat(),
            worker=str(connection.public_id),
        )
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
    day_off_connection_ids = set(
        WorkerScheduleDayOff.objects.filter(
            organization=shift.crew.organization,
            work_date=shift.work_date,
        ).values_list("connection_id", flat=True)
    )
    absent_connection_ids = set(
        ProjectCrewMemberAbsence.objects.filter(
            crew=shift.crew,
            work_date=shift.work_date,
        ).values_list("connection_id", flat=True)
    )
    unavailable_connection_ids = day_off_connection_ids | absent_connection_ids
    roster = [
        item
        for item in _active_roster_for_date(
            crew=shift.crew,
            work_date=shift.work_date,
            lock=True,
        )
        if item.connection_id not in unavailable_connection_ids
    ]
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
    driver_is_unavailable = resource.driver_connection_id in unavailable_connection_ids
    if not driver_is_unavailable:
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
        if driver_is_unavailable:
            _resolve_passenger_conflicts(
                connection=roster_entry.connection,
                shift=shift,
            )
            member = ProjectCrewShiftMember(
                shift=shift,
                connection=roster_entry.connection,
                role=ProjectCrewShiftMember.ROLE_PASSENGER,
                created_by=actor,
            )
            member.full_clean()
            member.save()
            _sync_member_worker_calendar(member=member, actor=actor)
        else:
            _add_passenger_to_shift(
                actor=actor,
                shift=shift,
                connection=roster_entry.connection,
            )


def _remove_unavailable_members(*, shift):
    """Keep days off and crew-specific absences authoritative on republish."""

    day_off_connection_ids = WorkerScheduleDayOff.objects.filter(
        organization=shift.crew.organization,
        work_date=shift.work_date,
    ).values_list("connection_id", flat=True)
    absent_connection_ids = ProjectCrewMemberAbsence.objects.filter(
        crew=shift.crew,
        work_date=shift.work_date,
    ).values_list("connection_id", flat=True)
    shift.members.filter(
        Q(connection_id__in=day_off_connection_ids)
        | Q(connection_id__in=absent_connection_ids)
    ).delete()


def _connection_is_unavailable_for_crew_day(*, crew, connection, work_date):
    """Return whether a worker must stay outside this crew on one day."""

    if WorkerScheduleDayOff.objects.filter(
        organization=crew.organization,
        connection=connection,
        work_date=work_date,
    ).exists():
        return True
    return ProjectCrewMemberAbsence.objects.filter(
        crew=crew,
        connection=connection,
        work_date=work_date,
    ).exists()


def project_crew_substitute_driver_candidates(
    *,
    crew,
    work_dates,
    candidate_connections=None,
):
    """Return drivers available for every selected primary-driver absence.

    Current passengers are allowed to take the wheel because their mirrored
    schedule belongs to the same crew day. Any other active schedule, day off
    or crew absence makes a worker unavailable for that date.
    """

    dates = _normalize_dates(work_dates)
    past_dates = [work_date for work_date in dates if work_date < timezone.localdate()]
    if past_dates:
        _operation_error(
            "substitution_date_in_past",
            "A substitute driver cannot be assigned to a past crew day.",
            work_dates=[item.isoformat() for item in past_dates],
        )
    resources_by_date = {
        work_date: _resource_for_date(crew=crew, work_date=work_date)
        for work_date in dates
    }
    if any(resource is None for resource in resources_by_date.values()):
        _operation_error(
            "crew_resource_missing",
            "The crew has no primary driver and vehicle for one of the selected dates.",
        )
    shifts_by_date = {
        shift.work_date: shift
        for shift in ProjectCrewShift.objects.filter(
            crew=crew,
            work_date__in=dates,
            state=ProjectCrewShift.STATE_PUBLISHED,
        ).prefetch_related("members")
    }
    missing_shift_dates = [item for item in dates if item not in shifts_by_date]
    if missing_shift_dates:
        _operation_error(
            "crew_shift_missing",
            "A published crew shift is required on every substitution date.",
            work_dates=[item.isoformat() for item in missing_shift_dates],
        )
    active_substitution_dates = set(
        ProjectCrewDriverSubstitution.objects.filter(
            crew=crew,
            work_date__in=dates,
            state=ProjectCrewDriverSubstitution.STATE_ACTIVE,
        ).values_list("work_date", flat=True)
    )
    driver_present_dates = [
        work_date
        for work_date, shift in shifts_by_date.items()
        if work_date not in active_substitution_dates
        if any(
            member.role == ProjectCrewShiftMember.ROLE_DRIVER
            for member in shift.members.all()
        )
    ]
    if driver_present_dates:
        _operation_error(
            "substitution_requires_driver_absence",
            "A substitute can be selected only for published crew days without a driver.",
            work_dates=[item.isoformat() for item in driver_present_dates],
        )

    primary_driver_ids = {
        resource.driver_connection_id for resource in resources_by_date.values()
    }
    candidates = SupportConnection.objects.filter(
        organization=crew.organization,
        is_archived=False,
        has_driving_license=True,
    ).exclude(id__in=primary_driver_ids).select_related("candidate")
    if candidate_connections is not None:
        candidates = candidates.filter(
            id__in=[item.id for item in candidate_connections]
        )

    passenger_ids = set(
        ProjectCrewPassenger.objects.filter(
            crew=crew,
            connection__in=candidates,
            starts_on__lte=max(dates),
        ).filter(
            Q(ends_on__isnull=True) | Q(ends_on__gte=min(dates))
        ).values_list("connection_id", flat=True)
    )
    passenger_ids.update(
        ProjectCrewShiftMember.objects.filter(
            shift__crew=crew,
            shift__work_date__in=dates,
            role=ProjectCrewShiftMember.ROLE_PASSENGER,
            connection__in=candidates,
        ).values_list("connection_id", flat=True)
    )

    unavailable_ids = set(
        WorkerScheduleDayOff.objects.filter(
            organization=crew.organization,
            connection__in=candidates,
            work_date__in=dates,
        ).values_list("connection_id", flat=True)
    )
    unavailable_ids.update(
        ProjectCrewMemberAbsence.objects.filter(
            organization=crew.organization,
            connection__in=candidates,
            work_date__in=dates,
        ).values_list("connection_id", flat=True)
    )
    active_substitute_pairs = set(
        ProjectCrewDriverSubstitution.objects.filter(
            crew=crew,
            state=ProjectCrewDriverSubstitution.STATE_ACTIVE,
            work_date__in=dates,
            substitute_driver_connection__in=candidates,
        ).values_list("substitute_driver_connection_id", "work_date")
    )
    schedule_conflicts = ScheduledWorkShift.objects.filter(
        organization=crew.organization,
        connection__in=candidates,
        work_date__in=dates,
        state__in=(
            ScheduledWorkShift.STATE_DRAFT,
            ScheduledWorkShift.STATE_PUBLISHED,
        ),
    ).select_related("project_crew_member__shift")
    for conflict in schedule_conflicts:
        membership = conflict.project_crew_member
        if membership is not None and membership.shift.crew_id == crew.id:
            if membership.role == ProjectCrewShiftMember.ROLE_PASSENGER:
                continue
            if (
                membership.role == ProjectCrewShiftMember.ROLE_DRIVER
                and (conflict.connection_id, conflict.work_date) in active_substitute_pairs
            ):
                continue
        unavailable_ids.add(conflict.connection_id)

    available = [item for item in candidates if item.id not in unavailable_ids]
    for item in available:
        item.is_current_crew_passenger = item.id in passenger_ids
    return sorted(
        available,
        key=lambda item: (
            not item.is_current_crew_passenger,
            (item.candidate.first_name or "").casefold(),
            (item.candidate.last_name or "").casefold(),
            item.id,
        ),
    )


def _restore_replaced_substitute_membership(*, substitution, actor):
    shift = ProjectCrewShift.objects.select_for_update().filter(
        crew=substitution.crew,
        work_date=substitution.work_date,
        state=ProjectCrewShift.STATE_PUBLISHED,
    ).first()
    if shift is None:
        return
    member = shift.members.select_for_update().filter(
        connection=substitution.substitute_driver_connection,
        role=ProjectCrewShiftMember.ROLE_DRIVER,
    ).first()
    if member is None:
        return
    if substitution.substitute_was_passenger:
        member.role = ProjectCrewShiftMember.ROLE_PASSENGER
        member.vehicle = None
        member.full_clean()
        member.save(update_fields=("role", "vehicle", "updated_at"))
        _sync_member_worker_calendar(member=member, actor=actor)
    else:
        member.delete()


def _close_active_substitutions(
    *,
    crew,
    work_dates,
    actor,
    state,
    substitute_connection=None,
):
    dates = sorted(set(work_dates))
    if not dates:
        return []
    queryset = (
        ProjectCrewDriverSubstitution.objects.select_for_update()
        .select_related("substitute_driver_connection", "crew")
        .filter(
            crew=crew,
            work_date__in=dates,
            state=ProjectCrewDriverSubstitution.STATE_ACTIVE,
        )
    )
    if substitute_connection is not None:
        queryset = queryset.filter(
            substitute_driver_connection=substitute_connection,
        )
    closed = list(queryset.order_by("work_date", "id"))
    ended_at = timezone.now()
    for substitution in closed:
        _restore_replaced_substitute_membership(
            substitution=substitution,
            actor=actor,
        )
        substitution.state = state
        substitution.ended_by = actor
        substitution.ended_at = ended_at
        substitution.full_clean()
        substitution.save(update_fields=("state", "ended_by", "ended_at"))
    return closed


@transaction.atomic
def assign_project_crew_substitute_driver(
    *,
    actor,
    crew,
    substitute_driver_connection,
    work_dates,
):
    """Replace the active substitute selection for a crew.

    The primary resource assignment is never changed. Active substitution
    records are closed as history, then the selected worker drives the crew's
    existing vehicle only on the chosen primary-driver absence dates.
    """

    crew = ProjectCrew.objects.select_for_update().select_related("organization").get(pk=crew.pk)
    organization = crew.organization
    _require_permissions(actor=actor, organization=organization)
    substitute = SupportConnection.objects.select_for_update().get(
        pk=substitute_driver_connection.pk
    )
    _require_connection(actor=actor, organization=organization, connection=substitute)
    if not substitute.has_driving_license:
        _operation_error(
            "driver_licence_not_confirmed",
            "The substitute driver must have a confirmed driving licence.",
        )
    dates = _normalize_dates(work_dates)
    candidates = project_crew_substitute_driver_candidates(
        crew=crew,
        work_dates=dates,
        candidate_connections=[substitute],
    )
    if not candidates:
        _operation_error(
            "substitute_driver_unavailable",
            "The selected substitute driver is unavailable on one or more selected dates.",
            work_dates=[item.isoformat() for item in dates],
        )

    shifts_by_date = {
        shift.work_date: shift
        for shift in ProjectCrewShift.objects.select_for_update().filter(
            crew=crew,
            work_date__in=dates,
            state=ProjectCrewShift.STATE_PUBLISHED,
        )
    }
    missing_dates = [item for item in dates if item not in shifts_by_date]
    if missing_dates:
        _operation_error(
            "crew_shift_missing",
            "A published crew shift is required on every substitution date.",
            work_dates=[item.isoformat() for item in missing_dates],
        )

    active_substitutions = _close_active_substitutions(
        crew=crew,
        work_dates=ProjectCrewDriverSubstitution.objects.filter(
            crew=crew,
            state=ProjectCrewDriverSubstitution.STATE_ACTIVE,
            work_date__gte=timezone.localdate(),
        ).values_list("work_date", flat=True),
        actor=actor,
        state=ProjectCrewDriverSubstitution.STATE_REPLACED,
    )

    created = []
    for work_date in dates:
        shift = shifts_by_date[work_date]
        resource = _resource_for_date(crew=crew, work_date=work_date, lock=True)
        if resource is None:
            _operation_error(
                "crew_resource_missing",
                f"The crew has no primary driver and vehicle on {work_date.isoformat()}.",
                work_date=work_date.isoformat(),
            )
        existing_member = shift.members.select_for_update().filter(
            connection=substitute,
        ).first()
        substitute_was_passenger = bool(
            existing_member
            and existing_member.role == ProjectCrewShiftMember.ROLE_PASSENGER
        )
        shift.members.filter(role=ProjectCrewShiftMember.ROLE_DRIVER).exclude(
            connection=substitute,
        ).delete()
        if existing_member is None:
            existing_member = ProjectCrewShiftMember(
                shift=shift,
                connection=substitute,
                role=ProjectCrewShiftMember.ROLE_DRIVER,
                vehicle=resource.vehicle,
                created_by=actor,
            )
        else:
            existing_member.role = ProjectCrewShiftMember.ROLE_DRIVER
            existing_member.vehicle = resource.vehicle
        existing_member.full_clean()
        existing_member.save()
        _sync_member_worker_calendar(member=existing_member, actor=actor)

        substitution = ProjectCrewDriverSubstitution(
            organization=organization,
            crew=crew,
            work_date=work_date,
            primary_driver_connection=resource.driver_connection,
            substitute_driver_connection=substitute,
            vehicle=resource.vehicle,
            substitute_was_passenger=substitute_was_passenger,
            state=ProjectCrewDriverSubstitution.STATE_ACTIVE,
            created_by=actor,
        )
        substitution.full_clean()
        substitution.save()
        created.append(substitution)

    record_audit_event(
        organization=organization,
        actor=actor,
        action="project_crew.substitute_driver_assigned",
        target=crew,
        details={
            "substitute_driver": str(substitute.public_id),
            "work_dates": [item.isoformat() for item in dates],
            "replaced_records": len(active_substitutions),
        },
    )
    return created


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
    today = timezone.localdate()
    starts_on = starts_on or today
    other_project_resources = list(
        ProjectCrewResourceAssignment.objects.select_for_update()
        .select_related("crew__project", "vehicle")
        .filter(
            crew__organization=organization,
            crew__state=ProjectCrew.STATE_ACTIVE,
            driver_connection=driver_connection,
            ends_on__isnull=True,
        )
        .order_by("-starts_on", "-id")
    )
    if other_project_resources:
        # A driver who already works in another project keeps the same car.
        # Whether the person can work for both projects is decided later from
        # the actual published shift dates, not while creating the crew.
        assigned_vehicle_ids = {item.vehicle_id for item in other_project_resources}
        if vehicle.id not in assigned_vehicle_ids:
            _operation_error(
                "driver_project_vehicle_locked",
                "A driver assigned to another project must keep the vehicle already attached to them.",
                vehicle=str(other_project_resources[0].vehicle.public_id),
            )

    vehicle_project_conflict = (
        ProjectCrewResourceAssignment.objects.select_for_update()
        .filter(
            crew__organization=organization,
            crew__state=ProjectCrew.STATE_ACTIVE,
            vehicle=vehicle,
            ends_on__isnull=True,
        )
        .exclude(driver_connection=driver_connection)
        .exists()
    )
    if vehicle_project_conflict:
        _operation_error(
            "driver_or_vehicle_already_assigned",
            "The selected vehicle is attached to another driver.",
        )

    legacy_resources = DriverVehicleAssignment.objects.select_for_update().filter(
        organization=organization,
        state__in=(
            DriverVehicleAssignment.STATE_DRAFT,
            DriverVehicleAssignment.STATE_PUBLISHED,
        ),
    ).filter(Q(ends_on__isnull=True) | Q(ends_on__gte=starts_on))
    legacy_vehicle_conflict = legacy_resources.filter(vehicle=vehicle).exclude(
        driver_connection=driver_connection,
    ).exists()
    if legacy_vehicle_conflict:
        _operation_error(
            "legacy_driver_or_vehicle_already_assigned",
            "The selected vehicle is attached to another driver in the fleet.",
        )

    # A fleet-only driver becomes a project driver. Their previous fleet row
    # is historical after this point; selecting another free car therefore
    # returns the old car to the fleet automatically.
    legacy_resources.filter(driver_connection=driver_connection).update(
        state=DriverVehicleAssignment.STATE_CANCELLED,
        cancelled_at=timezone.now(),
    )

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
        starts_on=starts_on,
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
def archive_project_crew(*, actor, crew):
    """Archive a crew and release every open worker/vehicle assignment.

    Historical rows remain available for audit, while cancelled crew days are
    mirrored to worker calendars so nobody remains attached to the deleted
    crew in the active workspace.
    """

    crew = (
        ProjectCrew.objects.select_for_update()
        .select_related("organization", "project")
        .get(pk=crew.pk)
    )
    organization = crew.organization
    _require_permissions(actor=actor, organization=organization)
    if crew.state == ProjectCrew.STATE_ARCHIVED:
        return crew

    today = timezone.localdate()
    shifts = list(
        ProjectCrewShift.objects.select_for_update()
        .filter(crew=crew)
        .prefetch_related("members")
        .order_by("work_date", "id")
    )
    _close_active_substitutions(
        crew=crew,
        work_dates=[shift.work_date for shift in shifts],
        actor=actor,
        state=ProjectCrewDriverSubstitution.STATE_CANCELLED,
    )
    cancelled_shifts = 0
    for shift in shifts:
        if shift.state != ProjectCrewShift.STATE_CANCELLED:
            shift.state = ProjectCrewShift.STATE_CANCELLED
            shift.updated_by = actor
            shift.save(update_fields=("state", "updated_by", "updated_at"))
            cancelled_shifts += 1
        _sync_shift_worker_calendars(shift=shift, actor=actor)

    released_resources = 0
    for assignment in ProjectCrewResourceAssignment.objects.select_for_update().filter(
        crew=crew,
        ends_on__isnull=True,
    ):
        assignment.ends_on = max(assignment.starts_on, today)
        assignment.save(update_fields=("ends_on", "updated_at"))
        released_resources += 1

    released_passengers = 0
    for assignment in ProjectCrewPassenger.objects.select_for_update().filter(
        crew=crew,
        ends_on__isnull=True,
    ):
        assignment.ends_on = max(assignment.starts_on, today)
        assignment.save(update_fields=("ends_on", "updated_at"))
        released_passengers += 1

    crew.state = ProjectCrew.STATE_ARCHIVED
    crew.save(update_fields=("state", "updated_at"))
    record_audit_event(
        organization=organization,
        actor=actor,
        action="project_crew.archived",
        target=crew,
        details={
            "project": str(crew.project.public_id),
            "cancelled_shifts": cancelled_shifts,
            "released_resources": released_resources,
            "released_passengers": released_passengers,
        },
    )
    return crew


@transaction.atomic
def archive_project(*, actor, project):
    """Hide a project and release all active crew and worker assignments."""

    project = (
        WorkProject.objects.select_for_update()
        .select_related("organization")
        .get(pk=project.pk)
    )
    organization = project.organization
    _require_permissions(actor=actor, organization=organization)
    if not project.is_active:
        return project

    active_crews = list(
        ProjectCrew.objects.select_for_update().filter(
            project=project,
            state=ProjectCrew.STATE_ACTIVE,
        )
    )
    for crew in active_crews:
        archive_project_crew(actor=actor, crew=crew)

    now = timezone.now()
    today = timezone.localdate()
    legacy_assignments = WorkerProjectAssignment.objects.select_for_update().filter(
        project=project,
        state__in=(
            WorkerProjectAssignment.STATE_DRAFT,
            WorkerProjectAssignment.STATE_PUBLISHED,
        ),
    )
    released_legacy_workers = legacy_assignments.update(
        state=WorkerProjectAssignment.STATE_CANCELLED,
        cancelled_at=now,
    )
    for legacy_crew in TransportCrew.objects.select_for_update().filter(
        project=project,
        state=TransportCrew.STATE_ACTIVE,
    ):
        legacy_crew.state = TransportCrew.STATE_ARCHIVED
        legacy_crew.ends_on = max(legacy_crew.starts_on, today)
        legacy_crew.save(update_fields=("state", "ends_on", "updated_at"))
    ProjectScheduleTemplate.objects.select_for_update().filter(
        project=project,
        is_active=True,
    ).update(is_active=False)
    project.is_active = False
    project.save(update_fields=("is_active", "updated_at"))
    record_audit_event(
        organization=organization,
        actor=actor,
        action="work_project.archived",
        target=project,
        details={
            "archived_crews": len(active_crews),
            "released_legacy_workers": released_legacy_workers,
        },
    )
    return project


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
            _remove_unavailable_members(shift=shift)
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
    _close_active_substitutions(
        crew=crew,
        work_dates=[shift.work_date for shift in shifts],
        actor=actor,
        state=ProjectCrewDriverSubstitution.STATE_CANCELLED,
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
    # Crew-specific absences only make sense while the crew has a published
    # shift on that date. Worker-wide days off deliberately remain intact.
    ProjectCrewMemberAbsence.objects.filter(
        crew=crew,
        work_date__in=dates,
    ).delete()
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
        if scope == PASSENGER_SCOPE_FUTURE and WorkerScheduleDayOff.objects.filter(
            connection=connection,
            work_date=shift.work_date,
        ).exists():
            continue
        if scope == PASSENGER_SCOPE_FUTURE and ProjectCrewMemberAbsence.objects.filter(
            crew=crew,
            connection=connection,
            work_date=shift.work_date,
        ).exists():
            continue
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
        ProjectCrewMemberAbsence.objects.filter(
            crew=crew,
            connection=connection,
            work_date__gte=effective_on,
        ).delete()
    else:
        dates = _normalize_dates(selected_dates)
        shifts = ProjectCrewShift.objects.select_for_update().filter(
            crew=crew,
            work_date__in=dates,
        )
        ProjectCrewMemberAbsence.objects.filter(
            crew=crew,
            connection=connection,
            work_date__in=dates,
        ).delete()
    _close_active_substitutions(
        crew=crew,
        work_dates=dates,
        actor=actor,
        state=ProjectCrewDriverSubstitution.STATE_CANCELLED,
        substitute_connection=connection,
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
    for membership in memberships:
        absence, _ = ProjectCrewMemberAbsence.objects.get_or_create(
            organization=organization,
            crew=membership.shift.crew,
            connection=connection,
            work_date=membership.shift.work_date,
            defaults={"created_by": actor},
        )
        absence.full_clean()
        _close_active_substitutions(
            crew=membership.shift.crew,
            work_dates=[membership.shift.work_date],
            actor=actor,
            state=ProjectCrewDriverSubstitution.STATE_CANCELLED,
            substitute_connection=connection,
        )
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
def mark_worker_schedule_days_off(*, actor, connection, work_dates):
    """Persist worker-wide days off and remove work/crew membership on those days."""

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
    for work_date in dates:
        day_off, _ = WorkerScheduleDayOff.objects.get_or_create(
            organization=organization,
            connection=connection,
            work_date=work_date,
            defaults={"created_by": actor},
        )
        day_off.full_clean()

    ProjectCrewMemberAbsence.objects.filter(
        organization=organization,
        connection=connection,
        work_date__in=dates,
    ).delete()

    memberships = list(
        ProjectCrewShiftMember.objects.select_for_update().select_related(
            "shift__crew"
        ).filter(
            connection=connection,
            shift__work_date__in=dates,
        )
    )
    for membership in memberships:
        _close_active_substitutions(
            crew=membership.shift.crew,
            work_dates=[membership.shift.work_date],
            actor=actor,
            state=ProjectCrewDriverSubstitution.STATE_CANCELLED,
            substitute_connection=connection,
        )
    if memberships:
        ProjectCrewShiftMember.objects.filter(
            pk__in=[item.pk for item in memberships]
        ).delete()

    legacy_shifts = ScheduledWorkShift.objects.select_for_update().filter(
        organization=organization,
        connection=connection,
        work_date__in=dates,
        project_crew_member__isnull=True,
        state__in=(
            ScheduledWorkShift.STATE_DRAFT,
            ScheduledWorkShift.STATE_PUBLISHED,
        ),
    )
    legacy_shifts.filter(state=ScheduledWorkShift.STATE_DRAFT).delete()
    legacy_shifts.filter(state=ScheduledWorkShift.STATE_PUBLISHED).update(
        state=ScheduledWorkShift.STATE_CANCELLED,
        cancelled_at=timezone.now(),
    )
    record_audit_event(
        organization=organization,
        actor=actor,
        action="worker.schedule_days_off_marked",
        target=connection,
        details={"work_dates": [item.isoformat() for item in dates]},
    )
    return dates


def _restore_connection_to_crew_shift(*, actor, shift, connection):
    """Restore one worker after cancelling a day off or crew absence."""

    existing = shift.members.select_for_update().filter(connection=connection).first()
    if existing:
        _sync_member_worker_calendar(member=existing, actor=actor)
        return existing

    resource = _resource_for_date(
        crew=shift.crew,
        work_date=shift.work_date,
        lock=True,
    )
    active_substitution = ProjectCrewDriverSubstitution.objects.select_for_update().filter(
        crew=shift.crew,
        work_date=shift.work_date,
        state=ProjectCrewDriverSubstitution.STATE_ACTIVE,
    ).first()
    if resource and resource.driver_connection_id == connection.id:
        if active_substitution:
            _close_active_substitutions(
                crew=shift.crew,
                work_dates=[shift.work_date],
                actor=actor,
                state=ProjectCrewDriverSubstitution.STATE_CANCELLED,
            )
        shift.members.filter(role=ProjectCrewShiftMember.ROLE_DRIVER).delete()
        member = ProjectCrewShiftMember(
            shift=shift,
            connection=connection,
            role=ProjectCrewShiftMember.ROLE_DRIVER,
            vehicle=resource.vehicle,
            created_by=actor,
        )
        member.full_clean()
        member.save()
        _sync_member_worker_calendar(member=member, actor=actor)
        return member

    roster_exists = ProjectCrewPassenger.objects.filter(
        crew=shift.crew,
        connection=connection,
        starts_on__lte=shift.work_date,
    ).filter(Q(ends_on__isnull=True) | Q(ends_on__gte=shift.work_date)).exists()
    if roster_exists:
        member, _ = _add_passenger_to_shift(
            actor=actor,
            shift=shift,
            connection=connection,
        )
        return member
    return None


@transaction.atomic
def restore_project_crew_member_days(*, actor, connection, work_dates):
    """Cancel crew-specific absences and restore applicable daily membership."""

    connection = (
        SupportConnection.objects.select_for_update()
        .select_related("organization")
        .get(pk=connection.pk)
    )
    organization = connection.organization
    _require_permissions(actor=actor, organization=organization)
    _require_connection(actor=actor, organization=organization, connection=connection)
    dates = _normalize_dates(work_dates)
    absences = list(
        ProjectCrewMemberAbsence.objects.select_for_update()
        .select_related("crew")
        .filter(connection=connection, work_date__in=dates)
    )
    if not absences:
        _operation_error(
            "selected_schedule_days_have_no_absence",
            "The selected days contain no crew absence to cancel.",
        )
    absence_keys = {(item.crew_id, item.work_date) for item in absences}
    ProjectCrewMemberAbsence.objects.filter(pk__in=[item.pk for item in absences]).delete()
    shifts = ProjectCrewShift.objects.select_for_update().select_related("crew").filter(
        crew_id__in={item.crew_id for item in absences},
        work_date__in={item.work_date for item in absences},
        state=ProjectCrewShift.STATE_PUBLISHED,
    )
    restored = []
    for shift in shifts:
        if (shift.crew_id, shift.work_date) not in absence_keys:
            continue
        member = _restore_connection_to_crew_shift(
            actor=actor,
            shift=shift,
            connection=connection,
        )
        if member:
            restored.append(shift.work_date)
    record_audit_event(
        organization=organization,
        actor=actor,
        action="project_crew.worker_days_restored",
        target=connection,
        details={"work_dates": [item.isoformat() for item in dates]},
    )
    return sorted(set(restored))


@transaction.atomic
def restore_worker_schedule_days_off(*, actor, connection, work_dates):
    """Cancel worker-wide days off and restore current crew membership."""

    connection = (
        SupportConnection.objects.select_for_update()
        .select_related("organization")
        .get(pk=connection.pk)
    )
    organization = connection.organization
    _require_permissions(actor=actor, organization=organization)
    _require_connection(actor=actor, organization=organization, connection=connection)
    dates = _normalize_dates(work_dates)
    days_off = list(
        WorkerScheduleDayOff.objects.select_for_update().filter(
            connection=connection,
            work_date__in=dates,
        )
    )
    if not days_off:
        _operation_error(
            "selected_schedule_days_have_no_day_off",
            "The selected days contain no day off to cancel.",
        )
    restored_dates = {item.work_date for item in days_off}
    WorkerScheduleDayOff.objects.filter(pk__in=[item.pk for item in days_off]).delete()
    # PostgreSQL does not allow SELECT FOR UPDATE together with DISTINCT.
    # First resolve the matching primary keys, then lock the concrete shift
    # rows in a separate query.  This keeps the operation atomic without the
    # production-only NotSupportedError that SQLite does not expose.
    shift_ids = list(ProjectCrewShift.objects.filter(
        state=ProjectCrewShift.STATE_PUBLISHED,
        work_date__in=restored_dates,
    ).filter(
        Q(
            crew__resource_assignments__driver_connection=connection,
            crew__resource_assignments__starts_on__lte=F("work_date"),
        )
        | Q(
            crew__passenger_assignments__connection=connection,
            crew__passenger_assignments__starts_on__lte=F("work_date"),
        )
    ).values_list("pk", flat=True).distinct())
    shifts = (
        ProjectCrewShift.objects.select_for_update()
        .select_related("crew")
        .filter(pk__in=shift_ids)
        .order_by("work_date", "pk")
    )
    restored = []
    for shift in shifts:
        member = _restore_connection_to_crew_shift(
            actor=actor,
            shift=shift,
            connection=connection,
        )
        if member:
            restored.append(shift.work_date)
    record_audit_event(
        organization=organization,
        actor=actor,
        action="worker.schedule_days_off_cancelled",
        target=connection,
        details={"work_dates": [item.isoformat() for item in dates]},
    )
    return sorted(set(restored))


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
    current = (
        ProjectCrewResourceAssignment.objects.select_for_update()
        .select_related("driver_connection", "vehicle")
        .filter(crew=crew, ends_on__isnull=True)
        .order_by("-starts_on", "-id")
        .first()
    )
    if current is None:
        current = _resource_for_date(crew=crew, work_date=effective_on, lock=True)
    if current is None:
        _operation_error(
            "crew_resource_missing",
            "The crew has no active driver and vehicle to replace on this date.",
        )
    if effective_on < current.starts_on:
        effective_on = current.starts_on
    if current.driver_connection_id == new_driver.id:
        return current

    future_substitution_dates = ProjectCrewDriverSubstitution.objects.filter(
        crew=crew,
        state=ProjectCrewDriverSubstitution.STATE_ACTIVE,
        work_date__gte=effective_on,
    ).values_list("work_date", flat=True)
    _close_active_substitutions(
        crew=crew,
        work_dates=future_substitution_dates,
        actor=actor,
        state=ProjectCrewDriverSubstitution.STATE_CANCELLED,
    )

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
        new_driver_is_unavailable = _connection_is_unavailable_for_crew_day(
            crew=crew,
            connection=new_driver,
            work_date=shift.work_date,
        )
        old_driver_is_unavailable = _connection_is_unavailable_for_crew_day(
            crew=crew,
            connection=old_driver,
            work_date=shift.work_date,
        )
        new_driver_conflicts = _overlapping_memberships(
            connection=new_driver,
            starts_at=shift.starts_at,
            ends_at=shift.ends_at,
            exclude_shift=shift,
        )
        if new_driver_conflicts and not new_driver_is_unavailable:
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
        if not new_driver_is_unavailable:
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
        if not old_driver_is_unavailable:
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
