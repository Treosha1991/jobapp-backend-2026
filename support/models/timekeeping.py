"""Daily work schedule and actual-time records for JobHub Support.

The first pilot deliberately stores one combined record per worker and work
date.  A worker enters the real start, end, and break; the server calculates
minutes from those values.  Split shifts can later become a separate model
without making a payroll calculation depend on a client-side total.
"""

import uuid
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from .operations import WorkerProjectAssignment
from .organization import SupportOrganization
from .pipeline import SupportConnection


class ShiftTemplate(models.Model):
    """A reusable planned-shift pattern, never a factual time record."""

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.CASCADE,
        related_name="shift_templates",
    )
    name = models.CharField(max_length=120)
    starts_at_time = models.TimeField()
    ends_at_time = models.TimeField()
    break_minutes = models.PositiveSmallIntegerField(default=0)
    worker_label = models.CharField(max_length=160, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_support_shift_templates",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "name"),
                name="support_unique_shift_template_name_per_organization",
            )
        ]
        indexes = [
            models.Index(
                fields=("organization", "is_active", "name"),
                name="support_sht_orgact_name_idx",
            ),
        ]


class CalendarMarkTemplate(models.Model):
    """An internal reusable label for individually approved absence requests.

    The template never owns dates and never decides an absence.  Dates and
    approval always remain on the original WorkerRequest record.
    """

    TYPE_DAY_OFF = "day_off"
    TYPE_VACATION = "vacation"
    TYPE_UNPAID_ABSENCE = "unpaid_absence"
    TYPE_UNABLE_TODAY = "unable_today"
    REQUEST_TYPE_CHOICES = [
        (TYPE_DAY_OFF, "Day off"),
        (TYPE_VACATION, "Vacation"),
        (TYPE_UNPAID_ABSENCE, "Unpaid absence"),
        (TYPE_UNABLE_TODAY, "Unable to work today"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.CASCADE,
        related_name="calendar_mark_templates",
    )
    # This is an internal staff label. Workers continue to see the localized
    # request type, so an employer's custom internal name is never leaked.
    name = models.CharField(max_length=120)
    request_type = models.CharField(max_length=32, choices=REQUEST_TYPE_CHOICES)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_support_calendar_mark_templates",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("request_type", "name", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "name"),
                name="support_unique_calendar_mark_template_name_per_org",
            )
        ]
        indexes = [
            models.Index(
                fields=("organization", "is_active", "request_type", "name"),
                name="support_cmt_org_type_nm_idx",
            ),
        ]


class CalendarMarkBatch(models.Model):
    """A reversible publication batch for already approved absence markers."""

    STATE_DRAFT = "draft"
    STATE_PUBLISHED = "published"
    STATE_CANCELLED = "cancelled"
    STATE_CHOICES = [
        (STATE_DRAFT, "Draft"),
        (STATE_PUBLISHED, "Published"),
        (STATE_CANCELLED, "Cancelled"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.CASCADE,
        related_name="calendar_mark_batches",
    )
    template = models.ForeignKey(
        CalendarMarkTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="batches",
    )
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=STATE_DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_support_calendar_mark_batches",
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="published_support_calendar_mark_batches",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(
                fields=("organization", "state", "created_at"),
                name="support_cmb_org_state_idx",
            ),
        ]


class CalendarMarkBatchItem(models.Model):
    """One approved request selected into one calendar-mark batch."""

    batch = models.ForeignKey(
        CalendarMarkBatch,
        on_delete=models.CASCADE,
        related_name="items",
    )
    request = models.ForeignKey(
        "support.WorkerRequest",
        on_delete=models.CASCADE,
        related_name="calendar_mark_batch_items",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("request__starts_on", "request__ends_on", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("batch", "request"),
                name="support_unique_calendar_mark_batch_item",
            ),
        ]
        indexes = [
            models.Index(fields=("request", "batch"), name="support_cmbi_req_batch_idx"),
        ]


class ScheduledShiftBatch(models.Model):
    """A reversible, all-or-nothing planned schedule for a group of workers."""

    STATE_DRAFT = "draft"
    STATE_PUBLISHED = "published"
    STATE_CANCELLED = "cancelled"
    STATE_CHOICES = [
        (STATE_DRAFT, "Draft"),
        (STATE_PUBLISHED, "Published"),
        (STATE_CANCELLED, "Cancelled"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.CASCADE,
        related_name="scheduled_shift_batches",
    )
    template = models.ForeignKey(
        ShiftTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="batches",
    )
    starts_on = models.DateField()
    ends_on = models.DateField()
    weekdays = models.JSONField(default=list)
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=STATE_DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_support_scheduled_shift_batches",
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="published_support_scheduled_shift_batches",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-starts_on", "-created_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(starts_on__lte=models.F("ends_on")),
                name="support_scheduled_shift_batch_valid_period",
            )
        ]
        indexes = [
            models.Index(
                fields=("organization", "state", "starts_on"),
                name="support_shb_orgstate_start_idx",
            ),
        ]


class ScheduledWorkShift(models.Model):
    """A staff-published planned shift, separate from the worker's actual time."""

    STATE_DRAFT = "draft"
    STATE_PUBLISHED = "published"
    STATE_CANCELLED = "cancelled"
    STATE_CHOICES = [
        (STATE_DRAFT, "Draft"),
        (STATE_PUBLISHED, "Published"),
        (STATE_CANCELLED, "Cancelled"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.CASCADE,
        related_name="scheduled_work_shifts",
    )
    connection = models.ForeignKey(
        SupportConnection,
        on_delete=models.CASCADE,
        related_name="scheduled_work_shifts",
    )
    batch = models.ForeignKey(
        ScheduledShiftBatch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shifts",
    )
    work_assignment = models.ForeignKey(
        WorkerProjectAssignment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scheduled_shifts",
    )
    schedule_template = models.ForeignKey(
        "support.ProjectScheduleTemplate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scheduled_shifts",
    )
    crew = models.ForeignKey(
        "support.TransportCrew",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scheduled_shifts",
    )
    project_crew_member = models.OneToOneField(
        "support.ProjectCrewShiftMember",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="worker_calendar_shift",
        help_text="Source crew-day membership in the project-first workspace.",
    )
    work_date = models.DateField()
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    break_minutes = models.PositiveSmallIntegerField(default=0)
    worker_label = models.CharField(max_length=160, blank=True, default="")
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=STATE_DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_support_scheduled_work_shifts",
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="published_support_scheduled_work_shifts",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("work_date", "starts_at", "id")
        constraints = [
            models.CheckConstraint(
                condition=Q(starts_at__lt=F("ends_at")),
                name="support_scheduled_shift_valid_period",
            ),
            models.UniqueConstraint(
                fields=("connection", "work_date"),
                condition=Q(
                    crew__isnull=True,
                    project_crew_member__isnull=True,
                    state__in=("draft", "published"),
                ),
                name="support_one_legacy_shift_day",
            ),
            models.UniqueConstraint(
                fields=("connection", "work_date", "crew"),
                condition=Q(
                    crew__isnull=False,
                    state__in=("draft", "published"),
                ),
                name="support_one_crew_shift_day",
            ),
        ]
        indexes = [
            models.Index(fields=("organization", "state", "work_date")),
            models.Index(fields=("connection", "state", "work_date")),
        ]


class WorkTimeEntry(models.Model):
    """The factual daily record submitted by a worker and checked by staff."""

    STATUS_SUBMITTED = "submitted"
    STATUS_CORRECTION_REQUESTED = "correction_requested"
    STATUS_CONFIRMED = "confirmed"
    STATUS_MANAGER_ADJUSTED = "manager_adjusted"
    STATUS_CHOICES = [
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_CORRECTION_REQUESTED, "Correction requested"),
        (STATUS_CONFIRMED, "Confirmed"),
        (STATUS_MANAGER_ADJUSTED, "Manager adjustment awaiting worker acknowledgement"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.CASCADE,
        related_name="work_time_entries",
    )
    connection = models.ForeignKey(
        SupportConnection,
        on_delete=models.CASCADE,
        related_name="work_time_entries",
    )
    scheduled_shift = models.ForeignKey(
        ScheduledWorkShift,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="time_entries",
    )
    work_date = models.DateField()
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField()
    break_minutes = models.PositiveSmallIntegerField(default=0)
    worked_minutes = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_SUBMITTED)
    revision = models.PositiveSmallIntegerField(default=1)
    manager_note = models.CharField(max_length=500, blank=True, default="")
    submitted_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="confirmed_support_time_entries",
    )
    worker_acknowledged_at = models.DateTimeField(null=True, blank=True)
    last_changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="last_changed_support_time_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-work_date", "-updated_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("connection", "work_date"),
                name="support_one_work_time_entry_per_day",
            ),
            models.CheckConstraint(
                condition=Q(started_at__lt=F("ended_at")),
                name="support_time_entry_valid_period",
            ),
            models.CheckConstraint(
                condition=Q(worked_minutes__gte=0),
                name="support_time_entry_nonnegative_minutes",
            ),
        ]
        indexes = [
            models.Index(fields=("organization", "work_date", "status")),
            models.Index(fields=("connection", "work_date")),
        ]

    @property
    def decimal_hours(self):
        return (Decimal(self.worked_minutes) / Decimal("60")).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )


class WorkTimeEntryRevision(models.Model):
    """Immutable history for every entry submission, decision, and change."""

    ACTION_SUBMITTED = "submitted"
    ACTION_CORRECTION_REQUESTED = "correction_requested"
    ACTION_CONFIRMED = "confirmed"
    ACTION_MANAGER_ADJUSTED = "manager_adjusted"
    ACTION_WORKER_ACKNOWLEDGED = "worker_acknowledged"
    ACTION_CHOICES = [
        (ACTION_SUBMITTED, "Submitted"),
        (ACTION_CORRECTION_REQUESTED, "Correction requested"),
        (ACTION_CONFIRMED, "Confirmed"),
        (ACTION_MANAGER_ADJUSTED, "Manager adjusted"),
        (ACTION_WORKER_ACKNOWLEDGED, "Worker acknowledged"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    entry = models.ForeignKey(
        WorkTimeEntry,
        on_delete=models.CASCADE,
        related_name="revisions",
    )
    revision = models.PositiveSmallIntegerField()
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    status_after = models.CharField(max_length=32, choices=WorkTimeEntry.STATUS_CHOICES)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField()
    break_minutes = models.PositiveSmallIntegerField()
    worked_minutes = models.PositiveSmallIntegerField()
    note = models.CharField(max_length=500, blank=True, default="")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_time_entry_revisions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("entry_id", "created_at", "id")
        indexes = [models.Index(fields=("entry", "revision", "created_at"))]
