"""Transactional creation for employer-owned operational registries."""

import hashlib
import json

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from support.models import (
    AuditEvent,
    HousingAssignment,
    HousingPlace,
    HousingRoom,
    HousingSite,
    ProjectCrew,
    ProjectCrewPassenger,
    ProjectCrewResourceAssignment,
    SupportOrganization,
    Vehicle,
    ProjectScheduleTemplate,
    WorkProject,
    Worksite,
)
from support.permission_codes import HOUSING_MANAGE, SCHEDULE_MANAGE, TRANSPORT_MANAGE
from support.permissions import require_permission

from .audit import record_audit_event


def create_housing_site(*, actor, organization, **data):
    require_permission(user=actor, organization=organization, permission_code=HOUSING_MANAGE)
    try:
        with transaction.atomic():
            site = HousingSite.objects.create(
                organization=organization,
                created_by=actor,
                **data,
            )
            record_audit_event(
                organization=organization,
                actor=actor,
                action="housing.site_created",
                target=site,
                details={},
            )
    except IntegrityError as exc:
        raise ValidationError(
            {"internal_name": "housing_site_internal_name_already_exists"}
        ) from exc
    return site


def create_housing_room(*, actor, organization, site, label, capacity):
    require_permission(user=actor, organization=organization, permission_code=HOUSING_MANAGE)
    if site.organization_id != organization.id:
        raise ValidationError({"site": "operation_related_record_not_in_organization"})
    try:
        with transaction.atomic():
            room = HousingRoom.objects.create(site=site, label=label, capacity=capacity)
            # Places are not an extra setup step for the employer.  They are
            # created with the room and are free until a housing assignment is
            # published.  Keeping the records separate still gives us a safe
            # occupancy check for every individual worker.
            HousingPlace.objects.bulk_create(
                [HousingPlace(room=room, label=str(number)) for number in range(1, capacity + 1)]
            )
            record_audit_event(
                organization=organization,
                actor=actor,
                action="housing.room_created",
                target=room,
                details={
                    "site": str(site.public_id),
                    "capacity": capacity,
                    "places_created": capacity,
                },
            )
    except IntegrityError as exc:
        raise ValidationError({"label": "housing_room_label_already_exists"}) from exc
    return room


def delete_housing_room(*, actor, organization, room):
    """Remove an unused room or archive it when historical stays exist."""

    require_permission(user=actor, organization=organization, permission_code=HOUSING_MANAGE)
    with transaction.atomic():
        room = HousingRoom.objects.select_for_update().select_related("site").get(pk=room.pk)
        if room.site.organization_id != organization.id:
            raise ValidationError({"room": "operation_related_record_not_in_organization"})
        active_assignments = room.places.filter(
            assignments__state__in=(
                HousingAssignment.STATE_DRAFT,
                HousingAssignment.STATE_PUBLISHED,
            )
        ).exists()
        if active_assignments:
            raise ValidationError({"room": "housing_room_has_active_assignments"})
        has_history = room.places.filter(assignments__isnull=False).exists()
        room_public_id = str(room.public_id)
        if has_history:
            room.is_active = False
            room.save(update_fields=["is_active", "updated_at"])
            room.places.update(is_active=False)
        else:
            room.delete()
        record_audit_event(
            organization=organization,
            actor=actor,
            action="housing.room_deleted",
            target=room if has_history else organization,
            details={"room": room_public_id, "archived": has_history},
        )


def create_housing_place(*, actor, organization, room, label):
    require_permission(user=actor, organization=organization, permission_code=HOUSING_MANAGE)
    with transaction.atomic():
        room = HousingRoom.objects.select_for_update().select_related("site").get(pk=room.pk)
        if room.site.organization_id != organization.id:
            raise ValidationError({"room": "operation_related_record_not_in_organization"})
        if not room.is_active or not room.site.is_active:
            raise ValidationError({"room": "housing_room_not_available"})
        if HousingPlace.objects.filter(room=room, is_active=True).count() >= room.capacity:
            raise ValidationError({"place": "housing_room_capacity_reached"})
        try:
            place = HousingPlace.objects.create(room=room, label=label)
        except IntegrityError as exc:
            raise ValidationError(
                {"label": "housing_place_label_already_exists"}
            ) from exc
        record_audit_event(
            organization=organization,
            actor=actor,
            action="housing.place_created",
            target=place,
            details={"room": str(room.public_id)},
        )
    return place


def create_worksite(*, actor, organization, **data):
    require_permission(user=actor, organization=organization, permission_code=SCHEDULE_MANAGE)
    try:
        with transaction.atomic():
            worksite = Worksite.objects.create(
                organization=organization,
                created_by=actor,
                **data,
            )
            record_audit_event(
                organization=organization,
                actor=actor,
                action="work.worksite_created",
                target=worksite,
                details={},
            )
    except IntegrityError as exc:
        raise ValidationError(
            {"internal_name": "worksite_internal_name_already_exists"}
        ) from exc
    return worksite


def create_work_project(*, actor, organization, worksite, **data):
    require_permission(user=actor, organization=organization, permission_code=SCHEDULE_MANAGE)
    if worksite.organization_id != organization.id:
        raise ValidationError({"worksite": "operation_related_record_not_in_organization"})
    if not worksite.is_active:
        raise ValidationError({"worksite": "worksite_not_available"})
    try:
        with transaction.atomic():
            project = WorkProject.objects.create(
                organization=organization,
                worksite=worksite,
                created_by=actor,
                **data,
            )
            record_audit_event(
                organization=organization,
                actor=actor,
                action="work.project_created",
                target=project,
                details={"worksite": str(worksite.public_id)},
            )
    except IntegrityError as exc:
        raise ValidationError(
            {"internal_name": "work_project_internal_name_already_exists"}
        ) from exc
    return project


def create_project(*, actor, organization, name, **data):
    """Create the employer-facing project and its one operational address."""

    require_permission(user=actor, organization=organization, permission_code=SCHEDULE_MANAGE)
    normalized_name = (name or "").strip()
    if not normalized_name:
        raise ValidationError({"name": "project_name_required"})
    request_id = data.pop("request_id", None)
    fingerprint_source = {"name": normalized_name, **data}
    request_fingerprint = hashlib.sha256(
        json.dumps(fingerprint_source, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    address_fields = {
        field: data.pop(field)
        for field in ("country_code", "city", "postal_code", "street", "building")
    }
    with transaction.atomic():
        # Serialize project creation per organization. This makes an
        # Idempotency-Key safe even when two network retries arrive together.
        organization = SupportOrganization.objects.select_for_update().get(pk=organization.pk)
        if request_id is not None:
            previous = (
                AuditEvent.objects.filter(
                    organization=organization,
                    actor=actor,
                    action="work.project_created",
                    request_id=request_id,
                )
                .order_by("id")
                .first()
            )
            if previous is not None:
                if previous.details.get("request_fingerprint") != request_fingerprint:
                    raise ValidationError(
                        {
                            "code": "idempotency_key_reused",
                            "message": "The idempotency key was already used with different project data.",
                        }
                    )
                return WorkProject.objects.select_related("worksite").get(
                    organization=organization,
                    public_id=previous.target_public_id,
                )
        try:
            worksite = Worksite.objects.create(
                organization=organization,
                internal_name=normalized_name,
                instructions=data.get("instructions", ""),
                created_by=actor,
                **address_fields,
            )
            project = WorkProject.objects.create(
                organization=organization,
                worksite=worksite,
                internal_name=normalized_name,
                worker_visible_name=normalized_name,
                created_by=actor,
                **data,
            )
        except IntegrityError as exc:
            raise ValidationError({"name": "project_name_already_exists"}) from exc
        record_audit_event(
            organization=organization,
            actor=actor,
            action="work.project_created",
            target=project,
            details={
                "worksite": str(worksite.public_id),
                "request_fingerprint": request_fingerprint,
            },
            request_id=request_id,
        )
    return project


def update_project(*, actor, project, name, **data):
    """Update project data and its address without changing past assignments."""

    organization = project.organization
    require_permission(user=actor, organization=organization, permission_code=SCHEDULE_MANAGE)
    normalized_name = (name or "").strip()
    if not normalized_name:
        raise ValidationError({"name": "project_name_required"})
    address_fields = {
        field: data.pop(field)
        for field in ("country_code", "city", "postal_code", "street", "building")
    }
    with transaction.atomic():
        project = WorkProject.objects.select_for_update().select_related("worksite").get(pk=project.pk)
        worksite = Worksite.objects.select_for_update().get(pk=project.worksite_id)
        permanent_worker_ids = set(
            ProjectCrewResourceAssignment.objects.filter(
                crew__project=project,
                crew__state=ProjectCrew.STATE_ACTIVE,
                ends_on__isnull=True,
            ).values_list("driver_connection_id", flat=True)
        )
        permanent_worker_ids.update(
            ProjectCrewPassenger.objects.filter(
                crew__project=project,
                crew__state=ProjectCrew.STATE_ACTIVE,
                ends_on__isnull=True,
            ).values_list("connection_id", flat=True)
        )
        minimum_capacity = len(permanent_worker_ids)
        if data["worker_capacity"] < minimum_capacity:
            raise ValidationError(
                {
                    "code": "project_capacity_below_permanent_roster",
                    "message": "Project capacity cannot be lower than its permanent crew roster.",
                    "field_errors": {
                        "worker_capacity": ["project_capacity_below_permanent_roster"]
                    },
                    "minimum": minimum_capacity,
                }
            )
        try:
            WorkProject.objects.filter(pk=project.pk).update(
                internal_name=normalized_name,
                worker_visible_name=normalized_name,
                updated_at=timezone.now(),
                **data,
            )
            Worksite.objects.filter(pk=worksite.pk).update(
                internal_name=normalized_name,
                instructions=data.get("instructions", ""),
                updated_at=timezone.now(),
                **address_fields,
            )
        except IntegrityError as exc:
            raise ValidationError({"name": "project_name_already_exists"}) from exc
        record_audit_event(
            organization=organization,
            actor=actor,
            action="work.project_updated",
            target=project,
            details={"worksite": str(worksite.public_id)},
        )
    return WorkProject.objects.select_related("worksite").get(pk=project.pk)


def create_project_schedule_template(
    *, actor, project, name, starts_at_time, ends_at_time, break_minutes
):
    """Store one reusable shift pattern for a project, without calendar days."""

    organization = project.organization
    require_permission(user=actor, organization=organization, permission_code=SCHEDULE_MANAGE)
    normalized_name = (name or "").strip()
    if not normalized_name:
        raise ValidationError({"name": "project_schedule_template_name_required"})
    with transaction.atomic():
        project = WorkProject.objects.select_for_update().get(pk=project.pk)
        if not project.is_active:
            raise ValidationError({"project": "work_project_not_available"})
        try:
            template = ProjectScheduleTemplate.objects.create(
                project=project,
                name=normalized_name,
                starts_at_time=starts_at_time,
                ends_at_time=ends_at_time,
                break_minutes=break_minutes,
                worker_label="",
                created_by=actor,
            )
        except IntegrityError as exc:
            raise ValidationError({"name": "project_schedule_template_name_already_exists"}) from exc
        record_audit_event(
            organization=organization,
            actor=actor,
            action="work.project_schedule_template_created",
            target=template,
            details={
                "project": str(project.public_id),
                "starts_at_time": starts_at_time.isoformat(),
                "ends_at_time": ends_at_time.isoformat(),
            },
        )
    return template


def create_vehicle(*, actor, organization, **data):
    require_permission(user=actor, organization=organization, permission_code=TRANSPORT_MANAGE)
    try:
        with transaction.atomic():
            vehicle = Vehicle.objects.create(
                organization=organization,
                created_by=actor,
                **data,
            )
            record_audit_event(
                organization=organization,
                actor=actor,
                action="transport.vehicle_created",
                target=vehicle,
                details={},
            )
    except IntegrityError as exc:
        raise ValidationError(
            {"registration_identifier": "vehicle_registration_already_exists"}
        ) from exc
    return vehicle
