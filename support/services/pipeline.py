import secrets

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from support.models import (
    ApplicationDecisionEvent,
    BotContentRevision,
    ConnectionStageEvent,
    EmploymentExclusivityLock,
    OrganizationMembership,
    PartnerPairRequest,
    SupportApplicantReference,
    SupportApplication,
    SupportConnection,
    SupportVacancy,
)
from support.permission_codes import CONNECTION_TRANSITION, ORGANIZATION_MANAGE, PIPELINE_REVIEW
from support.permissions import active_membership_for, require_permission

from .audit import record_audit_event
from .entitlements import support_access_snapshot_for


def _reference_code():
    return f"JH-{secrets.token_hex(3).upper()}-{secrets.token_hex(2).upper()}"


def applicant_reference_for(*, user):
    """Return a stable opaque code, retrying on the very unlikely collision."""

    reference = SupportApplicantReference.objects.filter(user=user).first()
    if reference is not None:
        return reference
    for _ in range(5):
        try:
            return SupportApplicantReference.objects.create(
                user=user,
                reference_code=_reference_code(),
            )
        except IntegrityError:
            reference = SupportApplicantReference.objects.filter(user=user).first()
            if reference is not None:
                return reference
    raise ValidationError({"reference": "reference_code_generation_failed"})


def create_support_vacancy(
    *,
    actor,
    organization,
    internal_title,
    internal_position_limit=None,
    public_vacancy=None,
):
    require_permission(
        user=actor,
        organization=organization,
        permission_code=ORGANIZATION_MANAGE,
    )
    vacancy = SupportVacancy.objects.create(
        organization=organization,
        public_vacancy=public_vacancy,
        internal_title=(internal_title or "").strip(),
        internal_position_limit=internal_position_limit,
        created_by=actor,
    )
    record_audit_event(
        organization=organization,
        actor=actor,
        action="vacancy.created",
        target=vacancy,
        details={"has_public_vacancy": public_vacancy is not None},
    )
    return vacancy


def create_bot_revision(*, actor, vacancy, source_language, content):
    require_permission(
        user=actor,
        organization=vacancy.organization,
        permission_code=ORGANIZATION_MANAGE,
    )
    with transaction.atomic():
        locked_vacancy = SupportVacancy.objects.select_for_update().get(pk=vacancy.pk)
        latest = (
            BotContentRevision.objects.filter(vacancy=locked_vacancy)
            .order_by("-version")
            .values_list("version", flat=True)
            .first()
            or 0
        )
        revision = BotContentRevision.objects.create(
            vacancy=locked_vacancy,
            version=latest + 1,
            source_language=source_language,
            content=content,
            created_by=actor,
        )
        record_audit_event(
            organization=locked_vacancy.organization,
            actor=actor,
            action="bot_revision.created",
            target=revision,
            details={"vacancy": str(locked_vacancy.public_id), "version": revision.version},
        )
    return revision


def publish_bot_revision(*, actor, revision):
    require_permission(
        user=actor,
        organization=revision.vacancy.organization,
        permission_code=ORGANIZATION_MANAGE,
    )
    with transaction.atomic():
        revision = BotContentRevision.objects.select_for_update().select_related("vacancy").get(
            pk=revision.pk
        )
        current = (
            BotContentRevision.objects.select_for_update()
            .filter(vacancy=revision.vacancy, status=BotContentRevision.STATUS_PUBLISHED)
            .exclude(pk=revision.pk)
            .first()
        )
        if current is not None:
            current.status = BotContentRevision.STATUS_ARCHIVED
            current.save(update_fields=["status", "updated_at"])
        revision.status = BotContentRevision.STATUS_PUBLISHED
        revision.published_by = actor
        revision.published_at = timezone.now()
        revision.save(update_fields=["status", "published_by", "published_at", "updated_at"])
        record_audit_event(
            organization=revision.vacancy.organization,
            actor=actor,
            action="bot_revision.published",
            target=revision,
            details={"vacancy": str(revision.vacancy.public_id), "version": revision.version},
        )
    return revision


def publish_support_vacancy(*, actor, vacancy):
    require_permission(
        user=actor,
        organization=vacancy.organization,
        permission_code=ORGANIZATION_MANAGE,
    )
    if vacancy.organization.status != vacancy.organization.STATUS_ACTIVE:
        raise ValidationError({"organization": "active_organization_required"})
    if not BotContentRevision.objects.filter(
        vacancy=vacancy,
        status=BotContentRevision.STATUS_PUBLISHED,
    ).exists():
        raise ValidationError({"vacancy": "published_bot_revision_required"})
    vacancy.status = SupportVacancy.STATUS_PUBLISHED
    vacancy.published_at = timezone.now()
    vacancy.save(update_fields=["status", "published_at", "updated_at"])
    record_audit_event(
        organization=vacancy.organization,
        actor=actor,
        action="vacancy.published",
        target=vacancy,
        details={},
    )
    return vacancy


def submit_application(
    *,
    candidate,
    vacancy,
    preferred_language,
    citizenship_country_code,
    current_country_code,
    availability_note,
    partner_reference_code,
    consent_version,
    questionnaire_version="",
    questionnaire_answers=None,
):
    if vacancy.status != SupportVacancy.STATUS_PUBLISHED:
        raise PermissionDenied("support_vacancy_not_available")
    partner_code = (partner_reference_code or "").strip().upper()
    if partner_code:
        partner = SupportApplicantReference.objects.filter(reference_code=partner_code).first()
        if partner is None or partner.user_id == candidate.id:
            raise ValidationError({"partner_reference_code": "partner_reference_not_available"})

    with transaction.atomic():
        existing = SupportApplication.objects.select_for_update().filter(
            vacancy=vacancy,
            candidate=candidate,
            status__in=[
                SupportApplication.STATUS_SUBMITTED,
                SupportApplication.STATUS_UNDER_REVIEW,
                SupportApplication.STATUS_APPROVED,
            ],
        )
        if existing.exists():
            raise ValidationError({"application": "open_application_already_exists"})
        latest_revision = (
            SupportApplication.objects.filter(vacancy=vacancy, candidate=candidate)
            .order_by("-revision")
            .values_list("revision", flat=True)
            .first()
            or 0
        )
        applicant_reference = applicant_reference_for(user=candidate)
        application = SupportApplication.objects.create(
            vacancy=vacancy,
            candidate=candidate,
            revision=latest_revision + 1,
            preferred_language=preferred_language,
            citizenship_country_code=(citizenship_country_code or "").upper(),
            current_country_code=(current_country_code or "").upper(),
            availability_note=(availability_note or "").strip(),
            partner_reference_code=partner_code,
            questionnaire_version=(questionnaire_version or "").strip(),
            questionnaire_answers=questionnaire_answers or {},
            consent_version=consent_version,
            consented_at=timezone.now(),
        )
        if partner_code:
            paired_application = (
                SupportApplication.objects.select_for_update()
                .filter(
                    candidate=partner.user,
                    vacancy__organization=vacancy.organization,
                    partner_reference_code=applicant_reference.reference_code,
                    status__in=[
                        SupportApplication.STATUS_SUBMITTED,
                        SupportApplication.STATUS_UNDER_REVIEW,
                        SupportApplication.STATUS_APPROVED,
                    ],
                )
                .order_by("-submitted_at", "-id")
                .first()
            )
            if paired_application is not None:
                reciprocal_pair_exists = PartnerPairRequest.objects.filter(
                    Q(
                        first_application=application,
                        second_application=paired_application,
                    )
                    | Q(
                        first_application=paired_application,
                        second_application=application,
                    )
                ).exists()
                if not reciprocal_pair_exists:
                    PartnerPairRequest.objects.create(
                        first_application=application,
                        second_application=paired_application,
                        state=PartnerPairRequest.STATUS_CONFIRMED,
                        first_confirmed_at=timezone.now(),
                        second_confirmed_at=timezone.now(),
                    )
        ApplicationDecisionEvent.objects.create(
            application=application,
            action=ApplicationDecisionEvent.ACTION_SUBMITTED,
            actor=candidate,
        )
        record_audit_event(
            organization=vacancy.organization,
            actor=candidate,
            action="application.submitted",
            target=application,
            details={"vacancy": str(vacancy.public_id), "revision": application.revision},
        )
    return application


def _locked_application_for_review(application):
    return (
        SupportApplication.objects.select_for_update()
        .select_related("vacancy__organization", "candidate")
        .get(pk=application.pk)
    )


def request_application_clarification(*, actor, application, note):
    organization = application.vacancy.organization
    require_permission(user=actor, organization=organization, permission_code=PIPELINE_REVIEW)
    with transaction.atomic():
        application = _locked_application_for_review(application)
        if application.status not in {
            SupportApplication.STATUS_SUBMITTED,
            SupportApplication.STATUS_UNDER_REVIEW,
        }:
            raise ValidationError({"application": "application_not_open_for_review"})
        application.status = SupportApplication.STATUS_UNDER_REVIEW
        application.save(update_fields=["status", "updated_at"])
        ApplicationDecisionEvent.objects.create(
            application=application,
            action=ApplicationDecisionEvent.ACTION_CLARIFICATION_REQUESTED,
            actor=actor,
            note=(note or "").strip(),
        )
        record_audit_event(
            organization=application.vacancy.organization,
            actor=actor,
            action="application.clarification_requested",
            target=application,
            details={},
        )
    return application


def answer_application_clarification(*, candidate, application, answer):
    """Save a text-only candidate answer to the latest unanswered question."""

    normalized_answer = (answer or "").strip()
    if not normalized_answer:
        raise ValidationError({"answer": "clarification_answer_required"})

    with transaction.atomic():
        application = _locked_application_for_review(application)
        if application.candidate_id != candidate.id:
            raise PermissionDenied("application_candidate_required")
        if application.status != SupportApplication.STATUS_UNDER_REVIEW:
            raise ValidationError({"application": "application_not_waiting_for_clarification"})

        latest_event = application.decision_events.order_by("-created_at", "-id").first()
        if (
            latest_event is None
            or latest_event.action
            != ApplicationDecisionEvent.ACTION_CLARIFICATION_REQUESTED
        ):
            raise ValidationError({"application": "clarification_not_pending"})

        ApplicationDecisionEvent.objects.create(
            application=application,
            action=ApplicationDecisionEvent.ACTION_CLARIFICATION_ANSWERED,
            actor=candidate,
            note=normalized_answer,
        )
        record_audit_event(
            organization=application.vacancy.organization,
            actor=candidate,
            action="application.clarification_answered",
            target=application,
            details={},
        )
    return application


def decline_application(*, actor, application, note):
    organization = application.vacancy.organization
    require_permission(user=actor, organization=organization, permission_code=PIPELINE_REVIEW)
    with transaction.atomic():
        application = _locked_application_for_review(application)
        if application.status not in {
            SupportApplication.STATUS_SUBMITTED,
            SupportApplication.STATUS_UNDER_REVIEW,
        }:
            raise ValidationError({"application": "application_not_open_for_review"})
        application.status = SupportApplication.STATUS_DECLINED
        application.save(update_fields=["status", "updated_at"])
        ApplicationDecisionEvent.objects.create(
            application=application,
            action=ApplicationDecisionEvent.ACTION_DECLINED,
            actor=actor,
            note=(note or "").strip(),
        )
        record_audit_event(
            organization=application.vacancy.organization,
            actor=actor,
            action="application.declined",
            target=application,
            details={},
        )
    return application


def approve_application(*, actor, application):
    organization = application.vacancy.organization
    manager_membership = require_permission(
        user=actor,
        organization=organization,
        permission_code=PIPELINE_REVIEW,
    )
    with transaction.atomic():
        application = _locked_application_for_review(application)
        if application.status not in {
            SupportApplication.STATUS_SUBMITTED,
            SupportApplication.STATUS_UNDER_REVIEW,
        }:
            raise ValidationError({"application": "application_not_open_for_review"})

        # Lock the candidate and the candidate's current employment lock before
        # accepting.  Two firms may continue to review applications, but a
        # candidate already in coordinator/active work cannot be accepted again.
        user_model = application.candidate.__class__
        user_model.objects.select_for_update().get(pk=application.candidate_id)
        active_lock = EmploymentExclusivityLock.objects.select_for_update().filter(
            candidate=application.candidate,
            state=EmploymentExclusivityLock.STATE_ACTIVE,
        ).first()
        if active_lock is not None:
            raise ValidationError({"application": "candidate_has_active_support_assignment"})

        access = support_access_snapshot_for(application.candidate)
        stage = (
            SupportConnection.STAGE_MANAGER
            if access["state"] == "active"
            else SupportConnection.STAGE_AWAITING_SUPPORT
        )
        connection = SupportConnection.objects.create(
            organization=application.vacancy.organization,
            vacancy=application.vacancy,
            application=application,
            candidate=application.candidate,
            assigned_manager=manager_membership,
            stage=stage,
        )
        application.status = SupportApplication.STATUS_APPROVED
        application.save(update_fields=["status", "updated_at"])
        ApplicationDecisionEvent.objects.create(
            application=application,
            action=ApplicationDecisionEvent.ACTION_APPROVED,
            actor=actor,
        )
        if stage == SupportConnection.STAGE_MANAGER:
            ConnectionStageEvent.objects.create(
                connection=connection,
                previous_stage=SupportConnection.STAGE_AWAITING_SUPPORT,
                next_stage=SupportConnection.STAGE_MANAGER,
                reason="support_access_already_active",
                actor=actor,
            )
        record_audit_event(
            organization=application.vacancy.organization,
            actor=actor,
            action="application.approved",
            target=connection,
            details={"application": str(application.public_id), "stage": stage},
        )
        from .notifications import enqueue_support_notification

        enqueue_support_notification(
            organization=application.vacancy.organization,
            recipient=application.candidate,
            notification_code="application.approved",
            target_kind="connection",
            target_public_id=connection.public_id,
            target_key=f"support:connection:{connection.public_id}",
            collapse_key=f"support:connection:{connection.public_id}",
            dedupe_key=f"application.approved:{connection.public_id}",
        )
    return connection


_ALLOWED_STAGE_TRANSITIONS = {
    SupportConnection.STAGE_MANAGER: {
        SupportConnection.STAGE_DOCUMENTS,
        SupportConnection.STAGE_CLOSED,
    },
    SupportConnection.STAGE_DOCUMENTS: {
        SupportConnection.STAGE_COORDINATOR,
        SupportConnection.STAGE_CLOSED,
    },
    SupportConnection.STAGE_COORDINATOR: {
        SupportConnection.STAGE_ACTIVE_WORKER,
        SupportConnection.STAGE_CLOSED,
    },
    SupportConnection.STAGE_ACTIVE_WORKER: {SupportConnection.STAGE_CLOSED},
    SupportConnection.STAGE_LIMITED_MANAGER: {SupportConnection.STAGE_CLOSED},
}


def transition_connection(*, actor, connection, next_stage, reason=""):
    organization = connection.organization
    require_permission(
        user=actor,
        organization=organization,
        permission_code=CONNECTION_TRANSITION,
    )
    with transaction.atomic():
        connection = (
            SupportConnection.objects.select_for_update()
            .select_related("candidate", "organization")
            .get(pk=connection.pk)
        )
        permitted_next_stages = _ALLOWED_STAGE_TRANSITIONS.get(connection.stage, set())
        if next_stage not in permitted_next_stages:
            raise ValidationError({"next_stage": "unsupported_connection_transition"})
        connection.candidate.__class__.objects.select_for_update().get(pk=connection.candidate_id)
        existing_lock = EmploymentExclusivityLock.objects.select_for_update().filter(
            candidate=connection.candidate,
            state=EmploymentExclusivityLock.STATE_ACTIVE,
        ).first()
        if next_stage in SupportConnection.EMPLOYMENT_PROTECTED_STAGES:
            if existing_lock is not None and existing_lock.connection_id != connection.id:
                raise ValidationError({"connection": "candidate_has_active_support_assignment"})
            if existing_lock is None:
                EmploymentExclusivityLock.objects.create(
                    candidate=connection.candidate,
                    connection=connection,
                )
        elif next_stage == SupportConnection.STAGE_CLOSED and existing_lock is not None:
            if existing_lock.connection_id == connection.id:
                existing_lock.state = EmploymentExclusivityLock.STATE_RELEASED
                existing_lock.released_at = timezone.now()
                existing_lock.save(update_fields=["state", "released_at"])

        previous_stage = connection.stage
        connection.stage = next_stage
        if next_stage == SupportConnection.STAGE_CLOSED:
            connection.is_archived = True
            connection.archived_at = timezone.now()
        connection.save(
            update_fields=["stage", "is_archived", "archived_at", "updated_at"]
        )
        ConnectionStageEvent.objects.create(
            connection=connection,
            previous_stage=previous_stage,
            next_stage=next_stage,
            reason=(reason or "").strip(),
            actor=actor,
        )
        record_audit_event(
            organization=connection.organization,
            actor=actor,
            action="connection.stage_changed",
            target=connection,
            details={"previous_stage": previous_stage, "next_stage": next_stage},
        )
        from .notifications import enqueue_support_notification

        enqueue_support_notification(
            organization=connection.organization,
            recipient=connection.candidate,
            notification_code="connection.stage_changed",
            target_kind="connection",
            target_public_id=connection.public_id,
            target_key=f"support:connection:{connection.public_id}",
            collapse_key=f"support:connection:{connection.public_id}",
            dedupe_key=(
                f"connection.stage_changed:{connection.public_id}:{previous_stage}:{next_stage}:"
                f"{connection.updated_at.isoformat()}"
            ),
            context={"stage": next_stage},
        )
    return connection
