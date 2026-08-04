"""Transactional creation for employer-owned operational registries."""

from django.db import IntegrityError, transaction
from rest_framework.exceptions import ValidationError

from support.models import (
    HousingPlace,
    HousingRoom,
    HousingSite,
    Vehicle,
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
