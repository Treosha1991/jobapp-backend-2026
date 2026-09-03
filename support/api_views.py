from calendar import monthrange
from datetime import date, timedelta
import logging
import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils.dateparse import parse_date
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.exceptions import (
    APIException,
    NotFound,
    PermissionDenied,
    ValidationError,
)
from rest_framework.response import Response
from rest_framework.views import APIView


logger = logging.getLogger(__name__)

from jobs.avatar_utils import avatar_public_url

from .feature_flags import is_project_first_workspace_enabled, is_support_feature_enabled
from .models import (
    Announcement,
    AnnouncementAcknowledgement,
    ApplicationDecisionEvent,
    AuditEvent,
    BotContentRevision,
    CalendarMarkBatch,
    CalendarMarkBatchItem,
    CalendarMarkTemplate,
    ContentTemplate,
    DocumentRequestPackage,
    DriverVehicleAssignment,
    HousingAssignment,
    HousingPlace,
    HousingRoom,
    HousingSite,
    InAppNotification,
    MembershipInvitation,
    OrganizationMembership,
    ProjectCrew,
    ProjectCrewMemberAbsence,
    ProjectCrewDriverSubstitution,
    ProjectCrewPassenger,
    ProjectCrewResourceAssignment,
    ProjectCrewShift,
    ProjectCrewShiftMember,
    RouteStop,
    ScheduledShiftBatch,
    ScheduledWorkShift,
    ShiftTemplate,
    SupportAccessGrant,
    SupportApplication,
    SupportConnection,
    SupportConversation,
    SupportConversationMember,
    SupportMessage,
    SupportOrganization,
    SupportWorkerDocumentReference,
    SupportVacancy,
    TaskAssignment,
    TransportRoute,
    TransportPassengerAssignment,
    Vehicle,
    WorkerAccessScope,
    WorkerProjectAssignment,
    WorkTimeEntry,
    WorkerRequest,
    WorkerRequestDate,
    WorkerTask,
    WorkerScheduleDayOff,
    WorkProject,
    Worksite,
)
from .permission_codes import (
    ANNOUNCEMENT_MANAGE,
    CHAT_MANAGE,
    DOCUMENT_REQUEST,
    HOUSING_MANAGE,
    ORGANIZATION_MANAGE,
    PIPELINE_REVIEW,
    SCHEDULE_MANAGE,
    TIME_EDIT,
    TIME_REVIEW,
    TIME_VIEW,
    TRANSPORT_MANAGE,
    REQUEST_DECIDE,
    TASK_MANAGE,
    WORKER_VIEW,
)
from .permissions import (
    active_membership_for,
    has_permission,
    has_unrestricted_worker_access,
    require_permission,
    require_worker_connection_access,
    worker_connection_queryset_for,
)
from .questionnaire import QUESTIONNAIRE_VERSION
from .selectors.workspace import transport_workspace_snapshot
from .selectors.worker_workspace import (
    worker_missing_time_entry_actions,
    worker_workspace_snapshot,
    worker_workspace_week_snapshot,
)
from .selectors.project_first import (
    project_first_creation_options,
    project_first_crew_days_payload,
    project_first_crew_payload,
    project_first_driver_exceptions_payload,
    project_first_project_payload,
    project_first_project_list,
    project_first_project_workspace,
)
from .serializers import (
    AnnouncementCreateSerializer,
    ApplicationReviewSerializer,
    ApplicationClarificationResponseSerializer,
    BotContentRevisionCreateSerializer,
    CalendarMarkBatchCreateSerializer,
    CalendarMarkTemplateCreateSerializer,
    ContentTemplateCreateSerializer,
    ConnectionTransitionSerializer,
    DocumentRequestPackageCreateSerializer,
    DocumentRequestPackageDecisionSerializer,
    GroupPushPreferenceSerializer,
    DriverVehicleAssignmentCreateSerializer,
    HousingAssignmentCreateSerializer,
    HousingAssignmentCheckOutSerializer,
    HousingAvailableWorkersQuerySerializer,
    HousingPlaceCreateSerializer,
    HousingRoomCreateSerializer,
    HousingSiteCreateSerializer,
    MembershipInvitationCreateSerializer,
    PermissionCodeSerializer,
    ProjectCreateSerializer,
    ProjectCrewCreateSerializer,
    ProjectCrewDriverAbsenceSerializer,
    ProjectCrewDriverReplaceSerializer,
    ProjectCrewVehicleSwapSerializer,
    ProjectCrewDriverSubstituteSerializer,
    ProjectCrewPassengerWriteSerializer,
    ProjectCrewShiftReleaseSerializer,
    ProjectCrewShiftReplaceSerializer,
    ProjectCrewUpdateSerializer,
    ProjectUpdateSerializer,
    SupportApplicationCreateSerializer,
    SupportMessageCreateSerializer,
    SupportMessageForwardSerializer,
    SupportContactMessageCreateSerializer,
    SupportOrganizationCreateSerializer,
    SupportVacancyCreateSerializer,
    TemporarySupportAccessGrantSerializer,
    WorkerTaskCreateSerializer,
    WorkerTaskStaffDecisionSerializer,
    WorkerTaskWorkerActionSerializer,
    TransportRouteCreateSerializer,
    VehicleCreateSerializer,
    WorkerAccessScopeCreateSerializer,
    WorkerProjectAssignmentCreateSerializer,
    WorkProjectCreateSerializer,
    WorksiteCreateSerializer,
    RoutePassengerCreateSerializer,
    RouteStopCreateSerializer,
    EmptyStrictInputSerializer,
    ScheduledWorkShiftCreateSerializer,
    ScheduledShiftBatchCreateSerializer,
    ShiftTemplateCreateSerializer,
    WorkTimeEntryCorrectionSerializer,
    WorkTimeEntryStaffEditSerializer,
    WorkTimeEntrySubmitSerializer,
    WorkerRequestCreateSerializer,
    WorkerRequestDecisionSerializer,
    WorkerShiftPeerChatOpenSerializer,
)
from .services.audit import record_audit_event
from .services.conversations import (
    find_private_manager_conversation,
    mark_conversation_read,
    open_manager_conversation,
    open_manager_conversation_for_staff,
    open_staff_conversation,
    contact_share_options,
    open_shared_contact_conversation,
    open_project_shift_peer_conversation,
    require_conversation_access,
    send_contact_message,
    send_text_message,
    forward_text_message_to_existing_conversation,
)
from .services.entitlements import support_access_snapshot_for
from .services.notifications import enqueue_support_notification
from .services.translations import (
    TranslationProviderNotConfigured,
    request_message_translation,
)
from .services.organizations import (
    accept_membership_invitation,
    activate_organization,
    create_membership_invitation,
    create_organization,
    grant_delegable_permission,
    grant_permission,
    grant_worker_access_scope,
    revoke_worker_access_scope,
)
from .services.operations import (
    add_route_passenger,
    add_route_stop,
    cancel_housing_assignment,
    cancel_transport_route,
    cancel_worker_project_assignment,
    create_driver_vehicle_assignment,
    create_housing_assignment,
    create_transport_route,
    create_worker_project_assignment,
    publish_housing_assignment,
    schedule_housing_check_out,
    publish_transport_route,
    publish_worker_project_assignment,
)
from .services.timekeeping import (
    acknowledge_staff_time_adjustment,
    cancel_calendar_mark_batch,
    cancel_scheduled_shift_batch,
    cancel_scheduled_shift,
    confirm_work_time_entry,
    create_scheduled_shift_batch,
    create_scheduled_shift,
    create_calendar_mark_batch,
    create_calendar_mark_template,
    create_shift_template,
    edit_work_time_entry,
    publish_scheduled_shift_batch,
    publish_calendar_mark_batch,
    publish_scheduled_shift,
    request_work_time_correction,
    submit_work_time_entry,
    worker_time_entry_access,
)
from .services.worker_requests import (
    cancel_worker_request,
    decline_extra_shift_date,
    decide_worker_request,
    refresh_extra_shift_requests,
    submit_worker_request,
)
from .services.documents import (
    create_document_request_package,
    mark_document_request_package_sent,
    review_document_request_package,
)
from .services.tasks import (
    acknowledge_announcement,
    create_announcement,
    create_content_template,
    create_worker_task,
    publish_announcement,
    publish_worker_task,
    staff_change_task_assignment,
    worker_change_task_assignment,
)
from .services.registries import (
    create_project,
    create_housing_place,
    create_housing_room,
    create_housing_site,
    create_vehicle,
    create_work_project,
    create_worksite,
    update_project,
)
from .services.project_crews import (
    PASSENGER_SCOPE_FUTURE,
    PASSENGER_SCOPE_SELECTED,
    archive_project,
    archive_project_crew,
    assign_project_crew_substitute_driver,
    assign_project_crew_passenger,
    cancel_project_crew_driver_absence,
    cancel_project_crew_substitute_driver,
    create_project_crew,
    publish_project_crew_shifts,
    mark_project_crew_driver_absence,
    project_crew_substitute_driver_candidates,
    project_crew_vehicle_swap_options,
    release_project_crew_shifts,
    release_project_crew_member_days,
    mark_worker_schedule_days_off,
    restore_worker_schedule_days_off,
    remove_project_crew_passenger,
    replace_project_crew_driver,
    swap_project_crew_vehicle,
    update_project_crew,
)
from .services.pipeline import (
    application_resubmission_state,
    answer_application_clarification,
    approve_application,
    create_bot_revision,
    create_support_vacancy,
    decline_application,
    publish_bot_revision,
    publish_support_vacancy,
    request_application_clarification,
    submit_application,
    transition_connection,
)


# A calendar mark is a projection of an individually approved request.  It is
# deliberately not a separate mutable record: otherwise an approved vacation,
# the calendar and the time ledger could disagree about the same day.
_CALENDAR_MARK_REQUEST_TYPES = frozenset(
    {
        WorkerRequest.TYPE_DAY_OFF,
        WorkerRequest.TYPE_VACATION,
        WorkerRequest.TYPE_UNPAID_ABSENCE,
        WorkerRequest.TYPE_UNABLE_TODAY,
    }
)


def _candidate_queue_counts(organization):
    """Return the two candidate queues shown in staff web and mobile UI."""

    return {
        "pending_applications": SupportApplication.objects.filter(
            vacancy__organization=organization,
            status__in=(
                SupportApplication.STATUS_SUBMITTED,
                SupportApplication.STATUS_UNDER_REVIEW,
            ),
        ).count(),
        "onboarding_candidates": SupportConnection.objects.filter(
            organization=organization,
            is_archived=False,
            stage=SupportConnection.STAGE_DOCUMENTS,
        ).count(),
    }


def _organization_payload(organization, membership=None):
    payload = {
        "id": str(organization.public_id),
        "display_name": organization.display_name,
        "status": organization.status,
    }
    if membership is not None:
        payload["membership"] = {
            "id": str(membership.public_id),
            "display_role": membership.display_role,
            "is_owner": membership.is_owner,
            "state": membership.state,
        }
    return payload


def _membership_payload(membership):
    return {
        "id": str(membership.public_id),
        "display_role": membership.display_role,
        "is_owner": membership.is_owner,
        "state": membership.state,
    }


def _invitation_payload(invitation):
    return {
        "id": str(invitation.public_id),
        "organization": _organization_payload(invitation.organization),
        "display_role": invitation.display_role,
        "state": invitation.state,
        "expires_at": invitation.expires_at,
        "permission_codes": [
            grant.permission_code
            for grant in invitation.permission_grants.all().order_by("permission_code")
        ],
    }


def _user_display_name(user):
    full_name = " ".join(item for item in [user.first_name, user.last_name] if item).strip()
    return full_name or user.username


def _user_identity_payload(user):
    profile = getattr(user, "profile", None)
    return {
        "first_name": (user.first_name or "").strip(),
        "last_name": (user.last_name or "").strip(),
        "display_name": _user_display_name(user),
        "avatar_url": avatar_public_url(getattr(profile, "avatar_key", "")),
    }


def _application_payload(application, *, include_staff_fields=False):
    public_vacancy = application.vacancy.public_vacancy
    payload = {
        "id": str(application.public_id),
        "vacancy_id": str(application.vacancy.public_id),
        "public_vacancy_id": application.vacancy.public_vacancy_id,
        "vacancy_title": (
            public_vacancy.title
            if public_vacancy is not None
            else application.vacancy.internal_title
        ),
        "organization": {
            "id": str(application.vacancy.organization.public_id),
            "display_name": application.vacancy.organization.display_name,
        },
        "status": application.status,
        "preferred_language": application.preferred_language,
        "submitted_at": application.submitted_at,
    }
    if include_staff_fields:
        payload.update(
            {
                "candidate": {
                    "id": str(application.candidate_id),
                    **_user_identity_payload(application.candidate),
                },
                "vacancy": {
                    "id": str(application.vacancy.public_id),
                    "internal_title": application.vacancy.internal_title,
                },
                "citizenship_country_code": application.citizenship_country_code,
                "current_country_code": application.current_country_code,
                "availability_note": application.availability_note,
                "partner_reference_code": application.partner_reference_code,
                "questionnaire_version": application.questionnaire_version,
                "questionnaire": application.questionnaire_answers,
                "revision": application.revision,
            }
        )
    return payload


def _candidate_application_payload(application):
    payload = _application_payload(application)
    resubmission = application_resubmission_state(application)
    payload["can_resubmit"] = resubmission["can_resubmit"]
    payload["resubmit_available_at"] = resubmission["available_at"]
    payload["resubmit_wait_seconds"] = resubmission["wait_seconds"]
    connection = getattr(application, "support_connection", None)
    payload["connection"] = _connection_payload(connection) if connection is not None else None
    latest_decision = (
        application.decision_events.exclude(
            action=ApplicationDecisionEvent.ACTION_CLARIFICATION_ANSWERED
        )
        .order_by("-created_at", "-id")
        .first()
    )
    payload["last_decision"] = (
        {
            "action": latest_decision.action,
            "note": latest_decision.note,
            "created_at": latest_decision.created_at,
        }
        if latest_decision is not None
        else None
    )
    latest_question = (
        application.decision_events.filter(
            action=ApplicationDecisionEvent.ACTION_CLARIFICATION_REQUESTED
        )
        .order_by("-created_at", "-id")
        .first()
    )
    latest_answer = None
    if latest_question is not None:
        latest_answer = (
            application.decision_events.filter(
                action=ApplicationDecisionEvent.ACTION_CLARIFICATION_ANSWERED,
                created_at__gte=latest_question.created_at,
            )
            .order_by("-created_at", "-id")
            .first()
        )
    payload["clarification"] = (
        {
            "question": latest_question.note,
            "requested_at": latest_question.created_at,
            "answer": latest_answer.note if latest_answer is not None else "",
            "answered_at": latest_answer.created_at if latest_answer is not None else None,
            "requires_response": latest_answer is None,
        }
        if latest_question is not None
        else None
    )
    conversation = None
    if connection is not None:
        manager_membership = connection.assigned_manager
        if manager_membership is not None:
            conversation = find_private_manager_conversation(
                organization=connection.organization,
                worker=application.candidate,
                manager=manager_membership.user,
            )
        else:
            conversation = (
                SupportConversation.objects.filter(
                    organization=connection.organization,
                    kind=SupportConversation.KIND_MANAGER,
                    state=SupportConversation.STATE_ACTIVE,
                    members__user=application.candidate,
                    members__left_at__isnull=True,
                )
                .filter(
                    Q(private_worker=application.candidate)
                    | Q(private_worker__isnull=True, connection=connection)
                )
                .distinct()
                .order_by("-updated_at", "-id")
                .first()
            )
    payload["manager_conversation_id"] = (
        str(conversation.public_id) if conversation is not None else None
    )
    payload["can_open_manager_chat"] = bool(
        connection is not None
        and connection.stage == SupportConnection.STAGE_MANAGER
        and not connection.is_archived
    )
    return payload


def _connection_payload(connection):
    return {
        "id": str(connection.public_id),
        "organization": {
            "id": str(connection.organization.public_id),
            "display_name": connection.organization.display_name,
        },
        "vacancy_id": str(connection.vacancy.public_id),
        "stage": connection.stage,
        "visible_stage": connection.visible_stage,
        "is_archived": connection.is_archived,
        "created_at": connection.created_at,
        "updated_at": connection.updated_at,
    }


def _conversation_payload(conversation, *, viewer):
    if "members" in getattr(conversation, "_prefetched_objects_cache", {}):
        members = list(conversation.members.all())
    else:
        members = list(
            conversation.members.select_related("user", "user__profile").all()
        )
    other_members = [
        member
        for member in members
        if member.left_at is None and member.user_id != viewer.id
    ]
    viewer_member = next(
        (member for member in members if member.user_id == viewer.id),
        None,
    )
    last_message = conversation.messages.order_by("-created_at", "-id").first()
    unread_messages = conversation.messages.exclude(sender=viewer)
    if viewer_member is not None and viewer_member.last_read_at is not None:
        unread_messages = unread_messages.filter(created_at__gt=viewer_member.last_read_at)
    unread_count = unread_messages.count()
    audience = (
        "workers"
        if any(member.role == SupportConversationMember.ROLE_WORKER for member in other_members)
        else "staff"
    )
    return {
        "id": str(conversation.public_id),
        "kind": conversation.kind,
        "title": conversation.title,
        "connection_id": str(conversation.connection.public_id)
        if conversation.connection_id
        else None,
        "organization": _organization_payload(conversation.organization),
        "state": conversation.state,
        "participants": [
            {**_user_identity_payload(member.user), "role": member.role}
            for member in other_members
        ],
        "audience": audience,
        "updated_at": conversation.updated_at,
        "last_message_preview": (
            "" if last_message is None or last_message.deleted_at else last_message.body
        ),
        "last_message_at": last_message.created_at if last_message is not None else None,
        "unread_count": unread_count,
        "is_read": unread_count == 0,
        "group_push_enabled": (
            viewer_member.group_push_enabled
            if conversation.kind == SupportConversation.KIND_GROUP and viewer_member is not None
            else None
        ),
        "can_share_contacts": bool(
            viewer_member is not None
            and viewer_member.role == SupportConversationMember.ROLE_STAFF
            and has_permission(
                user=viewer,
                organization=conversation.organization,
                permission_code=CHAT_MANAGE,
            )
        ),
    }


def _message_payload(message, *, viewer):
    shared_contact = None
    if message.kind == SupportMessage.KIND_CONTACT and message.shared_contact_user_id:
        shared_contact = {
            "target_type": (
                "worker" if message.shared_contact_connection_id else "staff"
            ),
            "target_id": (
                str(message.shared_contact_connection.public_id)
                if message.shared_contact_connection_id
                else str(message.shared_contact_membership.public_id)
                if message.shared_contact_membership_id
                else None
            ),
            "display_name": _user_display_name(message.shared_contact_user),
            "first_name": (message.shared_contact_user.first_name or "").strip(),
            "last_name": (message.shared_contact_user.last_name or "").strip(),
            "avatar_url": _user_identity_payload(message.shared_contact_user)["avatar_url"],
            "subtitle": (
                message.shared_contact_connection.vacancy.internal_title
                if message.shared_contact_connection_id
                else message.shared_contact_membership.display_role
                if message.shared_contact_membership_id
                else ""
            ),
        }
    reply_to = None
    if message.reply_to_id:
        reply_to = {
            "id": str(message.reply_to.public_id),
            "body": "" if message.reply_to.deleted_at else message.reply_to.body,
            "sender_display_name": (
                _user_display_name(message.reply_to.sender)
                if message.reply_to.sender
                else ""
            ),
            "is_mine": message.reply_to.sender_id == viewer.id,
            "deleted_at": message.reply_to.deleted_at,
        }
    return {
        "id": str(message.public_id),
        "kind": message.kind,
        "body": "" if message.deleted_at else message.body,
        "original_language": message.original_language,
        "is_mine": message.sender_id == viewer.id,
        "sender_display_name": _user_display_name(message.sender) if message.sender else "",
        "sender_first_name": (message.sender.first_name or "").strip() if message.sender else "",
        "sender_last_name": (message.sender.last_name or "").strip() if message.sender else "",
        "sender_avatar_url": (
            _user_identity_payload(message.sender)["avatar_url"] if message.sender else ""
        ),
        "created_at": message.created_at,
        "edited_at": message.edited_at,
        "deleted_at": message.deleted_at,
        "is_forwarded": message.forwarded_from_id is not None,
        "forwarded_from_sender_display_name": (
            _user_display_name(message.forwarded_from.sender)
            if message.forwarded_from_id and message.forwarded_from.sender
            else ""
        ),
        "forwarded_from_sender_avatar_url": (
            _user_identity_payload(message.forwarded_from.sender)["avatar_url"]
            if message.forwarded_from_id and message.forwarded_from.sender
            else ""
        ),
        "reply_to": reply_to,
        "shared_contact": shared_contact,
    }


def _staff_application_or_not_found(*, user, application_public_id):
    application = get_object_or_404(
        SupportApplication.objects.select_related(
            "vacancy__organization", "candidate", "candidate__profile"
        )
        .filter(
            vacancy__organization__memberships__user=user,
            vacancy__organization__memberships__state=OrganizationMembership.STATE_ACTIVE,
        )
        .distinct(),
        public_id=application_public_id,
    )
    require_permission(
        user=user,
        organization=application.vacancy.organization,
        permission_code=PIPELINE_REVIEW,
    )
    return application


class SupportFeatureAPIView(APIView):
    """Base API class that prevents accidental discovery before pilot enablement."""

    permission_classes = [permissions.IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not is_support_feature_enabled():
            raise NotFound("support_not_available")


def _require_active_support_access(user):
    """Keep worker-only operational data behind the Support entitlement."""

    if support_access_snapshot_for(user)["state"] != "active":
        raise PermissionDenied("support_access_required")


class JobHubOperatorRequiredMixin:
    def require_jobhub_operator(self, request):
        if not request.user.is_staff:
            raise PermissionDenied("support_operator_required")


class OrganizationAccessMixin:
    def get_organization(self, *, user, organization_public_id):
        organization = get_object_or_404(SupportOrganization, public_id=organization_public_id)
        membership = active_membership_for(user=user, organization=organization)
        if membership is None:
            # Match an absent object so public UUIDs do not become an oracle.
            raise NotFound("support_organization_not_found")
        return organization, membership


class ProjectFirstOrganizationAccessMixin(OrganizationAccessMixin):
    """Guard the whole-crew read model until scoped crew access exists."""

    def get_project_first_organization(self, *, user, organization_public_id):
        if not is_project_first_workspace_enabled():
            raise NotFound("project_first_workspace_not_available")
        organization, membership = self.get_organization(
            user=user,
            organization_public_id=organization_public_id,
        )
        if not (
            has_permission(
                user=user,
                organization=organization,
                permission_code=SCHEDULE_MANAGE,
            )
            and has_permission(
                user=user,
                organization=organization,
                permission_code=TRANSPORT_MANAGE,
            )
            and has_unrestricted_worker_access(
                user=user,
                organization=organization,
            )
        ):
            # Match the web workspace and avoid exposing that a project exists.
            raise NotFound("project_first_workspace_not_found")
        return organization, membership


_PROJECT_FIRST_ERROR_MESSAGES = {
    "ru": {
        "invalid_input": "Проверьте заполненные поля.",
        "idempotency_key_required": "Для создания проекта нужен заголовок Idempotency-Key.",
        "idempotency_key_invalid": "Idempotency-Key должен быть UUID.",
        "idempotency_key_reused": "Этот Idempotency-Key уже использован с другими данными проекта.",
        "project_name_required": "Укажите название проекта.",
        "project_name_already_exists": "Проект с таким названием уже существует.",
        "project_patch_empty": "Укажите хотя бы одно изменение проекта.",
        "country_code_must_be_iso_alpha_2": "Код страны должен состоять из двух латинских букв.",
        "period_end_must_not_be_before_start": "Дата окончания не может быть раньше даты начала.",
        "project_capacity_below_permanent_roster": "Количество мест не может быть меньше постоянного состава проекта.",
        "crew_idempotency_key_required": "Для создания экипажа нужен заголовок Idempotency-Key.",
        "crew_idempotency_key_invalid": "Idempotency-Key экипажа должен быть UUID.",
        "crew_idempotency_key_reused": "Этот Idempotency-Key уже использован с другими данными экипажа.",
        "crew_name_required": "Укажите название экипажа.",
        "crew_patch_empty": "Укажите хотя бы одно изменение экипажа.",
        "crew_not_available": "Экипаж недоступен для изменения.",
        "shift_idempotency_key_required": "Для изменения смен нужен заголовок Idempotency-Key.",
        "shift_idempotency_key_invalid": "Idempotency-Key смен должен быть UUID.",
        "shift_idempotency_key_reused": "Этот Idempotency-Key уже использован с другими данными смен.",
        "passenger_idempotency_key_required": "Для изменения состава пассажиров нужен заголовок Idempotency-Key.",
        "passenger_idempotency_key_invalid": "Idempotency-Key изменения пассажиров должен быть UUID.",
        "passenger_idempotency_key_reused": "Этот Idempotency-Key уже использован с другими данными пассажира.",
        "passenger_scope_invalid": "Выберите изменение на все будущие дни или только на выбранные даты.",
        "passenger_effective_on_required": "Укажите дату начала изменения постоянного состава.",
        "passenger_effective_on_not_allowed": "Для выбранных дат отдельная дата начала не используется.",
        "passenger_work_dates_required": "Выберите хотя бы один опубликованный день экипажа.",
        "passenger_work_dates_not_allowed": "Для постоянного состава отдельные даты смен не передаются.",
        "driver_replacement_idempotency_key_required": "Для постоянной замены водителя нужен заголовок Idempotency-Key.",
        "driver_replacement_idempotency_key_invalid": "Idempotency-Key замены водителя должен быть UUID.",
        "driver_replacement_idempotency_key_reused": "Этот Idempotency-Key уже использован с другими данными замены водителя.",
        "driver_absence_idempotency_key_required": "Для изменения отсутствия водителя нужен заголовок Idempotency-Key.",
        "driver_absence_idempotency_key_invalid": "Idempotency-Key отсутствия водителя должен быть UUID.",
        "driver_absence_idempotency_key_reused": "Этот Idempotency-Key уже использован с другими датами отсутствия водителя.",
        "driver_substitution_idempotency_key_required": "Для временной подмены водителя нужен заголовок Idempotency-Key.",
        "driver_substitution_idempotency_key_invalid": "Idempotency-Key подмены водителя должен быть UUID.",
        "driver_substitution_idempotency_key_reused": "Этот Idempotency-Key уже использован с другими данными подмены водителя.",
        "driver_absence_missing": "На одной из выбранных дат основной водитель не отмечен отсутствующим.",
        "driver_substitution_missing": "На выбранных датах нет активного подменного водителя.",
        "substitution_date_in_past": "Подменного водителя нельзя назначить на прошедший день.",
        "substitution_requires_driver_absence": "Подмену можно назначить только на опубликованные дни без основного водителя.",
        "substitute_driver_unavailable": "Выбранный подменный водитель занят или недоступен на одну из дат.",
        "replacement_driver_not_in_crew": "Нового водителя можно выбрать только из пассажиров этого экипажа.",
        "replacement_driver_shift_conflict": "У нового водителя есть пересекающаяся смена на одну из будущих дат экипажа.",
        "crew_shift_missing": "На одну из выбранных дат нет опубликованной смены экипажа.",
        "worker_drives_other_crew": "На одну из выбранных дат работник является водителем другого экипажа.",
        "worker_day_off": "На одну из выбранных дат у работника отмечен выходной.",
        "worker_absent_from_crew": "На одну из выбранных дат работник отмечен отсутствующим в экипаже.",
        "worker_is_crew_driver": "Водителя этого экипажа нельзя одновременно добавить пассажиром.",
        "work_dates_required": "Выберите хотя бы один календарный день.",
        "shift_time_required": "Укажите время начала и окончания смены.",
        "break_minutes_invalid": "Проверьте продолжительность паузы.",
        "crew_resource_missing": "На один из выбранных дней у экипажа нет водителя и автомобиля.",
        "crew_capacity_exceeded": "На один из выбранных дней превышено количество мест в автомобиле.",
        "driver_shift_conflict": "У водителя есть пересекающаяся смена в другом экипаже.",
        "driver_licence_not_confirmed": "У выбранного работника нет подтверждённого водительского удостоверения.",
        "vehicle_not_available": "Выбранный автомобиль недоступен.",
        "driver_project_vehicle_locked": "Водитель уже работает в другом проекте и должен сохранить закреплённый автомобиль.",
        "driver_or_vehicle_already_assigned": "Водитель или автомобиль уже закреплён за другим активным экипажем.",
        "legacy_driver_or_vehicle_already_assigned": "Автомобиль уже закреплён за другим водителем в автопарке.",
        "vehicle_swap_idempotency_key_required": "Для обмена автомобилей нужен заголовок Idempotency-Key.",
        "vehicle_swap_idempotency_key_invalid": "Idempotency-Key обмена автомобилей должен быть UUID.",
        "vehicle_swap_idempotency_key_reused": "Этот Idempotency-Key уже использован для другого обмена автомобилей.",
        "vehicle_already_assigned_to_crew": "Этот автомобиль уже назначен выбранному экипажу.",
        "vehicle_multiple_crews": "Автомобиль используется в нескольких экипажах и недоступен для быстрого обмена.",
        "vehicle_capacity_too_small": "В выбранном автомобиле недостаточно мест для этого экипажа.",
        "source_vehicle_capacity_too_small": "В текущем автомобиле недостаточно мест для второго экипажа.",
        "unsupported_support_field": "Запрос содержит неподдерживаемое поле.",
    },
    "en": {
        "invalid_input": "Check the submitted fields.",
        "idempotency_key_required": "The Idempotency-Key header is required to create a project.",
        "idempotency_key_invalid": "Idempotency-Key must be a UUID.",
        "idempotency_key_reused": "This Idempotency-Key was already used with different project data.",
        "project_name_required": "Enter the project name.",
        "project_name_already_exists": "A project with this name already exists.",
        "project_patch_empty": "Provide at least one project change.",
        "country_code_must_be_iso_alpha_2": "Country code must contain two Latin letters.",
        "period_end_must_not_be_before_start": "The end date cannot be before the start date.",
        "project_capacity_below_permanent_roster": "Capacity cannot be lower than the project's permanent roster.",
        "crew_idempotency_key_required": "The Idempotency-Key header is required to create a crew.",
        "crew_idempotency_key_invalid": "The crew Idempotency-Key must be a UUID.",
        "crew_idempotency_key_reused": "This Idempotency-Key was already used with different crew data.",
        "crew_name_required": "Enter the crew name.",
        "crew_patch_empty": "Provide at least one crew change.",
        "crew_not_available": "The crew is not available for changes.",
        "shift_idempotency_key_required": "The Idempotency-Key header is required to change shifts.",
        "shift_idempotency_key_invalid": "The shift Idempotency-Key must be a UUID.",
        "shift_idempotency_key_reused": "This Idempotency-Key was already used with different shift data.",
        "passenger_idempotency_key_required": "The Idempotency-Key header is required to change the passenger roster.",
        "passenger_idempotency_key_invalid": "The passenger Idempotency-Key must be a UUID.",
        "passenger_idempotency_key_reused": "This Idempotency-Key was already used with different passenger data.",
        "passenger_scope_invalid": "Choose all future crew days or selected dates.",
        "passenger_effective_on_required": "Enter the effective date for the permanent roster change.",
        "passenger_effective_on_not_allowed": "An effective date is not used with selected dates.",
        "passenger_work_dates_required": "Select at least one published crew day.",
        "passenger_work_dates_not_allowed": "Individual work dates are not used for the permanent roster.",
        "driver_replacement_idempotency_key_required": "The Idempotency-Key header is required for a permanent driver replacement.",
        "driver_replacement_idempotency_key_invalid": "The driver replacement Idempotency-Key must be a UUID.",
        "driver_replacement_idempotency_key_reused": "This Idempotency-Key was already used with different driver replacement data.",
        "driver_absence_idempotency_key_required": "The Idempotency-Key header is required to change driver absence.",
        "driver_absence_idempotency_key_invalid": "The driver-absence Idempotency-Key must be a UUID.",
        "driver_absence_idempotency_key_reused": "This Idempotency-Key was already used with different driver-absence dates.",
        "driver_substitution_idempotency_key_required": "The Idempotency-Key header is required for a temporary driver substitution.",
        "driver_substitution_idempotency_key_invalid": "The driver-substitution Idempotency-Key must be a UUID.",
        "driver_substitution_idempotency_key_reused": "This Idempotency-Key was already used with different substitute-driver data.",
        "driver_absence_missing": "The primary driver is not marked absent on one of the selected dates.",
        "driver_substitution_missing": "There is no active substitute driver on the selected dates.",
        "substitution_date_in_past": "A substitute driver cannot be assigned to a past crew day.",
        "substitution_requires_driver_absence": "A substitute can be assigned only to published days without the primary driver.",
        "substitute_driver_unavailable": "The selected substitute driver is busy or unavailable on one of the dates.",
        "replacement_driver_not_in_crew": "The new driver must be selected from this crew's passengers.",
        "replacement_driver_shift_conflict": "The new driver has an overlapping shift on one of the crew's future dates.",
        "crew_shift_missing": "No published crew shift exists on one of the selected dates.",
        "worker_drives_other_crew": "The worker is a driver of another crew on one of the selected dates.",
        "worker_day_off": "The worker has a day off on one of the selected dates.",
        "worker_absent_from_crew": "The worker is marked absent from the crew on one of the selected dates.",
        "worker_is_crew_driver": "The crew driver cannot also be added as its passenger.",
        "work_dates_required": "Select at least one calendar day.",
        "shift_time_required": "Enter the shift start and end times.",
        "break_minutes_invalid": "Check the break duration.",
        "crew_resource_missing": "The crew has no driver and vehicle on one of the selected days.",
        "crew_capacity_exceeded": "Vehicle capacity is exceeded on one of the selected days.",
        "driver_shift_conflict": "The driver has an overlapping shift in another crew.",
        "driver_licence_not_confirmed": "The selected worker has no confirmed driving licence.",
        "vehicle_not_available": "The selected vehicle is unavailable.",
        "driver_project_vehicle_locked": "The driver already works in another project and must keep the assigned vehicle.",
        "driver_or_vehicle_already_assigned": "The driver or vehicle is already assigned to another active crew.",
        "legacy_driver_or_vehicle_already_assigned": "The vehicle is already assigned to another fleet driver.",
        "vehicle_swap_idempotency_key_required": "The Idempotency-Key header is required to swap vehicles.",
        "vehicle_swap_idempotency_key_invalid": "The vehicle-swap Idempotency-Key must be a UUID.",
        "vehicle_swap_idempotency_key_reused": "This Idempotency-Key was already used for another vehicle swap.",
        "vehicle_already_assigned_to_crew": "This vehicle is already assigned to the selected crew.",
        "vehicle_multiple_crews": "This vehicle is used by multiple crews and is unavailable for quick swap.",
        "vehicle_capacity_too_small": "The selected vehicle has too few seats for this crew.",
        "source_vehicle_capacity_too_small": "The current vehicle has too few seats for the other crew.",
        "unsupported_support_field": "The request contains an unsupported field.",
    },
    "pl": {
        "invalid_input": "Sprawdź wypełnione pola.",
        "idempotency_key_required": "Do utworzenia projektu wymagany jest nagłówek Idempotency-Key.",
        "idempotency_key_invalid": "Idempotency-Key musi być identyfikatorem UUID.",
        "idempotency_key_reused": "Ten Idempotency-Key został już użyty z innymi danymi projektu.",
        "project_name_required": "Podaj nazwę projektu.",
        "project_name_already_exists": "Projekt o tej nazwie już istnieje.",
        "project_patch_empty": "Podaj co najmniej jedną zmianę projektu.",
        "country_code_must_be_iso_alpha_2": "Kod kraju musi składać się z dwóch liter łacińskich.",
        "period_end_must_not_be_before_start": "Data zakończenia nie może być wcześniejsza niż data rozpoczęcia.",
        "project_capacity_below_permanent_roster": "Liczba miejsc nie może być mniejsza niż stały skład projektu.",
        "crew_idempotency_key_required": "Do utworzenia ekipy wymagany jest nagłówek Idempotency-Key.",
        "crew_idempotency_key_invalid": "Idempotency-Key ekipy musi być identyfikatorem UUID.",
        "crew_idempotency_key_reused": "Ten Idempotency-Key został już użyty z innymi danymi ekipy.",
        "crew_name_required": "Podaj nazwę ekipy.",
        "crew_patch_empty": "Podaj co najmniej jedną zmianę ekipy.",
        "crew_not_available": "Ekipa nie jest dostępna do edycji.",
        "shift_idempotency_key_required": "Do zmiany zmian wymagany jest nagłówek Idempotency-Key.",
        "shift_idempotency_key_invalid": "Idempotency-Key zmian musi być identyfikatorem UUID.",
        "shift_idempotency_key_reused": "Ten Idempotency-Key został już użyty z innymi danymi zmian.",
        "passenger_idempotency_key_required": "Do zmiany listy pasażerów wymagany jest nagłówek Idempotency-Key.",
        "passenger_idempotency_key_invalid": "Idempotency-Key zmiany pasażerów musi być identyfikatorem UUID.",
        "passenger_idempotency_key_reused": "Ten Idempotency-Key został już użyty z innymi danymi pasażera.",
        "passenger_scope_invalid": "Wybierz wszystkie przyszłe dni ekipy albo wybrane daty.",
        "passenger_effective_on_required": "Podaj datę rozpoczęcia zmiany stałego składu.",
        "passenger_effective_on_not_allowed": "Dla wybranych dat nie używa się osobnej daty rozpoczęcia.",
        "passenger_work_dates_required": "Wybierz co najmniej jeden opublikowany dzień ekipy.",
        "passenger_work_dates_not_allowed": "Dla stałego składu nie podaje się osobnych dat zmian.",
        "driver_replacement_idempotency_key_required": "Do stałej zmiany kierowcy wymagany jest nagłówek Idempotency-Key.",
        "driver_replacement_idempotency_key_invalid": "Idempotency-Key zmiany kierowcy musi być identyfikatorem UUID.",
        "driver_replacement_idempotency_key_reused": "Ten Idempotency-Key został już użyty z innymi danymi zmiany kierowcy.",
        "driver_absence_idempotency_key_required": "Do zmiany nieobecności kierowcy wymagany jest nagłówek Idempotency-Key.",
        "driver_absence_idempotency_key_invalid": "Idempotency-Key nieobecności kierowcy musi być identyfikatorem UUID.",
        "driver_absence_idempotency_key_reused": "Ten Idempotency-Key został już użyty z innymi datami nieobecności kierowcy.",
        "driver_substitution_idempotency_key_required": "Do czasowego zastępstwa kierowcy wymagany jest nagłówek Idempotency-Key.",
        "driver_substitution_idempotency_key_invalid": "Idempotency-Key zastępstwa kierowcy musi być identyfikatorem UUID.",
        "driver_substitution_idempotency_key_reused": "Ten Idempotency-Key został już użyty z innymi danymi kierowcy zastępczego.",
        "driver_absence_missing": "W jednej z wybranych dat główny kierowca nie jest oznaczony jako nieobecny.",
        "driver_substitution_missing": "W wybranych datach nie ma aktywnego kierowcy zastępczego.",
        "substitution_date_in_past": "Nie można wyznaczyć kierowcy zastępczego na miniony dzień.",
        "substitution_requires_driver_absence": "Zastępstwo można wyznaczyć tylko w opublikowane dni bez głównego kierowcy.",
        "substitute_driver_unavailable": "Wybrany kierowca zastępczy jest zajęty lub niedostępny w jednej z dat.",
        "replacement_driver_not_in_crew": "Nowego kierowcę można wybrać tylko spośród pasażerów tej ekipy.",
        "replacement_driver_shift_conflict": "Nowy kierowca ma nakładającą się zmianę w jednym z przyszłych dni ekipy.",
        "crew_shift_missing": "W jednej z wybranych dat nie ma opublikowanej zmiany ekipy.",
        "worker_drives_other_crew": "W jednej z wybranych dat pracownik jest kierowcą innej ekipy.",
        "worker_day_off": "W jednej z wybranych dat pracownik ma dzień wolny.",
        "worker_absent_from_crew": "W jednej z wybranych dat pracownik jest oznaczony jako nieobecny w ekipie.",
        "worker_is_crew_driver": "Kierowcy tej ekipy nie można jednocześnie dodać jako pasażera.",
        "work_dates_required": "Wybierz co najmniej jeden dzień kalendarzowy.",
        "shift_time_required": "Podaj godzinę rozpoczęcia i zakończenia zmiany.",
        "break_minutes_invalid": "Sprawdź długość przerwy.",
        "crew_resource_missing": "W jednym z wybranych dni ekipa nie ma kierowcy i samochodu.",
        "crew_capacity_exceeded": "W jednym z wybranych dni przekroczono liczbę miejsc w samochodzie.",
        "driver_shift_conflict": "Kierowca ma nakładającą się zmianę w innej ekipie.",
        "driver_licence_not_confirmed": "Wybrany pracownik nie ma potwierdzonego prawa jazdy.",
        "vehicle_not_available": "Wybrany samochód jest niedostępny.",
        "driver_project_vehicle_locked": "Kierowca pracuje już w innym projekcie i musi zachować przypisany samochód.",
        "driver_or_vehicle_already_assigned": "Kierowca lub samochód jest już przypisany do innej aktywnej ekipy.",
        "legacy_driver_or_vehicle_already_assigned": "Samochód jest już przypisany do innego kierowcy we flocie.",
        "vehicle_swap_idempotency_key_required": "Do wymiany pojazdów wymagany jest nagłówek Idempotency-Key.",
        "vehicle_swap_idempotency_key_invalid": "Idempotency-Key wymiany pojazdów musi być identyfikatorem UUID.",
        "vehicle_swap_idempotency_key_reused": "Ten Idempotency-Key został już użyty do innej wymiany pojazdów.",
        "vehicle_already_assigned_to_crew": "Ten pojazd jest już przypisany do wybranej ekipy.",
        "vehicle_multiple_crews": "Pojazd jest używany przez kilka ekip i nie jest dostępny do szybkiej wymiany.",
        "vehicle_capacity_too_small": "Wybrany pojazd ma za mało miejsc dla tej ekipy.",
        "source_vehicle_capacity_too_small": "Obecny pojazd ma za mało miejsc dla drugiej ekipy.",
        "unsupported_support_field": "Żądanie zawiera nieobsługiwane pole.",
    },
    "uk": {
        "invalid_input": "Перевірте заповнені поля.",
        "idempotency_key_required": "Для створення проєкту потрібен заголовок Idempotency-Key.",
        "idempotency_key_invalid": "Idempotency-Key має бути UUID.",
        "idempotency_key_reused": "Цей Idempotency-Key уже використано з іншими даними проєкту.",
        "project_name_required": "Укажіть назву проєкту.",
        "project_name_already_exists": "Проєкт із такою назвою вже існує.",
        "project_patch_empty": "Укажіть хоча б одну зміну проєкту.",
        "country_code_must_be_iso_alpha_2": "Код країни має складатися з двох латинських літер.",
        "period_end_must_not_be_before_start": "Дата завершення не може бути раніше дати початку.",
        "project_capacity_below_permanent_roster": "Кількість місць не може бути меншою за постійний склад проєкту.",
        "crew_idempotency_key_required": "Для створення екіпажу потрібен заголовок Idempotency-Key.",
        "crew_idempotency_key_invalid": "Idempotency-Key екіпажу має бути UUID.",
        "crew_idempotency_key_reused": "Цей Idempotency-Key уже використано з іншими даними екіпажу.",
        "crew_name_required": "Укажіть назву екіпажу.",
        "crew_patch_empty": "Укажіть хоча б одну зміну екіпажу.",
        "crew_not_available": "Екіпаж недоступний для змін.",
        "shift_idempotency_key_required": "Для зміни змін потрібен заголовок Idempotency-Key.",
        "shift_idempotency_key_invalid": "Idempotency-Key змін має бути UUID.",
        "shift_idempotency_key_reused": "Цей Idempotency-Key уже використано з іншими даними змін.",
        "passenger_idempotency_key_required": "Для зміни складу пасажирів потрібен заголовок Idempotency-Key.",
        "passenger_idempotency_key_invalid": "Idempotency-Key зміни пасажирів має бути UUID.",
        "passenger_idempotency_key_reused": "Цей Idempotency-Key уже використано з іншими даними пасажира.",
        "passenger_scope_invalid": "Виберіть усі майбутні дні екіпажу або вибрані дати.",
        "passenger_effective_on_required": "Укажіть дату початку зміни постійного складу.",
        "passenger_effective_on_not_allowed": "Для вибраних дат окрема дата початку не використовується.",
        "passenger_work_dates_required": "Виберіть хоча б один опублікований день екіпажу.",
        "passenger_work_dates_not_allowed": "Для постійного складу окремі дати змін не передаються.",
        "driver_replacement_idempotency_key_required": "Для постійної заміни водія потрібен заголовок Idempotency-Key.",
        "driver_replacement_idempotency_key_invalid": "Idempotency-Key заміни водія має бути UUID.",
        "driver_replacement_idempotency_key_reused": "Цей Idempotency-Key уже використано з іншими даними заміни водія.",
        "driver_absence_idempotency_key_required": "Для зміни відсутності водія потрібен заголовок Idempotency-Key.",
        "driver_absence_idempotency_key_invalid": "Idempotency-Key відсутності водія має бути UUID.",
        "driver_absence_idempotency_key_reused": "Цей Idempotency-Key уже використано з іншими датами відсутності водія.",
        "driver_substitution_idempotency_key_required": "Для тимчасової підміни водія потрібен заголовок Idempotency-Key.",
        "driver_substitution_idempotency_key_invalid": "Idempotency-Key підміни водія має бути UUID.",
        "driver_substitution_idempotency_key_reused": "Цей Idempotency-Key уже використано з іншими даними підмінного водія.",
        "driver_absence_missing": "На одну з вибраних дат основного водія не позначено відсутнім.",
        "driver_substitution_missing": "На вибрані дати немає активного підмінного водія.",
        "substitution_date_in_past": "Підмінного водія не можна призначити на минулий день.",
        "substitution_requires_driver_absence": "Підміну можна призначити лише на опубліковані дні без основного водія.",
        "substitute_driver_unavailable": "Вибраний підмінний водій зайнятий або недоступний на одну з дат.",
        "replacement_driver_not_in_crew": "Нового водія можна вибрати лише з пасажирів цього екіпажу.",
        "replacement_driver_shift_conflict": "Новий водій має зміну, що перетинається, на одну з майбутніх дат екіпажу.",
        "crew_shift_missing": "На одну з вибраних дат немає опублікованої зміни екіпажу.",
        "worker_drives_other_crew": "На одну з вибраних дат працівник є водієм іншого екіпажу.",
        "worker_day_off": "На одну з вибраних дат у працівника вихідний.",
        "worker_absent_from_crew": "На одну з вибраних дат працівника позначено відсутнім в екіпажі.",
        "worker_is_crew_driver": "Водія цього екіпажу не можна одночасно додати пасажиром.",
        "work_dates_required": "Виберіть хоча б один календарний день.",
        "shift_time_required": "Укажіть час початку та завершення зміни.",
        "break_minutes_invalid": "Перевірте тривалість перерви.",
        "crew_resource_missing": "На один із вибраних днів екіпаж не має водія та автомобіля.",
        "crew_capacity_exceeded": "На один із вибраних днів перевищено кількість місць в автомобілі.",
        "driver_shift_conflict": "Водій має зміну, що перетинається, в іншому екіпажі.",
        "driver_licence_not_confirmed": "Вибраний працівник не має підтвердженого водійського посвідчення.",
        "vehicle_not_available": "Вибраний автомобіль недоступний.",
        "driver_project_vehicle_locked": "Водій уже працює в іншому проєкті й має зберегти закріплений автомобіль.",
        "driver_or_vehicle_already_assigned": "Водія або автомобіль уже закріплено за іншим активним екіпажем.",
        "legacy_driver_or_vehicle_already_assigned": "Автомобіль уже закріплено за іншим водієм в автопарку.",
        "vehicle_swap_idempotency_key_required": "Для обміну автомобілів потрібен заголовок Idempotency-Key.",
        "vehicle_swap_idempotency_key_invalid": "Idempotency-Key обміну автомобілів має бути UUID.",
        "vehicle_swap_idempotency_key_reused": "Цей Idempotency-Key уже використано для іншого обміну автомобілів.",
        "vehicle_already_assigned_to_crew": "Цей автомобіль уже призначений вибраному екіпажу.",
        "vehicle_multiple_crews": "Автомобіль використовується в кількох екіпажах і недоступний для швидкого обміну.",
        "vehicle_capacity_too_small": "У вибраному автомобілі недостатньо місць для цього екіпажу.",
        "source_vehicle_capacity_too_small": "У поточному автомобілі недостатньо місць для другого екіпажу.",
        "unsupported_support_field": "Запит містить непідтримуване поле.",
    },
}


def _project_first_language(request):
    language = (request.headers.get("Accept-Language") or "ru").split(",", 1)[0]
    language = language.split("-", 1)[0].strip().lower()
    return language if language in _PROJECT_FIRST_ERROR_MESSAGES else "ru"


def _project_first_error_message(request, code):
    language = _project_first_language(request)
    return _PROJECT_FIRST_ERROR_MESSAGES[language].get(
        code,
        _PROJECT_FIRST_ERROR_MESSAGES[language]["invalid_input"],
    )


def _project_first_error_codes(detail):
    if isinstance(detail, dict):
        return {str(key): _project_first_error_codes(value) for key, value in detail.items()}
    if isinstance(detail, (list, tuple)):
        return [code for value in detail for code in _project_first_error_codes(value)]
    raw = str(detail)
    return [raw if raw and all(char.islower() or char.isdigit() or char == "_" for char in raw) else getattr(detail, "code", "invalid_input")]


def _project_first_first_code(detail):
    if isinstance(detail, dict):
        if "code" in detail and not isinstance(detail["code"], (dict, list, tuple)):
            return str(detail["code"])
        for value in detail.values():
            code = _project_first_first_code(value)
            if code:
                return code
        return ""
    if isinstance(detail, (list, tuple)):
        for value in detail:
            code = _project_first_first_code(value)
            if code:
                return code
        return ""
    raw = str(detail)
    return raw if raw and all(char.islower() or char.isdigit() or char == "_" for char in raw) else "invalid_input"


def _project_first_write_error(request, *, code, field_errors=None, details=None):
    payload = {
        "code": code,
        "message": _project_first_error_message(request, code),
        "field_errors": field_errors or {},
    }
    if details:
        payload.update(details)
    return Response(payload, status=status.HTTP_400_BAD_REQUEST)


def _project_first_serializer_error(request, detail):
    field_errors = _project_first_error_codes(detail)
    code = _project_first_first_code(detail) or "invalid_input"
    return _project_first_write_error(
        request,
        code=code,
        field_errors=field_errors,
    )


def _project_first_service_error(request, detail):
    if isinstance(detail, dict) and "code" in detail:
        code = str(detail["code"])
        field_errors = _project_first_error_codes(detail.get("field_errors", {}))
        extra = {}
        for key, value in detail.items():
            if key in {"code", "message", "field_errors"}:
                continue
            if key == "minimum":
                extra[key] = int(str(value))
            else:
                extra[key] = str(value)
        return _project_first_write_error(
            request,
            code=code,
            field_errors=field_errors,
            details=extra,
        )
    return _project_first_serializer_error(request, detail)


def _project_update_payload(project):
    return {
        "name": project.internal_name,
        "country_code": project.worksite.country_code,
        "city": project.worksite.city,
        "postal_code": project.worksite.postal_code,
        "street": project.worksite.street,
        "building": project.worksite.building,
        "worker_capacity": project.worker_capacity,
        "starts_on": project.starts_on,
        "ends_on": project.ends_on,
        "contact_name": project.contact_name,
        "contact_phone": project.contact_phone,
        "contact_email": project.contact_email,
        "instructions": project.instructions,
    }


class SupportBootstrapAPIView(SupportFeatureAPIView):
    """Safe entry point for a future Support-capable mobile client.

    Package 0 exposes no organization, employee, vacancy, chat, payment, or
    personal data.  It only proves that the new versioned API can be deployed
    independently while the feature flag remains disabled.
    """

    def get(self, request):
        access = support_access_snapshot_for(request.user)
        memberships = list(
            OrganizationMembership.objects.filter(
                user=request.user,
                state=OrganizationMembership.STATE_ACTIVE,
            )
            .select_related("organization")
            .order_by("organization__display_name", "id")
        )
        mode = "staff" if memberships else ("worker" if access["state"] == "active" else "unconfigured")
        connections = list(
            SupportConnection.objects.filter(candidate=request.user, is_archived=False)
            .select_related("organization", "vacancy")
            .order_by("-updated_at", "-id")
        )

        return Response(
            {
                "feature_enabled": True,
                "mode": mode,
                "support_access": access,
                "staff_memberships": [
                    _organization_payload(membership.organization, membership)
                    for membership in memberships
                ],
                "connections": [_connection_payload(connection) for connection in connections],
                "available_actions": (
                    ["open_manager_chat"]
                    if access["state"] == "active"
                    and any(
                        connection.stage
                        in {
                            SupportConnection.STAGE_AWAITING_SUPPORT,
                            SupportConnection.STAGE_MANAGER,
                        }
                        for connection in connections
                    )
                    else []
                ),
            },
            status=status.HTTP_200_OK,
        )


class SupportStaffWorkspaceSummaryAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    """Small mobile-safe summary. Counts never bypass worker access scopes."""

    def get(self, request, organization_public_id):
        organization, membership = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )


        allowed_connections = worker_connection_queryset_for(
            user=request.user,
            organization=organization,
            queryset=SupportConnection.objects.filter(is_archived=False),
        )
        may_review_pipeline = has_permission(
            user=request.user,
            organization=organization,
            permission_code=PIPELINE_REVIEW,
        )
        may_view_workers = has_permission(
            user=request.user,
            organization=organization,
            permission_code=WORKER_VIEW,
        )
        may_decide_requests = has_permission(
            user=request.user,
            organization=organization,
            permission_code=REQUEST_DECIDE,
        )
        may_manage_chats = has_permission(
            user=request.user,
            organization=organization,
            permission_code=CHAT_MANAGE,
        )
        may_manage_tasks = has_permission(
            user=request.user,
            organization=organization,
            permission_code=TASK_MANAGE,
        )
        may_view_time = has_permission(
            user=request.user,
            organization=organization,
            permission_code=TIME_VIEW,
        )
        may_review_time = has_permission(
            user=request.user,
            organization=organization,
            permission_code=TIME_REVIEW,
        )
        may_edit_time = has_permission(
            user=request.user,
            organization=organization,
            permission_code=TIME_EDIT,
        )
        may_manage_transport = has_permission(
            user=request.user,
            organization=organization,
            permission_code=TRANSPORT_MANAGE,
        )
        may_manage_schedule = has_permission(
            user=request.user,
            organization=organization,
            permission_code=SCHEDULE_MANAGE,
        )
        may_manage_housing = has_permission(
            user=request.user,
            organization=organization,
            permission_code=HOUSING_MANAGE,
        )
        today = timezone.localdate()
        worker_count = allowed_connections.count() if may_view_workers else 0
        assigned_connection_ids = set()
        if may_view_workers:
            assigned_connection_ids.update(
                ProjectCrewResourceAssignment.objects.filter(
                    driver_connection__in=allowed_connections,
                    crew__state=ProjectCrew.STATE_ACTIVE,
                    crew__project__is_active=True,
                )
                .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=today))
                .values_list("driver_connection_id", flat=True)
            )
            assigned_connection_ids.update(
                ProjectCrewPassenger.objects.filter(
                    connection__in=allowed_connections,
                    crew__state=ProjectCrew.STATE_ACTIVE,
                    crew__project__is_active=True,
                )
                .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=today))
                .values_list("connection_id", flat=True)
            )
            assigned_connection_ids.update(
                ProjectCrewShiftMember.objects.filter(
                    connection__in=allowed_connections,
                    shift__state=ProjectCrewShift.STATE_PUBLISHED,
                    shift__work_date__gte=today,
                    shift__crew__state=ProjectCrew.STATE_ACTIVE,
                    shift__crew__project__is_active=True,
                ).values_list("connection_id", flat=True)
            )

        active_vehicles = Vehicle.objects.filter(
            organization=organization,
            is_active=True,
        )
        fleet_total = active_vehicles.count() if may_manage_transport else 0
        reserved_vehicle_ids = set()
        if may_manage_transport:
            reserved_vehicle_ids.update(
                ProjectCrewResourceAssignment.objects.filter(
                    crew__organization=organization,
                    crew__state=ProjectCrew.STATE_ACTIVE,
                    crew__project__is_active=True,
                )
                .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=today))
                .values_list("vehicle_id", flat=True)
            )
            reserved_vehicle_ids.update(
                DriverVehicleAssignment.objects.filter(
                    organization=organization,
                    state=DriverVehicleAssignment.STATE_PUBLISHED,
                )
                .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=today))
                .values_list("vehicle_id", flat=True)
            )

        active_housing_places = HousingPlace.objects.filter(
            room__site__organization=organization,
            room__site__is_active=True,
            room__is_active=True,
            is_active=True,
        )
        housing_places_total = (
            active_housing_places.count() if may_manage_housing else 0
        )
        housing_places_free = 0
        if may_manage_housing:
            local_now = timezone.localtime(timezone.now())
            day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            occupied_place_ids = HousingAssignment.objects.filter(
                organization=organization,
                state=HousingAssignment.STATE_PUBLISHED,
                check_in_at__lt=day_end,
            ).filter(
                Q(check_out_at__isnull=True) | Q(check_out_at__gt=day_start)
            ).values_list("place_id", flat=True)
            housing_places_free = active_housing_places.exclude(
                id__in=occupied_place_ids
            ).count()
        candidate_queue_counts = (
            _candidate_queue_counts(organization)
            if may_review_pipeline
            else {
                "pending_applications": 0,
                "onboarding_candidates": 0,
            }
        )
        return Response(
            {
                "organization": _organization_payload(organization, membership),
                "permissions": {
                    "pipeline_review": may_review_pipeline,
                    "worker_view": may_view_workers,
                    "request_decide": may_decide_requests,
                    "chat_manage": may_manage_chats,
                    "task_manage": may_manage_tasks,
                    "announcement_manage": has_permission(
                        user=request.user,
                        organization=organization,
                        permission_code=ANNOUNCEMENT_MANAGE,
                    ),
                    "time_view": may_view_time,
                    "time_review": may_review_time,
                    "time_edit": may_edit_time,
                    "transport_manage": may_manage_transport,
                    "schedule_manage": may_manage_schedule,
                    "housing_manage": may_manage_housing,
                },
                "counts": {
                    "workers": worker_count,
                    "workers_unassigned": max(
                        0,
                        worker_count - len(assigned_connection_ids),
                    ),
                    "fleet_total": fleet_total,
                    "fleet_in_reserve": (
                        active_vehicles.exclude(id__in=reserved_vehicle_ids).count()
                        if may_manage_transport
                        else 0
                    ),
                    "housing_places_total": housing_places_total,
                    "housing_places_free": housing_places_free,
                    **candidate_queue_counts,
                    "open_requests": (
                        WorkerRequest.objects.filter(
                            organization=organization,
                            connection__in=allowed_connections,
                            status__in=(
                                WorkerRequest.STATUS_SUBMITTED,
                                WorkerRequest.STATUS_NEEDS_CLARIFICATION,
                            ),
                        ).count()
                        if may_decide_requests
                        else 0
                    ),
                    "tasks_to_review": (
                        TaskAssignment.objects.filter(
                            task__organization=organization,
                            connection__in=allowed_connections,
                            task__state=WorkerTask.STATE_PUBLISHED,
                            status=TaskAssignment.STATUS_COMPLETED_BY_WORKER,
                        ).count()
                        if may_manage_tasks
                        else 0
                    ),
                    "time_entries_to_review": (
                        WorkTimeEntry.objects.filter(
                            organization=organization,
                            connection__in=allowed_connections,
                            status=WorkTimeEntry.STATUS_SUBMITTED,
                        ).count()
                        if may_review_time
                        else 0
                    ),
                },
            }
        )


class OrganizationProjectFirstProjectListAPIView(
    SupportFeatureAPIView,
    ProjectFirstOrganizationAccessMixin,
):
    """List canonical projects without exposing legacy operation records."""

    def get(self, request, organization_public_id):
        organization, membership = self.get_project_first_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        return Response(
            {
                "organization": _organization_payload(organization, membership),
                "projects": project_first_project_list(organization=organization),
                "creation_options": project_first_creation_options(
                    organization=organization
                ),
            }
        )

    def post(self, request, organization_public_id):
        organization, _ = self.get_project_first_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        raw_request_id = (request.headers.get("Idempotency-Key") or "").strip()
        if not raw_request_id:
            return _project_first_write_error(
                request,
                code="idempotency_key_required",
                field_errors={"Idempotency-Key": ["idempotency_key_required"]},
            )
        try:
            request_id = uuid.UUID(raw_request_id)
        except (TypeError, ValueError, AttributeError):
            return _project_first_write_error(
                request,
                code="idempotency_key_invalid",
                field_errors={"Idempotency-Key": ["idempotency_key_invalid"]},
            )
        serializer = ProjectCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return _project_first_serializer_error(request, serializer.errors)
        try:
            project = create_project(
                actor=request.user,
                organization=organization,
                request_id=request_id,
                **serializer.validated_data,
            )
        except ValidationError as error:
            return _project_first_service_error(request, error.detail)
        return Response(
            {"project": project_first_project_payload(project)},
            status=status.HTTP_201_CREATED,
        )


class OrganizationProjectFirstProjectDetailAPIView(
    SupportFeatureAPIView,
    ProjectFirstOrganizationAccessMixin,
):
    """Patch or archive one canonical project without touching its shifts on edit."""

    def _project(self, *, organization, project_public_id):
        return get_object_or_404(
            WorkProject.objects.select_related("worksite"),
            organization=organization,
            public_id=project_public_id,
        )

    def patch(self, request, organization_public_id, project_public_id):
        organization, _ = self.get_project_first_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        project = self._project(
            organization=organization,
            project_public_id=project_public_id,
        )
        if not project.is_active:
            raise NotFound("project_first_project_not_found")
        patch_serializer = ProjectUpdateSerializer(data=request.data)
        if not patch_serializer.is_valid():
            return _project_first_serializer_error(request, patch_serializer.errors)
        merged = _project_update_payload(project)
        merged.update(patch_serializer.validated_data)
        serializer = ProjectCreateSerializer(data=merged)
        if not serializer.is_valid():
            return _project_first_serializer_error(request, serializer.errors)
        try:
            project = update_project(
                actor=request.user,
                project=project,
                **serializer.validated_data,
            )
        except ValidationError as error:
            return _project_first_service_error(request, error.detail)
        return Response({"project": project_first_project_payload(project)})

    def delete(self, request, organization_public_id, project_public_id):
        organization, _ = self.get_project_first_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        serializer = EmptyStrictInputSerializer(data=request.data)
        if not serializer.is_valid():
            return _project_first_serializer_error(request, serializer.errors)
        project = self._project(
            organization=organization,
            project_public_id=project_public_id,
        )
        try:
            project = archive_project(actor=request.user, project=project)
        except ValidationError as error:
            return _project_first_service_error(request, error.detail)
        return Response(
            {
                "project": {
                    "id": str(project.public_id),
                    "is_active": project.is_active,
                },
                "deleted": True,
            }
        )


class OrganizationProjectFirstCrewListAPIView(
    SupportFeatureAPIView,
    ProjectFirstOrganizationAccessMixin,
):
    """Create a canonical crew with its initial driver and vehicle."""

    def post(self, request, organization_public_id, project_public_id):
        organization, _ = self.get_project_first_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        project = get_object_or_404(
            WorkProject.objects.select_related("worksite"),
            organization=organization,
            public_id=project_public_id,
            is_active=True,
        )
        raw_request_id = (request.headers.get("Idempotency-Key") or "").strip()
        if not raw_request_id:
            return _project_first_write_error(
                request,
                code="crew_idempotency_key_required",
                field_errors={
                    "Idempotency-Key": ["crew_idempotency_key_required"]
                },
            )
        try:
            request_id = uuid.UUID(raw_request_id)
        except (TypeError, ValueError, AttributeError):
            return _project_first_write_error(
                request,
                code="crew_idempotency_key_invalid",
                field_errors={
                    "Idempotency-Key": ["crew_idempotency_key_invalid"]
                },
            )
        serializer = ProjectCrewCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return _project_first_serializer_error(request, serializer.errors)
        data = serializer.validated_data
        driver = get_object_or_404(
            SupportConnection,
            organization=organization,
            public_id=data["driver_connection_id"],
            is_archived=False,
        )
        vehicle = get_object_or_404(
            Vehicle,
            organization=organization,
            public_id=data["vehicle_id"],
            is_active=True,
        )
        try:
            crew = create_project_crew(
                actor=request.user,
                organization=organization,
                project=project,
                driver_connection=driver,
                vehicle=vehicle,
                internal_name=data["internal_name"],
                starts_on=data["starts_on"],
                request_id=request_id,
            )
        except ValidationError as error:
            return _project_first_service_error(request, error.detail)
        return Response(
            {"crew": project_first_crew_payload(crew)},
            status=status.HTTP_201_CREATED,
        )


class OrganizationProjectFirstCrewDetailAPIView(
    SupportFeatureAPIView,
    ProjectFirstOrganizationAccessMixin,
):
    """Rename or archive one canonical project crew."""

    def _crew(self, *, organization, crew_public_id):
        return get_object_or_404(
            ProjectCrew.objects.select_related("project", "organization"),
            organization=organization,
            public_id=crew_public_id,
        )

    def patch(self, request, organization_public_id, crew_public_id):
        organization, _ = self.get_project_first_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        crew = self._crew(
            organization=organization,
            crew_public_id=crew_public_id,
        )
        if crew.state != ProjectCrew.STATE_ACTIVE or not crew.project.is_active:
            raise NotFound("project_first_crew_not_found")
        serializer = ProjectCrewUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return _project_first_serializer_error(request, serializer.errors)
        try:
            crew = update_project_crew(
                actor=request.user,
                crew=crew,
                **serializer.validated_data,
            )
        except ValidationError as error:
            return _project_first_service_error(request, error.detail)
        return Response({"crew": project_first_crew_payload(crew)})

    def delete(self, request, organization_public_id, crew_public_id):
        organization, _ = self.get_project_first_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        serializer = EmptyStrictInputSerializer(data=request.data)
        if not serializer.is_valid():
            return _project_first_serializer_error(request, serializer.errors)
        crew = self._crew(
            organization=organization,
            crew_public_id=crew_public_id,
        )
        try:
            crew = archive_project_crew(actor=request.user, crew=crew)
        except ValidationError as error:
            return _project_first_service_error(request, error.detail)
        return Response(
            {
                "crew": project_first_crew_payload(crew),
                "deleted": True,
            }
        )


class ProjectFirstCrewShiftWriteMixin(ProjectFirstOrganizationAccessMixin):
    """Shared tenant lookup and retry protection for crew-day writes."""

    def get_active_crew(self, *, organization, crew_public_id):
        return get_object_or_404(
            ProjectCrew.objects.select_related("project", "organization"),
            organization=organization,
            public_id=crew_public_id,
            state=ProjectCrew.STATE_ACTIVE,
            project__is_active=True,
        )

    def get_request_id(self, request):
        raw_request_id = (request.headers.get("Idempotency-Key") or "").strip()
        if not raw_request_id:
            return None, _project_first_write_error(
                request,
                code="shift_idempotency_key_required",
                field_errors={
                    "Idempotency-Key": ["shift_idempotency_key_required"]
                },
            )
        try:
            return uuid.UUID(raw_request_id), None
        except (TypeError, ValueError, AttributeError):
            return None, _project_first_write_error(
                request,
                code="shift_idempotency_key_invalid",
                field_errors={
                    "Idempotency-Key": ["shift_idempotency_key_invalid"]
                },
            )

    def write_response(self, *, crew, work_dates):
        return Response(
            {
                "crew": project_first_crew_payload(crew),
                "affected_dates": [item.isoformat() for item in work_dates],
                "days": project_first_crew_days_payload(
                    crew=crew,
                    work_dates=work_dates,
                ),
            }
        )


class OrganizationProjectFirstCrewShiftReplaceAPIView(
    SupportFeatureAPIView,
    ProjectFirstCrewShiftWriteMixin,
):
    """Create or replace published crew shifts for selected calendar days."""

    def post(self, request, organization_public_id, crew_public_id):
        organization, _ = self.get_project_first_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        crew = self.get_active_crew(
            organization=organization,
            crew_public_id=crew_public_id,
        )
        request_id, error_response = self.get_request_id(request)
        if error_response is not None:
            return error_response
        serializer = ProjectCrewShiftReplaceSerializer(data=request.data)
        if not serializer.is_valid():
            return _project_first_serializer_error(request, serializer.errors)
        data = serializer.validated_data
        try:
            publish_project_crew_shifts(
                actor=request.user,
                crew=crew,
                work_dates=data["work_dates"],
                starts_at_time=data["starts_at_time"],
                ends_at_time=data["ends_at_time"],
                break_minutes=data["break_minutes"],
                request_id=request_id,
            )
        except ValidationError as error:
            return _project_first_service_error(request, error.detail)
        return self.write_response(crew=crew, work_dates=data["work_dates"])


class OrganizationProjectFirstCrewShiftReleaseAPIView(
    SupportFeatureAPIView,
    ProjectFirstCrewShiftWriteMixin,
):
    """Cancel selected crew days while retaining their audit history."""

    def post(self, request, organization_public_id, crew_public_id):
        organization, _ = self.get_project_first_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        crew = self.get_active_crew(
            organization=organization,
            crew_public_id=crew_public_id,
        )
        request_id, error_response = self.get_request_id(request)
        if error_response is not None:
            return error_response
        serializer = ProjectCrewShiftReleaseSerializer(data=request.data)
        if not serializer.is_valid():
            return _project_first_serializer_error(request, serializer.errors)
        work_dates = serializer.validated_data["work_dates"]
        try:
            release_project_crew_shifts(
                actor=request.user,
                crew=crew,
                work_dates=work_dates,
                request_id=request_id,
            )
        except ValidationError as error:
            return _project_first_service_error(request, error.detail)
        return self.write_response(crew=crew, work_dates=work_dates)


class ProjectFirstCrewPassengerWriteMixin(ProjectFirstCrewShiftWriteMixin):
    """Shared contract for project-first passenger roster mutations."""

    PUBLIC_SCOPE_MAP = {
        ProjectCrewPassengerWriteSerializer.SCOPE_ALL_FUTURE: PASSENGER_SCOPE_FUTURE,
        ProjectCrewPassengerWriteSerializer.SCOPE_SELECTED_DATES: PASSENGER_SCOPE_SELECTED,
    }

    def get_request_id(self, request):
        raw_request_id = (request.headers.get("Idempotency-Key") or "").strip()
        if not raw_request_id:
            return None, _project_first_write_error(
                request,
                code="passenger_idempotency_key_required",
                field_errors={
                    "Idempotency-Key": ["passenger_idempotency_key_required"]
                },
            )
        try:
            return uuid.UUID(raw_request_id), None
        except (TypeError, ValueError, AttributeError):
            return None, _project_first_write_error(
                request,
                code="passenger_idempotency_key_invalid",
                field_errors={
                    "Idempotency-Key": ["passenger_idempotency_key_invalid"]
                },
            )

    def get_connection(self, *, organization, connection_id):
        return get_object_or_404(
            SupportConnection.objects.select_related("candidate"),
            organization=organization,
            public_id=connection_id,
            is_archived=False,
        )

    def affected_dates(self, *, crew, data):
        if data["scope"] == ProjectCrewPassengerWriteSerializer.SCOPE_SELECTED_DATES:
            return data["work_dates"]
        return list(
            ProjectCrewShift.objects.filter(
                crew=crew,
                state=ProjectCrewShift.STATE_PUBLISHED,
                work_date__gte=data["effective_on"],
            )
            .order_by("work_date")
            .values_list("work_date", flat=True)
        )

    def passenger_response(self, *, crew, connection, data, work_dates):
        candidate = connection.candidate
        return Response(
            {
                "crew": project_first_crew_payload(crew),
                "passenger": {
                    "id": str(connection.public_id),
                    "display_name": (
                        candidate.get_full_name().strip()
                        or candidate.username
                        or candidate.email
                    ),
                    "stage": connection.stage,
                },
                "scope": data["scope"],
                "effective_on": (
                    data.get("effective_on").isoformat()
                    if data.get("effective_on")
                    else None
                ),
                "affected_dates": [item.isoformat() for item in work_dates],
                "days": project_first_crew_days_payload(
                    crew=crew,
                    work_dates=work_dates,
                ),
            }
        )


class OrganizationProjectFirstCrewPassengerApplyAPIView(
    SupportFeatureAPIView,
    ProjectFirstCrewPassengerWriteMixin,
):
    """Add one worker to selected crew days or the permanent future roster."""

    def post(self, request, organization_public_id, crew_public_id):
        organization, _ = self.get_project_first_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        crew = self.get_active_crew(
            organization=organization,
            crew_public_id=crew_public_id,
        )
        request_id, error_response = self.get_request_id(request)
        if error_response is not None:
            return error_response
        serializer = ProjectCrewPassengerWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return _project_first_serializer_error(request, serializer.errors)
        data = serializer.validated_data
        connection = self.get_connection(
            organization=organization,
            connection_id=data["connection_id"],
        )
        try:
            _, shifts = assign_project_crew_passenger(
                actor=request.user,
                crew=crew,
                connection=connection,
                scope=self.PUBLIC_SCOPE_MAP[data["scope"]],
                selected_dates=data.get("work_dates"),
                effective_on=data.get("effective_on"),
                request_id=request_id,
            )
        except ValidationError as error:
            return _project_first_service_error(request, error.detail)
        work_dates = [shift.work_date for shift in shifts]
        return self.passenger_response(
            crew=crew,
            connection=connection,
            data=data,
            work_dates=work_dates,
        )


class OrganizationProjectFirstCrewPassengerRemoveAPIView(
    SupportFeatureAPIView,
    ProjectFirstCrewPassengerWriteMixin,
):
    """Remove one worker from selected crew days or the future roster."""

    def post(self, request, organization_public_id, crew_public_id):
        organization, _ = self.get_project_first_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        crew = self.get_active_crew(
            organization=organization,
            crew_public_id=crew_public_id,
        )
        request_id, error_response = self.get_request_id(request)
        if error_response is not None:
            return error_response
        serializer = ProjectCrewPassengerWriteSerializer(data=request.data)
        if not serializer.is_valid():
            return _project_first_serializer_error(request, serializer.errors)
        data = serializer.validated_data
        connection = self.get_connection(
            organization=organization,
            connection_id=data["connection_id"],
        )
        work_dates = self.affected_dates(crew=crew, data=data)
        try:
            remove_project_crew_passenger(
                actor=request.user,
                crew=crew,
                connection=connection,
                scope=self.PUBLIC_SCOPE_MAP[data["scope"]],
                selected_dates=data.get("work_dates"),
                effective_on=data.get("effective_on"),
                request_id=request_id,
            )
        except ValidationError as error:
            return _project_first_service_error(request, error.detail)
        return self.passenger_response(
            crew=crew,
            connection=connection,
            data=data,
            work_dates=work_dates,
        )


class OrganizationProjectFirstCrewDriverReplaceAPIView(
    SupportFeatureAPIView,
    ProjectFirstCrewShiftWriteMixin,
):
    """Permanently transfer a crew's vehicle and future driver role."""

    def get_request_id(self, request):
        raw_request_id = (request.headers.get("Idempotency-Key") or "").strip()
        if not raw_request_id:
            return None, _project_first_write_error(
                request,
                code="driver_replacement_idempotency_key_required",
                field_errors={
                    "Idempotency-Key": [
                        "driver_replacement_idempotency_key_required"
                    ]
                },
            )
        try:
            return uuid.UUID(raw_request_id), None
        except (TypeError, ValueError, AttributeError):
            return None, _project_first_write_error(
                request,
                code="driver_replacement_idempotency_key_invalid",
                field_errors={
                    "Idempotency-Key": [
                        "driver_replacement_idempotency_key_invalid"
                    ]
                },
            )

    def post(self, request, organization_public_id, crew_public_id):
        organization, _ = self.get_project_first_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        crew = self.get_active_crew(
            organization=organization,
            crew_public_id=crew_public_id,
        )
        request_id, error_response = self.get_request_id(request)
        if error_response is not None:
            return error_response
        serializer = ProjectCrewDriverReplaceSerializer(data=request.data)
        if not serializer.is_valid():
            return _project_first_serializer_error(request, serializer.errors)
        data = serializer.validated_data
        new_driver = get_object_or_404(
            SupportConnection.objects.select_related("candidate"),
            organization=organization,
            public_id=data["new_driver_connection_id"],
            is_archived=False,
        )
        try:
            replacement = replace_project_crew_driver(
                actor=request.user,
                crew=crew,
                new_driver_connection=new_driver,
                effective_on=data["effective_on"],
                request_id=request_id,
            )
        except ValidationError as error:
            return _project_first_service_error(request, error.detail)
        actual_effective_on = replacement.starts_on
        affected_dates = list(
            ProjectCrewShift.objects.filter(
                crew=crew,
                state=ProjectCrewShift.STATE_PUBLISHED,
                work_date__gte=actual_effective_on,
            )
            .order_by("work_date")
            .values_list("work_date", flat=True)
        )
        crew_payload = project_first_crew_payload(crew)
        return Response(
            {
                "crew": crew_payload,
                "replacement": {
                    "id": str(replacement.public_id),
                    "effective_on": actual_effective_on.isoformat(),
                    "driver_id": str(new_driver.public_id),
                    "vehicle_id": str(replacement.vehicle.public_id),
                },
                "affected_dates": [item.isoformat() for item in affected_dates],
                "days": project_first_crew_days_payload(
                    crew=crew,
                    work_dates=affected_dates,
                ),
            }
        )


class OrganizationProjectFirstCrewVehicleSwapAPIView(
    SupportFeatureAPIView,
    ProjectFirstCrewShiftWriteMixin,
):
    """Preview and confirm an atomic vehicle replacement or reciprocal swap."""

    def get_request_id(self, request):
        raw_request_id = (request.headers.get("Idempotency-Key") or "").strip()
        if not raw_request_id:
            return None, _project_first_write_error(
                request,
                code="vehicle_swap_idempotency_key_required",
                field_errors={
                    "Idempotency-Key": ["vehicle_swap_idempotency_key_required"]
                },
            )
        try:
            return uuid.UUID(raw_request_id), None
        except (TypeError, ValueError, AttributeError):
            return None, _project_first_write_error(
                request,
                code="vehicle_swap_idempotency_key_invalid",
                field_errors={
                    "Idempotency-Key": ["vehicle_swap_idempotency_key_invalid"]
                },
            )

    def _option_payload(self, item):
        resource = item["resource"]
        return {
            "vehicle": {
                "id": str(item["vehicle"].public_id),
                "internal_name": item["vehicle"].internal_name,
                "registration_identifier": item["vehicle"].registration_identifier,
                "seat_capacity": item["vehicle"].seat_capacity,
            },
            "eligible": item["eligible"],
            "reason": item["reason"],
            "required_seats": item["required_seats"],
            "target_required_seats": item["target_required_seats"],
            "assignment": (
                {
                    "crew_id": str(resource.crew.public_id),
                    "crew_name": resource.crew.internal_name,
                    "project_id": str(resource.crew.project.public_id),
                    "project_name": resource.crew.project.worker_visible_name
                    or resource.crew.project.internal_name,
                    "driver_id": str(resource.driver_connection.public_id),
                    "driver_name": _user_display_name(
                        resource.driver_connection.candidate
                    ),
                    "driver_avatar_url": _user_identity_payload(
                        resource.driver_connection.candidate
                    )["avatar_url"],
                }
                if resource is not None
                else None
            ),
        }

    def get(self, request, organization_public_id, crew_public_id):
        organization, _ = self.get_project_first_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        crew = self.get_active_crew(
            organization=organization,
            crew_public_id=crew_public_id,
        )
        effective_on = parse_date(str(request.query_params.get("effective_on") or ""))
        if effective_on is None:
            effective_on = timezone.localdate()
        try:
            source, options = project_crew_vehicle_swap_options(
                actor=request.user,
                crew=crew,
                effective_on=effective_on,
            )
        except ValidationError as error:
            return _project_first_service_error(request, error.detail)
        return Response(
            {
                "crew": project_first_crew_payload(crew),
                "effective_on": effective_on.isoformat(),
                "source_vehicle_id": str(source.vehicle.public_id),
                "required_seats": next(
                    (item["required_seats"] for item in options), 1
                ),
                "results": [self._option_payload(item) for item in options],
            }
        )

    def post(self, request, organization_public_id, crew_public_id):
        organization, _ = self.get_project_first_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        crew = self.get_active_crew(
            organization=organization,
            crew_public_id=crew_public_id,
        )
        request_id, error_response = self.get_request_id(request)
        if error_response is not None:
            return error_response
        serializer = ProjectCrewVehicleSwapSerializer(data=request.data)
        if not serializer.is_valid():
            return _project_first_serializer_error(request, serializer.errors)
        data = serializer.validated_data
        target_vehicle = get_object_or_404(
            Vehicle,
            organization=organization,
            public_id=data["target_vehicle_id"],
        )
        try:
            details = swap_project_crew_vehicle(
                actor=request.user,
                crew=crew,
                target_vehicle=target_vehicle,
                effective_on=data["effective_on"],
                request_id=request_id,
            )
        except ValidationError as error:
            return _project_first_service_error(request, error.detail)

        source_driver = get_object_or_404(
            SupportConnection.objects.select_related(
                "candidate", "candidate__profile"
            ),
            organization=organization,
            public_id=details["source_driver_id"],
        )
        driver_targets = [(source_driver, crew)]
        if details.get("target_driver_id"):
            target_driver = get_object_or_404(
                SupportConnection.objects.select_related(
                    "candidate", "candidate__profile"
                ),
                organization=organization,
                public_id=details["target_driver_id"],
            )
            target_crew = ProjectCrew.objects.get(
                organization=organization,
                public_id=details["target_crew_id"],
            )
            driver_targets.append((target_driver, target_crew))
        else:
            target_crew = None
        for connection, notification_crew in driver_targets:
            enqueue_support_notification(
                organization=organization,
                recipient=connection.candidate,
                notification_code="transport.vehicle_swapped",
                target_kind="crew",
                target_public_id=notification_crew.public_id,
                target_key=f"support:crew:{notification_crew.public_id}",
                collapse_key=f"support:crew:{notification_crew.public_id}",
                dedupe_key=(
                    f"transport.vehicle_swapped:{request_id}:{connection.public_id}"
                ),
                push_requested=True,
            )
        return Response(
            {
                "swap": details,
                "crew": project_first_crew_payload(crew),
                "target_crew": (
                    project_first_crew_payload(target_crew) if target_crew else None
                ),
                "drivers": [
                    {
                        "id": str(connection.public_id),
                        **_user_identity_payload(connection.candidate),
                        "chat_target": {
                            "target_type": "worker",
                            "target_id": str(connection.public_id),
                        },
                    }
                    for connection, _ in driver_targets
                ],
            }
        )


class ProjectFirstDriverExceptionWriteMixin(ProjectFirstCrewShiftWriteMixin):
    """Shared strict contract for absence and temporary-substitution writes."""

    idempotency_prefix = "driver_absence"

    def get_request_id(self, request):
        raw_request_id = (request.headers.get("Idempotency-Key") or "").strip()
        if not raw_request_id:
            code = f"{self.idempotency_prefix}_idempotency_key_required"
            return None, _project_first_write_error(
                request,
                code=code,
                field_errors={"Idempotency-Key": [code]},
            )
        try:
            return uuid.UUID(raw_request_id), None
        except (TypeError, ValueError, AttributeError):
            code = f"{self.idempotency_prefix}_idempotency_key_invalid"
            return None, _project_first_write_error(
                request,
                code=code,
                field_errors={"Idempotency-Key": [code]},
            )

    def exception_response(self, *, crew, work_dates, extra=None):
        payload = {
            "crew": project_first_crew_payload(crew),
            "affected_dates": [item.isoformat() for item in work_dates],
            "days": project_first_crew_days_payload(
                crew=crew,
                work_dates=work_dates,
            ),
        }
        payload.update(
            project_first_driver_exceptions_payload(
                crew=crew,
                work_dates=work_dates,
            )
        )
        if extra:
            payload.update(extra)
        return Response(payload)


class OrganizationProjectFirstCrewDriverAbsenceAPIView(
    SupportFeatureAPIView,
    ProjectFirstDriverExceptionWriteMixin,
):
    """Mark or cancel primary-driver absence on selected crew days."""

    idempotency_prefix = "driver_absence"

    def _context(self, request, organization_public_id, crew_public_id):
        organization, _ = self.get_project_first_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        crew = self.get_active_crew(
            organization=organization,
            crew_public_id=crew_public_id,
        )
        request_id, error_response = self.get_request_id(request)
        return crew, request_id, error_response

    def post(self, request, organization_public_id, crew_public_id):
        crew, request_id, error_response = self._context(
            request,
            organization_public_id,
            crew_public_id,
        )
        if error_response is not None:
            return error_response
        serializer = ProjectCrewDriverAbsenceSerializer(data=request.data)
        if not serializer.is_valid():
            return _project_first_serializer_error(request, serializer.errors)
        work_dates = serializer.validated_data["work_dates"]
        try:
            mark_project_crew_driver_absence(
                actor=request.user,
                crew=crew,
                work_dates=work_dates,
                request_id=request_id,
            )
        except ValidationError as error:
            return _project_first_service_error(request, error.detail)
        return self.exception_response(crew=crew, work_dates=work_dates)

    def delete(self, request, organization_public_id, crew_public_id):
        crew, request_id, error_response = self._context(
            request,
            organization_public_id,
            crew_public_id,
        )
        if error_response is not None:
            return error_response
        serializer = ProjectCrewDriverAbsenceSerializer(data=request.data)
        if not serializer.is_valid():
            return _project_first_serializer_error(request, serializer.errors)
        work_dates = serializer.validated_data["work_dates"]
        try:
            cancel_project_crew_driver_absence(
                actor=request.user,
                crew=crew,
                work_dates=work_dates,
                request_id=request_id,
            )
        except ValidationError as error:
            return _project_first_service_error(request, error.detail)
        return self.exception_response(crew=crew, work_dates=work_dates)


class OrganizationProjectFirstCrewDriverSubstituteAPIView(
    SupportFeatureAPIView,
    ProjectFirstDriverExceptionWriteMixin,
):
    """Assign, replace or cancel a temporary substitute driver by date."""

    idempotency_prefix = "driver_substitution"

    def _context(self, request, organization_public_id, crew_public_id):
        organization, _ = self.get_project_first_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        crew = self.get_active_crew(
            organization=organization,
            crew_public_id=crew_public_id,
        )
        request_id, error_response = self.get_request_id(request)
        return organization, crew, request_id, error_response

    def get(self, request, organization_public_id, crew_public_id):
        organization, _ = self.get_project_first_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        crew = self.get_active_crew(
            organization=organization,
            crew_public_id=crew_public_id,
        )
        raw_dates = request.query_params.getlist("work_date")
        if not raw_dates:
            raw_dates = request.query_params.getlist("work_dates")
        serializer = ProjectCrewDriverAbsenceSerializer(
            data={"work_dates": raw_dates},
        )
        if not serializer.is_valid():
            return _project_first_serializer_error(request, serializer.errors)
        work_dates = serializer.validated_data["work_dates"]
        try:
            candidates = project_crew_substitute_driver_candidates(
                crew=crew,
                work_dates=work_dates,
            )
        except ValidationError as error:
            return _project_first_service_error(request, error.detail)
        return Response(
            {
                "crew_id": str(crew.public_id),
                "work_dates": [item.isoformat() for item in work_dates],
                "results": [
                    {
                        "connection_id": str(item.public_id),
                        "display_name": (
                            item.candidate.get_full_name().strip()
                            or item.candidate.username
                            or item.candidate.email
                        ),
                        "stage": item.stage,
                        "is_current_crew_passenger": bool(
                            item.is_current_crew_passenger
                        ),
                    }
                    for item in candidates
                ],
            }
        )

    def post(self, request, organization_public_id, crew_public_id):
        organization, crew, request_id, error_response = self._context(
            request,
            organization_public_id,
            crew_public_id,
        )
        if error_response is not None:
            return error_response
        serializer = ProjectCrewDriverSubstituteSerializer(data=request.data)
        if not serializer.is_valid():
            return _project_first_serializer_error(request, serializer.errors)
        data = serializer.validated_data
        substitute = get_object_or_404(
            SupportConnection.objects.select_related("candidate"),
            organization=organization,
            public_id=data["substitute_driver_connection_id"],
            is_archived=False,
        )
        try:
            substitutions = assign_project_crew_substitute_driver(
                actor=request.user,
                crew=crew,
                substitute_driver_connection=substitute,
                work_dates=data["work_dates"],
                request_id=request_id,
            )
        except ValidationError as error:
            return _project_first_service_error(request, error.detail)
        audit = AuditEvent.objects.filter(
            organization=organization,
            actor=request.user,
            action="project_crew.substitute_driver_assigned",
            request_id=request_id,
        ).first()
        affected_dates = [
            date.fromisoformat(item)
            for item in (
                audit.details.get("affected_dates", [])
                if audit is not None
                else [item.work_date.isoformat() for item in substitutions]
            )
        ]
        return self.exception_response(
            crew=crew,
            work_dates=affected_dates,
            extra={
                "substitute_driver": {
                    "id": str(substitute.public_id),
                    "display_name": (
                        substitute.candidate.get_full_name().strip()
                        or substitute.candidate.username
                        or substitute.candidate.email
                    ),
                }
            },
        )

    def delete(self, request, organization_public_id, crew_public_id):
        _, crew, request_id, error_response = self._context(
            request,
            organization_public_id,
            crew_public_id,
        )
        if error_response is not None:
            return error_response
        serializer = ProjectCrewDriverAbsenceSerializer(data=request.data)
        if not serializer.is_valid():
            return _project_first_serializer_error(request, serializer.errors)
        work_dates = serializer.validated_data["work_dates"]
        try:
            cancel_project_crew_substitute_driver(
                actor=request.user,
                crew=crew,
                work_dates=work_dates,
                request_id=request_id,
            )
        except ValidationError as error:
            return _project_first_service_error(request, error.detail)
        return self.exception_response(crew=crew, work_dates=work_dates)


class OrganizationProjectFirstWorkspaceAPIView(
    SupportFeatureAPIView,
    ProjectFirstOrganizationAccessMixin,
):
    """Return the exact project/crew/day snapshot for one calendar month."""

    def get(self, request, organization_public_id, project_public_id):
        organization, membership = self.get_project_first_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        raw_month = (request.query_params.get("month") or "").strip()
        try:
            selected_month = (
                date.fromisoformat(f"{raw_month}-01")
                if raw_month
                else timezone.localdate().replace(day=1)
            )
        except ValueError as error:
            raise ValidationError({"month": "invalid_month"}) from error
        project = get_object_or_404(
            WorkProject.objects.select_related("worksite"),
            organization=organization,
            public_id=project_public_id,
            is_active=True,
        )
        payload = project_first_project_workspace(
            project=project,
            selected_month=selected_month,
        )
        payload["organization"] = _organization_payload(organization, membership)
        return Response(payload)


def _transport_connection_choice_payload(connection):
    return {
        "id": str(connection.public_id),
        **_user_identity_payload(connection.candidate),
        "stage": connection.stage,
        "vacancy_title": connection.vacancy.internal_title,
    }


def _transport_vehicle_payload(vehicle):
    return {
        "id": str(vehicle.public_id),
        "internal_name": vehicle.internal_name,
        "registration_identifier": vehicle.registration_identifier,
        "seat_capacity": vehicle.seat_capacity,
    }


def _transport_driver_assignment_payload(assignment):
    return {
        "id": str(assignment.public_id),
        "state": assignment.state,
        "starts_on": assignment.starts_on,
        "ends_on": assignment.ends_on,
        "driver": _transport_connection_choice_payload(
            assignment.driver_connection
        ),
        "vehicle": _transport_vehicle_payload(assignment.vehicle),
    }


def _transport_stop_payload(stop):
    return {
        "id": str(stop.public_id),
        "sequence": stop.sequence,
        "kind": stop.kind,
        "label": stop.label,
        "housing_site": (
            {
                "id": str(stop.housing_site.public_id),
                "internal_name": stop.housing_site.internal_name,
            }
            if stop.housing_site_id
            else None
        ),
    }


def _transport_route_payload(route):
    return {
        "id": str(route.public_id),
        "internal_name": route.internal_name,
        "state": route.state,
        "starts_on": route.starts_on,
        "ends_on": route.ends_on,
        "departure_time": route.departure_time,
        "reservation_expires_at": route.reservation_expires_at,
        "is_reservation_active": route.is_reservation_active,
        "driver_assignment": _transport_driver_assignment_payload(
            route.driver_vehicle_assignment
        ),
        "worksite": (
            {
                "id": str(route.worksite.public_id),
                "internal_name": route.worksite.internal_name,
            }
            if route.worksite_id
            else None
        ),
        "stops": [_transport_stop_payload(item) for item in route.stops_for_builder],
        "passengers": [
            {
                "id": str(item.public_id),
                "worker": _transport_connection_choice_payload(item.connection),
                "pickup_stop_id": str(item.pickup_stop.public_id),
                "dropoff_stop_id": str(item.dropoff_stop.public_id),
                "boarding_order": item.boarding_order,
            }
            for item in route.passengers_for_builder
        ],
        "passenger_choices": [
            _transport_connection_choice_payload(item)
            for item in route.passenger_choices
        ],
        "available_seat_count": route.available_seat_count,
        "next_boarding_order": route.next_boarding_order,
    }


class OrganizationTransportWorkspaceAPIView(SupportFeatureAPIView):
    """Return the staff transport builder without broad worker-directory data."""

    def get(self, request, organization_public_id):
        snapshot = transport_workspace_snapshot(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        return Response(
            {
                "organization": _organization_payload(
                    snapshot["organization"], snapshot["membership"]
                ),
                "routes": [
                    _transport_route_payload(item) for item in snapshot["routes"]
                ],
                "driver_assignments": [
                    _transport_driver_assignment_payload(item)
                    for item in snapshot["driver_assignments"]
                ],
                "worker_choices": [
                    _transport_connection_choice_payload(item)
                    for item in snapshot["worker_choices"]
                ],
                "housing_sites": [
                    {
                        "id": str(item.public_id),
                        "internal_name": item.internal_name,
                        "city": item.city,
                    }
                    for item in snapshot["housing_sites"]
                ],
                "worksites": [
                    {
                        "id": str(item.public_id),
                        "internal_name": item.internal_name,
                        "city": item.city,
                    }
                    for item in snapshot["worksites"]
                ],
                "vehicles": [
                    _transport_vehicle_payload(item) for item in snapshot["vehicles"]
                ],
            }
        )


class OrganizationScheduleWorkspaceAPIView(
    SupportFeatureAPIView,
    OrganizationAccessMixin,
):
    """A scope-filtered source for reusable schedules and batch drafts."""

    def get(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        require_permission(
            user=request.user,
            organization=organization,
            permission_code=SCHEDULE_MANAGE,
        )
        workers = list(
            worker_connection_queryset_for(
                user=request.user,
                organization=organization,
                queryset=SupportConnection.objects.filter(
                    is_archived=False,
                    stage__in=(
                        SupportConnection.STAGE_COORDINATOR,
                        SupportConnection.STAGE_ACTIVE_WORKER,
                    ),
                ).select_related("candidate", "candidate__profile", "vacancy"),
            ).order_by(
                "candidate__first_name",
                "candidate__last_name",
                "candidate__username",
            )
        )
        worker_ids = {item.id for item in workers}
        today = timezone.localdate()
        calendar_marks = list(
            WorkerRequest.objects.filter(
                organization=organization,
                connection_id__in=worker_ids,
                request_type__in=_CALENDAR_MARK_REQUEST_TYPES,
                status=WorkerRequest.STATUS_APPROVED,
                starts_on__lte=today + timedelta(days=62),
                ends_on__gte=today - timedelta(days=31),
            )
            .select_related(
                "connection__candidate", "connection__candidate__profile"
            )
            .order_by("starts_on", "ends_on", "id")[:250]
        )
        calendar_templates = list(
            CalendarMarkTemplate.objects.filter(
                organization=organization,
                is_active=True,
            ).order_by("request_type", "name", "id")
        )
        calendar_batches = list(
            CalendarMarkBatch.objects.filter(organization=organization)
            .select_related("template")
            .prefetch_related("items__request__connection__candidate__profile")
            .order_by("-created_at", "-id")[:40]
        )
        visible_calendar_batches = []
        for batch in calendar_batches:
            items = list(batch.items.all())
            if items and all(item.request.connection_id in worker_ids for item in items):
                batch.items_for_workspace = items
                visible_calendar_batches.append(batch)
        batches = list(
            ScheduledShiftBatch.objects.filter(
                organization=organization,
                state__in=(
                    ScheduledShiftBatch.STATE_DRAFT,
                    ScheduledShiftBatch.STATE_PUBLISHED,
                ),
            )
            .select_related("template")
            .prefetch_related("shifts__connection__candidate__profile")
            .order_by("-starts_on", "-created_at", "-id")[:20]
        )
        visible_batches = []
        for batch in batches:
            shifts = list(batch.shifts.all())
            if shifts and all(item.connection_id in worker_ids for item in shifts):
                batch.shifts_for_workspace = shifts
                visible_batches.append(batch)
        return Response(
            {
                "templates": [
                    _shift_template_payload(item)
                    for item in ShiftTemplate.objects.filter(
                        organization=organization,
                        is_active=True,
                    ).order_by("name", "id")
                ],
                "workers": [
                    {
                        "id": str(item.public_id),
                        **_user_identity_payload(item.candidate),
                        "stage": item.stage,
                        "vacancy_title": item.vacancy.internal_title,
                    }
                    for item in workers
                ],
                "batches": [
                    _scheduled_shift_batch_payload(
                        item,
                        shifts=item.shifts_for_workspace,
                    )
                    for item in visible_batches
                ],
                "calendar_marks": [
                    _calendar_mark_payload(item, include_staff_fields=True)
                    for item in calendar_marks
                ],
                "calendar_templates": [
                    _calendar_mark_template_payload(item) for item in calendar_templates
                ],
                "calendar_mark_batches": [
                    _calendar_mark_batch_payload(item, items=item.items_for_workspace)
                    for item in visible_calendar_batches
                ],
            }
        )


class OrganizationWorkerConnectionListAPIView(
    SupportFeatureAPIView,
    OrganizationAccessMixin,
):
    """A staff directory that is always bounded by the employee's worker scope."""

    def get(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        require_permission(
            user=request.user,
            organization=organization,
            permission_code=WORKER_VIEW,
        )
        queryset = worker_connection_queryset_for(
            user=request.user,
            organization=organization,
            queryset=SupportConnection.objects.filter(is_archived=False),
        ).select_related("candidate", "candidate__profile", "vacancy")
        term = (request.query_params.get("q") or "").strip()
        if term:
            queryset = queryset.filter(
                Q(candidate__first_name__icontains=term)
                | Q(candidate__last_name__icontains=term)
                | Q(candidate__username__icontains=term)
                | Q(vacancy__internal_title__icontains=term)
            )
        connections = list(queryset.order_by("-updated_at", "-id")[:250])
        connection_ids = [connection.id for connection in connections]
        project_crews_by_connection = {connection_id: [] for connection_id in connection_ids}
        seen_project_crews = {connection_id: set() for connection_id in connection_ids}

        def add_project_crew(connection_id, crew, role, assignment_type):
            if crew.id in seen_project_crews[connection_id]:
                return
            seen_project_crews[connection_id].add(crew.id)
            project = crew.project
            project_crews_by_connection[connection_id].append(
                {
                    "project_id": str(project.public_id),
                    "project_name": project.worker_visible_name or project.internal_name,
                    "crew_id": str(crew.public_id),
                    "crew_name": crew.internal_name,
                    "role": role,
                    "assignment_type": assignment_type,
                }
            )

        for assignment in (
            ProjectCrewResourceAssignment.objects.filter(
                driver_connection_id__in=connection_ids,
                ends_on__isnull=True,
                crew__state=ProjectCrew.STATE_ACTIVE,
                crew__project__is_active=True,
            )
            .select_related("crew__project")
            .order_by("driver_connection_id", "-starts_on", "-id")
        ):
            add_project_crew(
                assignment.driver_connection_id,
                assignment.crew,
                ProjectCrewShiftMember.ROLE_DRIVER,
                "permanent",
            )
        for assignment in (
            ProjectCrewPassenger.objects.filter(
                connection_id__in=connection_ids,
                ends_on__isnull=True,
                crew__state=ProjectCrew.STATE_ACTIVE,
                crew__project__is_active=True,
            )
            .select_related("crew__project")
            .order_by("connection_id", "-starts_on", "-id")
        ):
            add_project_crew(
                assignment.connection_id,
                assignment.crew,
                ProjectCrewShiftMember.ROLE_PASSENGER,
                "permanent",
            )
        for member in (
            ProjectCrewShiftMember.objects.filter(
                connection_id__in=connection_ids,
                shift__state=ProjectCrewShift.STATE_PUBLISHED,
                shift__work_date__gte=timezone.localdate(),
                shift__crew__state=ProjectCrew.STATE_ACTIVE,
                shift__crew__project__is_active=True,
            )
            .select_related("shift__crew__project")
            .order_by("connection_id", "shift__work_date", "id")
        ):
            add_project_crew(
                member.connection_id,
                member.shift.crew,
                member.role,
                "scheduled",
            )
        return Response(
            {
                "results": [
                    {
                        "id": str(connection.public_id),
                        "candidate": {
                            **_user_identity_payload(connection.candidate),
                        },
                        "vacancy": {
                            "id": str(connection.vacancy.public_id),
                            "internal_title": connection.vacancy.internal_title,
                        },
                        "stage": connection.stage,
                        "visible_stage": connection.visible_stage,
                        "has_driving_license": connection.has_driving_license,
                        "project_crews": project_crews_by_connection[connection.id],
                        "created_at": connection.created_at,
                        "updated_at": connection.updated_at,
                    }
                    for connection in connections
                ]
            }
        )


class OrganizationChatDirectoryAPIView(
    SupportFeatureAPIView,
    OrganizationAccessMixin,
):
    """People visible in the two mobile staff chat directories."""

    def get(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        memberships = list(
            OrganizationMembership.objects.filter(
                organization=organization,
                state=OrganizationMembership.STATE_ACTIVE,
            )
            .select_related("user", "user__profile")
            .order_by("-created_at", "-id")
        )
        conversations = list(
            SupportConversation.objects.filter(
                organization=organization,
                state=SupportConversation.STATE_ACTIVE,
                members__user=request.user,
                members__left_at__isnull=True,
            )
            .select_related("connection", "private_worker")
            .prefetch_related("members__user__profile")
            .distinct()
        )
        conversation_by_worker = {}
        for conversation in conversations:
            if conversation.kind != SupportConversation.KIND_MANAGER:
                continue
            if (
                conversation.private_manager_id is not None
                and conversation.private_manager_id != request.user.id
            ):
                continue
            worker_id = conversation.private_worker_id or (
                conversation.connection.candidate_id
                if conversation.connection_id is not None
                else None
            )
            if worker_id is not None:
                conversation_by_worker.setdefault(worker_id, conversation)
        staff_conversation_by_user = {}
        for conversation in conversations:
            if conversation.connection_id is not None:
                continue
            for member in conversation.members.all():
                if member.left_at is None and member.user_id != request.user.id:
                    staff_conversation_by_user.setdefault(member.user_id, conversation)

        results = []
        if has_permission(
            user=request.user,
            organization=organization,
            permission_code=WORKER_VIEW,
        ) and has_permission(
            user=request.user,
            organization=organization,
            permission_code=CHAT_MANAGE,
        ):
            worker_queryset = worker_connection_queryset_for(
                user=request.user,
                organization=organization,
                queryset=SupportConnection.objects.filter(is_archived=False),
            ).select_related("candidate", "candidate__profile", "vacancy")
            seen_worker_user_ids = set()
            for connection in worker_queryset.order_by("-created_at", "-id")[:250]:
                if connection.candidate_id in seen_worker_user_ids:
                    continue
                seen_worker_user_ids.add(connection.candidate_id)
                conversation = conversation_by_worker.get(connection.candidate_id)
                results.append(
                    {
                        "target_type": "worker",
                        "target_id": str(connection.public_id),
                        "display_name": _user_display_name(connection.candidate),
                        "first_name": (connection.candidate.first_name or "").strip(),
                        "last_name": (connection.candidate.last_name or "").strip(),
                        "avatar_url": _user_identity_payload(connection.candidate)["avatar_url"],
                        "subtitle": connection.vacancy.internal_title,
                        "created_at": connection.created_at,
                        "conversation": (
                            _conversation_payload(conversation, viewer=request.user)
                            if conversation is not None
                            else None
                        ),
                    }
                )
        for membership in memberships:
            if membership.user_id == request.user.id:
                continue
            conversation = staff_conversation_by_user.get(membership.user_id)
            results.append(
                {
                    "target_type": "staff",
                    "target_id": str(membership.public_id),
                    "display_name": _user_display_name(membership.user),
                    "first_name": (membership.user.first_name or "").strip(),
                    "last_name": (membership.user.last_name or "").strip(),
                    "avatar_url": _user_identity_payload(membership.user)["avatar_url"],
                    "subtitle": membership.display_role,
                    "created_at": membership.created_at,
                    "conversation": (
                        _conversation_payload(conversation, viewer=request.user)
                        if conversation is not None
                        else None
                    ),
                }
            )
        return Response({"results": results})


class OrganizationChatDirectoryOpenAPIView(
    SupportFeatureAPIView,
    OrganizationAccessMixin,
):
    """Open a scoped worker chat or a private company-staff chat."""

    def post(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        target_type = str(request.data.get("target_type") or "").strip()
        target_id = str(request.data.get("target_id") or "").strip()
        if target_type not in {"worker", "staff"} or not target_id:
            raise ValidationError({"target": "invalid_chat_directory_target"})

        if target_type == "worker":
            require_permission(
                user=request.user,
                organization=organization,
                permission_code=CHAT_MANAGE,
            )
            connection = get_object_or_404(
                SupportConnection.objects.select_related(
                    "organization", "candidate", "candidate__profile"
                ),
                organization=organization,
                public_id=target_id,
                is_archived=False,
            )
            require_worker_connection_access(
                user=request.user,
                organization=organization,
                connection=connection,
            )
            conversation, created = open_manager_conversation_for_staff(
                actor=request.user,
                connection=connection,
            )
        else:
            target_membership = get_object_or_404(
                OrganizationMembership.objects.select_related(
                    "organization", "user", "user__profile"
                ),
                organization=organization,
                public_id=target_id,
                state=OrganizationMembership.STATE_ACTIVE,
            )
            conversation, created = open_staff_conversation(
                actor=request.user,
                target_membership=target_membership,
            )
        conversation = SupportConversation.objects.prefetch_related(
            "members__user__profile"
        ).get(
            pk=conversation.pk
        )
        return Response(
            {
                "created": created,
                "conversation": _conversation_payload(conversation, viewer=request.user),
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class OrganizationWorkerConnectionSummaryAPIView(
    SupportFeatureAPIView,
    OrganizationAccessMixin,
):
    """A mobile employee card bounded by both a permission and worker scope.

    The response is deliberately split into sections.  A staff member with
    access to the worker directory does not automatically receive a housing
    address, vehicle number, time record, or request history: each section is
    produced only for the matching operational permission.
    """

    def get(self, request, organization_public_id, connection_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        require_permission(
            user=request.user,
            organization=organization,
            permission_code=WORKER_VIEW,
        )
        connection = get_object_or_404(
            worker_connection_queryset_for(
                user=request.user,
                organization=organization,
                queryset=SupportConnection.objects.filter(
                    is_archived=False,
                ).select_related("candidate", "candidate__profile", "vacancy"),
            ),
            public_id=connection_public_id,
        )

        raw_month = str(request.query_params.get("month") or "").strip()
        if raw_month:
            month_start = parse_date(f"{raw_month}-01")
            if month_start is None:
                raise ValidationError({"month": "Use YYYY-MM format."})
        else:
            local_today = timezone.localdate()
            month_start = local_today.replace(day=1)
        month_end = month_start.replace(
            day=monthrange(month_start.year, month_start.month)[1]
        )
        local_today = timezone.localdate()

        may_manage_housing = has_permission(
            user=request.user,
            organization=organization,
            permission_code=HOUSING_MANAGE,
        )
        may_manage_schedule = has_permission(
            user=request.user,
            organization=organization,
            permission_code=SCHEDULE_MANAGE,
        )
        may_manage_transport = has_permission(
            user=request.user,
            organization=organization,
            permission_code=TRANSPORT_MANAGE,
        )
        may_view_time = has_permission(
            user=request.user,
            organization=organization,
            permission_code=TIME_VIEW,
        )
        may_decide_requests = has_permission(
            user=request.user,
            organization=organization,
            permission_code=REQUEST_DECIDE,
        )
        may_request_documents = has_permission(
            user=request.user,
            organization=organization,
            permission_code=DOCUMENT_REQUEST,
        )
        can_operate = connection.stage in {
            SupportConnection.STAGE_COORDINATOR,
            SupportConnection.STAGE_ACTIVE_WORKER,
        }

        housing = []
        if may_manage_housing:
            housing = [
                {
                    "id": str(item.public_id),
                    "state": item.state,
                    "check_in_at": item.check_in_at,
                    "check_out_at": item.check_out_at,
                    "place": {
                        "site_name": item.place.room.site.internal_name,
                        "country_code": item.place.room.site.country_code,
                        "city": item.place.room.site.city,
                        "postal_code": item.place.room.site.postal_code,
                        "street": item.place.room.site.street,
                        "building": item.place.room.site.building,
                        "room_label": item.place.room.label,
                        "place_label": item.place.label,
                    },
                }
                for item in HousingAssignment.objects.filter(connection=connection)
                .select_related("place__room__site")
                .order_by("-check_in_at", "-id")[:20]
            ]

        work = []
        scheduled_shifts = []
        if may_manage_schedule:
            work = [
                {
                    "id": str(item.public_id),
                    "state": item.state,
                    "worker_role": item.worker_role,
                    "starts_at": item.starts_at,
                    "ends_at": item.ends_at,
                    "project": {
                        "internal_name": item.project.internal_name,
                        "worker_visible_name": item.project.worker_visible_name,
                        "worksite_name": item.project.worksite.internal_name,
                        "city": item.project.worksite.city,
                        "street": item.project.worksite.street,
                        "building": item.project.worksite.building,
                    },
                }
                for item in WorkerProjectAssignment.objects.filter(connection=connection)
                .select_related("project__worksite")
                .order_by("-starts_at", "-id")[:20]
            ]
            scheduled_shifts = [
                _scheduled_shift_payload(item)
                for item in ScheduledWorkShift.objects.filter(connection=connection)
                .select_related("connection", "work_assignment")
                .order_by("-work_date", "-starts_at", "-id")[:14]
            ]

        driver_assignments = []
        passenger_routes = []
        if may_manage_transport:
            driver_assignments = [
                {
                    "id": str(item.public_id),
                    "state": item.state,
                    "starts_on": item.starts_on,
                    "ends_on": item.ends_on,
                    "vehicle": {
                        "internal_name": item.vehicle.internal_name,
                        "registration_identifier": item.vehicle.registration_identifier,
                        "seat_capacity": item.vehicle.seat_capacity,
                    },
                }
                for item in DriverVehicleAssignment.objects.filter(
                    driver_connection=connection
                )
                .select_related("vehicle")
                .order_by("-starts_on", "-id")[:20]
            ]
            passenger_routes = [
                {
                    "id": str(item.public_id),
                    "boarding_order": item.boarding_order,
                    "pickup_label": item.pickup_stop.label,
                    "dropoff_label": item.dropoff_stop.label,
                    "route": {
                        "id": str(item.route.public_id),
                        "internal_name": item.route.internal_name,
                        "state": item.route.state,
                        "starts_on": item.route.starts_on,
                        "ends_on": item.route.ends_on,
                        "departure_time": item.route.departure_time,
                        "vehicle_name": item.route.driver_vehicle_assignment.vehicle.internal_name,
                    },
                }
                for item in TransportPassengerAssignment.objects.filter(
                    connection=connection
                )
                .select_related(
                    "pickup_stop",
                    "dropoff_stop",
                    "route__driver_vehicle_assignment__vehicle",
                )
                .order_by("-route__starts_on", "-id")[:20]
            ]

        time_entries = []
        if may_view_time:
            time_entries = [
                _time_entry_payload(item, include_staff_fields=True)
                for item in WorkTimeEntry.objects.filter(connection=connection)
                .select_related(
                    "connection__candidate",
                    "connection__vacancy",
                    "scheduled_shift",
                    "confirmed_by",
                )
                .order_by("-work_date", "-id")[:14]
            ]

        worker_requests = []
        if may_decide_requests:
            worker_requests = [
                _worker_request_payload(item, include_staff_fields=True)
                for item in WorkerRequest.objects.filter(connection=connection)
                .select_related(
                    "connection__candidate",
                    "connection__vacancy",
                    "reviewed_by",
                )
                .order_by("-submitted_at", "-created_at", "-id")[:20]
            ]

        # Project-first is now the source of truth for the mobile worker
        # calendar.  Legacy assignments remain in the response only for old
        # clients during the migration window.
        calendar_days = {}
        current_project = None
        if may_manage_schedule:
            worker_memberships = list(
                ProjectCrewShiftMember.objects.filter(
                    connection=connection,
                    shift__crew__organization=organization,
                    shift__state=ProjectCrewShift.STATE_PUBLISHED,
                    shift__work_date__range=(month_start, month_end),
                )
                .select_related(
                    "shift__crew__project__worksite",
                    "vehicle",
                )
                .order_by("shift__work_date", "shift__starts_at", "id")
            )
            shift_ids = {item.shift_id for item in worker_memberships}
            accessible_worker_ids = set(
                worker_connection_queryset_for(
                    user=request.user,
                    organization=organization,
                    queryset=SupportConnection.objects.filter(is_archived=False),
                ).values_list("id", flat=True)
            )
            members_by_shift = {}
            if shift_ids and may_manage_transport:
                for member in (
                    ProjectCrewShiftMember.objects.filter(
                        shift_id__in=shift_ids,
                        connection_id__in=accessible_worker_ids,
                    )
                    .select_related(
                        "connection__candidate",
                        "connection__candidate__profile",
                        "vehicle",
                    )
                    .order_by("role", "connection__candidate__first_name", "id")
                ):
                    members_by_shift.setdefault(member.shift_id, []).append(
                        {
                            "connection_id": str(member.connection.public_id),
                            **_user_identity_payload(member.connection.candidate),
                            "role": member.role,
                            "vehicle": (
                                {
                                    "name": member.vehicle.internal_name,
                                    "registration_identifier": member.vehicle.registration_identifier,
                                }
                                if member.vehicle_id
                                else None
                            ),
                        }
                    )
            for membership in worker_memberships:
                shift = membership.shift
                day = calendar_days.setdefault(
                    shift.work_date.isoformat(),
                    {"date": shift.work_date, "shifts": [], "day_off": False, "absences": []},
                )
                day["shifts"].append(
                    {
                        "id": str(shift.public_id),
                        "starts_at": shift.starts_at,
                        "ends_at": shift.ends_at,
                        "break_minutes": shift.break_minutes,
                        "role": membership.role,
                        "project": {
                            "id": str(shift.crew.project.public_id),
                            "name": shift.crew.project.internal_name,
                        },
                        "crew": {
                            "id": str(shift.crew.public_id),
                            "name": shift.crew.internal_name,
                        },
                        "vehicle": (
                            {
                                "name": membership.vehicle.internal_name,
                                "registration_identifier": membership.vehicle.registration_identifier,
                            }
                            if membership.vehicle_id
                            else None
                        ),
                        "members": members_by_shift.get(shift.id, []),
                    }
                )
                if shift.work_date == local_today and current_project is None:
                    current_project = {
                        "id": str(shift.crew.project.public_id),
                        "name": shift.crew.project.internal_name,
                        "crew_id": str(shift.crew.public_id),
                        "crew_name": shift.crew.internal_name,
                    }

            # Prefer today's project even when another month is open.  A
            # worker without a shift today is still assigned when a future
            # shift or an active crew roster exists.
            if current_project is None:
                today_membership = (
                    ProjectCrewShiftMember.objects.filter(
                        connection=connection,
                        shift__crew__organization=organization,
                        shift__state=ProjectCrewShift.STATE_PUBLISHED,
                        shift__work_date=local_today,
                    )
                    .select_related("shift__crew__project")
                    .order_by("shift__starts_at", "id")
                    .first()
                )
                if today_membership is not None:
                    today_crew = today_membership.shift.crew
                    current_project = {
                        "id": str(today_crew.project.public_id),
                        "name": today_crew.project.internal_name,
                        "crew_id": str(today_crew.public_id),
                        "crew_name": today_crew.internal_name,
                    }

            if current_project is None:
                upcoming_membership = (
                    ProjectCrewShiftMember.objects.filter(
                        connection=connection,
                        shift__crew__organization=organization,
                        shift__state=ProjectCrewShift.STATE_PUBLISHED,
                        shift__work_date__gt=local_today,
                    )
                    .select_related("shift__crew__project")
                    .order_by("shift__work_date", "shift__starts_at", "id")
                    .first()
                )
                if upcoming_membership is not None:
                    upcoming_crew = upcoming_membership.shift.crew
                    current_project = {
                        "id": str(upcoming_crew.project.public_id),
                        "name": upcoming_crew.project.internal_name,
                        "crew_id": str(upcoming_crew.public_id),
                        "crew_name": upcoming_crew.internal_name,
                    }

            if current_project is None:
                active_driver_assignment = (
                    ProjectCrewResourceAssignment.objects.filter(
                        crew__organization=organization,
                        crew__state=ProjectCrew.STATE_ACTIVE,
                        crew__project__is_active=True,
                        driver_connection=connection,
                        starts_on__lte=local_today,
                    )
                    .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=local_today))
                    .select_related("crew__project")
                    .order_by("-starts_on", "-id")
                    .first()
                )
                active_passenger_assignment = None
                if active_driver_assignment is None:
                    active_passenger_assignment = (
                        ProjectCrewPassenger.objects.filter(
                            crew__organization=organization,
                            crew__state=ProjectCrew.STATE_ACTIVE,
                            crew__project__is_active=True,
                            connection=connection,
                            starts_on__lte=local_today,
                        )
                        .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=local_today))
                        .select_related("crew__project")
                        .order_by("-starts_on", "-id")
                        .first()
                    )
                active_assignment = active_driver_assignment or active_passenger_assignment
                if active_assignment is not None:
                    active_crew = active_assignment.crew
                    current_project = {
                        "id": str(active_crew.project.public_id),
                        "name": active_crew.project.internal_name,
                        "crew_id": str(active_crew.public_id),
                        "crew_name": active_crew.internal_name,
                    }

            for day_off in WorkerScheduleDayOff.objects.filter(
                organization=organization,
                connection=connection,
                work_date__range=(month_start, month_end),
            ):
                day = calendar_days.setdefault(
                    day_off.work_date.isoformat(),
                    {"date": day_off.work_date, "shifts": [], "day_off": False, "absences": []},
                )
                day["day_off"] = True

            for absence in (
                ProjectCrewMemberAbsence.objects.filter(
                    organization=organization,
                    connection=connection,
                    work_date__range=(month_start, month_end),
                )
                .select_related("crew__project")
                .order_by("work_date", "id")
            ):
                day = calendar_days.setdefault(
                    absence.work_date.isoformat(),
                    {"date": absence.work_date, "shifts": [], "day_off": False, "absences": []},
                )
                day["absences"].append(
                    {
                        "project": {
                            "id": str(absence.crew.project.public_id),
                            "name": absence.crew.project.internal_name,
                        },
                        "crew": {
                            "id": str(absence.crew.public_id),
                            "name": absence.crew.internal_name,
                        },
                    }
                )

        current_housing = None
        if may_manage_housing:
            now = timezone.now()
            published_housing_assignments = (
                HousingAssignment.objects.filter(
                    connection=connection,
                    state=HousingAssignment.STATE_PUBLISHED,
                )
                .filter(Q(check_out_at__isnull=True) | Q(check_out_at__gte=now))
                .select_related("place__room__site")
            )
            current_housing_assignment = (
                published_housing_assignments.filter(check_in_at__lte=now)
                .order_by("-check_in_at", "-id")
                .first()
            )
            if current_housing_assignment is None:
                current_housing_assignment = (
                    published_housing_assignments.filter(check_in_at__gt=now)
                    .order_by("check_in_at", "id")
                    .first()
                )
            if current_housing_assignment is not None:
                place = current_housing_assignment.place
                current_housing = {
                    "site_name": place.room.site.internal_name,
                    "room_label": place.room.label,
                    "place_label": place.label,
                    "check_in_at": current_housing_assignment.check_in_at,
                    "check_out_at": current_housing_assignment.check_out_at,
                }

        document_packages = []
        document_reference_code = None
        if may_request_documents:
            packages = list(
                DocumentRequestPackage.objects.filter(
                    organization=organization,
                    connection=connection,
                )
                .select_related(
                    "connection__candidate",
                    "account_reference",
                    "created_by",
                    "reviewed_by",
                )
                .order_by("-created_at", "-id")[:30]
            )
            document_packages = [
                _document_request_package_payload(item, include_staff_fields=True)
                for item in packages
            ]
            if packages:
                document_reference_code = packages[0].account_reference.reference_code
            else:
                reference = SupportWorkerDocumentReference.objects.filter(
                    user=connection.candidate
                ).first()
                if reference is not None:
                    document_reference_code = reference.reference_code

        manager_conversation = find_private_manager_conversation(
            organization=organization,
            worker=connection.candidate,
            manager=request.user,
        )

        available_housing_places = []
        if may_manage_housing and can_operate:
            available_housing_places = [
                {
                    "id": str(item.public_id),
                    "site_name": item.room.site.internal_name,
                    "room_label": item.room.label,
                    "place_label": item.label,
                }
                for item in HousingPlace.objects.filter(
                    room__site__organization=organization,
                    is_active=True,
                    room__is_active=True,
                    room__site__is_active=True,
                )
                .select_related("room__site")
                .order_by("room__site__internal_name", "room__label", "label", "id")[:250]
            ]

        available_work_projects = []
        if may_manage_schedule and can_operate:
            available_work_projects = [
                {
                    "id": str(item.public_id),
                    "internal_name": item.internal_name,
                    "worker_visible_name": item.worker_visible_name,
                    "worksite_name": item.worksite.internal_name,
                }
                for item in WorkProject.objects.filter(
                    organization=organization,
                    is_active=True,
                    worksite__is_active=True,
                )
                .select_related("worksite")
                .order_by("internal_name", "id")[:250]
            ]

        available_vehicles = []
        if may_manage_transport and can_operate:
            available_vehicles = [
                {
                    "id": str(item.public_id),
                    "internal_name": item.internal_name,
                    "registration_identifier": item.registration_identifier,
                    "seat_capacity": item.seat_capacity,
                }
                for item in Vehicle.objects.filter(
                    organization=organization,
                    is_active=True,
                ).order_by("internal_name", "id")[:250]
            ]

        return Response(
            {
                "connection": {
                    "id": str(connection.public_id),
                    "candidate": {
                        **_user_identity_payload(connection.candidate),
                        "has_driving_license": connection.has_driving_license,
                    },
                    "vacancy": {
                        "id": str(connection.vacancy.public_id),
                        "internal_title": connection.vacancy.internal_title,
                    },
                    "stage": connection.stage,
                    "visible_stage": connection.visible_stage,
                },
                "available_sections": {
                    "housing": may_manage_housing,
                    "work": may_manage_schedule,
                    "transport": may_manage_transport,
                    "time": may_view_time,
                    "requests": may_decide_requests,
                    "documents": may_request_documents,
                },
                "available_actions": {
                    "create_housing": may_manage_housing and can_operate,
                    "create_work": may_manage_schedule and can_operate,
                    "create_shift": may_manage_schedule and can_operate,
                    "create_driver_assignment": may_manage_transport and can_operate,
                },
                "available_resources": {
                    "housing_places": available_housing_places,
                    "work_projects": available_work_projects,
                    "vehicles": available_vehicles,
                },
                "housing": housing,
                "work": work,
                "scheduled_shifts": scheduled_shifts,
                "driver_assignments": driver_assignments,
                "passenger_routes": passenger_routes,
                "time_entries": time_entries,
                "requests": worker_requests,
                "profile_header": {
                    "current_project": current_project,
                    "current_housing": current_housing,
                },
                "calendar": {
                    "month": month_start.strftime("%Y-%m"),
                    "days": list(calendar_days.values()),
                },
                "manager_conversation_id": (
                    str(manager_conversation.public_id)
                    if manager_conversation is not None
                    else None
                ),
                "document_reference_code": document_reference_code,
                "document_packages": document_packages,
            }
        )


class WorkerConnectionScheduleWriteMixin(OrganizationAccessMixin):
    """Permission-scoped point changes for one worker calendar."""

    def get_connection(self, *, user, organization, connection_public_id):
        require_permission(
            user=user,
            organization=organization,
            permission_code=SCHEDULE_MANAGE,
        )
        return get_object_or_404(
            worker_connection_queryset_for(
                user=user,
                organization=organization,
                queryset=SupportConnection.objects.filter(is_archived=False),
            ),
            public_id=connection_public_id,
        )

    def validated_dates(self, request):
        serializer = ProjectCrewShiftReleaseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data["work_dates"]

    def response(self, dates):
        return Response({"affected_dates": [item.isoformat() for item in dates]})


class OrganizationWorkerConnectionScheduleReleaseAPIView(
    SupportFeatureAPIView,
    WorkerConnectionScheduleWriteMixin,
):
    """Release only the selected worker, retaining the rest of each crew."""

    def post(self, request, organization_public_id, connection_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        connection = self.get_connection(
            user=request.user,
            organization=organization,
            connection_public_id=connection_public_id,
        )
        dates = self.validated_dates(request)
        try:
            affected = release_project_crew_member_days(
                actor=request.user,
                connection=connection,
                work_dates=dates,
            )
        except ValidationError as error:
            return _project_first_service_error(request, error.detail)
        return self.response(affected)


class OrganizationWorkerConnectionDayOffAPIView(
    SupportFeatureAPIView,
    WorkerConnectionScheduleWriteMixin,
):
    """Mark or cancel persistent worker-wide days off."""

    def post(self, request, organization_public_id, connection_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        connection = self.get_connection(
            user=request.user,
            organization=organization,
            connection_public_id=connection_public_id,
        )
        dates = self.validated_dates(request)
        try:
            affected = mark_worker_schedule_days_off(
                actor=request.user,
                connection=connection,
                work_dates=dates,
            )
        except ValidationError as error:
            return _project_first_service_error(request, error.detail)
        return self.response(affected)

    def delete(self, request, organization_public_id, connection_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        connection = self.get_connection(
            user=request.user,
            organization=organization,
            connection_public_id=connection_public_id,
        )
        dates = self.validated_dates(request)
        try:
            affected = restore_worker_schedule_days_off(
                actor=request.user,
                connection=connection,
                work_dates=dates,
            )
        except ValidationError as error:
            return _project_first_service_error(request, error.detail)
        return self.response(affected)


class SupportOrganizationCreateAPIView(SupportFeatureAPIView, JobHubOperatorRequiredMixin):
    def post(self, request):
        self.require_jobhub_operator(request)
        serializer = SupportOrganizationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization, membership = create_organization(
            jobhub_operator=request.user,
            **serializer.validated_data,
        )
        return Response(
            {
                "organization": _organization_payload(organization, membership),
                "owner_membership": _membership_payload(membership),
            },
            status=status.HTTP_201_CREATED,
        )


class SupportOrganizationDetailAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    def get(self, request, organization_public_id):
        organization, membership = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        return Response({"organization": _organization_payload(organization, membership)})


class SupportOrganizationActivateAPIView(
    SupportFeatureAPIView,
    JobHubOperatorRequiredMixin,
):
    def post(self, request, organization_public_id):
        self.require_jobhub_operator(request)
        organization = get_object_or_404(SupportOrganization, public_id=organization_public_id)
        organization = activate_organization(
            jobhub_operator=request.user,
            organization=organization,
        )
        return Response({"organization": _organization_payload(organization)})


class MembershipInvitationCreateAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    def post(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        serializer = MembershipInvitationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invitation = create_membership_invitation(
            actor=request.user,
            organization=organization,
            invited_email=serializer.validated_data["email"],
            display_role=serializer.validated_data.get("display_role", ""),
            permission_codes=serializer.validated_data["permission_codes"],
        )
        invitation = (
            MembershipInvitation.objects.select_related("organization")
            .prefetch_related("permission_grants")
            .get(pk=invitation.pk)
        )
        return Response({"invitation": _invitation_payload(invitation)}, status=status.HTTP_201_CREATED)


class MyMembershipInvitationListAPIView(SupportFeatureAPIView):
    def get(self, request):
        invitations = (
            MembershipInvitation.objects.filter(
                invited_user=request.user,
                state=MembershipInvitation.STATUS_PENDING,
            )
            .select_related("organization")
            .prefetch_related("permission_grants")
            .order_by("-created_at", "-id")
        )
        return Response({"results": [_invitation_payload(item) for item in invitations]})


class MembershipInvitationAcceptAPIView(SupportFeatureAPIView):
    def post(self, request, invitation_public_id):
        invitation = get_object_or_404(
            MembershipInvitation,
            public_id=invitation_public_id,
            invited_user=request.user,
        )
        membership = accept_membership_invitation(user=request.user, invitation=invitation)
        return Response(
            {
                "membership": _membership_payload(membership),
                "organization": _organization_payload(membership.organization, membership),
            },
            status=status.HTTP_200_OK,
        )


class PermissionGrantCreateAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    def post(self, request, organization_public_id, membership_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        membership = get_object_or_404(
            OrganizationMembership,
            public_id=membership_public_id,
            organization=organization,
        )
        serializer = PermissionCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        grant, created = grant_permission(
            actor=request.user,
            organization=organization,
            membership=membership,
            permission_code=serializer.validated_data["permission_code"],
        )
        return Response(
            {
                "created": created,
                "grant": {
                    "id": str(grant.public_id),
                    "permission_code": grant.permission_code,
                    "scope": grant.scope_kind,
                },
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class DelegablePermissionGrantCreateAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    def post(self, request, organization_public_id, membership_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        membership = get_object_or_404(
            OrganizationMembership,
            public_id=membership_public_id,
            organization=organization,
        )
        serializer = PermissionCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        grant, created = grant_delegable_permission(
            actor=request.user,
            organization=organization,
            membership=membership,
            permission_code=serializer.validated_data["permission_code"],
        )
        return Response(
            {
                "created": created,
                "grant": {
                    "id": str(grant.public_id),
                    "permission_code": grant.permission_code,
                    "scope": grant.scope_kind,
                },
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class WorkerAccessScopeCreateAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    def post(self, request, organization_public_id, membership_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        membership = get_object_or_404(
            OrganizationMembership,
            public_id=membership_public_id,
            organization=organization,
        )
        serializer = WorkerAccessScopeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        connection = get_object_or_404(
            SupportConnection,
            organization=organization,
            public_id=serializer.validated_data["connection_id"],
        )
        scope, created = grant_worker_access_scope(
            actor=request.user,
            organization=organization,
            membership=membership,
            connection=connection,
        )
        return Response(
            {
                "created": created,
                "worker_scope": {
                    "id": str(scope.public_id),
                    "membership_id": str(membership.public_id),
                    "connection_id": str(connection.public_id),
                    "is_active": scope.is_active,
                },
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class WorkerAccessScopeRevokeAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    def post(self, request, organization_public_id, scope_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        scope = get_object_or_404(
            WorkerAccessScope.objects.select_related("membership__organization"),
            public_id=scope_public_id,
            membership__organization=organization,
        )
        scope = revoke_worker_access_scope(actor=request.user, scope=scope)
        return Response(
            {
                "worker_scope": {
                    "id": str(scope.public_id),
                    "is_active": scope.is_active,
                    "revoked_at": scope.revoked_at,
                }
            }
        )


class TemporarySupportAccessGrantCreateAPIView(
    SupportFeatureAPIView,
    JobHubOperatorRequiredMixin,
):
    def post(self, request):
        self.require_jobhub_operator(request)
        serializer = TemporarySupportAccessGrantSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user_model = get_user_model()
        user = user_model.objects.filter(
            email__iexact=serializer.validated_data["user_email"],
            is_active=True,
        ).first()
        if user is None:
            raise ValidationError({"user_email": "registered_jobhub_account_required"})
        organization = None
        organization_public_id = serializer.validated_data.get("organization_public_id")
        if organization_public_id:
            organization = get_object_or_404(SupportOrganization, public_id=organization_public_id)
        now = timezone.now()
        grant = SupportAccessGrant.objects.create(
            user=user,
            organization=organization,
            granted_by=request.user,
            starts_at=now,
            ends_at=now + timedelta(days=int(serializer.validated_data["duration_days"])),
            reason=serializer.validated_data["reason"],
        )
        record_audit_event(
            organization=organization,
            actor=request.user,
            action="support_access.temporary_granted",
            target=grant,
            details={"duration_days": int(serializer.validated_data["duration_days"])},
        )
        enqueue_support_notification(
            organization=organization,
            recipient=user,
            notification_code="support.access_changed",
            target_kind="support_access",
            target_public_id=grant.public_id,
            target_key=f"support:access:{grant.public_id}",
            collapse_key=f"support:access:{user.id}",
            dedupe_key=f"support.access.granted:{grant.public_id}",
        )
        return Response(
            {
                "grant": {
                    "id": str(grant.public_id),
                    "user_email": user.email,
                    "ends_at": grant.ends_at,
                    "status": grant.status,
                }
            },
            status=status.HTTP_201_CREATED,
        )


def _staff_vacancy_or_not_found(*, user, vacancy_public_id):
    vacancy = get_object_or_404(
        SupportVacancy.objects.select_related("organization")
        .filter(
            organization__memberships__user=user,
            organization__memberships__state=OrganizationMembership.STATE_ACTIVE,
        )
        .distinct(),
        public_id=vacancy_public_id,
    )
    require_permission(
        user=user,
        organization=vacancy.organization,
        permission_code=ORGANIZATION_MANAGE,
    )
    return vacancy


class SupportVacancyCreateAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    def post(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        serializer = SupportVacancyCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        public_vacancy = None
        public_vacancy_id = serializer.validated_data.get("public_vacancy_id")
        if public_vacancy_id is not None:
            from jobs.models import Vacancy

            public_vacancy = get_object_or_404(Vacancy, pk=public_vacancy_id)
        vacancy = create_support_vacancy(
            actor=request.user,
            organization=organization,
            internal_title=serializer.validated_data["internal_title"],
            internal_position_limit=serializer.validated_data.get("internal_position_limit"),
            public_vacancy=public_vacancy,
        )
        return Response(
            {
                "vacancy": {
                    "id": str(vacancy.public_id),
                    "internal_title": vacancy.internal_title,
                    "status": vacancy.status,
                }
            },
            status=status.HTTP_201_CREATED,
        )


class BotContentRevisionCreateAPIView(SupportFeatureAPIView):
    def post(self, request, vacancy_public_id):
        vacancy = _staff_vacancy_or_not_found(
            user=request.user,
            vacancy_public_id=vacancy_public_id,
        )
        serializer = BotContentRevisionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        revision = create_bot_revision(
            actor=request.user,
            vacancy=vacancy,
            source_language=serializer.validated_data["source_language"],
            content=serializer.validated_data["content"],
        )
        return Response(
            {
                "bot_revision": {
                    "id": str(revision.public_id),
                    "version": revision.version,
                    "status": revision.status,
                }
            },
            status=status.HTTP_201_CREATED,
        )


class BotContentRevisionPublishAPIView(SupportFeatureAPIView):
    def post(self, request, revision_public_id):
        revision = get_object_or_404(
            BotContentRevision.objects.select_related("vacancy__organization")
            .filter(
                vacancy__organization__memberships__user=request.user,
                vacancy__organization__memberships__state=OrganizationMembership.STATE_ACTIVE,
            )
            .distinct(),
            public_id=revision_public_id,
        )
        # The service makes the final server-side permission check.  Returning
        # a normal denial here is acceptable because this is a staff-only ID.
        revision = publish_bot_revision(actor=request.user, revision=revision)
        return Response(
            {
                "bot_revision": {
                    "id": str(revision.public_id),
                    "version": revision.version,
                    "status": revision.status,
                    "published_at": revision.published_at,
                }
            }
        )


class SupportVacancyPublishAPIView(SupportFeatureAPIView):
    def post(self, request, vacancy_public_id):
        vacancy = _staff_vacancy_or_not_found(
            user=request.user,
            vacancy_public_id=vacancy_public_id,
        )
        vacancy = publish_support_vacancy(actor=request.user, vacancy=vacancy)
        return Response(
            {
                "vacancy": {
                    "id": str(vacancy.public_id),
                    "status": vacancy.status,
                    "published_at": vacancy.published_at,
                }
            }
        )


class SupportVacancyBotAPIView(SupportFeatureAPIView):
    def get(self, request, vacancy_public_id):
        language = request.query_params.get("language", BotContentRevision.LANGUAGE_RU).lower()
        if language not in {
            BotContentRevision.LANGUAGE_RU,
            BotContentRevision.LANGUAGE_EN,
            BotContentRevision.LANGUAGE_PL,
            BotContentRevision.LANGUAGE_UK,
        }:
            raise ValidationError({"language": "unsupported_support_language"})
        vacancy = get_object_or_404(
            SupportVacancy.objects.filter(
                status=SupportVacancy.STATUS_PUBLISHED,
                organization__status=SupportOrganization.STATUS_ACTIVE,
            ),
            public_id=vacancy_public_id,
        )
        revision = get_object_or_404(
            BotContentRevision.objects.filter(
                vacancy=vacancy,
                status=BotContentRevision.STATUS_PUBLISHED,
            ),
        )
        return Response(
            {
                "vacancy": {"id": str(vacancy.public_id)},
                "questionnaire_version": QUESTIONNAIRE_VERSION,
                "bot": {
                    "version": revision.version,
                    "language": language,
                    "content": revision.content[language],
                },
            }
        )


class PublicVacancySupportWorkflowAPIView(SupportFeatureAPIView):
    """Candidate-facing Support entry point for one public JobHub vacancy."""

    permission_classes = [permissions.AllowAny]

    def get(self, request, public_vacancy_id):
        language = request.query_params.get(
            "language", BotContentRevision.LANGUAGE_RU
        ).lower()
        if language not in {
            BotContentRevision.LANGUAGE_RU,
            BotContentRevision.LANGUAGE_EN,
            BotContentRevision.LANGUAGE_PL,
            BotContentRevision.LANGUAGE_UK,
        }:
            raise ValidationError({"language": "unsupported_support_language"})
        vacancy = get_object_or_404(
            SupportVacancy.objects.select_related(
                "organization", "public_vacancy"
            ).filter(
                public_vacancy_id=public_vacancy_id,
                status=SupportVacancy.STATUS_PUBLISHED,
                organization__status=SupportOrganization.STATUS_ACTIVE,
            )
        )
        revision = (
            BotContentRevision.objects.filter(
                vacancy=vacancy,
                status=BotContentRevision.STATUS_PUBLISHED,
            )
            .order_by("-version")
            .first()
        )
        application = None
        if request.user.is_authenticated:
            application = (
                SupportApplication.objects.filter(
                    vacancy=vacancy,
                    candidate=request.user,
                )
                .select_related(
                    "vacancy__organization",
                    "vacancy__public_vacancy",
                    "support_connection__organization",
                    "support_connection__vacancy",
                )
                .prefetch_related("decision_events")
                .order_by("-revision", "-submitted_at", "-id")
                .first()
            )
        return Response(
            {
                "workflow": {
                    "id": str(vacancy.public_id),
                    "organization": _organization_payload(vacancy.organization),
                    "vacancy_title": vacancy.public_vacancy.title,
                    "questionnaire_version": QUESTIONNAIRE_VERSION,
                },
                "bot": (
                    {
                        "version": revision.version,
                        "language": language,
                        "content": revision.content[language],
                    }
                    if revision is not None
                    else None
                ),
                "application": (
                    _candidate_application_payload(application)
                    if application is not None
                    else None
                ),
            }
        )


class SupportApplicationCreateAPIView(SupportFeatureAPIView):
    def post(self, request, vacancy_public_id):
        vacancy = get_object_or_404(
            SupportVacancy.objects.select_related("organization").filter(
                status=SupportVacancy.STATUS_PUBLISHED,
                organization__status=SupportOrganization.STATUS_ACTIVE,
            ),
            public_id=vacancy_public_id,
        )
        serializer = SupportApplicationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        application = submit_application(
            candidate=request.user,
            vacancy=vacancy,
            preferred_language=serializer.validated_data["preferred_language"],
            citizenship_country_code=serializer.validated_data["citizenship_country_code"],
            current_country_code=serializer.validated_data["current_country_code"],
            availability_note=serializer.validated_data["availability_note"],
            partner_reference_code=serializer.validated_data["partner_reference_code"],
            consent_version=serializer.validated_data["consent_version"],
            questionnaire_version=serializer.validated_data["questionnaire_version"],
            questionnaire_answers=serializer.validated_data["questionnaire"],
        )
        applicant_reference = request.user.support_applicant_reference
        return Response(
            {
                "application": _candidate_application_payload(application),
                "applicant_reference_code": applicant_reference.reference_code,
            },
            status=status.HTTP_201_CREATED,
        )


class MySupportApplicationListAPIView(SupportFeatureAPIView):
    def get(self, request):
        applications = (
            SupportApplication.objects.filter(candidate=request.user)
            .select_related(
                "vacancy__organization",
                "vacancy__public_vacancy",
                "support_connection__organization",
                "support_connection__vacancy",
            )
            .prefetch_related("decision_events")
            .order_by("-submitted_at", "-id")
        )
        return Response(
            {"results": [_candidate_application_payload(item) for item in applications]}
        )


class SupportApplicationQueueAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    def get(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        require_permission(
            user=request.user,
            organization=organization,
            permission_code=PIPELINE_REVIEW,
        )
        applications = (
            SupportApplication.objects.filter(vacancy__organization=organization)
            .select_related("vacancy", "candidate", "candidate__profile")
            .order_by("-submitted_at", "-id")
        )
        may_request_documents = has_permission(
            user=request.user,
            organization=organization,
            permission_code=DOCUMENT_REQUEST,
        )
        processing_connections = list(
            SupportConnection.objects.filter(
                organization=organization,
                is_archived=False,
                stage=SupportConnection.STAGE_DOCUMENTS,
            )
            .select_related(
                "candidate",
                "candidate__profile",
                "vacancy",
                "application__candidate",
                "application__candidate__profile",
                "application__vacancy",
            )
            .prefetch_related("stage_events", "document_request_packages__account_reference")
            .order_by("-updated_at", "-id")
        )
        processing_results = []
        for connection in processing_connections:
            processing_started_at = next(
                (
                    event.created_at
                    for event in reversed(list(connection.stage_events.all()))
                    if event.next_stage == SupportConnection.STAGE_DOCUMENTS
                ),
                connection.updated_at,
            )
            processing_results.append(
                {
                    "connection_id": str(connection.public_id),
                    "stage": connection.stage,
                    "processing_started_at": processing_started_at,
                    "updated_at": connection.updated_at,
                    "candidate": {
                        "id": str(connection.candidate_id),
                        **_user_identity_payload(connection.candidate),
                    },
                    "vacancy": {
                        "id": str(connection.vacancy.public_id),
                        "internal_title": connection.vacancy.internal_title,
                    },
                    "application": _application_payload(
                        connection.application,
                        include_staff_fields=True,
                    ),
                    "document_packages": (
                        [
                            _document_request_package_payload(
                                package,
                                include_staff_fields=False,
                            )
                            for package in connection.document_request_packages.all()
                        ]
                        if may_request_documents
                        else []
                    ),
                }
            )
        return Response(
            {
                "results": [
                    _application_payload(item, include_staff_fields=True)
                    for item in applications
                ],
                "processing_results": processing_results,
                "counts": _candidate_queue_counts(organization),
                "permissions": {"document_request": may_request_documents},
            }
        )


class SupportApplicationClarificationAPIView(SupportFeatureAPIView):
    def post(self, request, application_public_id):
        application = _staff_application_or_not_found(
            user=request.user,
            application_public_id=application_public_id,
        )
        serializer = ApplicationReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        application = request_application_clarification(
            actor=request.user,
            application=application,
            note=serializer.validated_data["note"],
        )
        return Response({"application": _application_payload(application, include_staff_fields=True)})


class MySupportApplicationClarificationResponseAPIView(SupportFeatureAPIView):
    def post(self, request, application_public_id):
        application = get_object_or_404(
            SupportApplication.objects.select_related(
                "vacancy__organization", "candidate"
            ),
            public_id=application_public_id,
            candidate=request.user,
        )
        serializer = ApplicationClarificationResponseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        application = answer_application_clarification(
            candidate=request.user,
            application=application,
            answer=serializer.validated_data["answer"],
        )
        return Response({"application": _candidate_application_payload(application)})


class SupportApplicationApproveAPIView(SupportFeatureAPIView):
    def post(self, request, application_public_id):
        application = _staff_application_or_not_found(
            user=request.user,
            application_public_id=application_public_id,
        )
        connection = approve_application(actor=request.user, application=application)
        return Response(
            {"connection": _connection_payload(connection)},
            status=status.HTTP_201_CREATED,
        )


class SupportApplicationDeclineAPIView(SupportFeatureAPIView):
    def post(self, request, application_public_id):
        application = _staff_application_or_not_found(
            user=request.user,
            application_public_id=application_public_id,
        )
        serializer = ApplicationReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        application = decline_application(
            actor=request.user,
            application=application,
            note=serializer.validated_data["note"],
        )
        return Response({"application": _application_payload(application, include_staff_fields=True)})


class MySupportConnectionListAPIView(SupportFeatureAPIView):
    def get(self, request):
        connections = (
            SupportConnection.objects.filter(candidate=request.user)
            .select_related("organization", "vacancy")
            .order_by("-updated_at", "-id")
        )
        return Response({"results": [_connection_payload(item) for item in connections]})


class SupportConnectionOpenManagerConversationAPIView(SupportFeatureAPIView):
    def post(self, request, connection_public_id):
        connection = get_object_or_404(
            SupportConnection.objects.select_related("organization"),
            public_id=connection_public_id,
            candidate=request.user,
            is_archived=False,
        )
        conversation, created = open_manager_conversation(
            candidate=request.user,
            connection=connection,
        )
        conversation = SupportConversation.objects.prefetch_related(
            "members__user__profile"
        ).get(pk=conversation.pk)
        return Response(
            {"created": created, "conversation": _conversation_payload(conversation, viewer=request.user)},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class SupportConnectionTransitionAPIView(SupportFeatureAPIView):
    def post(self, request, connection_public_id):
        connection = get_object_or_404(
            SupportConnection.objects.select_related("organization")
            .filter(
                organization__memberships__user=request.user,
                organization__memberships__state=OrganizationMembership.STATE_ACTIVE,
            )
            .distinct(),
            public_id=connection_public_id,
        )
        serializer = ConnectionTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        connection = transition_connection(
            actor=request.user,
            connection=connection,
            next_stage=serializer.validated_data["next_stage"],
            reason=serializer.validated_data["reason"],
        )
        return Response({"connection": _connection_payload(connection)})


class MySupportConversationListAPIView(SupportFeatureAPIView):
    def get(self, request):
        organization_public_id = (request.query_params.get("organization") or "").strip()
        organization_filter = {}
        if organization_public_id:
            organization = get_object_or_404(
                SupportOrganization,
                public_id=organization_public_id,
            )
            has_staff_access = (
                active_membership_for(
                    user=request.user,
                    organization=organization,
                )
                is not None
            )
            has_worker_access = SupportConnection.objects.filter(
                candidate=request.user,
                organization=organization,
                is_archived=False,
            ).exists()
            if not (has_staff_access or has_worker_access):
                raise NotFound("support_organization_not_found")
            organization_filter = {"organization": organization}
        conversations = (
            SupportConversation.objects.filter(
                members__user=request.user,
                members__left_at__isnull=True,
                state=SupportConversation.STATE_ACTIVE,
                **organization_filter,
            )
            .select_related("connection", "organization")
            .prefetch_related("members__user__profile")
            .distinct()
            .order_by("-updated_at", "-id")
        )
        visible = []
        for conversation in conversations:
            try:
                require_conversation_access(user=request.user, conversation=conversation)
            except PermissionDenied:
                continue
            visible.append(_conversation_payload(conversation, viewer=request.user))
        # Read state changes only the badge and date styling.  It must never
        # reorder the directory: a newly sent or received message always moves
        # its conversation to the top, while merely opening a chat does not.
        visible.sort(
            key=lambda item: item["last_message_at"] or item["updated_at"],
            reverse=True,
        )
        return Response({"results": visible})


class SupportConversationGroupPushPreferenceAPIView(SupportFeatureAPIView):
    def post(self, request, conversation_public_id):
        serializer = GroupPushPreferenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        conversation = get_object_or_404(
            SupportConversation.objects.filter(
                public_id=conversation_public_id,
                state=SupportConversation.STATE_ACTIVE,
                members__user=request.user,
                members__left_at__isnull=True,
            )
            .prefetch_related("members__user__profile")
            .distinct()
        )
        if conversation.kind != SupportConversation.KIND_GROUP:
            raise ValidationError({"conversation": "group_push_preference_requires_group"})
        member = get_object_or_404(
            SupportConversationMember,
            conversation=conversation,
            user=request.user,
            left_at__isnull=True,
        )
        member.group_push_enabled = serializer.validated_data["enabled"]
        member.save(update_fields=["group_push_enabled"])
        return Response({"conversation": _conversation_payload(conversation, viewer=request.user)})


class SupportConversationMessageListAPIView(SupportFeatureAPIView):
    def get(self, request, conversation_public_id):
        conversation = get_object_or_404(
            SupportConversation.objects.filter(
                members__user=request.user,
                members__left_at__isnull=True,
            )
            .select_related("organization", "connection")
            .distinct(),
            public_id=conversation_public_id,
        )
        require_conversation_access(user=request.user, conversation=conversation)
        messages = conversation.messages.select_related(
            "sender",
            "sender__profile",
            "forwarded_from__sender",
            "forwarded_from__sender__profile",
            "reply_to__sender",
            "reply_to__sender__profile",
            "shared_contact_user",
            "shared_contact_user__profile",
            "shared_contact_connection__vacancy",
            "shared_contact_membership",
        ).all()
        return Response(
            {
                "conversation": _conversation_payload(conversation, viewer=request.user),
                "messages": [_message_payload(item, viewer=request.user) for item in messages],
            }
        )


class SupportConversationMessageCreateAPIView(SupportFeatureAPIView):
    def post(self, request, conversation_public_id):
        conversation = get_object_or_404(
            SupportConversation.objects.filter(
                members__user=request.user,
                members__left_at__isnull=True,
            )
            .select_related("organization")
            .distinct(),
            public_id=conversation_public_id,
        )
        serializer = SupportMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reply_to = None
        reply_to_message_id = serializer.validated_data.get(
            "reply_to_message_id"
        )
        if reply_to_message_id:
            reply_to = get_object_or_404(
                SupportMessage.objects.select_related("sender", "sender__profile"),
                conversation=conversation,
                public_id=reply_to_message_id,
                deleted_at__isnull=True,
            )
        message, created = send_text_message(
            sender=request.user,
            conversation=conversation,
            body=serializer.validated_data["body"],
            original_language=serializer.validated_data["original_language"],
            client_message_id=serializer.validated_data["client_message_id"],
            reply_to=reply_to,
        )
        return Response(
            {"created": created, "message": _message_payload(message, viewer=request.user)},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class SupportMessageForwardAPIView(SupportFeatureAPIView):
    def post(self, request, conversation_public_id, message_public_id):
        source_conversation = get_object_or_404(
            SupportConversation.objects.filter(
                members__user=request.user,
                members__left_at__isnull=True,
            )
            .select_related("organization")
            .distinct(),
            public_id=conversation_public_id,
            state=SupportConversation.STATE_ACTIVE,
        )
        source_message = get_object_or_404(
            SupportMessage.objects.select_related("conversation", "sender"),
            conversation=source_conversation,
            public_id=message_public_id,
            kind=SupportMessage.KIND_TEXT,
            deleted_at__isnull=True,
        )
        serializer = SupportMessageForwardSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target_conversation = get_object_or_404(
            SupportConversation.objects.filter(
                members__user=request.user,
                members__left_at__isnull=True,
            )
            .select_related("organization")
            .distinct(),
            public_id=serializer.validated_data["target_conversation_id"],
            state=SupportConversation.STATE_ACTIVE,
        )
        message, created = forward_text_message_to_existing_conversation(
            sender=request.user,
            source_message=source_message,
            target_conversation=target_conversation,
            client_message_id=serializer.validated_data["client_message_id"],
        )
        message = SupportMessage.objects.select_related(
            "sender",
            "sender__profile",
            "forwarded_from__sender",
            "forwarded_from__sender__profile",
        ).get(pk=message.pk)
        return Response(
            {
                "created": created,
                "conversation": _conversation_payload(
                    target_conversation,
                    viewer=request.user,
                ),
                "message": _message_payload(message, viewer=request.user),
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class SupportConversationContactOptionsAPIView(SupportFeatureAPIView):
    def get(self, request, conversation_public_id):
        conversation = get_object_or_404(
            SupportConversation.objects.filter(
                members__user=request.user,
                members__left_at__isnull=True,
            )
            .select_related("organization")
            .distinct(),
            public_id=conversation_public_id,
            state=SupportConversation.STATE_ACTIVE,
        )
        return Response(
            {
                "results": contact_share_options(
                    sender=request.user,
                    conversation=conversation,
                )
            }
        )


class SupportConversationContactMessageCreateAPIView(SupportFeatureAPIView):
    def post(self, request, conversation_public_id):
        conversation = get_object_or_404(
            SupportConversation.objects.filter(
                members__user=request.user,
                members__left_at__isnull=True,
            )
            .select_related("organization")
            .distinct(),
            public_id=conversation_public_id,
            state=SupportConversation.STATE_ACTIVE,
        )
        serializer = SupportContactMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message, created = send_contact_message(
            sender=request.user,
            conversation=conversation,
            **serializer.validated_data,
        )
        message = SupportMessage.objects.select_related(
            "sender",
            "sender__profile",
            "shared_contact_user",
            "shared_contact_user__profile",
            "shared_contact_connection__vacancy",
            "shared_contact_membership",
        ).get(pk=message.pk)
        return Response(
            {"created": created, "message": _message_payload(message, viewer=request.user)},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class SupportMessageSharedContactOpenAPIView(SupportFeatureAPIView):
    def post(self, request, conversation_public_id, message_public_id):
        conversation = get_object_or_404(
            SupportConversation.objects.filter(
                members__user=request.user,
                members__left_at__isnull=True,
            )
            .select_related("organization")
            .distinct(),
            public_id=conversation_public_id,
            state=SupportConversation.STATE_ACTIVE,
        )
        message = get_object_or_404(
            SupportMessage.objects.select_related(
                "conversation__organization",
                "shared_contact_user",
                "shared_contact_connection__candidate",
                "shared_contact_membership__user",
            ),
            conversation=conversation,
            public_id=message_public_id,
            deleted_at__isnull=True,
        )
        target_conversation, created = open_shared_contact_conversation(
            actor=request.user,
            message=message,
        )
        target_conversation = SupportConversation.objects.prefetch_related(
            "members__user__profile"
        ).get(pk=target_conversation.pk)
        return Response(
            {
                "created": created,
                "conversation": _conversation_payload(
                    target_conversation,
                    viewer=request.user,
                ),
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class SupportConversationReadAPIView(SupportFeatureAPIView):
    def post(self, request, conversation_public_id):
        conversation = get_object_or_404(
            SupportConversation.objects.filter(
                members__user=request.user,
                members__left_at__isnull=True,
            )
            .select_related("organization")
            .distinct(),
            public_id=conversation_public_id,
        )
        member = mark_conversation_read(user=request.user, conversation=conversation)
        return Response(
            {
                "last_read_at": member.last_read_at,
                "notification_target": f"support:conversation:{conversation.public_id}",
            }
        )


class SupportMessageTranslationAPIView(SupportFeatureAPIView):
    """Return a requested translation without ever replacing the original."""

    def post(self, request, conversation_public_id, message_public_id, target_language):
        conversation = get_object_or_404(
            SupportConversation.objects.filter(
                members__user=request.user,
                members__left_at__isnull=True,
            )
            .select_related("organization", "connection")
            .distinct(),
            public_id=conversation_public_id,
        )
        require_conversation_access(user=request.user, conversation=conversation)
        message = get_object_or_404(
            SupportMessage.objects.filter(conversation=conversation, deleted_at__isnull=True),
            public_id=message_public_id,
        )
        try:
            payload = request_message_translation(
                message=message,
                requested_by=request.user,
                target_language=target_language,
            )
        except ValueError as exc:
            raise ValidationError({"target_language": str(exc)})
        except TranslationProviderNotConfigured:
            return Response(
                {"detail": "translation_provider_not_configured"},
                status=status.HTTP_409_CONFLICT,
            )
        return Response({"message_id": str(message.public_id), "translation": payload})


def _document_package_id_from_notification(notification):
    if not notification.outbox.notification_code.startswith("documents."):
        return None
    parts = notification.outbox.dedupe_key.split(":", 2)
    if len(parts) < 3 or parts[0] != "document-package":
        return None
    try:
        return uuid.UUID(parts[1])
    except (TypeError, ValueError):
        return None


def _document_notification_requires_action(notification):
    package_public_id = _document_package_id_from_notification(notification)
    if package_public_id is None:
        return False
    return DocumentRequestPackage.objects.filter(
        public_id=package_public_id,
        connection__candidate=notification.recipient,
        status__in=(
            DocumentRequestPackage.STATUS_REQUESTED,
            DocumentRequestPackage.STATUS_NEEDS_CORRECTION,
        ),
    ).exists()


def _in_app_notification_payload(notification, *, requires_action=False):
    outbox = notification.outbox
    return {
        "id": str(notification.public_id),
        "code": outbox.notification_code,
        "category": _notification_category(outbox.notification_code),
        "target": {
            "kind": outbox.target_kind,
            "id": str(outbox.target_public_id),
            "key": outbox.target_key,
        },
        "read_at": notification.read_at,
        "requires_action": requires_action,
        "created_at": notification.created_at,
    }


def _notification_category(code):
    prefix = (code or "").partition(".")[0]
    return {
        "conversation": "chat",
        "work": "work",
        "housing": "housing",
        "transport": "transport",
        "documents": "documents",
        "worker_request": "requests",
        "worker_task": "tasks",
        "announcement": "announcements",
        "schedule": "schedule",
        "time": "time",
        "application": "applications",
        "connection": "access",
        "support": "access",
    }.get(prefix, "other")


class MySupportNotificationListAPIView(SupportFeatureAPIView):
    def get(self, request):
        queryset = InAppNotification.objects.filter(recipient=request.user).select_related(
            "outbox"
        )
        include_chat = (request.query_params.get("include_chat") or "1").strip().lower()
        if include_chat in {"0", "false", "no"}:
            queryset = queryset.exclude(outbox__notification_code="conversation.message")

        active_document_package_ids = set(
            DocumentRequestPackage.objects.filter(
                connection__candidate=request.user,
                status__in=(
                    DocumentRequestPackage.STATUS_REQUESTED,
                    DocumentRequestPackage.STATUS_NEEDS_CORRECTION,
                ),
            ).values_list("public_id", flat=True)
        )
        active_document_notification_ids = set()
        remaining_document_package_ids = set(active_document_package_ids)
        if remaining_document_package_ids:
            document_notifications = queryset.filter(
                outbox__notification_code__startswith="documents."
            ).order_by("-created_at", "-id")
            for item in document_notifications:
                package_public_id = _document_package_id_from_notification(item)
                if package_public_id in remaining_document_package_ids:
                    active_document_notification_ids.add(item.id)
                    remaining_document_package_ids.remove(package_public_id)
                    if not remaining_document_package_ids:
                        break

        unread_counts = {}
        for unread_notification in queryset.filter(read_at__isnull=True):
            code = unread_notification.outbox.notification_code
            if code.startswith("documents.") and (
                _document_package_id_from_notification(unread_notification) is not None
            ):
                continue
            category = _notification_category(code)
            unread_counts[category] = unread_counts.get(category, 0) + 1
        if active_document_package_ids:
            unread_counts["documents"] = (
                unread_counts.get("documents", 0) + len(active_document_package_ids)
            )

        missing_time_actions = worker_missing_time_entry_actions(
            now=timezone.now(),
            user=request.user,
        )
        if missing_time_actions:
            unread_counts["time"] = (
                unread_counts.get("time", 0) + len(missing_time_actions)
            )

        notifications = queryset.order_by("-created_at", "-id")[:100]
        results = [
            _in_app_notification_payload(
                item,
                requires_action=item.id in active_document_notification_ids,
            )
            for item in notifications
        ]
        results.extend(
            {
                "id": (
                    f"time-missing:{item['connection_public_id']}:"
                    f"{item['work_date'].isoformat()}"
                ),
                "code": "time.missing",
                "category": "time",
                "target": {
                    "kind": "connection",
                    "id": str(item["connection_public_id"]),
                    "key": (
                        f"support:time-missing:{item['connection_public_id']}:"
                        f"{item['work_date'].isoformat()}"
                    ),
                },
                "read_at": None,
                "requires_action": True,
                "created_at": item["available_at"],
                "work_date": item["work_date"].isoformat(),
            }
            for item in missing_time_actions
        )
        results.sort(key=lambda item: item["created_at"], reverse=True)
        return Response(
            {
                "results": results[:100],
                "unread_count": sum(unread_counts.values()),
                "unread_counts": unread_counts,
                "action_counts": {
                    "documents": len(active_document_package_ids),
                    "time": len(missing_time_actions),
                },
            }
        )


class SupportNotificationReadAPIView(SupportFeatureAPIView):
    def post(self, request, notification_public_id):
        notification = get_object_or_404(
            InAppNotification.objects.select_related("outbox"),
            public_id=notification_public_id,
            recipient=request.user,
        )
        requires_action = _document_notification_requires_action(notification)
        if notification.read_at is None and not requires_action:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at"])
        return Response(
            {
                "notification": _in_app_notification_payload(
                    notification,
                    requires_action=requires_action,
                )
            }
        )


def _operation_organization(view, *, request, organization_public_id, permission_code):
    organization, _ = view.get_organization(
        user=request.user,
        organization_public_id=organization_public_id,
    )
    require_permission(
        user=request.user,
        organization=organization,
        permission_code=permission_code,
    )
    return organization


def _operational_object_or_not_found(*, model, user, public_id):
    """Do not make cross-organization operational UUIDs an existence oracle."""

    return get_object_or_404(
        model.objects.filter(
            organization__memberships__user=user,
            organization__memberships__state=OrganizationMembership.STATE_ACTIVE,
        ).distinct(),
        public_id=public_id,
    )


def _housing_site_payload(site):
    return {
        "id": str(site.public_id),
        "internal_name": site.internal_name,
        "country_code": site.country_code,
        "city": site.city,
        "postal_code": site.postal_code,
        "street": site.street,
        "building": site.building,
        "is_active": site.is_active,
    }


def _housing_assignment_detail_payload(assignment, *, may_view_worker=True):
    return {
        "id": str(assignment.public_id),
        "state": assignment.state,
        "check_in_at": assignment.check_in_at,
        "check_out_at": assignment.check_out_at,
        "worker": (
            {
                "connection_id": str(assignment.connection.public_id),
                **_user_identity_payload(assignment.connection.candidate),
            }
            if may_view_worker
            else None
        ),
        "worker_restricted": not may_view_worker,
    }


def _housing_site_workspace_payload(
    site,
    *,
    rooms,
    places_by_room,
    assignments_by_place,
    visible_connection_ids,
):
    payload = _housing_site_payload(site)
    payload["rooms"] = [
        {
            "id": str(room.public_id),
            "label": room.label,
            "capacity": room.capacity,
            "is_active": room.is_active,
            "places": [
                {
                    "id": str(place.public_id),
                    "label": place.label,
                    "is_active": place.is_active,
                    "assignments": [
                        _housing_assignment_detail_payload(
                            assignment,
                            may_view_worker=(
                                assignment.connection_id in visible_connection_ids
                            ),
                        )
                        for assignment in assignments_by_place.get(place.id, [])
                    ],
                }
                for place in places_by_room.get(room.id, [])
            ],
        }
        for room in rooms
    ]
    return payload


def _worksite_payload(worksite):
    return {
        "id": str(worksite.public_id),
        "internal_name": worksite.internal_name,
        "country_code": worksite.country_code,
        "city": worksite.city,
        "postal_code": worksite.postal_code,
        "street": worksite.street,
        "building": worksite.building,
        "is_active": worksite.is_active,
    }


def _operation_assignment_payload(assignment):
    connection = getattr(assignment, "connection", None)
    if connection is None:
        connection = getattr(assignment, "driver_connection", None)
    return {
        "id": str(assignment.public_id),
        "connection_id": str(connection.public_id),
        "state": assignment.state,
        "created_at": assignment.created_at,
        "published_at": assignment.published_at,
    }


def _duration_label(minutes):
    return f"{minutes // 60}:{minutes % 60:02d}"


def _scheduled_shift_payload(shift):
    if shift is None:
        return None
    return {
        "id": str(shift.public_id),
        "connection_id": str(shift.connection.public_id),
        "work_date": shift.work_date,
        "starts_at": shift.starts_at,
        "ends_at": shift.ends_at,
        "break_minutes": shift.break_minutes,
        "worker_label": shift.worker_label,
        "state": shift.state,
        "work_assignment_id": str(shift.work_assignment.public_id)
        if shift.work_assignment_id
        else None,
        "published_at": shift.published_at,
    }


def _shift_template_payload(template):
    return {
        "id": str(template.public_id),
        "name": template.name,
        "starts_at_time": template.starts_at_time,
        "ends_at_time": template.ends_at_time,
        "break_minutes": template.break_minutes,
        "worker_label": template.worker_label,
        "is_active": template.is_active,
    }


def _calendar_mark_template_payload(template):
    return {
        "id": str(template.public_id),
        "name": template.name,
        "request_type": template.request_type,
        "is_active": template.is_active,
    }


def _calendar_mark_batch_payload(batch, *, items=None):
    batch_items = list(items) if items is not None else list(batch.items.all())
    return {
        "id": str(batch.public_id),
        "state": batch.state,
        "template": _calendar_mark_template_payload(batch.template)
        if batch.template_id
        else None,
        "request_count": len(batch_items),
        "requests": [
            _calendar_mark_payload(item.request, include_staff_fields=True)
            for item in batch_items
        ],
        "published_at": batch.published_at,
        "cancelled_at": batch.cancelled_at,
    }


def _scheduled_shift_batch_payload(batch, *, shifts=None):
    batch_shifts = list(shifts) if shifts is not None else list(batch.shifts.all())
    workers = []
    seen_connection_ids = set()
    for item in batch_shifts:
        if item.connection_id in seen_connection_ids:
            continue
        seen_connection_ids.add(item.connection_id)
        workers.append(
            {
                "id": str(item.connection.public_id),
                **_user_identity_payload(item.connection.candidate),
            }
        )
    return {
        "id": str(batch.public_id),
        "state": batch.state,
        "template": _shift_template_payload(batch.template)
        if batch.template_id
        else None,
        "starts_on": batch.starts_on,
        "ends_on": batch.ends_on,
        "weekdays": batch.weekdays,
        "shift_count": len(batch_shifts),
        "workers": workers,
        "published_at": batch.published_at,
        "cancelled_at": batch.cancelled_at,
    }


def _time_entry_payload(entry, *, include_staff_fields=False):
    payload = {
        "id": str(entry.public_id),
        "connection_id": str(entry.connection.public_id),
        "scheduled_shift_id": str(entry.scheduled_shift.public_id)
        if entry.scheduled_shift_id
        else None,
        "work_date": entry.work_date,
        "started_at": entry.started_at,
        "ended_at": entry.ended_at,
        "break_minutes": entry.break_minutes,
        "worked_minutes": entry.worked_minutes,
        "worked_duration": _duration_label(entry.worked_minutes),
        "decimal_hours": str(entry.decimal_hours),
        "status": entry.status,
        "revision": entry.revision,
        "manager_note": entry.manager_note,
        "submitted_at": entry.submitted_at,
        "confirmed_at": entry.confirmed_at,
        "worker_acknowledged_at": entry.worker_acknowledged_at,
    }
    if include_staff_fields:
        payload["worker"] = {
            "id": str(entry.connection.candidate_id),
            **_user_identity_payload(entry.connection.candidate),
        }
        payload["vacancy"] = {
            "id": str(entry.connection.vacancy.public_id),
            "title": entry.connection.vacancy.internal_title,
        }
        payload["confirmed_by"] = (
            _user_display_name(entry.confirmed_by) if entry.confirmed_by_id else None
        )
    return payload


def _worker_request_payload(item, *, include_staff_fields=False):
    extra_shift_dates = getattr(item, "extra_shift_dates_for_payload", None)
    if extra_shift_dates is None and item.is_extra_shift:
        extra_shift_dates = list(
            item.extra_shift_dates.select_related("decided_by").order_by(
                "work_date", "id"
            )
        )
    payload = {
        "id": str(item.public_id),
        "connection_id": str(item.connection.public_id),
        "request_type": item.request_type,
        "status": item.status,
        "starts_on": item.starts_on,
        "ends_on": item.ends_on,
        "worker_note": item.worker_note,
        "manager_note": item.manager_note,
        "is_urgent": item.is_urgent,
        "is_extra_shift": item.is_extra_shift,
        "requested_dates": [
            {
                "id": str(date_item.public_id),
                "date": date_item.work_date,
                "status": date_item.status,
                "manager_note": date_item.manager_note,
                "decided_at": date_item.decided_at,
            }
            for date_item in (extra_shift_dates or ())
        ],
        "submitted_at": item.submitted_at,
        "reviewed_at": item.reviewed_at,
        "cancelled_at": item.cancelled_at,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
    if include_staff_fields:
        payload["worker"] = {
            "id": str(item.connection.candidate_id),
            **_user_identity_payload(item.connection.candidate),
        }
        payload["vacancy"] = {
            "id": str(item.connection.vacancy.public_id),
            "title": item.connection.vacancy.internal_title,
        }
        payload["reviewed_by"] = (
            _user_display_name(item.reviewed_by) if item.reviewed_by_id else None
        )
    return payload


def _calendar_mark_payload(item, *, include_staff_fields=False):
    """Return the non-sensitive calendar projection of an approved request."""

    payload = {
        "request_id": str(item.public_id),
        "connection_id": str(item.connection.public_id),
        "request_type": item.request_type,
        "starts_on": item.starts_on,
        "ends_on": item.ends_on,
    }
    if include_staff_fields:
        payload["worker"] = {
            "id": str(item.connection.candidate_id),
            **_user_identity_payload(item.connection.candidate),
        }
    return payload


def _document_request_package_payload(item, *, include_staff_fields=False):
    payload = {
        "id": str(item.public_id),
        "connection_id": str(item.connection.public_id),
        "recipient_email": item.recipient_email,
        "account_reference_code": item.account_reference.reference_code,
        "requested_items": item.requested_items,
        "additional_instructions": item.additional_instructions,
        "manager_note": item.manager_note,
        "status": item.status,
        "sent_marked_at": item.sent_marked_at,
        "reviewed_at": item.reviewed_at,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
    if include_staff_fields:
        payload["worker"] = {
            "id": str(item.connection.candidate_id),
            **_user_identity_payload(item.connection.candidate),
        }
        payload["created_by"] = (
            _user_display_name(item.created_by) if item.created_by_id else None
        )
        payload["reviewed_by"] = (
            _user_display_name(item.reviewed_by) if item.reviewed_by_id else None
        )
    return payload


def _task_assignment_payload(item, *, include_staff_fields=False):
    task = item.task
    payload = {
        "id": str(item.public_id),
        "connection_id": str(item.connection.public_id),
        "task_id": str(task.public_id),
        "title": task.title,
        "instructions": task.instructions,
        "translations": task.translations,
        "original_language": task.original_language,
        "priority": task.priority,
        "context_kind": task.context_kind,
        "due_at": task.due_at,
        "task_state": task.state,
        "status": item.status,
        "worker_note": item.worker_note,
        "manager_note": item.manager_note,
        "completed_at": item.completed_at,
        "confirmed_at": item.confirmed_at,
        "returned_at": item.returned_at,
        "cancelled_at": item.cancelled_at,
        "published_at": task.published_at,
        "responsible_name": (
            _user_display_name(task.responsible_membership.user)
            if task.responsible_membership_id and task.responsible_membership.user_id
            else None
        ),
    }
    if include_staff_fields:
        payload["worker"] = {
            "id": str(item.connection.candidate_id),
            **_user_identity_payload(item.connection.candidate),
        }
        payload["vacancy"] = {
            "id": str(item.connection.vacancy.public_id),
            "title": item.connection.vacancy.internal_title,
        }
    return payload


def _announcement_payload(item, *, include_staff_fields=False):
    announcement = item.announcement if isinstance(item, AnnouncementAcknowledgement) else item
    payload = {
        "id": str(announcement.public_id),
        "recipient_id": str(item.public_id)
        if isinstance(item, AnnouncementAcknowledgement)
        else None,
        "title": announcement.title,
        "body": announcement.body,
        "translations": announcement.translations,
        "original_language": announcement.original_language,
        "importance": announcement.importance,
        "requires_acknowledgement": announcement.requires_acknowledgement,
        "acknowledged_at": item.acknowledged_at
        if isinstance(item, AnnouncementAcknowledgement)
        else None,
        "expires_at": announcement.expires_at,
        "state": announcement.state,
        "published_at": announcement.published_at,
    }
    if include_staff_fields:
        payload["recipient_count"] = announcement.acknowledgements.count()
    return payload


def _content_template_payload(item):
    """Staff-only wording.  No worker or delivery data is exposed here."""

    return {
        "id": str(item.public_id),
        "name": item.name,
        "kind": item.kind,
        "source_language": item.source_language,
        "translations": item.translations,
        "is_active": item.is_active,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _worker_time_connection_or_not_found(*, user, connection_public_id):
    return get_object_or_404(
        SupportConnection.objects.select_related("organization", "vacancy"),
        public_id=connection_public_id,
        candidate=user,
        is_archived=False,
    )


def _worker_operation_summary_payload(connection):
    """Only published, current facts for this one worker connection."""

    now = timezone.now()
    today = timezone.localdate()
    housing = (
        HousingAssignment.objects.filter(
            connection=connection,
            state=HousingAssignment.STATE_PUBLISHED,
            check_in_at__lte=now,
        )
        .filter(
            Q(check_out_at__isnull=True) | Q(check_out_at__gt=now)
        )
        .select_related("place__room__site")
        .order_by("-check_in_at", "-id")
        .first()
    )
    work = (
        WorkerProjectAssignment.objects.filter(
            connection=connection,
            state=WorkerProjectAssignment.STATE_PUBLISHED,
            starts_at__lte=now,
        )
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
        .select_related("project__worksite")
        .order_by("-starts_at", "-id")
        .first()
    )
    passenger = (
        TransportPassengerAssignment.objects.filter(
            connection=connection,
            route__state=TransportRoute.STATE_PUBLISHED,
            route__starts_on__lte=today,
        )
        .filter(Q(route__ends_on__isnull=True) | Q(route__ends_on__gte=today))
        .select_related("route__driver_vehicle_assignment__vehicle", "pickup_stop", "dropoff_stop")
        .order_by("-route__starts_on", "-id")
        .first()
    )
    driver = (
        DriverVehicleAssignment.objects.filter(
            driver_connection=connection,
            state=DriverVehicleAssignment.STATE_PUBLISHED,
            starts_on__lte=today,
        )
        .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=today))
        .select_related("vehicle")
        .order_by("-starts_on", "-id")
        .first()
    )
    return {
        "connection_id": str(connection.public_id),
        "housing": None if housing is None else {
            "id": str(housing.public_id),
            "address": ", ".join(filter(None, [housing.place.room.site.street, housing.place.room.site.building, housing.place.room.site.postal_code, housing.place.room.site.city])),
            "room": housing.place.room.label,
            "place": housing.place.label,
            "rules_text": housing.place.room.site.rules_text,
            "contact_name": housing.place.room.site.contact_name,
            "contact_phone": housing.place.room.site.contact_phone,
            "check_in_at": housing.check_in_at,
        },
        "work": None if work is None else {
            "id": str(work.public_id),
            "name": work.project.worker_visible_name,
            "role": work.worker_role,
            "address": ", ".join(filter(None, [work.project.worksite.street, work.project.worksite.building, work.project.worksite.postal_code, work.project.worksite.city])),
            "instructions": work.project.instructions or work.project.worksite.instructions,
        },
        "transport": None if passenger is None else {
            "route_id": str(passenger.route.public_id),
            "route_name": passenger.route.internal_name,
            "pickup": passenger.pickup_stop.label,
            "dropoff": passenger.dropoff_stop.label,
            "departure_time": passenger.route.departure_time,
            "vehicle": passenger.route.driver_vehicle_assignment.vehicle.registration_identifier,
        },
        "driver": None if driver is None else {
            "vehicle": driver.vehicle.registration_identifier,
            "seat_capacity": driver.vehicle.seat_capacity,
        },
    }


def _worker_driver_manifest_payload(connection):
    """A driver receives only passengers assigned to routes of their vehicle."""

    today = timezone.localdate()
    routes = list(
        TransportRoute.objects.filter(
            driver_vehicle_assignment__driver_connection=connection,
            driver_vehicle_assignment__state=DriverVehicleAssignment.STATE_PUBLISHED,
            state=TransportRoute.STATE_PUBLISHED,
            starts_on__lte=today,
        )
        .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=today))
        .select_related("driver_vehicle_assignment__vehicle", "worksite")
        .prefetch_related(
            "passenger_assignments__connection__candidate__profile",
            "passenger_assignments__pickup_stop",
            "passenger_assignments__dropoff_stop",
        )
        .order_by("starts_on", "departure_time", "id")
    )
    return {
        "connection_id": str(connection.public_id),
        "results": [
            {
                "id": str(route.public_id),
                "route_name": route.internal_name,
                "vehicle": route.driver_vehicle_assignment.vehicle.registration_identifier,
                "seat_capacity": route.driver_vehicle_assignment.vehicle.seat_capacity,
                "departure_time": route.departure_time,
                "worksite": route.worksite.internal_name if route.worksite_id else "",
                "passengers": [
                    {
                        "name": _user_display_name(item.connection.candidate),
                        "avatar_url": _user_identity_payload(
                            item.connection.candidate
                        )["avatar_url"],
                        "pickup": item.pickup_stop.label,
                        "dropoff": item.dropoff_stop.label,
                        "boarding_order": item.boarding_order,
                    }
                    for item in route.passenger_assignments.all()
                ],
            }
            for route in routes
        ],
    }


def _staff_time_entry_or_not_found(*, user, entry_public_id, permission_code):
    entry = get_object_or_404(
        WorkTimeEntry.objects.select_related(
            "organization",
            "connection__candidate",
            "connection__vacancy",
            "scheduled_shift",
            "confirmed_by",
        )
        .filter(
            organization__memberships__user=user,
            organization__memberships__state=OrganizationMembership.STATE_ACTIVE,
        )
        .distinct(),
        public_id=entry_public_id,
    )
    require_permission(
        user=user,
        organization=entry.organization,
        permission_code=permission_code,
    )
    require_worker_connection_access(
        user=user,
        organization=entry.organization,
        connection=entry.connection,
    )
    return entry


def _staff_worker_request_or_not_found(*, user, request_public_id):
    item = get_object_or_404(
        WorkerRequest.objects.select_related(
            "organization",
            "connection__candidate",
            "connection__vacancy",
            "reviewed_by",
        )
        .filter(
            organization__memberships__user=user,
            organization__memberships__state=OrganizationMembership.STATE_ACTIVE,
        )
        .distinct(),
        public_id=request_public_id,
    )
    require_permission(
        user=user,
        organization=item.organization,
        permission_code=REQUEST_DECIDE,
    )
    require_worker_connection_access(
        user=user,
        organization=item.organization,
        connection=item.connection,
    )
    return item


def _staff_document_request_package_or_not_found(*, user, package_public_id):
    package = get_object_or_404(
        DocumentRequestPackage.objects.select_related(
            "organization",
            "connection__candidate",
            "connection__vacancy",
            "account_reference",
            "created_by",
            "reviewed_by",
        )
        .filter(
            organization__memberships__user=user,
            organization__memberships__state=OrganizationMembership.STATE_ACTIVE,
        )
        .distinct(),
        public_id=package_public_id,
    )
    require_permission(
        user=user,
        organization=package.organization,
        permission_code=DOCUMENT_REQUEST,
    )
    require_worker_connection_access(
        user=user,
        organization=package.organization,
        connection=package.connection,
    )
    return package


def _staff_worker_task_or_not_found(*, user, task_public_id):
    task = get_object_or_404(
        WorkerTask.objects.select_related("organization")
        .filter(
            organization__memberships__user=user,
            organization__memberships__state=OrganizationMembership.STATE_ACTIVE,
        )
        .distinct(),
        public_id=task_public_id,
    )
    require_permission(
        user=user,
        organization=task.organization,
        permission_code=TASK_MANAGE,
    )
    return task


def _staff_task_assignment_or_not_found(*, user, assignment_public_id):
    assignment = get_object_or_404(
        TaskAssignment.objects.select_related(
            "task__organization",
            "task__responsible_membership__user",
            "connection__candidate",
            "connection__vacancy",
        )
        .filter(
            task__organization__memberships__user=user,
            task__organization__memberships__state=OrganizationMembership.STATE_ACTIVE,
        )
        .distinct(),
        public_id=assignment_public_id,
    )
    require_permission(
        user=user,
        organization=assignment.task.organization,
        permission_code=TASK_MANAGE,
    )
    require_worker_connection_access(
        user=user,
        organization=assignment.task.organization,
        connection=assignment.connection,
    )
    return assignment


def _staff_announcement_or_not_found(*, user, announcement_public_id):
    announcement = get_object_or_404(
        Announcement.objects.select_related("organization")
        .filter(
            organization__memberships__user=user,
            organization__memberships__state=OrganizationMembership.STATE_ACTIVE,
        )
        .distinct(),
        public_id=announcement_public_id,
    )
    require_permission(
        user=user,
        organization=announcement.organization,
        permission_code=ANNOUNCEMENT_MANAGE,
    )
    return announcement


def _staff_scheduled_shift_or_not_found(*, user, shift_public_id):
    shift = get_object_or_404(
        ScheduledWorkShift.objects.select_related(
            "organization",
            "connection__candidate",
            "connection__vacancy",
            "work_assignment",
        )
        .filter(
            organization__memberships__user=user,
            organization__memberships__state=OrganizationMembership.STATE_ACTIVE,
        )
        .distinct(),
        public_id=shift_public_id,
    )
    require_permission(
        user=user,
        organization=shift.organization,
        permission_code=SCHEDULE_MANAGE,
    )
    require_worker_connection_access(
        user=user,
        organization=shift.organization,
        connection=shift.connection,
    )
    return shift


def _staff_calendar_mark_batch_or_not_found(*, user, batch_public_id):
    batch = get_object_or_404(
        CalendarMarkBatch.objects.select_related("organization", "template")
        .prefetch_related("items__request__connection")
        .filter(
            organization__memberships__user=user,
            organization__memberships__state=OrganizationMembership.STATE_ACTIVE,
        )
        .distinct(),
        public_id=batch_public_id,
    )
    require_permission(
        user=user,
        organization=batch.organization,
        permission_code=SCHEDULE_MANAGE,
    )
    for item in batch.items.all():
        try:
            require_worker_connection_access(
                user=user,
                organization=batch.organization,
                connection=item.request.connection,
            )
        except PermissionDenied as error:
            raise NotFound("support_calendar_mark_batch_not_found") from error
    return batch


def _staff_scheduled_shift_batch_or_not_found(*, user, batch_public_id):
    batch = get_object_or_404(
        ScheduledShiftBatch.objects.select_related("organization", "template")
        .filter(
            organization__memberships__user=user,
            organization__memberships__state=OrganizationMembership.STATE_ACTIVE,
        )
        .distinct(),
        public_id=batch_public_id,
    )
    require_permission(
        user=user,
        organization=batch.organization,
        permission_code=SCHEDULE_MANAGE,
    )
    allowed_connections = worker_connection_queryset_for(
        user=user,
        organization=batch.organization,
        queryset=SupportConnection.objects.filter(is_archived=False),
    )
    if batch.shifts.exclude(connection__in=allowed_connections).exists():
        raise NotFound("support_scheduled_shift_batch_not_found")
    return batch


def _query_date(value, *, field_name, default=None):
    raw_value = (value or "").strip()
    if not raw_value:
        return default
    parsed = parse_date(raw_value)
    if parsed is None:
        raise ValidationError({field_name: "date_must_use_iso_format"})
    return parsed


class MySupportOperationSummaryAPIView(SupportFeatureAPIView):
    def get(self, request, connection_public_id):
        _require_active_support_access(request.user)
        connection = _worker_time_connection_or_not_found(
            user=request.user,
            connection_public_id=connection_public_id,
        )
        return Response(_worker_operation_summary_payload(connection))


class MySupportWorkspaceAPIView(SupportFeatureAPIView):
    """Return a project-first, worker-owned mobile workspace snapshot."""

    def get(self, request, connection_public_id):
        _require_active_support_access(request.user)
        if not is_project_first_workspace_enabled():
            raise NotFound("project_first_workspace_not_available")
        connection = get_object_or_404(
            SupportConnection.objects.select_related(
                "organization",
                "candidate",
                "candidate__profile",
                "assigned_manager__user",
            ),
            public_id=connection_public_id,
            candidate=request.user,
            is_archived=False,
        )
        raw_month = (request.query_params.get("month") or "").strip()
        try:
            selected_month = (
                date.fromisoformat(f"{raw_month}-01")
                if raw_month
                else timezone.localdate().replace(day=1)
            )
        except ValueError as error:
            raise ValidationError({"month": "invalid_month"}) from error
        return Response(
            worker_workspace_snapshot(
                connection=connection,
                selected_month=selected_month,
            )
        )


class MySupportWorkspaceWeekAPIView(SupportFeatureAPIView):
    """Return the selected worker-owned ISO week from project-first days."""

    def get(self, request, connection_public_id):
        _require_active_support_access(request.user)
        if not is_project_first_workspace_enabled():
            raise NotFound("project_first_workspace_not_available")
        connection = get_object_or_404(
            SupportConnection.objects.select_related(
                "organization",
                "candidate",
                "candidate__profile",
            ),
            public_id=connection_public_id,
            candidate=request.user,
            is_archived=False,
        )
        raw_selected_date = (request.query_params.get("selected_date") or "").strip()
        try:
            selected_date = (
                date.fromisoformat(raw_selected_date)
                if raw_selected_date
                else timezone.localdate()
            )
            week_start = selected_date - timedelta(days=selected_date.weekday())
            week_start + timedelta(days=6)
        except (ValueError, OverflowError) as error:
            raise ValidationError({"selected_date": "invalid_date"}) from error
        return Response(
            worker_workspace_week_snapshot(
                connection=connection,
                selected_date=selected_date,
            )
        )


class MyProjectShiftPeerConversationAPIView(SupportFeatureAPIView):
    """Open a private peer chat only for two members of the selected shift."""

    def post(self, request, connection_public_id, shift_public_id):
        _require_active_support_access(request.user)
        if not is_project_first_workspace_enabled():
            raise NotFound("project_first_workspace_not_available")
        connection = get_object_or_404(
            SupportConnection.objects.select_related("organization", "candidate")
            .exclude(stage=SupportConnection.STAGE_CLOSED),
            public_id=connection_public_id,
            candidate=request.user,
            is_archived=False,
        )
        shift = get_object_or_404(
            ProjectCrewShift.objects.select_related("crew__organization").filter(
                crew__organization=connection.organization,
                state=ProjectCrewShift.STATE_PUBLISHED,
                members__connection=connection,
            ).distinct(),
            public_id=shift_public_id,
        )
        serializer = WorkerShiftPeerChatOpenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        target_connection = get_object_or_404(
            SupportConnection.objects.select_related(
                "organization", "candidate", "candidate__profile"
            )
            .exclude(stage=SupportConnection.STAGE_CLOSED),
            organization=connection.organization,
            public_id=serializer.validated_data["target_connection_id"],
            is_archived=False,
        )
        conversation, created = open_project_shift_peer_conversation(
            actor=request.user,
            actor_connection=connection,
            target_connection=target_connection,
            shift=shift,
        )
        conversation = SupportConversation.objects.prefetch_related(
            "members__user__profile"
        ).get(pk=conversation.pk)
        return Response(
            {
                "created": created,
                "conversation": _conversation_payload(
                    conversation,
                    viewer=request.user,
                ),
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class MySupportDriverManifestAPIView(SupportFeatureAPIView):
    def get(self, request, connection_public_id):
        _require_active_support_access(request.user)
        connection = _worker_time_connection_or_not_found(
            user=request.user,
            connection_public_id=connection_public_id,
        )
        return Response(_worker_driver_manifest_payload(connection))


class MyWorkTimeDayAPIView(SupportFeatureAPIView):
    """Return one worker-owned day without exposing staff-only history."""

    def get(self, request, connection_public_id):
        _require_active_support_access(request.user)
        connection = _worker_time_connection_or_not_found(
            user=request.user,
            connection_public_id=connection_public_id,
        )
        work_date = _query_date(
            request.query_params.get("work_date"),
            field_name="work_date",
            default=timezone.localdate(),
        )
        scheduled_shift = (
            ScheduledWorkShift.objects.filter(
                connection=connection,
                work_date=work_date,
                state=ScheduledWorkShift.STATE_PUBLISHED,
            )
            .select_related("connection", "work_assignment")
            .order_by("-ends_at", "-id")
            .first()
        )
        entry = (
            WorkTimeEntry.objects.filter(connection=connection, work_date=work_date)
            .select_related("connection", "scheduled_shift")
            .first()
        )
        # The calendar is informational.  An approved absence never creates a
        # fictional zero-hour entry and never edits a factual entry by itself.
        calendar_marks = list(
            WorkerRequest.objects.filter(
                connection=connection,
                request_type__in=_CALENDAR_MARK_REQUEST_TYPES,
                status=WorkerRequest.STATUS_APPROVED,
                starts_on__lte=work_date,
                ends_on__gte=work_date,
            ).order_by("starts_on", "ends_on", "id")
        )
        return Response(
            {
                "connection_id": str(connection.public_id),
                "work_date": work_date,
                "server_now": timezone.now(),
                "scheduled_shift": _scheduled_shift_payload(scheduled_shift),
                "time_entry": _time_entry_payload(entry) if entry else None,
                "time_entry_access": worker_time_entry_access(
                    scheduled_shift=scheduled_shift,
                    entry=entry,
                ),
                "calendar_marks": [
                    _calendar_mark_payload(item) for item in calendar_marks
                ],
            }
        )


class MyScheduledShiftListAPIView(SupportFeatureAPIView):
    """Worker-visible published shifts for a selected request period."""

    def get(self, request, connection_public_id):
        _require_active_support_access(request.user)
        connection = _worker_time_connection_or_not_found(
            user=request.user,
            connection_public_id=connection_public_id,
        )
        date_from = _query_date(
            request.query_params.get("date_from"),
            field_name="date_from",
            default=timezone.localdate(),
        )
        date_to = _query_date(
            request.query_params.get("date_to"),
            field_name="date_to",
            default=date_from,
        )
        if date_to < date_from or (date_to - date_from).days > 366:
            raise ValidationError({"date_range": "scheduled_shift_range_must_be_between_0_and_366_days"})
        shifts = list(
            ScheduledWorkShift.objects.filter(
                connection=connection,
                work_date__range=(date_from, date_to),
                state=ScheduledWorkShift.STATE_PUBLISHED,
            )
            .select_related("connection", "work_assignment")
            .order_by("work_date", "starts_at", "id")
        )
        calendar_marks = list(
            WorkerRequest.objects.filter(
                connection=connection,
                request_type__in=_CALENDAR_MARK_REQUEST_TYPES,
                status=WorkerRequest.STATUS_APPROVED,
                starts_on__lte=date_to,
                ends_on__gte=date_from,
            ).order_by("starts_on", "ends_on", "id")
        )
        return Response(
            {
                "connection_id": str(connection.public_id),
                "date_from": date_from,
                "date_to": date_to,
                "results": [_scheduled_shift_payload(shift) for shift in shifts],
                "calendar_marks": [
                    _calendar_mark_payload(item) for item in calendar_marks
                ],
            }
        )


class MyWorkTimeEntrySubmitAPIView(SupportFeatureAPIView):
    def post(self, request, connection_public_id):
        _require_active_support_access(request.user)
        connection = _worker_time_connection_or_not_found(
            user=request.user,
            connection_public_id=connection_public_id,
        )
        serializer = WorkTimeEntrySubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = submit_work_time_entry(
            worker=request.user,
            connection=connection,
            **serializer.validated_data,
        )
        entry = WorkTimeEntry.objects.select_related("connection", "scheduled_shift").get(pk=entry.pk)
        return Response({"time_entry": _time_entry_payload(entry)}, status=status.HTTP_201_CREATED)


class MyWorkTimeEntryAcknowledgeAPIView(SupportFeatureAPIView):
    def post(self, request, entry_public_id):
        _require_active_support_access(request.user)
        serializer = EmptyStrictInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = get_object_or_404(
            WorkTimeEntry.objects.select_related("connection", "scheduled_shift"),
            public_id=entry_public_id,
            connection__candidate=request.user,
        )
        entry = acknowledge_staff_time_adjustment(worker=request.user, entry=entry)
        entry = WorkTimeEntry.objects.select_related("connection", "scheduled_shift").get(pk=entry.pk)
        return Response({"time_entry": _time_entry_payload(entry)})


class MyWorkerRequestListCreateAPIView(SupportFeatureAPIView):
    """Worker-owned requests for one connection; never exposes another worker."""

    def get(self, request, connection_public_id):
        _require_active_support_access(request.user)
        connection = _worker_time_connection_or_not_found(
            user=request.user,
            connection_public_id=connection_public_id,
        )
        results = list(
            WorkerRequest.objects.filter(connection=connection)
            .select_related("connection", "connection__vacancy", "reviewed_by")
            .order_by("-submitted_at", "-created_at", "-id")
        )
        refresh_extra_shift_requests(results)
        return Response({"results": [_worker_request_payload(item) for item in results]})

    def post(self, request, connection_public_id):
        _require_active_support_access(request.user)
        connection = _worker_time_connection_or_not_found(
            user=request.user,
            connection_public_id=connection_public_id,
        )
        serializer = WorkerRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            item = submit_worker_request(
                worker=request.user,
                connection=connection,
                **serializer.validated_data,
            )
        except APIException:
            raise
        except Exception:
            logger.exception(
                "support_worker_request_submit_failed connection=%s type=%s",
                connection.public_id,
                serializer.validated_data.get("request_type", ""),
            )
            return Response(
                {
                    "code": "worker_request_submission_failed",
                    "detail": (
                        "Не удалось отправить запрос из-за временной ошибки сервера. "
                        "Попробуйте ещё раз через несколько минут. Если запрос срочный, "
                        "напишите менеджеру в чате."
                    ),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        item = WorkerRequest.objects.select_related(
            "connection", "connection__vacancy", "reviewed_by"
        ).get(pk=item.pk)
        return Response({"worker_request": _worker_request_payload(item)}, status=status.HTTP_201_CREATED)


class MyWorkerRequestCancelAPIView(SupportFeatureAPIView):
    def post(self, request, request_public_id):
        _require_active_support_access(request.user)
        serializer = EmptyStrictInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = get_object_or_404(
            WorkerRequest.objects.select_related("connection", "connection__vacancy", "reviewed_by"),
            public_id=request_public_id,
            connection__candidate=request.user,
        )
        item = cancel_worker_request(worker=request.user, request=item)
        item = WorkerRequest.objects.select_related(
            "connection", "connection__vacancy", "reviewed_by"
        ).get(pk=item.pk)
        return Response({"worker_request": _worker_request_payload(item)})


class MyDocumentRequestPackageListAPIView(SupportFeatureAPIView):
    """Worker reads e-mail hand-off cards only for their own connection."""

    def get(self, request, connection_public_id):
        _require_active_support_access(request.user)
        connection = _worker_time_connection_or_not_found(
            user=request.user,
            connection_public_id=connection_public_id,
        )
        packages = list(
            DocumentRequestPackage.objects.filter(connection=connection)
            .select_related("connection", "account_reference")
            .order_by("-updated_at", "-id")
        )
        return Response({"results": [_document_request_package_payload(item) for item in packages]})


class MyDocumentRequestPackageMarkSentAPIView(SupportFeatureAPIView):
    def post(self, request, package_public_id):
        _require_active_support_access(request.user)
        serializer = EmptyStrictInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        package = get_object_or_404(
            DocumentRequestPackage.objects.select_related(
                "organization", "connection", "account_reference"
            ),
            public_id=package_public_id,
            connection__candidate=request.user,
            connection__is_archived=False,
        )
        package = mark_document_request_package_sent(worker=request.user, package=package)
        package = DocumentRequestPackage.objects.select_related(
            "connection", "account_reference"
        ).get(pk=package.pk)
        return Response({"document_package": _document_request_package_payload(package)})


class MyWorkerTaskListAPIView(SupportFeatureAPIView):
    """Worker-only view of published tasks for one of their connections."""

    def get(self, request, connection_public_id):
        _require_active_support_access(request.user)
        connection = _worker_time_connection_or_not_found(
            user=request.user,
            connection_public_id=connection_public_id,
        )
        items = list(
            TaskAssignment.objects.filter(
                connection=connection,
                task__state=WorkerTask.STATE_PUBLISHED,
            )
            .select_related(
                "task__responsible_membership__user",
                "connection__candidate",
                "connection__vacancy",
            )
            .order_by("-task__priority", "task__due_at", "-task__published_at", "-id")
        )
        return Response({"results": [_task_assignment_payload(item) for item in items]})


class MyWorkerTaskActionAPIView(SupportFeatureAPIView):
    action = None

    def post(self, request, assignment_public_id):
        _require_active_support_access(request.user)
        serializer = WorkerTaskWorkerActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assignment = get_object_or_404(
            TaskAssignment.objects.select_related("task__organization", "connection__candidate"),
            public_id=assignment_public_id,
            connection__candidate=request.user,
            connection__is_archived=False,
        )
        assignment = worker_change_task_assignment(
            worker=request.user,
            assignment=assignment,
            action=self.action,
            worker_note=serializer.validated_data["worker_note"],
        )
        assignment = TaskAssignment.objects.select_related(
            "task__responsible_membership__user",
            "connection__candidate",
            "connection__vacancy",
        ).get(pk=assignment.pk)
        return Response({"task_assignment": _task_assignment_payload(assignment)})


class MyWorkerTaskStartAPIView(MyWorkerTaskActionAPIView):
    action = "start"


class MyWorkerTaskCompleteAPIView(MyWorkerTaskActionAPIView):
    action = "complete"


class MyAnnouncementListAPIView(SupportFeatureAPIView):
    """Worker reads only announcements addressed to their own connection."""

    def get(self, request, connection_public_id):
        _require_active_support_access(request.user)
        connection = _worker_time_connection_or_not_found(
            user=request.user,
            connection_public_id=connection_public_id,
        )
        now = timezone.now()
        items = list(
            AnnouncementAcknowledgement.objects.filter(
                connection=connection,
                announcement__state=Announcement.STATE_PUBLISHED,
            )
            .filter(Q(announcement__expires_at__isnull=True) | Q(announcement__expires_at__gt=now))
            .select_related("announcement", "connection__candidate")
            .order_by("-announcement__published_at", "-id")
        )
        return Response({"results": [_announcement_payload(item) for item in items]})


class MyAnnouncementAcknowledgeAPIView(SupportFeatureAPIView):
    def post(self, request, recipient_public_id):
        _require_active_support_access(request.user)
        serializer = EmptyStrictInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        recipient = get_object_or_404(
            AnnouncementAcknowledgement.objects.select_related(
                "announcement__organization",
                "connection__candidate",
            ),
            public_id=recipient_public_id,
            connection__candidate=request.user,
            connection__is_archived=False,
        )
        recipient = acknowledge_announcement(worker=request.user, acknowledgement=recipient)
        recipient = AnnouncementAcknowledgement.objects.select_related(
            "announcement",
            "connection__candidate",
        ).get(pk=recipient.pk)
        return Response({"announcement": _announcement_payload(recipient)})


class OrganizationDocumentRequestPackageListCreateAPIView(
    SupportFeatureAPIView,
    OrganizationAccessMixin,
):
    """Scoped staff queue and creation API; it intentionally accepts no files."""

    def get(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        require_permission(
            user=request.user,
            organization=organization,
            permission_code=DOCUMENT_REQUEST,
        )
        connections = worker_connection_queryset_for(
            user=request.user,
            organization=organization,
            queryset=SupportConnection.objects.all(),
        )
        packages = list(
            DocumentRequestPackage.objects.filter(
                organization=organization,
                connection__in=connections,
            )
            .select_related(
                "connection__candidate",
                "connection__vacancy",
                "account_reference",
                "created_by",
                "reviewed_by",
            )
            .order_by("-updated_at", "-id")[:250]
        )
        return Response(
            {
                "results": [
                    _document_request_package_payload(item, include_staff_fields=True)
                    for item in packages
                ]
            }
        )

    def post(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        serializer = DocumentRequestPackageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        connection = get_object_or_404(
            SupportConnection.objects.select_related("organization", "candidate"),
            public_id=data["connection_id"],
            organization=organization,
        )
        package = create_document_request_package(
            actor=request.user,
            organization=organization,
            connection=connection,
            requested_items=data["requested_items"],
            additional_instructions=data["additional_instructions"],
        )
        package = DocumentRequestPackage.objects.select_related(
            "connection__candidate",
            "connection__vacancy",
            "account_reference",
            "created_by",
            "reviewed_by",
        ).get(pk=package.pk)
        return Response(
            {"document_package": _document_request_package_payload(package, include_staff_fields=True)},
            status=status.HTTP_201_CREATED,
        )


class DocumentRequestPackageDecisionAPIView(SupportFeatureAPIView):
    action = None

    def post(self, request, package_public_id):
        serializer = DocumentRequestPackageDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        manager_note = serializer.validated_data["manager_note"]
        package = _staff_document_request_package_or_not_found(
            user=request.user,
            package_public_id=package_public_id,
        )
        package = review_document_request_package(
            actor=request.user,
            package=package,
            action=self.action,
            manager_note=manager_note,
        )
        package = DocumentRequestPackage.objects.select_related(
            "connection__candidate",
            "connection__vacancy",
            "account_reference",
            "created_by",
            "reviewed_by",
        ).get(pk=package.pk)
        return Response(
            {"document_package": _document_request_package_payload(package, include_staff_fields=True)}
        )


class DocumentRequestPackageNeedsCorrectionAPIView(DocumentRequestPackageDecisionAPIView):
    action = "needs_correction"


class DocumentRequestPackageCompleteAPIView(DocumentRequestPackageDecisionAPIView):
    action = "complete"


class DocumentRequestPackageNotRequiredAPIView(DocumentRequestPackageDecisionAPIView):
    action = "not_required"


class DocumentRequestPackageCancelAPIView(DocumentRequestPackageDecisionAPIView):
    action = "cancel"


class OrganizationContentWorkspaceAPIView(
    SupportFeatureAPIView,
    OrganizationAccessMixin,
):
    """Minimal, scope-safe data needed to compose an operational update.

    This endpoint intentionally does not require ``worker.view``: a manager
    who may send a task or announcement still needs to choose only workers in
    their already granted operational scope.  It never returns profile, home,
    document or payroll data.
    """

    def get(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        may_manage_tasks = has_permission(
            user=request.user,
            organization=organization,
            permission_code=TASK_MANAGE,
        )
        may_manage_announcements = has_permission(
            user=request.user,
            organization=organization,
            permission_code=ANNOUNCEMENT_MANAGE,
        )
        if not may_manage_tasks and not may_manage_announcements:
            raise PermissionDenied("support_permission_denied")

        allowed_kinds = []
        if may_manage_tasks:
            allowed_kinds.append(ContentTemplate.KIND_TASK)
        if may_manage_announcements:
            allowed_kinds.append(ContentTemplate.KIND_ANNOUNCEMENT)
        connections = list(
            worker_connection_queryset_for(
                user=request.user,
                organization=organization,
                queryset=SupportConnection.objects.filter(is_archived=False),
            )
            .select_related("candidate", "candidate__profile", "vacancy")
            .order_by("candidate__first_name", "candidate__last_name", "id")[:250]
        )
        templates = list(
            ContentTemplate.objects.filter(
                organization=organization,
                kind__in=allowed_kinds,
                is_active=True,
            ).order_by("kind", "name", "id")
        )
        return Response(
            {
                "permissions": {
                    "task_manage": may_manage_tasks,
                    "announcement_manage": may_manage_announcements,
                },
                "connections": [
                    {
                        "id": str(connection.public_id),
                        **_user_identity_payload(connection.candidate),
                        "vacancy_title": connection.vacancy.internal_title,
                        "stage": connection.stage,
                    }
                    for connection in connections
                ],
                "templates": [_content_template_payload(item) for item in templates],
            }
        )


class OrganizationContentTemplateListCreateAPIView(
    SupportFeatureAPIView,
    OrganizationAccessMixin,
):
    """Create and list reusable wording without creating deliveries."""

    def get(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        allowed_kinds = []
        if has_permission(
            user=request.user,
            organization=organization,
            permission_code=TASK_MANAGE,
        ):
            allowed_kinds.append(ContentTemplate.KIND_TASK)
        if has_permission(
            user=request.user,
            organization=organization,
            permission_code=ANNOUNCEMENT_MANAGE,
        ):
            allowed_kinds.append(ContentTemplate.KIND_ANNOUNCEMENT)
        if not allowed_kinds:
            raise PermissionDenied("support_permission_denied")
        return Response(
            {
                "results": [
                    _content_template_payload(item)
                    for item in ContentTemplate.objects.filter(
                        organization=organization,
                        kind__in=allowed_kinds,
                    ).order_by("kind", "name", "id")
                ]
            }
        )

    def post(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        serializer = ContentTemplateCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = create_content_template(
            actor=request.user,
            organization=organization,
            **serializer.validated_data,
        )
        return Response(
            {"content_template": _content_template_payload(item)},
            status=status.HTTP_201_CREATED,
        )


class OrganizationWorkerTaskListCreateAPIView(
    SupportFeatureAPIView,
    OrganizationAccessMixin,
):
    """Scoped task queue and draft creation for an employer organization."""

    def get(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        require_permission(
            user=request.user,
            organization=organization,
            permission_code=TASK_MANAGE,
        )
        allowed_connections = worker_connection_queryset_for(
            user=request.user,
            organization=organization,
            queryset=SupportConnection.objects.all(),
        )
        status_filter = (request.query_params.get("status") or "").strip()
        queryset = TaskAssignment.objects.filter(
            task__organization=organization,
            connection__in=allowed_connections,
        )
        if status_filter:
            valid_statuses = {value for value, _label in TaskAssignment.STATUS_CHOICES}
            if status_filter not in valid_statuses:
                raise ValidationError({"status": "unsupported_task_assignment_status"})
            queryset = queryset.filter(status=status_filter)
        items = list(
            queryset.select_related(
                "task__responsible_membership__user",
                "connection__candidate",
                "connection__candidate__profile",
                "connection__vacancy",
            ).order_by("-task__priority", "task__due_at", "-task__published_at", "-id")[:250]
        )
        return Response(
            {"results": [_task_assignment_payload(item, include_staff_fields=True) for item in items]}
        )

    def post(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        serializer = WorkerTaskCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = create_worker_task(
            actor=request.user,
            organization=organization,
            **serializer.validated_data,
        )
        return Response(
            {
                "worker_task": {
                    "id": str(task.public_id),
                    "state": task.state,
                    "recipient_count": task.assignments.count(),
                }
            },
            status=status.HTTP_201_CREATED,
        )


class WorkerTaskPublishAPIView(SupportFeatureAPIView):
    def post(self, request, task_public_id):
        serializer = EmptyStrictInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = _staff_worker_task_or_not_found(
            user=request.user,
            task_public_id=task_public_id,
        )
        task = publish_worker_task(actor=request.user, task=task)
        return Response(
            {
                "worker_task": {
                    "id": str(task.public_id),
                    "state": task.state,
                    "published_at": task.published_at,
                }
            }
        )


class WorkerTaskStaffDecisionAPIView(SupportFeatureAPIView):
    action = None

    def post(self, request, assignment_public_id):
        serializer = WorkerTaskStaffDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assignment = _staff_task_assignment_or_not_found(
            user=request.user,
            assignment_public_id=assignment_public_id,
        )
        assignment = staff_change_task_assignment(
            actor=request.user,
            assignment=assignment,
            action=self.action,
            manager_note=serializer.validated_data["manager_note"],
        )
        assignment = TaskAssignment.objects.select_related(
            "task__responsible_membership__user",
            "connection__candidate",
            "connection__candidate__profile",
            "connection__vacancy",
        ).get(pk=assignment.pk)
        return Response(
            {"task_assignment": _task_assignment_payload(assignment, include_staff_fields=True)}
        )


class WorkerTaskConfirmAPIView(WorkerTaskStaffDecisionAPIView):
    action = "confirm"


class WorkerTaskReturnAPIView(WorkerTaskStaffDecisionAPIView):
    action = "return"


class WorkerTaskCancelAPIView(WorkerTaskStaffDecisionAPIView):
    action = "cancel"


class OrganizationAnnouncementListCreateAPIView(
    SupportFeatureAPIView,
    OrganizationAccessMixin,
):
    """Scoped announcement drafts. Publication remains an explicit action."""

    def get(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        require_permission(
            user=request.user,
            organization=organization,
            permission_code=ANNOUNCEMENT_MANAGE,
        )
        allowed_connections = worker_connection_queryset_for(
            user=request.user,
            organization=organization,
            queryset=SupportConnection.objects.all(),
        )
        items = list(
            Announcement.objects.filter(
                organization=organization,
                acknowledgements__connection__in=allowed_connections,
            )
            .distinct()
            .order_by("-published_at", "-created_at", "-id")[:250]
        )
        return Response({"results": [_announcement_payload(item) for item in items]})

    def post(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        serializer = AnnouncementCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        announcement = create_announcement(
            actor=request.user,
            organization=organization,
            **serializer.validated_data,
        )
        return Response(
            {
                "announcement": {
                    "id": str(announcement.public_id),
                    "state": announcement.state,
                    "recipient_count": announcement.acknowledgements.count(),
                }
            },
            status=status.HTTP_201_CREATED,
        )


class AnnouncementPublishAPIView(SupportFeatureAPIView):
    def post(self, request, announcement_public_id):
        serializer = EmptyStrictInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        announcement = _staff_announcement_or_not_found(
            user=request.user,
            announcement_public_id=announcement_public_id,
        )
        announcement = publish_announcement(actor=request.user, announcement=announcement)
        return Response(
            {
                "announcement": {
                    "id": str(announcement.public_id),
                    "state": announcement.state,
                    "published_at": announcement.published_at,
                }
            }
        )


class OrganizationWorkerRequestListAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    """Scope-filtered review queue.  No batch action is intentionally exposed."""

    def get(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        require_permission(
            user=request.user,
            organization=organization,
            permission_code=REQUEST_DECIDE,
        )
        allowed_connections = worker_connection_queryset_for(
            user=request.user,
            organization=organization,
            queryset=SupportConnection.objects.all(),
        )
        status_filter = (request.query_params.get("status") or "").strip()
        queryset = WorkerRequest.objects.filter(
            organization=organization,
            connection__in=allowed_connections,
        )
        if status_filter:
            valid_statuses = {value for value, _label in WorkerRequest.STATUS_CHOICES}
            if status_filter not in valid_statuses:
                raise ValidationError({"status": "unsupported_worker_request_status"})
            queryset = queryset.filter(status=status_filter)
        items = list(
            queryset.select_related(
                "connection__candidate",
                "connection__candidate__profile",
                "connection__vacancy",
                "reviewed_by",
            ).order_by("-submitted_at", "-created_at", "-id")[:250]
        )
        refresh_extra_shift_requests(items)
        return Response(
            {"results": [_worker_request_payload(item, include_staff_fields=True) for item in items]}
        )


class WorkerRequestDecisionAPIView(SupportFeatureAPIView):
    action = None

    def post(self, request, request_public_id):
        serializer = WorkerRequestDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        manager_note = serializer.validated_data["manager_note"]
        if self.action == "decline" and not manager_note:
            raise ValidationError({"manager_note": "manager_note_required_for_request_decision"})
        item = _staff_worker_request_or_not_found(
            user=request.user,
            request_public_id=request_public_id,
        )
        item = decide_worker_request(
            actor=request.user,
            request=item,
            action=self.action,
            manager_note=manager_note,
        )
        item = WorkerRequest.objects.select_related(
            "connection__candidate",
            "connection__candidate__profile",
            "connection__vacancy",
            "reviewed_by",
        ).get(pk=item.pk)
        refresh_extra_shift_requests([item])
        return Response({"worker_request": _worker_request_payload(item, include_staff_fields=True)})


class WorkerRequestApproveAPIView(WorkerRequestDecisionAPIView):
    action = "approve"


class WorkerRequestDeclineAPIView(WorkerRequestDecisionAPIView):
    action = "decline"


class WorkerRequestExtraDateDeclineAPIView(SupportFeatureAPIView):
    def post(self, request, request_public_id, request_date_public_id):
        serializer = WorkerRequestDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        manager_note = serializer.validated_data["manager_note"]
        if not manager_note:
            raise ValidationError(
                {"manager_note": "manager_note_required_for_request_decision"}
            )
        item = _staff_worker_request_or_not_found(
            user=request.user,
            request_public_id=request_public_id,
        )
        request_date = get_object_or_404(
            WorkerRequestDate,
            public_id=request_date_public_id,
            request=item,
        )
        item = decline_extra_shift_date(
            actor=request.user,
            request=item,
            request_date=request_date,
            manager_note=manager_note,
        )
        item = WorkerRequest.objects.select_related(
            "connection__candidate",
            "connection__candidate__profile",
            "connection__vacancy",
            "reviewed_by",
        ).get(pk=item.pk)
        refresh_extra_shift_requests([item])
        return Response(
            {"worker_request": _worker_request_payload(item, include_staff_fields=True)}
        )


class OrganizationTimeEntryListAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    """A scope-filtered staff queue with human and decimal totals."""

    def get(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        require_permission(user=request.user, organization=organization, permission_code=TIME_VIEW)
        today = timezone.localdate()
        date_from = _query_date(
            request.query_params.get("date_from"),
            field_name="date_from",
            default=today - timedelta(days=6),
        )
        date_to = _query_date(
            request.query_params.get("date_to"),
            field_name="date_to",
            default=today,
        )
        if date_to < date_from or (date_to - date_from).days > 62:
            raise ValidationError({"date_range": "time_entry_range_must_be_between_0_and_62_days"})
        allowed_connections = worker_connection_queryset_for(
            user=request.user,
            organization=organization,
            queryset=SupportConnection.objects.all(),
        )
        entries = list(
            WorkTimeEntry.objects.filter(
                organization=organization,
                connection__in=allowed_connections,
                work_date__range=(date_from, date_to),
            )
            .select_related(
                "connection__candidate",
                "connection__candidate__profile",
                "connection__vacancy",
                "scheduled_shift",
                "confirmed_by",
            )
            .order_by("-work_date", "connection__candidate__last_name", "connection__candidate__first_name", "id")
        )
        total_minutes = sum(entry.worked_minutes for entry in entries)
        return Response(
            {
                "date_from": date_from,
                "date_to": date_to,
                "totals": {
                    "worked_minutes": total_minutes,
                    "worked_duration": _duration_label(total_minutes),
                    "decimal_hours": f"{total_minutes / 60:.2f}",
                    "entry_count": len(entries),
                },
                "results": [
                    _time_entry_payload(entry, include_staff_fields=True) for entry in entries
                ],
            }
        )


class ScheduledWorkShiftCreateAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    def post(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        require_permission(
            user=request.user,
            organization=organization,
            permission_code=SCHEDULE_MANAGE,
        )
        serializer = ScheduledWorkShiftCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        connection = get_object_or_404(
            SupportConnection,
            organization=organization,
            public_id=data.pop("connection_id"),
        )
        # Validate the worker scope before looking up an optional assignment.
        # Otherwise a staff member could use a guessed assignment UUID as an
        # existence oracle for another worker.
        require_worker_connection_access(
            user=request.user,
            organization=organization,
            connection=connection,
        )
        work_assignment_id = data.pop("work_assignment_id")
        work_assignment = (
            get_object_or_404(
                WorkerProjectAssignment,
                organization=organization,
                public_id=work_assignment_id,
            )
            if work_assignment_id
            else None
        )
        shift = create_scheduled_shift(
            actor=request.user,
            organization=organization,
            connection=connection,
            work_assignment=work_assignment,
            **data,
        )
        shift = ScheduledWorkShift.objects.select_related("connection", "work_assignment").get(pk=shift.pk)
        return Response({"scheduled_shift": _scheduled_shift_payload(shift)}, status=status.HTTP_201_CREATED)


class OrganizationShiftTemplateCreateAPIView(
    SupportFeatureAPIView,
    OrganizationAccessMixin,
):
    def post(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        serializer = ShiftTemplateCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        template = create_shift_template(
            actor=request.user,
            organization=organization,
            **serializer.validated_data,
        )
        return Response(
            {"shift_template": _shift_template_payload(template)},
            status=status.HTTP_201_CREATED,
        )


class OrganizationCalendarMarkTemplateCreateAPIView(
    SupportFeatureAPIView,
    OrganizationAccessMixin,
):
    def post(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        serializer = CalendarMarkTemplateCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        template = create_calendar_mark_template(
            actor=request.user,
            organization=organization,
            **serializer.validated_data,
        )
        return Response(
            {"calendar_mark_template": _calendar_mark_template_payload(template)},
            status=status.HTTP_201_CREATED,
        )


class CalendarMarkBatchCreateAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    def post(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        require_permission(
            user=request.user,
            organization=organization,
            permission_code=SCHEDULE_MANAGE,
        )
        serializer = CalendarMarkBatchCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        template = get_object_or_404(
            CalendarMarkTemplate,
            organization=organization,
            public_id=data["template_id"],
            is_active=True,
        )
        request_ids = data["worker_request_ids"]
        worker_requests = list(
            WorkerRequest.objects.filter(
                organization=organization,
                public_id__in=request_ids,
            ).select_related("connection__candidate")
        )
        if len(worker_requests) != len(request_ids):
            raise NotFound("support_calendar_mark_request_not_found")
        for item in worker_requests:
            try:
                require_worker_connection_access(
                    user=request.user,
                    organization=organization,
                    connection=item.connection,
                )
            except PermissionDenied as error:
                raise NotFound("support_calendar_mark_request_not_found") from error
        batch = create_calendar_mark_batch(
            actor=request.user,
            organization=organization,
            template=template,
            requests=worker_requests,
        )
        items = list(
            CalendarMarkBatchItem.objects.filter(batch=batch)
            .select_related("request__connection__candidate")
            .order_by("request__starts_on", "request__ends_on", "id")
        )
        return Response(
            {"calendar_mark_batch": _calendar_mark_batch_payload(batch, items=items)},
            status=status.HTTP_201_CREATED,
        )


class CalendarMarkBatchPublishAPIView(SupportFeatureAPIView):
    def post(self, request, batch_public_id):
        serializer = EmptyStrictInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        batch = _staff_calendar_mark_batch_or_not_found(
            user=request.user,
            batch_public_id=batch_public_id,
        )
        batch = publish_calendar_mark_batch(actor=request.user, batch=batch)
        items = list(
            CalendarMarkBatchItem.objects.filter(batch=batch)
            .select_related("request__connection__candidate")
            .order_by("request__starts_on", "request__ends_on", "id")
        )
        return Response(
            {"calendar_mark_batch": _calendar_mark_batch_payload(batch, items=items)}
        )


class CalendarMarkBatchCancelAPIView(SupportFeatureAPIView):
    def post(self, request, batch_public_id):
        serializer = EmptyStrictInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        batch = _staff_calendar_mark_batch_or_not_found(
            user=request.user,
            batch_public_id=batch_public_id,
        )
        batch = cancel_calendar_mark_batch(actor=request.user, batch=batch)
        items = list(
            CalendarMarkBatchItem.objects.filter(batch=batch)
            .select_related("request__connection__candidate")
            .order_by("request__starts_on", "request__ends_on", "id")
        )
        return Response(
            {"calendar_mark_batch": _calendar_mark_batch_payload(batch, items=items)}
        )


class ScheduledShiftBatchCreateAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    def post(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        require_permission(
            user=request.user,
            organization=organization,
            permission_code=SCHEDULE_MANAGE,
        )
        serializer = ScheduledShiftBatchCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        template = get_object_or_404(
            ShiftTemplate,
            organization=organization,
            public_id=data.pop("template_id"),
            is_active=True,
        )
        connection_ids = data.pop("connection_ids")
        connections = list(
            worker_connection_queryset_for(
                user=request.user,
                organization=organization,
                queryset=SupportConnection.objects.filter(
                    is_archived=False,
                    public_id__in=connection_ids,
                ).select_related("candidate"),
            )
        )
        if len(connections) != len(connection_ids):
            raise NotFound("support_worker_not_found")
        batch = create_scheduled_shift_batch(
            actor=request.user,
            organization=organization,
            template=template,
            connections=connections,
            **data,
        )
        shifts = list(
            ScheduledWorkShift.objects.filter(batch=batch)
            .select_related(
                "connection__candidate", "connection__candidate__profile"
            )
            .order_by("connection_id", "work_date", "id")
        )
        return Response(
            {
                "scheduled_shift_batch": _scheduled_shift_batch_payload(
                    batch,
                    shifts=shifts,
                )
            },
            status=status.HTTP_201_CREATED,
        )


class ScheduledShiftBatchPublishAPIView(SupportFeatureAPIView):
    def post(self, request, batch_public_id):
        serializer = EmptyStrictInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        batch = _staff_scheduled_shift_batch_or_not_found(
            user=request.user,
            batch_public_id=batch_public_id,
        )
        batch = publish_scheduled_shift_batch(actor=request.user, batch=batch)
        shifts = list(
            ScheduledWorkShift.objects.filter(batch=batch)
            .select_related("connection__candidate")
            .order_by("connection_id", "work_date", "id")
        )
        return Response(
            {
                "scheduled_shift_batch": _scheduled_shift_batch_payload(
                    batch,
                    shifts=shifts,
                )
            }
        )


class ScheduledShiftBatchCancelAPIView(SupportFeatureAPIView):
    def post(self, request, batch_public_id):
        serializer = EmptyStrictInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        batch = _staff_scheduled_shift_batch_or_not_found(
            user=request.user,
            batch_public_id=batch_public_id,
        )
        batch = cancel_scheduled_shift_batch(actor=request.user, batch=batch)
        shifts = list(
            ScheduledWorkShift.objects.filter(batch=batch)
            .select_related("connection__candidate")
            .order_by("connection_id", "work_date", "id")
        )
        return Response(
            {
                "scheduled_shift_batch": _scheduled_shift_batch_payload(
                    batch,
                    shifts=shifts,
                )
            }
        )


class ScheduledWorkShiftPublishAPIView(SupportFeatureAPIView):
    def post(self, request, shift_public_id):
        serializer = EmptyStrictInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        shift = _staff_scheduled_shift_or_not_found(
            user=request.user,
            shift_public_id=shift_public_id,
        )
        shift = publish_scheduled_shift(actor=request.user, shift=shift)
        shift = ScheduledWorkShift.objects.select_related("connection", "work_assignment").get(pk=shift.pk)
        return Response({"scheduled_shift": _scheduled_shift_payload(shift)})


class ScheduledWorkShiftCancelAPIView(SupportFeatureAPIView):
    def post(self, request, shift_public_id):
        serializer = EmptyStrictInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        shift = _staff_scheduled_shift_or_not_found(
            user=request.user,
            shift_public_id=shift_public_id,
        )
        shift = cancel_scheduled_shift(actor=request.user, shift=shift)
        shift = ScheduledWorkShift.objects.select_related("connection", "work_assignment").get(pk=shift.pk)
        return Response({"scheduled_shift": _scheduled_shift_payload(shift)})


class WorkTimeEntryCorrectionAPIView(SupportFeatureAPIView):
    def post(self, request, entry_public_id):
        serializer = WorkTimeEntryCorrectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = _staff_time_entry_or_not_found(
            user=request.user,
            entry_public_id=entry_public_id,
            permission_code=TIME_REVIEW,
        )
        entry = request_work_time_correction(
            actor=request.user,
            entry=entry,
            reason=serializer.validated_data["reason"],
        )
        entry = WorkTimeEntry.objects.select_related(
            "connection__candidate",
            "connection__candidate__profile",
            "connection__vacancy",
            "scheduled_shift",
            "confirmed_by",
        ).get(pk=entry.pk)
        return Response({"time_entry": _time_entry_payload(entry, include_staff_fields=True)})


class WorkTimeEntryConfirmAPIView(SupportFeatureAPIView):
    def post(self, request, entry_public_id):
        serializer = EmptyStrictInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = _staff_time_entry_or_not_found(
            user=request.user,
            entry_public_id=entry_public_id,
            permission_code=TIME_REVIEW,
        )
        entry = confirm_work_time_entry(actor=request.user, entry=entry)
        entry = WorkTimeEntry.objects.select_related(
            "connection__candidate",
            "connection__candidate__profile",
            "connection__vacancy",
            "scheduled_shift",
            "confirmed_by",
        ).get(pk=entry.pk)
        return Response({"time_entry": _time_entry_payload(entry, include_staff_fields=True)})


class WorkTimeEntryStaffEditAPIView(SupportFeatureAPIView):
    def post(self, request, entry_public_id):
        serializer = WorkTimeEntryStaffEditSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = _staff_time_entry_or_not_found(
            user=request.user,
            entry_public_id=entry_public_id,
            permission_code=TIME_EDIT,
        )
        entry = edit_work_time_entry(
            actor=request.user,
            entry=entry,
            **serializer.validated_data,
        )
        entry = WorkTimeEntry.objects.select_related(
            "connection__candidate",
            "connection__candidate__profile",
            "connection__vacancy",
            "scheduled_shift",
            "confirmed_by",
        ).get(pk=entry.pk)
        return Response({"time_entry": _time_entry_payload(entry, include_staff_fields=True)})


class HousingSiteListCreateAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    def get(self, request, organization_public_id):
        organization = _operation_organization(
            self,
            request=request,
            organization_public_id=organization_public_id,
            permission_code=HOUSING_MANAGE,
        )
        sites = list(
            HousingSite.objects.filter(organization=organization).order_by(
                "internal_name", "id"
            )
        )
        rooms = list(
            HousingRoom.objects.filter(site__organization=organization)
            .order_by("label", "id")
        )
        places = list(
            HousingPlace.objects.filter(room__site__organization=organization)
            .order_by("label", "id")
        )
        now = timezone.now()
        assignments = list(
            HousingAssignment.objects.filter(
                organization=organization,
                state__in=(
                    HousingAssignment.STATE_DRAFT,
                    HousingAssignment.STATE_PUBLISHED,
                ),
            )
            .filter(Q(check_out_at__isnull=True) | Q(check_out_at__gt=now))
            .select_related(
                "connection__candidate", "connection__candidate__profile"
            )
            .order_by("check_in_at", "id")
        )
        visible_connection_ids = set(
            worker_connection_queryset_for(
                user=request.user,
                organization=organization,
                queryset=SupportConnection.objects.filter(is_archived=False),
            ).values_list("id", flat=True)
        )
        rooms_by_site = {}
        for room in rooms:
            rooms_by_site.setdefault(room.site_id, []).append(room)
        places_by_room = {}
        for place in places:
            places_by_room.setdefault(place.room_id, []).append(place)
        assignments_by_place = {}
        for assignment in assignments:
            assignments_by_place.setdefault(assignment.place_id, []).append(assignment)
        return Response(
            {
                "results": [
                    _housing_site_workspace_payload(
                        site,
                        rooms=rooms_by_site.get(site.id, []),
                        places_by_room=places_by_room,
                        assignments_by_place=assignments_by_place,
                        visible_connection_ids=visible_connection_ids,
                    )
                    for site in sites
                ]
            }
        )

    def post(self, request, organization_public_id):
        organization = _operation_organization(
            self,
            request=request,
            organization_public_id=organization_public_id,
            permission_code=HOUSING_MANAGE,
        )
        serializer = HousingSiteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        site = create_housing_site(
            actor=request.user,
            organization=organization,
            **serializer.validated_data,
        )
        return Response({"housing_site": _housing_site_payload(site)}, status=status.HTTP_201_CREATED)


class HousingRoomCreateAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    def post(self, request, organization_public_id):
        organization = _operation_organization(self, request=request, organization_public_id=organization_public_id, permission_code=HOUSING_MANAGE)
        serializer = HousingRoomCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        site = get_object_or_404(HousingSite, organization=organization, public_id=data.pop("site_id"))
        room = create_housing_room(
            actor=request.user,
            organization=organization,
            site=site,
            **data,
        )
        return Response(
            {
                "housing_room": {
                    "id": str(room.public_id),
                    "site_id": str(site.public_id),
                    "label": room.label,
                    "capacity": room.capacity,
                    "places": [
                        {"id": str(place.public_id), "label": place.label}
                        for place in room.places.order_by("id")
                    ],
                }
            },
            status=status.HTTP_201_CREATED,
        )


class HousingPlaceCreateAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    def post(self, request, organization_public_id):
        organization = _operation_organization(self, request=request, organization_public_id=organization_public_id, permission_code=HOUSING_MANAGE)
        serializer = HousingPlaceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        room = get_object_or_404(HousingRoom.objects.select_related("site"), site__organization=organization, public_id=data.pop("room_id"))
        place = create_housing_place(
            actor=request.user,
            organization=organization,
            room=room,
            **data,
        )
        return Response({"housing_place": {"id": str(place.public_id), "room_id": str(room.public_id), "label": place.label}}, status=status.HTTP_201_CREATED)


class WorksiteListCreateAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    def get(self, request, organization_public_id):
        organization = _operation_organization(self, request=request, organization_public_id=organization_public_id, permission_code=SCHEDULE_MANAGE)
        worksites = Worksite.objects.filter(organization=organization).order_by("internal_name", "id")
        return Response({"results": [_worksite_payload(item) for item in worksites]})

    def post(self, request, organization_public_id):
        organization = _operation_organization(self, request=request, organization_public_id=organization_public_id, permission_code=SCHEDULE_MANAGE)
        serializer = WorksiteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        worksite = create_worksite(
            actor=request.user,
            organization=organization,
            **serializer.validated_data,
        )
        return Response({"worksite": _worksite_payload(worksite)}, status=status.HTTP_201_CREATED)


class WorkProjectCreateAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    def post(self, request, organization_public_id):
        organization = _operation_organization(self, request=request, organization_public_id=organization_public_id, permission_code=SCHEDULE_MANAGE)
        serializer = WorkProjectCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        worksite = get_object_or_404(Worksite, organization=organization, public_id=data.pop("worksite_id"))
        project = create_work_project(
            actor=request.user,
            organization=organization,
            worksite=worksite,
            **data,
        )
        return Response({"work_project": {"id": str(project.public_id), "worksite_id": str(worksite.public_id), "internal_name": project.internal_name, "worker_visible_name": project.worker_visible_name}}, status=status.HTTP_201_CREATED)


class VehicleListCreateAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    def get(self, request, organization_public_id):
        organization = _operation_organization(self, request=request, organization_public_id=organization_public_id, permission_code=TRANSPORT_MANAGE)
        today = timezone.localdate()
        vehicles = list(
            Vehicle.objects.filter(organization=organization).order_by(
                "internal_name", "id"
            )
        )
        visible_connection_ids = set(
            worker_connection_queryset_for(
                user=request.user,
                organization=organization,
                queryset=SupportConnection.objects.filter(
                    organization=organization,
                    is_archived=False,
                ),
            ).values_list("id", flat=True)
        )

        def driver_identity(connection):
            if connection.id not in visible_connection_ids:
                return {
                    "id": None,
                    "first_name": "",
                    "last_name": "",
                    "display_name": "",
                    "avatar_url": "",
                    "identity_restricted": True,
                }
            return {
                "id": str(connection.public_id),
                **_user_identity_payload(connection.candidate),
                "identity_restricted": False,
            }

        # Project crews are the canonical source for the new employer workflow.
        # Keep the published legacy assignment as a fallback for vehicles that
        # have not yet been moved into a project crew.
        current_driver_by_vehicle = {}
        project_resources = (
            ProjectCrewResourceAssignment.objects.filter(
                crew__organization=organization,
                crew__state=ProjectCrew.STATE_ACTIVE,
                starts_on__lte=today,
            )
            .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=today))
            .select_related(
                "driver_connection__candidate",
                "driver_connection__candidate__profile",
                "crew__project",
            )
            .order_by("vehicle_id", "-starts_on", "-id")
        )
        for resource in project_resources:
            current_driver_by_vehicle.setdefault(
                resource.vehicle_id,
                {
                    **driver_identity(resource.driver_connection),
                    "project": resource.crew.project.worker_visible_name
                    or resource.crew.project.internal_name,
                    "project_id": str(resource.crew.project.public_id),
                    "crew": resource.crew.internal_name,
                    "crew_id": str(resource.crew.public_id),
                },
            )

        legacy_resources = (
            DriverVehicleAssignment.objects.filter(
                organization=organization,
                state=DriverVehicleAssignment.STATE_PUBLISHED,
                starts_on__lte=today,
            )
            .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=today))
            .select_related(
                "driver_connection__candidate",
                "driver_connection__candidate__profile",
            )
            .order_by("vehicle_id", "-starts_on", "-id")
        )
        for resource in legacy_resources:
            current_driver_by_vehicle.setdefault(
                resource.vehicle_id,
                {
                    **driver_identity(resource.driver_connection),
                    "project": None,
                    "project_id": None,
                    "crew": None,
                    "crew_id": None,
                },
            )

        return Response(
            {
                "results": [
                    {
                        "id": str(item.public_id),
                        "internal_name": item.internal_name,
                        "registration_identifier": item.registration_identifier,
                        "seat_capacity": item.seat_capacity,
                        "is_active": item.is_active,
                        "current_driver": current_driver_by_vehicle.get(item.id),
                    }
                    for item in vehicles
                ]
            }
        )

    def post(self, request, organization_public_id):
        organization = _operation_organization(self, request=request, organization_public_id=organization_public_id, permission_code=TRANSPORT_MANAGE)
        serializer = VehicleCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        vehicle = create_vehicle(
            actor=request.user,
            organization=organization,
            **serializer.validated_data,
        )
        return Response({"vehicle": {"id": str(vehicle.public_id), "internal_name": vehicle.internal_name, "registration_identifier": vehicle.registration_identifier, "seat_capacity": vehicle.seat_capacity}}, status=status.HTTP_201_CREATED)


class HousingAssignmentCreateAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    def post(self, request, organization_public_id):
        organization, _ = self.get_organization(user=request.user, organization_public_id=organization_public_id)
        serializer = HousingAssignmentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        connection = get_object_or_404(SupportConnection, organization=organization, public_id=data.pop("connection_id"))
        place = get_object_or_404(HousingPlace.objects.select_related("room__site"), room__site__organization=organization, public_id=data.pop("place_id"))
        assignment = create_housing_assignment(actor=request.user, organization=organization, connection=connection, place=place, **data)
        return Response({"housing_assignment": _operation_assignment_payload(assignment)}, status=status.HTTP_201_CREATED)


class HousingAvailableWorkersAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    """List scoped workers who have no published housing from check-in onward."""

    def get(self, request, organization_public_id):
        organization = _operation_organization(
            self,
            request=request,
            organization_public_id=organization_public_id,
            permission_code=HOUSING_MANAGE,
        )
        serializer = HousingAvailableWorkersQuerySerializer(
            data=request.query_params
        )
        serializer.is_valid(raise_exception=True)
        check_in_at = serializer.validated_data["check_in_at"]
        unavailable_connection_ids = (
            HousingAssignment.objects.filter(
                organization=organization,
                state=HousingAssignment.STATE_PUBLISHED,
            )
            .filter(
                Q(check_out_at__isnull=True) | Q(check_out_at__gt=check_in_at)
            )
            .values_list("connection_id", flat=True)
        )
        connections = (
            worker_connection_queryset_for(
                user=request.user,
                organization=organization,
                queryset=SupportConnection.objects.filter(
                    organization=organization,
                    is_archived=False,
                    stage__in=(
                        SupportConnection.STAGE_COORDINATOR,
                        SupportConnection.STAGE_ACTIVE_WORKER,
                    ),
                ),
            )
            .exclude(id__in=unavailable_connection_ids)
            .select_related("candidate", "candidate__profile")
            .order_by(
                "candidate__first_name",
                "candidate__last_name",
                "candidate__username",
                "id",
            )
        )
        return Response(
            {
                "check_in_at": check_in_at,
                "results": [
                    {
                        "id": str(connection.public_id),
                        "stage": connection.stage,
                        "candidate": {
                            "id": str(connection.candidate_id),
                            **_user_identity_payload(connection.candidate),
                        },
                    }
                    for connection in connections
                ],
            }
        )


class HousingAssignmentAssignAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    """Create and publish a housing assignment as one atomic mobile action."""

    def post(self, request, organization_public_id):
        organization, _ = self.get_organization(
            user=request.user,
            organization_public_id=organization_public_id,
        )
        serializer = HousingAssignmentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        connection = get_object_or_404(
            SupportConnection,
            organization=organization,
            public_id=data.pop("connection_id"),
        )
        place = get_object_or_404(
            HousingPlace.objects.select_related("room__site"),
            room__site__organization=organization,
            public_id=data.pop("place_id"),
        )
        with transaction.atomic():
            assignment = create_housing_assignment(
                actor=request.user,
                organization=organization,
                connection=connection,
                place=place,
                **data,
            )
            assignment = publish_housing_assignment(
                actor=request.user,
                assignment=assignment,
            )
        assignment = HousingAssignment.objects.select_related(
            "connection__candidate", "connection__candidate__profile"
        ).get(pk=assignment.pk)
        return Response(
            {
                "housing_assignment": _housing_assignment_detail_payload(
                    assignment
                )
            },
            status=status.HTTP_201_CREATED,
        )


class HousingAssignmentPublishAPIView(SupportFeatureAPIView):
    def post(self, request, assignment_public_id):
        assignment = _operational_object_or_not_found(
            model=HousingAssignment,
            user=request.user,
            public_id=assignment_public_id,
        )
        assignment = publish_housing_assignment(actor=request.user, assignment=assignment)
        return Response({"housing_assignment": _operation_assignment_payload(assignment)})


class HousingAssignmentCancelAPIView(SupportFeatureAPIView):
    def post(self, request, assignment_public_id):
        assignment = _operational_object_or_not_found(
            model=HousingAssignment,
            user=request.user,
            public_id=assignment_public_id,
        )
        assignment = cancel_housing_assignment(actor=request.user, assignment=assignment)
        return Response({"housing_assignment": _operation_assignment_payload(assignment)})


class HousingAssignmentCheckOutAPIView(SupportFeatureAPIView):
    def post(self, request, assignment_public_id):
        serializer = HousingAssignmentCheckOutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assignment = _operational_object_or_not_found(
            model=HousingAssignment,
            user=request.user,
            public_id=assignment_public_id,
        )
        assignment = schedule_housing_check_out(
            actor=request.user,
            assignment=assignment,
            check_out_at=serializer.validated_data["check_out_at"],
        )
        assignment = HousingAssignment.objects.select_related(
            "connection__candidate", "connection__candidate__profile"
        ).get(pk=assignment.pk)
        return Response(
            {"housing_assignment": _housing_assignment_detail_payload(assignment)}
        )


class WorkerProjectAssignmentCreateAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    def post(self, request, organization_public_id):
        organization, _ = self.get_organization(user=request.user, organization_public_id=organization_public_id)
        serializer = WorkerProjectAssignmentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        connection = get_object_or_404(SupportConnection, organization=organization, public_id=data.pop("connection_id"))
        project = get_object_or_404(WorkProject, organization=organization, public_id=data.pop("project_id"))
        assignment = create_worker_project_assignment(actor=request.user, organization=organization, connection=connection, project=project, **data)
        return Response({"work_assignment": _operation_assignment_payload(assignment)}, status=status.HTTP_201_CREATED)


class WorkerProjectAssignmentPublishAPIView(SupportFeatureAPIView):
    def post(self, request, assignment_public_id):
        assignment = _operational_object_or_not_found(
            model=WorkerProjectAssignment,
            user=request.user,
            public_id=assignment_public_id,
        )
        assignment = publish_worker_project_assignment(actor=request.user, assignment=assignment)
        return Response({"work_assignment": _operation_assignment_payload(assignment)})


class WorkerProjectAssignmentCancelAPIView(SupportFeatureAPIView):
    def post(self, request, assignment_public_id):
        assignment = _operational_object_or_not_found(
            model=WorkerProjectAssignment,
            user=request.user,
            public_id=assignment_public_id,
        )
        assignment = cancel_worker_project_assignment(actor=request.user, assignment=assignment)
        return Response({"work_assignment": _operation_assignment_payload(assignment)})


class DriverVehicleAssignmentCreateAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    def post(self, request, organization_public_id):
        organization, _ = self.get_organization(user=request.user, organization_public_id=organization_public_id)
        serializer = DriverVehicleAssignmentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        driver_connection = get_object_or_404(SupportConnection, organization=organization, public_id=data.pop("driver_connection_id"))
        vehicle = get_object_or_404(Vehicle, organization=organization, public_id=data.pop("vehicle_id"))
        assignment = create_driver_vehicle_assignment(actor=request.user, organization=organization, driver_connection=driver_connection, vehicle=vehicle, **data)
        return Response({"driver_vehicle_assignment": _operation_assignment_payload(assignment)}, status=status.HTTP_201_CREATED)


class TransportRouteCreateAPIView(SupportFeatureAPIView, OrganizationAccessMixin):
    def post(self, request, organization_public_id):
        organization, _ = self.get_organization(user=request.user, organization_public_id=organization_public_id)
        serializer = TransportRouteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        driver_assignment = get_object_or_404(DriverVehicleAssignment, organization=organization, public_id=data.pop("driver_vehicle_assignment_id"))
        worksite_id = data.pop("worksite_id")
        worksite = get_object_or_404(Worksite, organization=organization, public_id=worksite_id) if worksite_id else None
        route = create_transport_route(actor=request.user, organization=organization, driver_vehicle_assignment=driver_assignment, worksite=worksite, **data)
        return Response({"transport_route": {"id": str(route.public_id), "state": route.state, "reservation_expires_at": route.reservation_expires_at}}, status=status.HTTP_201_CREATED)


class TransportRouteStopCreateAPIView(SupportFeatureAPIView):
    def post(self, request, route_public_id):
        route = _operational_object_or_not_found(
            model=TransportRoute,
            user=request.user,
            public_id=route_public_id,
        )
        serializer = RouteStopCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        housing_site_id = data.pop("housing_site_id")
        housing_site = get_object_or_404(HousingSite, organization=route.organization, public_id=housing_site_id) if housing_site_id else None
        stop = add_route_stop(actor=request.user, route=route, housing_site=housing_site, **data)
        return Response({"route_stop": {"id": str(stop.public_id), "route_id": str(route.public_id), "sequence": stop.sequence, "kind": stop.kind}}, status=status.HTTP_201_CREATED)


class TransportRoutePassengerCreateAPIView(SupportFeatureAPIView):
    def post(self, request, route_public_id):
        route = _operational_object_or_not_found(
            model=TransportRoute,
            user=request.user,
            public_id=route_public_id,
        )
        serializer = RoutePassengerCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        connection = get_object_or_404(SupportConnection, organization=route.organization, public_id=data.pop("connection_id"))
        pickup_stop = get_object_or_404(RouteStop, route=route, public_id=data.pop("pickup_stop_id"))
        dropoff_stop = get_object_or_404(RouteStop, route=route, public_id=data.pop("dropoff_stop_id"))
        passenger = add_route_passenger(actor=request.user, route=route, connection=connection, pickup_stop=pickup_stop, dropoff_stop=dropoff_stop, **data)
        return Response({"transport_passenger": {"id": str(passenger.public_id), "route_id": str(route.public_id), "connection_id": str(connection.public_id)}}, status=status.HTTP_201_CREATED)


class TransportRoutePublishAPIView(SupportFeatureAPIView):
    def post(self, request, route_public_id):
        route = _operational_object_or_not_found(
            model=TransportRoute,
            user=request.user,
            public_id=route_public_id,
        )
        route = publish_transport_route(actor=request.user, route=route)
        return Response({"transport_route": {"id": str(route.public_id), "state": route.state, "published_at": route.published_at}})


class TransportRouteCancelAPIView(SupportFeatureAPIView):
    def post(self, request, route_public_id):
        route = _operational_object_or_not_found(
            model=TransportRoute,
            user=request.user,
            public_id=route_public_id,
        )
        route = cancel_transport_route(actor=request.user, route=route)
        return Response(
            {
                "transport_route": {
                    "id": str(route.public_id),
                    "state": route.state,
                    "cancelled_at": route.cancelled_at,
                }
            }
        )
