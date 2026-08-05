"""Operational registries and unpublished/published worker assignments.

These tables deliberately separate employer-maintained registries from a
worker assignment. A worker never receives a draft: it becomes visible only
through an explicit publishing service after every conflict check succeeds.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from .organization import SupportOrganization
from .pipeline import SupportConnection


class HousingSite(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(SupportOrganization, on_delete=models.CASCADE, related_name="housing_sites")
    internal_name = models.CharField(max_length=160)
    country_code = models.CharField(max_length=2)
    city = models.CharField(max_length=120)
    postal_code = models.CharField(max_length=20, blank=True, default="")
    street = models.CharField(max_length=160)
    building = models.CharField(max_length=40)
    rules_text = models.TextField(max_length=5000, blank=True, default="")
    contact_name = models.CharField(max_length=160, blank=True, default="")
    contact_phone = models.CharField(max_length=48, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_support_housing_sites")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("internal_name", "id")
        constraints = [models.UniqueConstraint(fields=("organization", "internal_name"), name="support_unique_housing_site_internal_name")]
        indexes = [models.Index(fields=("organization", "is_active", "internal_name"))]


class HousingRoom(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    site = models.ForeignKey(HousingSite, on_delete=models.CASCADE, related_name="rooms")
    label = models.CharField(max_length=80)
    capacity = models.PositiveSmallIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("site_id", "label", "id")
        constraints = [
            models.UniqueConstraint(fields=("site", "label"), name="support_unique_housing_room_label_per_site"),
            models.CheckConstraint(condition=Q(capacity__gte=1), name="support_housing_room_capacity_positive"),
        ]
        indexes = [models.Index(fields=("site", "is_active"))]


class HousingPlace(models.Model):
    """A concrete bed/place. It prevents ambiguous capacity-only allocation."""

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    room = models.ForeignKey(HousingRoom, on_delete=models.CASCADE, related_name="places")
    label = models.CharField(max_length=80)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("room_id", "label", "id")
        constraints = [models.UniqueConstraint(fields=("room", "label"), name="support_unique_housing_place_label_per_room")]
        indexes = [models.Index(fields=("room", "is_active"))]


class HousingAssignment(models.Model):
    STATE_DRAFT = "draft"
    STATE_PUBLISHED = "published"
    STATE_CANCELLED = "cancelled"
    STATE_CHOICES = [(STATE_DRAFT, "Draft"), (STATE_PUBLISHED, "Published"), (STATE_CANCELLED, "Cancelled")]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(SupportOrganization, on_delete=models.CASCADE, related_name="housing_assignments")
    connection = models.ForeignKey(SupportConnection, on_delete=models.CASCADE, related_name="housing_assignments")
    place = models.ForeignKey(HousingPlace, on_delete=models.PROTECT, related_name="assignments")
    check_in_at = models.DateTimeField()
    check_out_at = models.DateTimeField(null=True, blank=True)
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=STATE_DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_support_housing_assignments")
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="published_support_housing_assignments")
    published_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-check_in_at", "-id")
        constraints = [models.CheckConstraint(condition=Q(check_out_at__isnull=True) | Q(check_in_at__lt=F("check_out_at")), name="support_housing_assignment_valid_period")]
        indexes = [
            models.Index(fields=("organization", "state", "check_in_at")),
            models.Index(fields=("connection", "state", "check_in_at")),
            models.Index(fields=("place", "state", "check_in_at")),
        ]


class Worksite(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(SupportOrganization, on_delete=models.CASCADE, related_name="worksites")
    internal_name = models.CharField(max_length=160)
    country_code = models.CharField(max_length=2)
    city = models.CharField(max_length=120)
    postal_code = models.CharField(max_length=20, blank=True, default="")
    street = models.CharField(max_length=160)
    building = models.CharField(max_length=40)
    instructions = models.TextField(max_length=5000, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_support_worksites")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("internal_name", "id")
        constraints = [models.UniqueConstraint(fields=("organization", "internal_name"), name="support_unique_worksite_internal_name")]
        indexes = [models.Index(fields=("organization", "is_active", "internal_name"))]


class WorkProject(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(SupportOrganization, on_delete=models.CASCADE, related_name="work_projects")
    worksite = models.ForeignKey(Worksite, on_delete=models.PROTECT, related_name="projects")
    internal_name = models.CharField(max_length=160)
    worker_visible_name = models.CharField(max_length=160)
    instructions = models.TextField(max_length=5000, blank=True, default="")
    worker_capacity = models.PositiveSmallIntegerField(default=1)
    starts_on = models.DateField(default=timezone.localdate)
    ends_on = models.DateField(null=True, blank=True)
    contact_name = models.CharField(max_length=160, blank=True, default="")
    contact_phone = models.CharField(max_length=48, blank=True, default="")
    contact_email = models.EmailField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_support_work_projects")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("internal_name", "id")
        constraints = [
            models.UniqueConstraint(fields=("organization", "internal_name"), name="support_unique_work_project_internal_name"),
            models.CheckConstraint(
                condition=Q(ends_on__isnull=True) | Q(starts_on__lte=F("ends_on")),
                name="support_work_project_valid_period",
            ),
        ]
        indexes = [models.Index(fields=("organization", "is_active", "internal_name"))]


class WorkerProjectAssignment(models.Model):
    STATE_DRAFT = HousingAssignment.STATE_DRAFT
    STATE_PUBLISHED = HousingAssignment.STATE_PUBLISHED
    STATE_CANCELLED = HousingAssignment.STATE_CANCELLED
    STATE_CHOICES = HousingAssignment.STATE_CHOICES

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(SupportOrganization, on_delete=models.CASCADE, related_name="worker_project_assignments")
    connection = models.ForeignKey(SupportConnection, on_delete=models.CASCADE, related_name="project_assignments")
    project = models.ForeignKey(WorkProject, on_delete=models.PROTECT, related_name="worker_assignments")
    worker_role = models.CharField(max_length=160, blank=True, default="")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=STATE_DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_support_project_assignments")
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="published_support_project_assignments")
    published_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-starts_at", "-id")
        constraints = [models.CheckConstraint(condition=Q(ends_at__isnull=True) | Q(starts_at__lt=F("ends_at")), name="support_project_assignment_valid_period")]
        indexes = [
            models.Index(fields=("organization", "state", "starts_at")),
            models.Index(fields=("connection", "state", "starts_at")),
            models.Index(fields=("project", "state", "starts_at")),
        ]


class ProjectScheduleTemplate(models.Model):
    """A project-owned shift pattern with explicitly selected calendar days."""

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    project = models.ForeignKey(
        WorkProject,
        on_delete=models.CASCADE,
        related_name="schedule_templates",
    )
    name = models.CharField(max_length=120)
    starts_at_time = models.TimeField()
    ends_at_time = models.TimeField()
    break_minutes = models.PositiveSmallIntegerField(default=0)
    worker_label = models.CharField(max_length=160, blank=True, default="")
    # ISO dates keep the selection intentionally explicit: the employer may
    # choose any days in the calendar instead of being limited to weekdays.
    calendar_dates = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_project_schedule_templates",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("project", "name"),
                name="support_unique_project_schedule_template_name",
            )
        ]
        indexes = [models.Index(fields=("project", "is_active", "name"))]


class WorkerProjectScheduleTemplateSelection(models.Model):
    """The project templates selected for one worker assignment."""

    assignment = models.ForeignKey(
        WorkerProjectAssignment,
        on_delete=models.CASCADE,
        related_name="schedule_template_selections",
    )
    template = models.ForeignKey(
        ProjectScheduleTemplate,
        on_delete=models.PROTECT,
        related_name="assignment_selections",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("assignment", "template"),
                name="support_unique_worker_project_schedule_template",
            )
        ]
        indexes = [models.Index(fields=("template", "assignment"))]


class Vehicle(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(SupportOrganization, on_delete=models.CASCADE, related_name="vehicles")
    internal_name = models.CharField(max_length=120)
    registration_identifier = models.CharField(max_length=64)
    seat_capacity = models.PositiveSmallIntegerField()
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_support_vehicles")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("internal_name", "id")
        constraints = [
            models.UniqueConstraint(fields=("organization", "registration_identifier"), name="support_unique_vehicle_registration_per_organization"),
            models.CheckConstraint(condition=Q(seat_capacity__gte=2), name="support_vehicle_includes_driver_and_passenger"),
        ]
        indexes = [models.Index(fields=("organization", "is_active", "internal_name"))]


class DriverVehicleAssignment(models.Model):
    STATE_DRAFT = HousingAssignment.STATE_DRAFT
    STATE_PUBLISHED = HousingAssignment.STATE_PUBLISHED
    STATE_CANCELLED = HousingAssignment.STATE_CANCELLED
    STATE_CHOICES = HousingAssignment.STATE_CHOICES

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(SupportOrganization, on_delete=models.CASCADE, related_name="driver_vehicle_assignments")
    driver_connection = models.ForeignKey(SupportConnection, on_delete=models.CASCADE, related_name="driver_vehicle_assignments")
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT, related_name="driver_assignments")
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True)
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=STATE_DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_support_driver_vehicle_assignments")
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="published_support_driver_vehicle_assignments")
    published_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-starts_on", "-id")
        constraints = [models.CheckConstraint(condition=Q(ends_on__isnull=True) | Q(starts_on__lte=F("ends_on")), name="support_driver_vehicle_assignment_valid_period")]
        indexes = [
            models.Index(fields=("organization", "state", "starts_on")),
            models.Index(fields=("driver_connection", "state", "starts_on")),
            models.Index(fields=("vehicle", "state", "starts_on")),
        ]


class TransportRoute(models.Model):
    STATE_DRAFT = HousingAssignment.STATE_DRAFT
    STATE_PUBLISHED = HousingAssignment.STATE_PUBLISHED
    STATE_CANCELLED = HousingAssignment.STATE_CANCELLED
    STATE_CHOICES = HousingAssignment.STATE_CHOICES

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(SupportOrganization, on_delete=models.CASCADE, related_name="transport_routes")
    internal_name = models.CharField(max_length=160)
    worksite = models.ForeignKey(Worksite, on_delete=models.PROTECT, null=True, blank=True, related_name="transport_routes")
    driver_vehicle_assignment = models.ForeignKey(DriverVehicleAssignment, on_delete=models.PROTECT, related_name="routes")
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True)
    departure_time = models.TimeField(null=True, blank=True)
    reservation_expires_at = models.DateTimeField(null=True, blank=True)
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=STATE_DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_support_transport_routes")
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="published_support_transport_routes")
    published_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-starts_on", "-id")
        constraints = [
            models.UniqueConstraint(fields=("organization", "internal_name"), name="support_unique_transport_route_internal_name"),
            models.CheckConstraint(condition=Q(ends_on__isnull=True) | Q(starts_on__lte=F("ends_on")), name="support_transport_route_valid_period"),
        ]
        indexes = [
            models.Index(fields=("organization", "state", "starts_on")),
            models.Index(fields=("driver_vehicle_assignment", "state", "starts_on")),
        ]


class RouteStop(models.Model):
    KIND_PICKUP = "pickup"
    KIND_DROPOFF = "dropoff"
    KIND_CHOICES = [(KIND_PICKUP, "Pickup"), (KIND_DROPOFF, "Dropoff")]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    route = models.ForeignKey(TransportRoute, on_delete=models.CASCADE, related_name="stops")
    sequence = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    label = models.CharField(max_length=160)
    housing_site = models.ForeignKey(HousingSite, on_delete=models.PROTECT, null=True, blank=True, related_name="route_stops")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("route_id", "sequence", "id")
        constraints = [models.UniqueConstraint(fields=("route", "sequence"), name="support_unique_transport_route_stop_sequence")]


class TransportPassengerAssignment(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    route = models.ForeignKey(TransportRoute, on_delete=models.CASCADE, related_name="passenger_assignments")
    connection = models.ForeignKey(SupportConnection, on_delete=models.CASCADE, related_name="transport_passenger_assignments")
    pickup_stop = models.ForeignKey(RouteStop, on_delete=models.PROTECT, related_name="pickup_passenger_assignments")
    dropoff_stop = models.ForeignKey(RouteStop, on_delete=models.PROTECT, related_name="dropoff_passenger_assignments")
    boarding_order = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("route_id", "boarding_order", "id")
        constraints = [
            models.UniqueConstraint(fields=("route", "connection"), name="support_unique_transport_passenger_per_route"),
            models.CheckConstraint(condition=Q(boarding_order__gte=1), name="support_transport_passenger_boarding_order_positive"),
        ]
        indexes = [models.Index(fields=("connection", "route")), models.Index(fields=("route", "boarding_order"))]


class WorkerAccessScope(models.Model):
    """A staff member's explicit operational access to one worker connection.

    Permissions answer *what* a staff member may do.  This record answers
    *whose* data they may open or change.  Owners and organization managers do
    not need one record per worker; every other operational staff member does.
    Revocation retains the history instead of deleting an access decision.
    """

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    membership = models.ForeignKey(
        "support.OrganizationMembership",
        on_delete=models.CASCADE,
        related_name="worker_access_scopes",
    )
    connection = models.ForeignKey(
        SupportConnection,
        on_delete=models.CASCADE,
        related_name="staff_access_scopes",
    )
    is_active = models.BooleanField(default=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="granted_support_worker_scopes",
    )
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="revoked_support_worker_scopes",
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("membership_id", "connection_id", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("membership", "connection"),
                condition=Q(is_active=True),
                name="support_unique_active_worker_access_scope",
            ),
        ]
        indexes = [
            models.Index(
                fields=("membership", "is_active"),
                name="support_was_membership_active",
            ),
            models.Index(
                fields=("connection", "is_active"),
                name="support_was_connection_active",
            ),
        ]
