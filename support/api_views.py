from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils.dateparse import parse_date
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from .feature_flags import is_support_feature_enabled
from .models import (
    Announcement,
    AnnouncementAcknowledgement,
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
    SupportVacancy,
    TaskAssignment,
    TransportRoute,
    TransportPassengerAssignment,
    Vehicle,
    WorkerAccessScope,
    WorkerProjectAssignment,
    WorkTimeEntry,
    WorkerRequest,
    WorkerTask,
    WorkProject,
    Worksite,
)
from .permission_codes import (
    ANNOUNCEMENT_MANAGE,
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
    require_permission,
    require_worker_connection_access,
    worker_connection_queryset_for,
)
from .selectors.workspace import transport_workspace_snapshot
from .serializers import (
    AnnouncementCreateSerializer,
    ApplicationReviewSerializer,
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
    HousingPlaceCreateSerializer,
    HousingRoomCreateSerializer,
    HousingSiteCreateSerializer,
    MembershipInvitationCreateSerializer,
    PermissionCodeSerializer,
    SupportApplicationCreateSerializer,
    SupportMessageCreateSerializer,
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
)
from .services.audit import record_audit_event
from .services.conversations import (
    mark_conversation_read,
    open_manager_conversation,
    require_conversation_access,
    send_text_message,
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
)
from .services.worker_requests import (
    cancel_worker_request,
    decide_worker_request,
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
    create_housing_place,
    create_housing_room,
    create_housing_site,
    create_vehicle,
    create_work_project,
    create_worksite,
)
from .services.pipeline import (
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
                    "display_name": _user_display_name(application.candidate),
                },
                "vacancy": {
                    "id": str(application.vacancy.public_id),
                    "internal_title": application.vacancy.internal_title,
                },
                "citizenship_country_code": application.citizenship_country_code,
                "current_country_code": application.current_country_code,
                "availability_note": application.availability_note,
                "partner_reference_code": application.partner_reference_code,
                "revision": application.revision,
            }
        )
    return payload


def _candidate_application_payload(application):
    payload = _application_payload(application)
    connection = getattr(application, "support_connection", None)
    payload["connection"] = _connection_payload(connection) if connection is not None else None
    latest_decision = application.decision_events.order_by("-created_at", "-id").first()
    payload["last_decision"] = (
        {
            "action": latest_decision.action,
            "note": latest_decision.note,
            "created_at": latest_decision.created_at,
        }
        if latest_decision is not None
        else None
    )
    conversation = None
    if connection is not None:
        conversation = (
            SupportConversation.objects.filter(
                connection=connection,
                kind=SupportConversation.KIND_MANAGER,
                state=SupportConversation.STATE_ACTIVE,
                members__user=application.candidate,
                members__left_at__isnull=True,
            )
            .distinct()
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
    other_members = [
        member
        for member in conversation.members.select_related("user").filter(left_at__isnull=True)
        if member.user_id != viewer.id
    ]
    viewer_member = next(
        (member for member in conversation.members.all() if member.user_id == viewer.id),
        None,
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
            {"display_name": _user_display_name(member.user), "role": member.role}
            for member in other_members
        ],
        "updated_at": conversation.updated_at,
        "group_push_enabled": (
            viewer_member.group_push_enabled
            if conversation.kind == SupportConversation.KIND_GROUP and viewer_member is not None
            else None
        ),
    }


def _message_payload(message, *, viewer):
    return {
        "id": str(message.public_id),
        "body": "" if message.deleted_at else message.body,
        "original_language": message.original_language,
        "is_mine": message.sender_id == viewer.id,
        "sender_display_name": _user_display_name(message.sender) if message.sender else "",
        "created_at": message.created_at,
        "edited_at": message.edited_at,
        "deleted_at": message.deleted_at,
    }


def _staff_application_or_not_found(*, user, application_public_id):
    application = get_object_or_404(
        SupportApplication.objects.select_related("vacancy__organization", "candidate")
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
        return Response(
            {
                "organization": _organization_payload(organization, membership),
                "permissions": {
                    "pipeline_review": may_review_pipeline,
                    "worker_view": may_view_workers,
                    "request_decide": may_decide_requests,
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
                },
                "counts": {
                    "workers": allowed_connections.count() if may_view_workers else 0,
                    "pending_applications": (
                        SupportApplication.objects.filter(
                            vacancy__organization=organization,
                            status__in=(
                                SupportApplication.STATUS_SUBMITTED,
                                SupportApplication.STATUS_UNDER_REVIEW,
                            ),
                        ).count()
                        if may_review_pipeline
                        else 0
                    ),
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


def _transport_connection_choice_payload(connection):
    return {
        "id": str(connection.public_id),
        "display_name": _user_display_name(connection.candidate),
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
                ).select_related("candidate", "vacancy"),
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
            .select_related("connection__candidate")
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
            .prefetch_related("items__request__connection__candidate")
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
            .prefetch_related("shifts__connection__candidate")
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
                        "display_name": _user_display_name(item.candidate),
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
        ).select_related("candidate", "vacancy")
        term = (request.query_params.get("q") or "").strip()
        if term:
            queryset = queryset.filter(
                Q(candidate__first_name__icontains=term)
                | Q(candidate__last_name__icontains=term)
                | Q(candidate__username__icontains=term)
                | Q(vacancy__internal_title__icontains=term)
            )
        connections = queryset.order_by("-updated_at", "-id")[:250]
        return Response(
            {
                "results": [
                    {
                        "id": str(connection.public_id),
                        "candidate": {
                            "display_name": _user_display_name(connection.candidate),
                        },
                        "vacancy": {
                            "id": str(connection.vacancy.public_id),
                            "internal_title": connection.vacancy.internal_title,
                        },
                        "stage": connection.stage,
                        "visible_stage": connection.visible_stage,
                        "updated_at": connection.updated_at,
                    }
                    for connection in connections
                ]
            }
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
                ).select_related("candidate", "vacancy"),
            ),
            public_id=connection_public_id,
        )

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
                        "display_name": _user_display_name(connection.candidate),
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
            }
        )


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
            .select_related("vacancy", "candidate")
            .order_by("-submitted_at", "-id")
        )
        return Response(
            {"results": [_application_payload(item, include_staff_fields=True) for item in applications]}
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
        conversation = SupportConversation.objects.prefetch_related("members__user").get(pk=conversation.pk)
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
            if active_membership_for(user=request.user, organization=organization) is None:
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
            .prefetch_related("members__user")
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
            .prefetch_related("members__user")
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
        messages = conversation.messages.select_related("sender").all()
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
        message, created = send_text_message(
            sender=request.user,
            conversation=conversation,
            body=serializer.validated_data["body"],
            original_language=serializer.validated_data["original_language"],
            client_message_id=serializer.validated_data["client_message_id"],
        )
        return Response(
            {"created": created, "message": _message_payload(message, viewer=request.user)},
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
        return Response({"last_read_at": member.last_read_at})


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


def _in_app_notification_payload(notification):
    outbox = notification.outbox
    return {
        "id": str(notification.public_id),
        "code": outbox.notification_code,
        "target": {
            "kind": outbox.target_kind,
            "id": str(outbox.target_public_id),
            "key": outbox.target_key,
        },
        "read_at": notification.read_at,
        "created_at": notification.created_at,
    }


class MySupportNotificationListAPIView(SupportFeatureAPIView):
    def get(self, request):
        notifications = (
            InAppNotification.objects.filter(recipient=request.user)
            .select_related("outbox")
            .order_by("-created_at", "-id")[:100]
        )
        return Response({"results": [_in_app_notification_payload(item) for item in notifications]})


class SupportNotificationReadAPIView(SupportFeatureAPIView):
    def post(self, request, notification_public_id):
        notification = get_object_or_404(
            InAppNotification.objects.select_related("outbox"),
            public_id=notification_public_id,
            recipient=request.user,
        )
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at"])
        return Response({"notification": _in_app_notification_payload(notification)})


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
                "display_name": _user_display_name(item.connection.candidate),
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
            "display_name": _user_display_name(entry.connection.candidate),
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
        "submitted_at": item.submitted_at,
        "reviewed_at": item.reviewed_at,
        "cancelled_at": item.cancelled_at,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
    if include_staff_fields:
        payload["worker"] = {
            "id": str(item.connection.candidate_id),
            "display_name": _user_display_name(item.connection.candidate),
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
            "display_name": _user_display_name(item.connection.candidate),
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
            "display_name": _user_display_name(item.connection.candidate),
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
            "display_name": _user_display_name(item.connection.candidate),
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
            "passenger_assignments__connection__candidate",
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
                "scheduled_shift": _scheduled_shift_payload(scheduled_shift),
                "time_entry": _time_entry_payload(entry) if entry else None,
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
        return Response({"results": [_worker_request_payload(item) for item in results]})

    def post(self, request, connection_public_id):
        _require_active_support_access(request.user)
        connection = _worker_time_connection_or_not_found(
            user=request.user,
            connection_public_id=connection_public_id,
        )
        serializer = WorkerRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = submit_worker_request(
            worker=request.user,
            connection=connection,
            **serializer.validated_data,
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
            .select_related("candidate", "vacancy")
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
                        "display_name": _user_display_name(connection.candidate),
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
                "connection__candidate", "connection__vacancy", "reviewed_by"
            ).order_by("-submitted_at", "-created_at", "-id")[:250]
        )
        return Response(
            {"results": [_worker_request_payload(item, include_staff_fields=True) for item in items]}
        )


class WorkerRequestDecisionAPIView(SupportFeatureAPIView):
    action = None

    def post(self, request, request_public_id):
        serializer = WorkerRequestDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        manager_note = serializer.validated_data["manager_note"]
        if self.action in {"clarify", "decline"} and not manager_note:
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
            "connection__candidate", "connection__vacancy", "reviewed_by"
        ).get(pk=item.pk)
        return Response({"worker_request": _worker_request_payload(item, include_staff_fields=True)})


class WorkerRequestClarificationAPIView(WorkerRequestDecisionAPIView):
    action = "clarify"


class WorkerRequestApproveAPIView(WorkerRequestDecisionAPIView):
    action = "approve"


class WorkerRequestDeclineAPIView(WorkerRequestDecisionAPIView):
    action = "decline"


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
            .select_related("connection__candidate")
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
            "connection__candidate", "connection__vacancy", "scheduled_shift", "confirmed_by"
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
            "connection__candidate", "connection__vacancy", "scheduled_shift", "confirmed_by"
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
            "connection__candidate", "connection__vacancy", "scheduled_shift", "confirmed_by"
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
        sites = HousingSite.objects.filter(organization=organization).order_by("internal_name", "id")
        return Response({"results": [_housing_site_payload(item) for item in sites]})

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
        vehicles = Vehicle.objects.filter(organization=organization).order_by("internal_name", "id")
        return Response({"results": [{"id": str(item.public_id), "internal_name": item.internal_name, "registration_identifier": item.registration_identifier, "seat_capacity": item.seat_capacity, "is_active": item.is_active} for item in vehicles]})

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
