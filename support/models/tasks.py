"""Worker tasks and announcements for the JobHub Support workspace.

Tasks and announcements deliberately contain no file or document field. A task
is an operational instruction, never a contract, proof of employment or a
request for sensitive personal information.
"""

import uuid

from django.conf import settings
from django.db import models

from .organization import OrganizationMembership, SupportOrganization
from .pipeline import SupportConnection


LANGUAGE_CHOICES = [
    ("ru", "Russian"),
    ("en", "English"),
    ("pl", "Polish"),
    ("uk", "Ukrainian"),
]


class WorkerTask(models.Model):
    """An employer-created instruction visible to workers only on publish."""

    STATE_DRAFT = "draft"
    STATE_PUBLISHED = "published"
    STATE_CANCELLED = "cancelled"
    STATE_CHOICES = [
        (STATE_DRAFT, "Draft"),
        (STATE_PUBLISHED, "Published"),
        (STATE_CANCELLED, "Cancelled"),
    ]

    PRIORITY_NORMAL = "normal"
    PRIORITY_IMPORTANT = "important"
    PRIORITY_CHOICES = [
        (PRIORITY_NORMAL, "Normal"),
        (PRIORITY_IMPORTANT, "Important"),
    ]

    CONTEXT_GENERAL = "general"
    CONTEXT_ARRIVAL = "arrival"
    CONTEXT_HOUSING = "housing"
    CONTEXT_TRANSPORT = "transport"
    CONTEXT_WORK = "work"
    CONTEXT_FINANCE = "finance"
    CONTEXT_CHOICES = [
        (CONTEXT_GENERAL, "General"),
        (CONTEXT_ARRIVAL, "Arrival"),
        (CONTEXT_HOUSING, "Housing"),
        (CONTEXT_TRANSPORT, "Transport"),
        (CONTEXT_WORK, "Work"),
        (CONTEXT_FINANCE, "Finance"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.CASCADE,
        related_name="worker_tasks",
    )
    title = models.CharField(max_length=180)
    instructions = models.TextField(max_length=5000)
    translations = models.JSONField(default=dict)
    original_language = models.CharField(
        max_length=2,
        choices=LANGUAGE_CHOICES,
        default="en",
    )
    priority = models.CharField(
        max_length=16,
        choices=PRIORITY_CHOICES,
        default=PRIORITY_NORMAL,
    )
    context_kind = models.CharField(
        max_length=24,
        choices=CONTEXT_CHOICES,
        default=CONTEXT_GENERAL,
    )
    due_at = models.DateTimeField(null=True, blank=True)
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=STATE_DRAFT)
    responsible_membership = models.ForeignKey(
        OrganizationMembership,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="responsible_worker_tasks",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_support_worker_tasks",
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="published_support_worker_tasks",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("due_at", "-created_at", "-id")
        indexes = [
            models.Index(fields=("organization", "state", "due_at")),
            models.Index(fields=("responsible_membership", "state", "due_at")),
        ]


class ContentTemplate(models.Model):
    """Reusable multilingual source for a task or an announcement draft.

    A template is never itself visible to a worker. Applying it creates a new
    draft through the regular task/announcement workflow, so publication and
    the recipient list still need their own deliberate confirmation.
    """

    KIND_TASK = "task"
    KIND_ANNOUNCEMENT = "announcement"
    KIND_CHOICES = [
        (KIND_TASK, "Task"),
        (KIND_ANNOUNCEMENT, "Announcement"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.CASCADE,
        related_name="content_templates",
    )
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    translations = models.JSONField(default=dict)
    source_language = models.CharField(max_length=2, choices=LANGUAGE_CHOICES)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_support_content_templates",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("kind", "name", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "name"),
                name="support_unique_content_template_name_per_org",
            ),
        ]
        indexes = [
            models.Index(
                fields=("organization", "kind", "is_active", "name"),
                name="support_ct_org_kind_active_idx",
            ),
        ]


class TaskAssignment(models.Model):
    """One independent task state for one worker connection."""

    STATUS_NEW = "new"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED_BY_WORKER = "completed_by_worker"
    STATUS_CONFIRMED = "confirmed"
    STATUS_RETURNED = "returned"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_NEW, "New"),
        (STATUS_IN_PROGRESS, "In progress"),
        (STATUS_COMPLETED_BY_WORKER, "Completed by worker"),
        (STATUS_CONFIRMED, "Confirmed"),
        (STATUS_RETURNED, "Returned"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    task = models.ForeignKey(WorkerTask, on_delete=models.CASCADE, related_name="assignments")
    connection = models.ForeignKey(
        SupportConnection,
        on_delete=models.CASCADE,
        related_name="task_assignments",
    )
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_NEW)
    worker_note = models.CharField(max_length=500, blank=True, default="")
    manager_note = models.CharField(max_length=500, blank=True, default="")
    completed_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    last_changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="changed_support_task_assignments",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("task__due_at", "-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("task", "connection"),
                name="support_unique_task_assignment_per_connection",
            ),
        ]
        indexes = [
            models.Index(fields=("connection", "status", "updated_at")),
            models.Index(fields=("task", "status")),
        ]


class Announcement(models.Model):
    """A scoped, publish-once information item for workers.

    A published announcement is not edited in place. The employer creates a
    new publication when important text changes, so acknowledgement stays
    meaningful without pretending to be a legal signature.
    """

    STATE_DRAFT = "draft"
    STATE_PUBLISHED = "published"
    STATE_ARCHIVED = "archived"
    STATE_CHOICES = [
        (STATE_DRAFT, "Draft"),
        (STATE_PUBLISHED, "Published"),
        (STATE_ARCHIVED, "Archived"),
    ]

    IMPORTANCE_NORMAL = "normal"
    IMPORTANCE_IMPORTANT = "important"
    IMPORTANCE_CHOICES = [
        (IMPORTANCE_NORMAL, "Normal"),
        (IMPORTANCE_IMPORTANT, "Important"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.CASCADE,
        related_name="announcements",
    )
    title = models.CharField(max_length=180)
    body = models.TextField(max_length=8000)
    translations = models.JSONField(default=dict)
    original_language = models.CharField(
        max_length=2,
        choices=LANGUAGE_CHOICES,
        default="en",
    )
    importance = models.CharField(
        max_length=16,
        choices=IMPORTANCE_CHOICES,
        default=IMPORTANCE_NORMAL,
    )
    requires_acknowledgement = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=STATE_DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_support_announcements",
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="published_support_announcements",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-published_at", "-created_at", "-id")
        indexes = [
            models.Index(fields=("organization", "state", "published_at")),
            models.Index(fields=("organization", "expires_at")),
        ]


class AnnouncementAcknowledgement(models.Model):
    """Recipient record and optional read acknowledgement for one worker."""

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    announcement = models.ForeignKey(
        Announcement,
        on_delete=models.CASCADE,
        related_name="acknowledgements",
    )
    connection = models.ForeignKey(
        SupportConnection,
        on_delete=models.CASCADE,
        related_name="announcement_acknowledgements",
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acknowledged_support_announcements",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-announcement__published_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("announcement", "connection"),
                name="support_unique_announcement_recipient_per_connection",
            ),
        ]
        indexes = [
            models.Index(fields=("connection", "acknowledged_at")),
            models.Index(fields=("announcement", "acknowledged_at")),
        ]
