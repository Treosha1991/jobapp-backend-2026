"""Project-first crew and schedule models.

These models form the isolated foundation for the new employer workflow.  They
intentionally do not replace the legacy ``TransportCrew`` tables yet.  A crew
belongs to a project, its current driver/vehicle pair is effective-dated, and
the actual composition is stored for every published calendar day.
"""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from .operations import Vehicle, WorkProject
from .organization import SupportOrganization
from .pipeline import SupportConnection


class ProjectCrew(models.Model):
    """A stable crew owned by one project.

    Driver, vehicle and passenger changes never change this identity.  That
    lets services keep a reliable history while replacing resources for future
    dates.
    """

    STATE_ACTIVE = "active"
    STATE_ARCHIVED = "archived"
    STATE_CHOICES = [
        (STATE_ACTIVE, "Active"),
        (STATE_ARCHIVED, "Archived"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.CASCADE,
        related_name="project_crews",
    )
    project = models.ForeignKey(
        WorkProject,
        on_delete=models.PROTECT,
        related_name="project_crews",
    )
    internal_name = models.CharField(max_length=160, blank=True, default="")
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=STATE_ACTIVE)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_project_first_crews",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("project__internal_name", "internal_name", "id")
        indexes = [
            models.Index(fields=("organization", "state"), name="support_pc_org_state_idx"),
            models.Index(fields=("project", "state"), name="support_pc_project_state_idx"),
        ]

    def clean(self):
        super().clean()
        if self.project_id and self.organization_id:
            project_organization_id = self.project.organization_id
            if project_organization_id != self.organization_id:
                raise ValidationError({"project": "Project and crew must belong to the same organization."})


class ProjectCrewResourceAssignment(models.Model):
    """Effective-dated driver and vehicle history for a project crew."""

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    crew = models.ForeignKey(
        ProjectCrew,
        on_delete=models.CASCADE,
        related_name="resource_assignments",
    )
    driver_connection = models.ForeignKey(
        SupportConnection,
        on_delete=models.PROTECT,
        related_name="project_crew_driver_assignments",
    )
    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.PROTECT,
        related_name="project_crew_resource_assignments",
    )
    starts_on = models.DateField(default=timezone.localdate)
    ends_on = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_project_crew_resource_assignments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-starts_on", "-id")
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_on__isnull=True) | Q(starts_on__lte=F("ends_on")),
                name="support_pc_resource_valid_period",
            ),
            models.UniqueConstraint(
                fields=("crew",),
                condition=Q(ends_on__isnull=True),
                name="support_pc_one_open_resource",
            ),
        ]
        indexes = [
            models.Index(fields=("crew", "starts_on"), name="support_pc_res_crew_start_idx"),
            models.Index(fields=("driver_connection", "starts_on"), name="support_pc_res_driver_idx"),
            models.Index(fields=("vehicle", "starts_on"), name="support_pc_res_vehicle_idx"),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.crew_id and self.driver_connection_id:
            if self.crew.organization_id != self.driver_connection.organization_id:
                errors["driver_connection"] = "Driver and crew must belong to the same organization."
            elif not self.driver_connection.has_driving_license:
                errors["driver_connection"] = "The selected worker does not have a confirmed driving licence."
        if self.crew_id and self.vehicle_id and self.crew.organization_id != self.vehicle.organization_id:
            errors["vehicle"] = "Vehicle and crew must belong to the same organization."
        if errors:
            raise ValidationError(errors)


class ProjectCrewPassenger(models.Model):
    """Default passenger roster used for future published crew days."""

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    crew = models.ForeignKey(
        ProjectCrew,
        on_delete=models.CASCADE,
        related_name="passenger_assignments",
    )
    connection = models.ForeignKey(
        SupportConnection,
        on_delete=models.PROTECT,
        related_name="project_crew_passenger_assignments",
    )
    starts_on = models.DateField(default=timezone.localdate)
    ends_on = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_project_crew_passengers",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("crew_id", "starts_on", "id")
        constraints = [
            models.CheckConstraint(
                condition=Q(ends_on__isnull=True) | Q(starts_on__lte=F("ends_on")),
                name="support_pc_passenger_valid_period",
            ),
            models.UniqueConstraint(
                fields=("crew", "connection"),
                condition=Q(ends_on__isnull=True),
                name="support_pc_one_open_passenger",
            ),
        ]
        indexes = [
            models.Index(fields=("crew", "starts_on"), name="support_pc_pass_crew_start_idx"),
            models.Index(fields=("connection", "starts_on"), name="support_pc_pass_worker_idx"),
        ]

    def clean(self):
        super().clean()
        if self.crew_id and self.connection_id:
            if self.crew.organization_id != self.connection.organization_id:
                raise ValidationError({"connection": "Passenger and crew must belong to the same organization."})


class ProjectCrewShift(models.Model):
    """One directly entered and published calendar day for a crew."""

    STATE_PUBLISHED = "published"
    STATE_CANCELLED = "cancelled"
    STATE_CHOICES = [
        (STATE_PUBLISHED, "Published"),
        (STATE_CANCELLED, "Cancelled"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    crew = models.ForeignKey(
        ProjectCrew,
        on_delete=models.CASCADE,
        related_name="calendar_shifts",
    )
    work_date = models.DateField()
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    break_minutes = models.PositiveSmallIntegerField(default=0)
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=STATE_PUBLISHED)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_project_crew_shifts",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_project_crew_shifts",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("work_date", "starts_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=Q(starts_at__lt=F("ends_at")),
                name="support_pc_shift_valid_period",
            ),
            models.UniqueConstraint(
                fields=("crew", "work_date"),
                name="support_pc_one_shift_per_day",
            ),
        ]
        indexes = [
            models.Index(fields=("crew", "state", "work_date"), name="support_pc_shift_crew_day_idx"),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.starts_at and self.ends_at:
            duration_minutes = int((self.ends_at - self.starts_at).total_seconds() // 60)
            if duration_minutes <= 0:
                errors["ends_at"] = "Shift end must be later than shift start."
            elif self.break_minutes >= duration_minutes:
                errors["break_minutes"] = "Break must be shorter than the shift."
        if self.starts_at and self.work_date and timezone.localtime(self.starts_at).date() != self.work_date:
            errors["work_date"] = "Work date must match the local start date."
        if errors:
            raise ValidationError(errors)


class ProjectCrewShiftMember(models.Model):
    """Driver/passenger snapshot for one concrete crew calendar day."""

    ROLE_DRIVER = "driver"
    ROLE_PASSENGER = "passenger"
    ROLE_CHOICES = [
        (ROLE_DRIVER, "Driver"),
        (ROLE_PASSENGER, "Passenger"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    shift = models.ForeignKey(
        ProjectCrewShift,
        on_delete=models.CASCADE,
        related_name="members",
    )
    connection = models.ForeignKey(
        SupportConnection,
        on_delete=models.PROTECT,
        related_name="project_crew_shift_memberships",
    )
    role = models.CharField(max_length=16, choices=ROLE_CHOICES)
    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="project_crew_shift_driver_entries",
        help_text="Required only for the driver snapshot.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_project_crew_shift_members",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("shift_id", "role", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("shift", "connection"),
                name="support_pc_unique_shift_worker",
            ),
            models.UniqueConstraint(
                fields=("shift",),
                condition=Q(role="driver"),
                name="support_pc_one_shift_driver",
            ),
            models.CheckConstraint(
                condition=(
                    Q(role="driver", vehicle__isnull=False)
                    | Q(role="passenger", vehicle__isnull=True)
                ),
                name="support_pc_member_vehicle_by_role",
            ),
        ]
        indexes = [
            models.Index(fields=("connection", "role"), name="support_pc_member_worker_idx"),
            models.Index(fields=("shift", "role"), name="support_pc_member_shift_idx"),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.shift_id and self.connection_id:
            if self.shift.crew.organization_id != self.connection.organization_id:
                errors["connection"] = "Worker and crew shift must belong to the same organization."
        if self.role == self.ROLE_DRIVER:
            if not self.connection_id or not self.connection.has_driving_license:
                errors["connection"] = "The driver must have a confirmed driving licence."
            if not self.vehicle_id:
                errors["vehicle"] = "A driver entry requires a vehicle."
        elif self.vehicle_id:
            errors["vehicle"] = "A passenger entry cannot contain a vehicle."
        if self.vehicle_id and self.shift_id:
            if self.vehicle.organization_id != self.shift.crew.organization_id:
                errors["vehicle"] = "Vehicle and crew shift must belong to the same organization."
        if errors:
            raise ValidationError(errors)


class ProjectCrewMemberAbsence(models.Model):
    """A crew-specific absence for one worker on one calendar day.

    The record belongs to the crew rather than to a daily shift membership so
    it survives a driver/passenger role change.  Removing the worker from the
    crew completely is handled by the service layer and removes these records.
    """

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.CASCADE,
        related_name="project_crew_member_absences",
    )
    crew = models.ForeignKey(
        ProjectCrew,
        on_delete=models.CASCADE,
        related_name="member_absences",
    )
    connection = models.ForeignKey(
        SupportConnection,
        on_delete=models.PROTECT,
        related_name="project_crew_absences",
    )
    work_date = models.DateField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_project_crew_absences",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("work_date", "crew_id", "connection_id")
        constraints = [
            models.UniqueConstraint(
                fields=("crew", "connection", "work_date"),
                name="support_pc_unique_member_absence",
            ),
        ]
        indexes = [
            models.Index(fields=("organization", "work_date"), name="support_pc_abs_org_day_idx"),
            models.Index(fields=("connection", "work_date"), name="support_pc_abs_worker_day_idx"),
            models.Index(fields=("crew", "work_date"), name="support_pc_abs_crew_day_idx"),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.crew_id and self.organization_id and self.crew.organization_id != self.organization_id:
            errors["crew"] = "Crew and absence must belong to the same organization."
        if self.connection_id and self.organization_id:
            if self.connection.organization_id != self.organization_id:
                errors["connection"] = "Worker and absence must belong to the same organization."
        if errors:
            raise ValidationError(errors)


class WorkerScheduleDayOff(models.Model):
    """A worker-wide day off independent of project, crew and role changes."""

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.CASCADE,
        related_name="worker_schedule_days_off",
    )
    connection = models.ForeignKey(
        SupportConnection,
        on_delete=models.PROTECT,
        related_name="schedule_days_off",
    )
    work_date = models.DateField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_worker_schedule_days_off",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("work_date", "connection_id")
        constraints = [
            models.UniqueConstraint(
                fields=("connection", "work_date"),
                name="support_unique_worker_day_off",
            ),
        ]
        indexes = [
            models.Index(fields=("organization", "work_date"), name="support_dayoff_org_day_idx"),
            models.Index(fields=("connection", "work_date"), name="support_dayoff_worker_day_idx"),
        ]

    def clean(self):
        super().clean()
        if self.connection_id and self.organization_id:
            if self.connection.organization_id != self.organization_id:
                raise ValidationError(
                    {"connection": "Worker and day off must belong to the same organization."}
                )


class ProjectCrewDriverSubstitution(models.Model):
    """Date-specific substitute driver history for a project crew.

    Replacing a substitute closes the active record instead of overwriting it,
    preserving who drove the crew and its vehicle on every affected date.
    """

    STATE_ACTIVE = "active"
    STATE_REPLACED = "replaced"
    STATE_CANCELLED = "cancelled"
    STATE_CHOICES = [
        (STATE_ACTIVE, "Active"),
        (STATE_REPLACED, "Replaced"),
        (STATE_CANCELLED, "Cancelled"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.CASCADE,
        related_name="project_crew_driver_substitutions",
    )
    crew = models.ForeignKey(
        ProjectCrew,
        on_delete=models.CASCADE,
        related_name="driver_substitutions",
    )
    work_date = models.DateField()
    primary_driver_connection = models.ForeignKey(
        SupportConnection,
        on_delete=models.PROTECT,
        related_name="project_crew_primary_driver_substitutions",
    )
    substitute_driver_connection = models.ForeignKey(
        SupportConnection,
        on_delete=models.PROTECT,
        related_name="project_crew_substitute_driver_dates",
    )
    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.PROTECT,
        related_name="project_crew_driver_substitutions",
    )
    substitute_was_passenger = models.BooleanField(
        default=False,
        help_text="Whether the substitute must return to the passenger role when replaced.",
    )
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=STATE_ACTIVE)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_project_crew_driver_substitutions",
    )
    ended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ended_project_crew_driver_substitutions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-work_date", "-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("crew", "work_date"),
                condition=Q(state="active"),
                name="support_pc_one_active_substitution",
            ),
            models.CheckConstraint(
                condition=~Q(primary_driver_connection=F("substitute_driver_connection")),
                name="support_pc_substitute_diff_driver",
            ),
        ]
        indexes = [
            models.Index(fields=("organization", "work_date"), name="support_pc_sub_org_day_idx"),
            models.Index(fields=("crew", "work_date", "state"), name="support_pc_sub_crew_day_idx"),
            models.Index(
                fields=("substitute_driver_connection", "work_date"),
                name="support_pc_sub_driver_day_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.crew_id and self.organization_id and self.crew.organization_id != self.organization_id:
            errors["crew"] = "Crew and substitution must belong to the same organization."
        for field_name in ("primary_driver_connection", "substitute_driver_connection"):
            connection_id = getattr(self, f"{field_name}_id")
            if connection_id and self.organization_id:
                connection = getattr(self, field_name)
                if connection.organization_id != self.organization_id:
                    errors[field_name] = "Driver and substitution must belong to the same organization."
                elif not connection.has_driving_license:
                    errors[field_name] = "The driver must have a confirmed driving licence."
        if self.vehicle_id and self.organization_id and self.vehicle.organization_id != self.organization_id:
            errors["vehicle"] = "Vehicle and substitution must belong to the same organization."
        if (
            self.primary_driver_connection_id
            and self.primary_driver_connection_id == self.substitute_driver_connection_id
        ):
            errors["substitute_driver_connection"] = "Substitute driver must differ from primary driver."
        if self.state == self.STATE_ACTIVE and self.ended_at:
            errors["ended_at"] = "An active substitution cannot have an end timestamp."
        if self.state != self.STATE_ACTIVE and not self.ended_at:
            errors["ended_at"] = "A closed substitution requires an end timestamp."
        if errors:
            raise ValidationError(errors)
