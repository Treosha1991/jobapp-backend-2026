"""Read-only planning helpers for the guarded project-first staging reset."""

from collections import OrderedDict

from support.models import (
    DriverVehicleAssignment,
    HousingAssignment,
    HousingPlace,
    HousingRoom,
    HousingSite,
    ProjectCrew,
    ProjectScheduleTemplate,
    ScheduledShiftBatch,
    ScheduledWorkShift,
    ShiftTemplate,
    SupportConnection,
    TransportCrew,
    TransportRoute,
    Vehicle,
    WorkerProjectAssignment,
    WorkProject,
    WorkTimeEntry,
    Worksite,
)


def reset_target_querysets(organization):
    """Return operational records removed during the one-time cutover."""

    return OrderedDict(
        (
            ("project_crews", ProjectCrew.objects.filter(organization=organization)),
            ("transport_crews", TransportCrew.objects.filter(organization=organization)),
            (
                "scheduled_work_shifts",
                ScheduledWorkShift.objects.filter(organization=organization),
            ),
            ("transport_routes", TransportRoute.objects.filter(organization=organization)),
            (
                "driver_vehicle_assignments",
                DriverVehicleAssignment.objects.filter(organization=organization),
            ),
            (
                "worker_project_assignments",
                WorkerProjectAssignment.objects.filter(organization=organization),
            ),
            (
                "project_schedule_templates",
                ProjectScheduleTemplate.objects.filter(project__organization=organization),
            ),
            ("work_projects", WorkProject.objects.filter(organization=organization)),
            ("worksites", Worksite.objects.filter(organization=organization)),
            (
                "scheduled_shift_batches",
                ScheduledShiftBatch.objects.filter(organization=organization),
            ),
            ("shift_templates", ShiftTemplate.objects.filter(organization=organization)),
        )
    )


def preserved_counts(organization):
    """Return registry/factual records that the reset must not remove."""

    return OrderedDict(
        (
            ("workers", SupportConnection.objects.filter(organization=organization).count()),
            ("housing_sites", HousingSite.objects.filter(organization=organization).count()),
            (
                "housing_rooms",
                HousingRoom.objects.filter(site__organization=organization).count(),
            ),
            (
                "housing_places",
                HousingPlace.objects.filter(room__site__organization=organization).count(),
            ),
            (
                "housing_assignments",
                HousingAssignment.objects.filter(organization=organization).count(),
            ),
            ("vehicles", Vehicle.objects.filter(organization=organization).count()),
            (
                "work_time_entries",
                WorkTimeEntry.objects.filter(organization=organization).count(),
            ),
        )
    )


def build_project_first_reset_plan(organization):
    """Build an organization-specific report without modifying any data."""

    targets = reset_target_querysets(organization)
    return {
        "organization": organization,
        "delete_counts": OrderedDict(
            (label, queryset.count()) for label, queryset in targets.items()
        ),
        "preserve_counts": preserved_counts(organization),
        "confirmation": f"RESET-{organization.public_id}",
    }
