"""Read-only planning helpers for the guarded project-first staging reset."""

from collections import OrderedDict

from django.db import transaction

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
from support.services.audit import record_audit_event


class ProjectFirstResetError(RuntimeError):
    """Raised when the guarded reset cannot prove its invariants."""


def reset_target_querysets(organization, *, include_work_time=False):
    """Return operational records removed during the one-time cutover."""

    targets = OrderedDict(
        (
            (
                "work_time_entries",
                WorkTimeEntry.objects.filter(organization=organization),
            ),
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
    if not include_work_time:
        targets.pop("work_time_entries")
    return targets


def preserved_counts(organization, *, include_work_time=False):
    """Return registry/factual records that the reset must not remove."""

    preserved = OrderedDict(
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
    if include_work_time:
        preserved.pop("work_time_entries")
    return preserved


def build_project_first_reset_plan(organization, *, include_work_time=False):
    """Build an organization-specific report without modifying any data."""

    targets = reset_target_querysets(
        organization,
        include_work_time=include_work_time,
    )
    return {
        "organization": organization,
        "delete_counts": OrderedDict(
            (label, queryset.count()) for label, queryset in targets.items()
        ),
        "preserve_counts": preserved_counts(
            organization,
            include_work_time=include_work_time,
        ),
        "include_work_time": include_work_time,
        "confirmation": (
            f"RESET-{organization.public_id}-WITH-WORK-TIME"
            if include_work_time
            else f"RESET-{organization.public_id}"
        ),
    }


def execute_project_first_reset(*, organization, actor, include_work_time=False):
    """Apply an already-authorized reset and verify all invariants atomically."""

    plan = build_project_first_reset_plan(
        organization,
        include_work_time=include_work_time,
    )
    preserved_before = plan["preserve_counts"]

    with transaction.atomic():
        for queryset in reset_target_querysets(
            organization,
            include_work_time=include_work_time,
        ).values():
            queryset.delete()

        preserved_after = preserved_counts(
            organization,
            include_work_time=include_work_time,
        )
        if preserved_after != preserved_before:
            raise ProjectFirstResetError("preserved_data_count_changed")

        remaining = {
            label: queryset.count()
            for label, queryset in reset_target_querysets(
                organization,
                include_work_time=include_work_time,
            ).items()
            if queryset.exists()
        }
        if remaining:
            raise ProjectFirstResetError(
                "reset_targets_remain: "
                + ", ".join(f"{key}={value}" for key, value in remaining.items())
            )

        record_audit_event(
            organization=organization,
            actor=actor,
            action="project_first.staging_reset",
            target=organization,
            details={
                "deleted_counts": dict(plan["delete_counts"]),
                "preserved_counts": dict(preserved_before),
                "included_work_time_entries": include_work_time,
            },
        )

    return plan
