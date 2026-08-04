import uuid

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from .organization import OrganizationMembership, SupportOrganization


class SupportVacancy(models.Model):
    """An employer's Support workflow for one public or internal vacancy.

    This is deliberately separate from ``jobs.Vacancy``.  The optional link is
    only a bridge to the public JobHub listing; Support-specific limits and
    candidate operations never change the public vacancy.
    """

    STATUS_DRAFT = "draft"
    STATUS_PUBLISHED = "published"
    STATUS_PAUSED = "paused"
    STATUS_ARCHIVED = "archived"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_PUBLISHED, "Published"),
        (STATUS_PAUSED, "Paused"),
        (STATUS_ARCHIVED, "Archived"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.CASCADE,
        related_name="support_vacancies",
    )
    public_vacancy = models.OneToOneField(
        "jobs.Vacancy",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_workflow",
    )
    internal_title = models.CharField(max_length=160)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    # This is an employer-only planning value.  It must never be serialized to
    # the public bot or candidate application endpoints.
    internal_position_limit = models.PositiveSmallIntegerField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_support_vacancies",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    paused_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("organization_id", "-updated_at", "-id")
        indexes = [
            models.Index(fields=("organization", "status", "updated_at")),
            models.Index(fields=("status", "updated_at")),
        ]

    def __str__(self):
        return f"SupportVacancy #{self.id} {self.internal_title}"


class BotContentRevision(models.Model):
    """A complete, four-language bot scenario for a Support vacancy."""

    STATUS_DRAFT = "draft"
    STATUS_PUBLISHED = "published"
    STATUS_ARCHIVED = "archived"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_PUBLISHED, "Published"),
        (STATUS_ARCHIVED, "Archived"),
    ]

    LANGUAGE_RU = "ru"
    LANGUAGE_EN = "en"
    LANGUAGE_PL = "pl"
    LANGUAGE_UK = "uk"
    LANGUAGE_CHOICES = [
        (LANGUAGE_RU, "Russian"),
        (LANGUAGE_EN, "English"),
        (LANGUAGE_PL, "Polish"),
        (LANGUAGE_UK, "Ukrainian"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    vacancy = models.ForeignKey(
        SupportVacancy,
        on_delete=models.CASCADE,
        related_name="bot_revisions",
    )
    version = models.PositiveSmallIntegerField()
    source_language = models.CharField(max_length=2, choices=LANGUAGE_CHOICES)
    # Validated by the serializer/service as a complete RU/EN/PL/UK mapping.
    content = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_support_bot_revisions",
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="published_support_bot_revisions",
    )
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("vacancy_id", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("vacancy", "version"),
                name="support_unique_bot_revision_version",
            ),
            models.UniqueConstraint(
                fields=("vacancy",),
                condition=Q(status="published"),
                name="support_one_published_bot_revision_per_vacancy",
            ),
        ]
        indexes = [
            models.Index(fields=("vacancy", "status", "version")),
        ]


class SupportApplicantReference(models.Model):
    """A non-sensitive code candidates may share to identify a partner."""

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_applicant_reference",
    )
    reference_code = models.CharField(max_length=24, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=("reference_code",))]


class SupportApplication(models.Model):
    """Minimal candidacy data.  It intentionally has no document fields."""

    STATUS_SUBMITTED = "submitted"
    STATUS_UNDER_REVIEW = "under_review"
    STATUS_APPROVED = "approved"
    STATUS_DECLINED = "declined"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_SUBMITTED, "Submitted"),
        (STATUS_UNDER_REVIEW, "Under review"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_DECLINED, "Declined"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    LANGUAGE_RU = BotContentRevision.LANGUAGE_RU
    LANGUAGE_EN = BotContentRevision.LANGUAGE_EN
    LANGUAGE_PL = BotContentRevision.LANGUAGE_PL
    LANGUAGE_UK = BotContentRevision.LANGUAGE_UK
    LANGUAGE_CHOICES = BotContentRevision.LANGUAGE_CHOICES

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    vacancy = models.ForeignKey(
        SupportVacancy,
        on_delete=models.CASCADE,
        related_name="applications",
    )
    candidate = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_applications",
    )
    revision = models.PositiveSmallIntegerField(default=1)
    preferred_language = models.CharField(max_length=2, choices=LANGUAGE_CHOICES)
    citizenship_country_code = models.CharField(max_length=2, blank=True, default="")
    current_country_code = models.CharField(max_length=2, blank=True, default="")
    availability_note = models.CharField(max_length=500, blank=True, default="")
    partner_reference_code = models.CharField(max_length=24, blank=True, default="")
    consent_version = models.CharField(max_length=32)
    consented_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_SUBMITTED)
    submitted_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-submitted_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("vacancy", "candidate", "revision"),
                name="support_unique_application_revision",
            ),
            models.UniqueConstraint(
                fields=("vacancy", "candidate"),
                condition=Q(status__in=["submitted", "under_review", "approved"]),
                name="support_one_open_application_per_vacancy_candidate",
            ),
        ]
        indexes = [
            models.Index(fields=("vacancy", "status", "submitted_at")),
            models.Index(fields=("candidate", "status", "submitted_at")),
        ]

    def __str__(self):
        return f"SupportApplication #{self.id} candidate={self.candidate_id}"


class ApplicationDecisionEvent(models.Model):
    ACTION_SUBMITTED = "submitted"
    ACTION_CLARIFICATION_REQUESTED = "clarification_requested"
    ACTION_APPROVED = "approved"
    ACTION_DECLINED = "declined"
    ACTION_CANCELLED = "cancelled"
    ACTION_CHOICES = [
        (ACTION_SUBMITTED, "Submitted"),
        (ACTION_CLARIFICATION_REQUESTED, "Clarification requested"),
        (ACTION_APPROVED, "Approved"),
        (ACTION_DECLINED, "Declined"),
        (ACTION_CANCELLED, "Cancelled"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    application = models.ForeignKey(
        SupportApplication,
        on_delete=models.CASCADE,
        related_name="decision_events",
    )
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_application_decision_events",
    )
    # Only a short, neutral clarification or decision note.  It is not a
    # document request and does not accept files.
    note = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "id")
        indexes = [models.Index(fields=("application", "created_at"))]


class PartnerPairRequest(models.Model):
    """Manual grouping aid for a couple; neither side exposes private data."""

    STATUS_PENDING = "pending"
    STATUS_CONFIRMED = "confirmed"
    STATUS_DECLINED = "declined"
    STATUS_CLOSED = "closed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_CONFIRMED, "Confirmed"),
        (STATUS_DECLINED, "Declined"),
        (STATUS_CLOSED, "Closed"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    first_application = models.ForeignKey(
        SupportApplication,
        on_delete=models.CASCADE,
        related_name="first_partner_pair_requests",
    )
    second_application = models.ForeignKey(
        SupportApplication,
        on_delete=models.CASCADE,
        related_name="second_partner_pair_requests",
    )
    state = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    first_confirmed_at = models.DateTimeField(null=True, blank=True)
    second_confirmed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_support_partner_pair_requests",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~Q(first_application=F("second_application")),
                name="support_partner_pair_requires_two_applications",
            ),
        ]
        indexes = [
            models.Index(fields=("state", "updated_at")),
        ]


class SupportConnection(models.Model):
    """The ongoing candidate-to-organization connection after approval."""

    STAGE_AWAITING_SUPPORT = "awaiting_support"
    STAGE_MANAGER = "manager_stage"
    STAGE_DOCUMENTS = "documents_stage"
    STAGE_COORDINATOR = "coordinator_stage"
    STAGE_ACTIVE_WORKER = "active_worker"
    STAGE_LIMITED_MANAGER = "limited_manager_access"
    STAGE_CLOSED = "closed"
    STAGE_CHOICES = [
        (STAGE_AWAITING_SUPPORT, "Awaiting Support access"),
        (STAGE_MANAGER, "Manager stage"),
        (STAGE_DOCUMENTS, "Documents stage"),
        (STAGE_COORDINATOR, "Coordinator stage"),
        (STAGE_ACTIVE_WORKER, "Active worker"),
        (STAGE_LIMITED_MANAGER, "Limited manager access"),
        (STAGE_CLOSED, "Closed"),
    ]
    EMPLOYMENT_PROTECTED_STAGES = frozenset({STAGE_COORDINATOR, STAGE_ACTIVE_WORKER})

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    organization = models.ForeignKey(
        SupportOrganization,
        on_delete=models.CASCADE,
        related_name="support_connections",
    )
    vacancy = models.ForeignKey(
        SupportVacancy,
        on_delete=models.CASCADE,
        related_name="connections",
    )
    application = models.OneToOneField(
        SupportApplication,
        on_delete=models.PROTECT,
        related_name="support_connection",
    )
    candidate = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_connections",
    )
    assigned_manager = models.ForeignKey(
        OrganizationMembership,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_support_connections",
    )
    stage = models.CharField(max_length=32, choices=STAGE_CHOICES, default=STAGE_AWAITING_SUPPORT)
    visible_stage = models.CharField(max_length=80, blank=True, default="")
    has_driving_license = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at", "-id")
        indexes = [
            models.Index(fields=("candidate", "stage", "is_archived")),
            models.Index(fields=("organization", "stage", "is_archived")),
            models.Index(fields=("vacancy", "stage", "is_archived")),
        ]

    def __str__(self):
        return f"SupportConnection #{self.id} candidate={self.candidate_id}"


class ConnectionStageEvent(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    connection = models.ForeignKey(
        SupportConnection,
        on_delete=models.CASCADE,
        related_name="stage_events",
    )
    previous_stage = models.CharField(max_length=32, choices=SupportConnection.STAGE_CHOICES)
    next_stage = models.CharField(max_length=32, choices=SupportConnection.STAGE_CHOICES)
    reason = models.CharField(max_length=255, blank=True, default="")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_connection_stage_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "id")
        indexes = [models.Index(fields=("connection", "created_at"))]


class EmploymentExclusivityLock(models.Model):
    """Prevents a candidate from entering two active employer workflows."""

    STATE_ACTIVE = "active"
    STATE_RELEASED = "released"
    STATE_CHOICES = [(STATE_ACTIVE, "Active"), (STATE_RELEASED, "Released")]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    candidate = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_employment_locks",
    )
    connection = models.OneToOneField(
        SupportConnection,
        on_delete=models.CASCADE,
        related_name="employment_exclusivity_lock",
    )
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=STATE_ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("candidate",),
                condition=Q(state="active"),
                name="support_one_active_employment_lock_per_candidate",
            ),
        ]
        indexes = [models.Index(fields=("candidate", "state"))]
