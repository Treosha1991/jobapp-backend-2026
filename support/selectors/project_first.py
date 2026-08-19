"""Read models for the project-first employer workspace.

The employer web page and the mobile API must read the same canonical tables.
This module deliberately contains no mutations; write operations stay in the
transactional ``support.services.project_crews`` service layer.
"""

from calendar import monthrange
from datetime import date

from django.db.models import Prefetch, Q
from django.utils import timezone

from support.models import (
    DriverVehicleAssignment,
    ProjectCrew,
    ProjectCrewDriverSubstitution,
    ProjectCrewMemberAbsence,
    ProjectCrewPassenger,
    ProjectCrewResourceAssignment,
    ProjectCrewShift,
    ProjectCrewShiftMember,
    SupportConnection,
    Vehicle,
    WorkerScheduleDayOff,
    WorkProject,
)


def _display_name(connection):
    user = connection.candidate
    full_name = user.get_full_name().strip()
    return full_name or user.username or user.email


def _connection_payload(connection):
    return {
        "id": str(connection.public_id),
        "display_name": _display_name(connection),
        "stage": connection.stage,
        "has_driving_license": connection.has_driving_license,
    }


def _vehicle_payload(vehicle):
    return {
        "id": str(vehicle.public_id),
        "internal_name": vehicle.internal_name,
        "registration_identifier": vehicle.registration_identifier,
        "seat_capacity": vehicle.seat_capacity,
    }


def project_first_crew_payload(crew):
    """Return one canonical crew summary after a write operation."""

    resource = (
        ProjectCrewResourceAssignment.objects.filter(crew=crew, ends_on__isnull=True)
        .select_related("driver_connection__candidate", "vehicle")
        .order_by("-starts_on", "-id")
        .first()
    )
    passengers = (
        ProjectCrewPassenger.objects.filter(crew=crew, ends_on__isnull=True)
        .select_related("connection__candidate")
        .order_by(
            "connection__candidate__first_name",
            "connection__candidate__last_name",
            "id",
        )
    )
    return {
        "id": str(crew.public_id),
        "project_id": str(crew.project.public_id),
        "internal_name": crew.internal_name,
        "state": crew.state,
        "current_resource": (
            {
                "id": str(resource.public_id),
                "driver": _connection_payload(resource.driver_connection),
                "vehicle": _vehicle_payload(resource.vehicle),
                "starts_on": resource.starts_on.isoformat(),
                "ends_on": resource.ends_on.isoformat() if resource.ends_on else None,
            }
            if resource is not None
            else None
        ),
        "default_passengers": [
            {
                "id": str(item.public_id),
                "worker": _connection_payload(item.connection),
                "starts_on": item.starts_on.isoformat(),
                "ends_on": item.ends_on.isoformat() if item.ends_on else None,
            }
            for item in passengers
        ],
    }


def _crew_shift_payload(shift):
    members = []
    for member in shift.members.select_related("connection__candidate", "vehicle"):
        item = {
            "id": str(member.public_id),
            "role": member.role,
            "worker": _connection_payload(member.connection),
        }
        if member.vehicle_id:
            item["vehicle"] = _vehicle_payload(member.vehicle)
        members.append(item)
    return {
        "id": str(shift.public_id),
        "state": shift.state,
        "work_date": shift.work_date.isoformat(),
        "starts_at": timezone.localtime(shift.starts_at).isoformat(),
        "ends_at": timezone.localtime(shift.ends_at).isoformat(),
        "break_minutes": shift.break_minutes,
        "has_driver": any(
            item["role"] == ProjectCrewShiftMember.ROLE_DRIVER for item in members
        ),
        "members": members,
    }


def project_first_crew_days_payload(*, crew, work_dates):
    """Return exact canonical state for the dates affected by a write."""

    dates = sorted(set(work_dates))
    shifts = {
        shift.work_date: shift
        for shift in ProjectCrewShift.objects.filter(
            crew=crew,
            work_date__in=dates,
        )
        .prefetch_related(
            Prefetch(
                "members",
                queryset=ProjectCrewShiftMember.objects.select_related(
                    "connection__candidate",
                    "vehicle",
                ).order_by("role", "connection__candidate__first_name", "id"),
            )
        )
        .order_by("work_date", "id")
    }
    return [
        {
            "date": work_date.isoformat(),
            "shift": (
                _crew_shift_payload(shifts[work_date])
                if work_date in shifts
                else None
            ),
        }
        for work_date in dates
    ]


def project_first_driver_exceptions_payload(*, crew, work_dates):
    """Return exact driver-absence and temporary-substitution state by day."""

    dates = sorted(set(work_dates))
    absences = ProjectCrewMemberAbsence.objects.filter(
        crew=crew,
        work_date__in=dates,
    ).select_related("connection__candidate")
    substitutions = ProjectCrewDriverSubstitution.objects.filter(
        crew=crew,
        work_date__in=dates,
        state=ProjectCrewDriverSubstitution.STATE_ACTIVE,
    ).select_related(
        "primary_driver_connection__candidate",
        "substitute_driver_connection__candidate",
        "vehicle",
    )
    return {
        "driver_absences": [
            {
                "id": str(item.public_id),
                "work_date": item.work_date.isoformat(),
                "driver": _connection_payload(item.connection),
            }
            for item in absences.order_by("work_date", "id")
        ],
        "driver_substitutions": [
            {
                "id": str(item.public_id),
                "work_date": item.work_date.isoformat(),
                "primary_driver": _connection_payload(
                    item.primary_driver_connection
                ),
                "substitute_driver": _connection_payload(
                    item.substitute_driver_connection
                ),
                "vehicle": _vehicle_payload(item.vehicle),
                "state": item.state,
            }
            for item in substitutions.order_by("work_date", "id")
        ],
    }


def _worksite_payload(worksite):
    return {
        "id": str(worksite.public_id),
        "internal_name": worksite.internal_name,
        "country_code": worksite.country_code,
        "city": worksite.city,
        "postal_code": worksite.postal_code,
        "street": worksite.street,
        "building": worksite.building,
    }


def project_first_project_payload(project, *, include_counts=False):
    payload = {
        "id": str(project.public_id),
        "is_active": project.is_active,
        "internal_name": project.internal_name,
        "worker_visible_name": project.worker_visible_name,
        "worker_capacity": project.worker_capacity,
        "starts_on": project.starts_on.isoformat(),
        "ends_on": project.ends_on.isoformat() if project.ends_on else None,
        "contact": {
            "name": project.contact_name,
            "phone": project.contact_phone,
            "email": project.contact_email,
        },
        "instructions": project.instructions,
        "worksite": _worksite_payload(project.worksite),
    }
    if include_counts:
        crews = list(project.project_crews.all())
        worker_ids = set()
        shift_count = 0
        for crew in crews:
            worker_ids.update(
                assignment.driver_connection_id
                for assignment in crew.resource_assignments.all()
                if assignment.ends_on is None
            )
            worker_ids.update(
                assignment.connection_id
                for assignment in crew.passenger_assignments.all()
                if assignment.ends_on is None
            )
            shift_count += sum(
                1
                for shift in crew.calendar_shifts.all()
                if shift.state == ProjectCrewShift.STATE_PUBLISHED
            )
        payload["summary"] = {
            "crew_count": len(crews),
            "permanent_worker_count": len(worker_ids),
            "published_shift_count": shift_count,
        }
    return payload


def project_first_project_list(*, organization):
    projects = list(
        WorkProject.objects.filter(organization=organization, is_active=True)
        .select_related("worksite")
        .prefetch_related(
            Prefetch(
                "project_crews",
                queryset=ProjectCrew.objects.filter(state=ProjectCrew.STATE_ACTIVE)
                .prefetch_related(
                    "resource_assignments",
                    "passenger_assignments",
                    "calendar_shifts",
                )
                .order_by("internal_name", "id"),
            )
        )
        .order_by("internal_name", "id")
    )
    return [project_first_project_payload(project, include_counts=True) for project in projects]


def project_first_creation_options(*, organization):
    """Return bounded driver and vehicle choices for project/crew creation.

    The project-first access mixin already requires unrestricted worker access,
    schedule management and transport management.  Keeping these choices in the
    same API response lets mobile clients use the exact same canonical IDs as
    the write endpoints without falling back to the legacy route builder.
    """

    drivers = list(
        SupportConnection.objects.filter(
            organization=organization,
            is_archived=False,
            has_driving_license=True,
            stage__in=(
                SupportConnection.STAGE_COORDINATOR,
                SupportConnection.STAGE_ACTIVE_WORKER,
            ),
        )
        .select_related("candidate")
        .order_by(
            "candidate__first_name",
            "candidate__last_name",
            "candidate__username",
            "id",
        )[:250]
    )
    driver_ids = [item.id for item in drivers]
    project_resources = list(
        ProjectCrewResourceAssignment.objects.filter(
            crew__organization=organization,
            crew__state=ProjectCrew.STATE_ACTIVE,
            driver_connection_id__in=driver_ids,
            ends_on__isnull=True,
        )
        .select_related("crew__project", "vehicle")
        .order_by("driver_connection_id", "-starts_on", "-id")
    )
    fleet_resources = list(
        DriverVehicleAssignment.objects.filter(
            organization=organization,
            driver_connection_id__in=driver_ids,
            state__in=(
                DriverVehicleAssignment.STATE_DRAFT,
                DriverVehicleAssignment.STATE_PUBLISHED,
            ),
            ends_on__isnull=True,
        )
        .select_related("vehicle")
        .order_by("driver_connection_id", "-starts_on", "-id")
    )
    project_by_driver = {}
    for resource in project_resources:
        project_by_driver.setdefault(resource.driver_connection_id, []).append(resource)
    fleet_by_driver = {}
    for resource in fleet_resources:
        fleet_by_driver.setdefault(resource.driver_connection_id, resource)

    return {
        "drivers": [
            {
                **_connection_payload(connection),
                "preferred_vehicle_id": str(
                    (
                        project_by_driver.get(connection.id, [None])[0].vehicle.public_id
                        if project_by_driver.get(connection.id)
                        else fleet_by_driver[connection.id].vehicle.public_id
                    )
                )
                if project_by_driver.get(connection.id) or connection.id in fleet_by_driver
                else None,
                "project_vehicle_locked": bool(project_by_driver.get(connection.id)),
                "project_names": sorted(
                    {
                        item.crew.project.worker_visible_name
                        or item.crew.project.internal_name
                        for item in project_by_driver.get(connection.id, [])
                    },
                    key=str.casefold,
                ),
            }
            for connection in drivers
        ],
        "vehicles": [
            _vehicle_payload(vehicle)
            for vehicle in Vehicle.objects.filter(
                organization=organization,
                is_active=True,
            ).order_by("internal_name", "registration_identifier", "id")[:250]
        ],
    }


def project_first_project_workspace(*, project, selected_month):
    """Return one calendar month with exact crew membership per published day."""

    month_start = selected_month.replace(day=1)
    month_end = month_start.replace(day=monthrange(month_start.year, month_start.month)[1])
    crews = list(
        ProjectCrew.objects.filter(project=project, state=ProjectCrew.STATE_ACTIVE)
        .prefetch_related(
            Prefetch(
                "resource_assignments",
                queryset=ProjectCrewResourceAssignment.objects.filter(
                    Q(ends_on__isnull=True) | Q(ends_on__gte=month_start),
                    starts_on__lte=month_end,
                )
                .select_related("driver_connection__candidate", "vehicle")
                .order_by("-starts_on", "-id"),
            ),
            Prefetch(
                "passenger_assignments",
                queryset=ProjectCrewPassenger.objects.filter(
                    Q(ends_on__isnull=True) | Q(ends_on__gte=month_start),
                    starts_on__lte=month_end,
                )
                .select_related("connection__candidate")
                .order_by("starts_on", "id"),
            ),
            Prefetch(
                "calendar_shifts",
                queryset=ProjectCrewShift.objects.filter(
                    state=ProjectCrewShift.STATE_PUBLISHED,
                    work_date__range=(month_start, month_end),
                )
                .prefetch_related(
                    Prefetch(
                        "members",
                        queryset=ProjectCrewShiftMember.objects.select_related(
                            "connection__candidate",
                            "vehicle",
                        ).order_by("role", "connection__candidate__first_name", "id"),
                    )
                )
                .order_by("work_date", "id"),
            ),
            Prefetch(
                "member_absences",
                queryset=ProjectCrewMemberAbsence.objects.filter(
                    work_date__range=(month_start, month_end),
                )
                .select_related("connection__candidate")
                .order_by("work_date", "connection_id"),
            ),
            Prefetch(
                "driver_substitutions",
                queryset=ProjectCrewDriverSubstitution.objects.filter(
                    work_date__range=(month_start, month_end),
                )
                .select_related(
                    "primary_driver_connection__candidate",
                    "substitute_driver_connection__candidate",
                    "vehicle",
                )
                .order_by("work_date", "id"),
            ),
        )
        .order_by("internal_name", "id")
    )

    connection_ids = set()
    for crew in crews:
        connection_ids.update(item.driver_connection_id for item in crew.resource_assignments.all())
        connection_ids.update(item.connection_id for item in crew.passenger_assignments.all())
        for shift in crew.calendar_shifts.all():
            connection_ids.update(item.connection_id for item in shift.members.all())
    connection_public_ids = dict(
        SupportConnection.objects.filter(id__in=connection_ids).values_list(
            "id",
            "public_id",
        )
    )
    day_offs = {}
    for connection_id, work_date in WorkerScheduleDayOff.objects.filter(
        organization=project.organization,
        connection_id__in=connection_ids,
        work_date__range=(month_start, month_end),
    ).values_list("connection_id", "work_date"):
        day_offs.setdefault(connection_id, []).append(work_date.isoformat())

    crew_payloads = []
    for crew in crews:
        shifts_by_date = {}
        for shift in crew.calendar_shifts.all():
            members = []
            for member in shift.members.all():
                item = {
                    "id": str(member.public_id),
                    "role": member.role,
                    "worker": _connection_payload(member.connection),
                }
                if member.vehicle_id:
                    item["vehicle"] = _vehicle_payload(member.vehicle)
                members.append(item)
            shifts_by_date[shift.work_date] = {
                "id": str(shift.public_id),
                "state": shift.state,
                "work_date": shift.work_date.isoformat(),
                "starts_at": timezone.localtime(shift.starts_at).isoformat(),
                "ends_at": timezone.localtime(shift.ends_at).isoformat(),
                "break_minutes": shift.break_minutes,
                "has_driver": any(item["role"] == ProjectCrewShiftMember.ROLE_DRIVER for item in members),
                "members": members,
            }

        calendar = []
        for day_number in range(1, month_end.day + 1):
            work_date = date(month_start.year, month_start.month, day_number)
            calendar.append(
                {
                    "date": work_date.isoformat(),
                    "shift": shifts_by_date.get(work_date),
                }
            )

        crew_payloads.append(
            {
                "id": str(crew.public_id),
                "internal_name": crew.internal_name,
                "state": crew.state,
                "resources": [
                    {
                        "id": str(item.public_id),
                        "driver": _connection_payload(item.driver_connection),
                        "vehicle": _vehicle_payload(item.vehicle),
                        "starts_on": item.starts_on.isoformat(),
                        "ends_on": item.ends_on.isoformat() if item.ends_on else None,
                    }
                    for item in crew.resource_assignments.all()
                ],
                "default_passengers": [
                    {
                        "id": str(item.public_id),
                        "worker": _connection_payload(item.connection),
                        "starts_on": item.starts_on.isoformat(),
                        "ends_on": item.ends_on.isoformat() if item.ends_on else None,
                    }
                    for item in crew.passenger_assignments.all()
                ],
                "calendar": calendar,
                "absences": [
                    {
                        "id": str(item.public_id),
                        "worker": _connection_payload(item.connection),
                        "work_date": item.work_date.isoformat(),
                    }
                    for item in crew.member_absences.all()
                ],
                "driver_substitutions": [
                    {
                        "id": str(item.public_id),
                        "work_date": item.work_date.isoformat(),
                        "state": item.state,
                        "primary_driver": _connection_payload(item.primary_driver_connection),
                        "substitute_driver": _connection_payload(item.substitute_driver_connection),
                        "vehicle": _vehicle_payload(item.vehicle),
                    }
                    for item in crew.driver_substitutions.all()
                ],
            }
        )

    return {
        "month": month_start.strftime("%Y-%m"),
        "project": project_first_project_payload(project),
        "crews": crew_payloads,
        "worker_days_off": {
            str(connection_public_ids[connection_id]): sorted(work_dates)
            for connection_id, work_dates in day_offs.items()
            if connection_id in connection_public_ids
        },
    }
