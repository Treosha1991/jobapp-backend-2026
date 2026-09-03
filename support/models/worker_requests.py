"""Worker-to-employer requests kept separate from schedules and payroll.

The module contains absence, vacation, extra-shift availability and exit
requests.  A decision records the employer's response but never changes a
published shift, the Support subscription or an employment connection by
itself.
"""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q

from .organization import SupportOrganization
from .pipeline import SupportConnection


class WorkerRequest(models.Model):
    """A worker's request for an employer to review.

    No upload or health-document field exists here.  The optional note is for
    the minimum operational context and the mobile copy will explicitly ask a
    worker not to include medical details.
    """

    TYPE_DAY_OFF = "day_off"
    TYPE_VACATION = "vacation"
    TYPE_UNPAID_ABSENCE = "unpaid_absence"
    TYPE_UNABLE_TODAY = "unable_today"
    TYPE_EXTRA_SHIFT = "extra_shift"
    TYPE_EXIT = "exit_request"
    TYPE_CHOICES = [
        (TYPE_DAY_OFF, "Day off"),
        (TYPE_VACATION, "Vacation"),
        (TYPE_UNPAID_ABSENCE, "Unpaid absence"),
        (TYPE_UNABLE_TODAY, "Unable to work today"),
        (TYPE_EXTRA_SHIFT, "Extra shifts"),
        (TYPE_EXIT, "Exit request"),
    ]

    STATUS_DRAFT = "draft"
    STATUS_SUBMITTED = "submitted"
    STATUS_NEEDS_CLARIFICATION = "needs_clarification"
    STATUS_APPROVED = "approved"
    STATUS_DECLINED = "declined"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_NEEDS_CLARIFICATION, "Needs clarification"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_DECLINED, "Declined"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.CASCADE,
        related_name="worker_requests",
    )
    connection = models.ForeignKey(
        SupportConnection,
        on_delete=models.CASCADE,
        related_name="worker_requests",
    )
    request_type = models.CharField(max_length=32, choices=TYPE_CHOICES)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    starts_on = models.DateField()
    ends_on = models.DateField()
    worker_note = models.CharField(max_length=500, blank=True, default="")
    manager_note = models.CharField(max_length=500, blank=True, default="")
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_support_worker_requests",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    last_changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="last_changed_support_worker_requests",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-submitted_at", "-created_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=Q(starts_on__lte=F("ends_on")),
                name="support_worker_request_valid_dates",
            ),
        ]
        indexes = [
            models.Index(fields=("organization", "status", "starts_on")),
            models.Index(fields=("connection", "status", "starts_on")),
        ]

    @property
    def is_urgent(self):
        return self.request_type == self.TYPE_UNABLE_TODAY

    @property
    def is_extra_shift(self):
        return self.request_type == self.TYPE_EXTRA_SHIFT


class WorkerRequestDate(models.Model):
    """One independently actionable date in an extra-shift request."""

    STATUS_REQUESTED = "requested"
    STATUS_ASSIGNED = "assigned"
    STATUS_DECLINED = "declined"
    STATUS_EXPIRED = "expired"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_REQUESTED, "Requested"),
        (STATUS_ASSIGNED, "Shift assigned"),
        (STATUS_DECLINED, "Declined"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    request = models.ForeignKey(
        WorkerRequest,
        on_delete=models.CASCADE,
        related_name="extra_shift_dates",
    )
    work_date = models.DateField()
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_REQUESTED,
    )
    manager_note = models.CharField(max_length=500, blank=True, default="")
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decided_support_worker_request_dates",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("work_date", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("request", "work_date"),
                name="support_unique_extra_shift_request_date",
            ),
        ]
        indexes = [
            models.Index(fields=("request", "status", "work_date")),
        ]


class WorkerRequestEvent(models.Model):
    """Append-only history of a request's state changes.

    It deliberately contains no copy of a worker note or medical detail.  The
    request itself is the current, scoped operational record; the history is
    only who changed what and when.
    """

    ACTION_SUBMITTED = "submitted"
    ACTION_CLARIFICATION_REQUESTED = "clarification_requested"
    ACTION_APPROVED = "approved"
    ACTION_DECLINED = "declined"
    ACTION_CANCELLED = "cancelled"
    ACTION_EXTRA_DATE_DECLINED = "extra_date_declined"
    ACTION_CHOICES = [
        (ACTION_SUBMITTED, "Submitted"),
        (ACTION_CLARIFICATION_REQUESTED, "Clarification requested"),
        (ACTION_APPROVED, "Approved"),
        (ACTION_DECLINED, "Declined"),
        (ACTION_CANCELLED, "Cancelled"),
        (ACTION_EXTRA_DATE_DECLINED, "Extra-shift date declined"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    request = models.ForeignKey(
        WorkerRequest,
        on_delete=models.CASCADE,
        related_name="events",
    )
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    status_after = models.CharField(max_length=32, choices=WorkerRequest.STATUS_CHOICES)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_worker_request_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("request_id", "created_at", "id")
        indexes = [models.Index(fields=("request", "created_at"))]
