import csv
import logging
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlsplit

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime, parse_time
from django.utils.http import url_has_allowed_host_and_scheme
from rest_framework.exceptions import APIException, ValidationError

from jobs.web_i18n import get_lang, tr

from .feature_flags import is_support_feature_enabled
from .permissions import worker_connection_queryset_for
from .questionnaire import (
    DURATION_CHOICES,
    EXPERIENCE_SECTORS,
    LANGUAGE_LEVELS,
    LEGAL_STATUSES,
    label as questionnaire_label,
    questionnaire_is_complete,
    questionnaire_tags,
)
from .models import (
    DocumentRequestPackage,
    DriverVehicleAssignment,
    HousingAssignment,
    HousingPlace,
    HousingRoom,
    HousingSite,
    OrganizationMembership,
    ProjectCrewResourceAssignment,
    ProjectScheduleTemplate,
    RouteStop,
    ScheduledWorkShift,
    SupportApplication,
    SupportConnection,
    SupportConversation,
    TransportCrew,
    TransportPassengerAssignment,
    TransportRoute,
    Vehicle,
    WorkerProjectAssignment,
    WorkerAccessScope,
    WorkTimeEntry,
    WorkerRequest,
    WorkProject,
    Worksite,
)
from .selectors.workspace import (
    candidate_applications_snapshot,
    registry_snapshot,
    housing_workspace_snapshot,
    projects_snapshot,
    team_management_snapshot,
    timekeeping_snapshot,
    transport_workspace_snapshot,
    fleet_snapshot,
    worker_requests_snapshot,
    worker_card_snapshot,
    conversation_workspace_snapshot,
    workspace_snapshot,
)
from .serializers import (
    DocumentRequestPackageDecisionSerializer,
    HousingPlaceCreateSerializer,
    HousingRoomCreateSerializer,
    HousingSiteCreateSerializer,
    VehicleCreateSerializer,
    DriverVehicleAssignmentCreateSerializer,
    RoutePassengerCreateSerializer,
    RouteStopCreateSerializer,
    ScheduledWorkShiftCreateSerializer,
    TransportRouteCreateSerializer,
    WorkTimeEntryCorrectionSerializer,
    WorkTimeEntryStaffEditSerializer,
    WorkerRequestDecisionSerializer,
    WorkProjectCreateSerializer,
    WorksiteCreateSerializer,
    ProjectCreateSerializer,
    ProjectScheduleTemplateCreateSerializer,
)


logger = logging.getLogger(__name__)
from .services.operations import (
    add_passenger_to_driver_schedule,
    apply_transport_crew_schedule_override,
    create_transport_crew_for_schedule,
    ensure_transport_crew_for_route,
    remove_passenger_from_driver_schedule,
    replace_transport_crew_resources_for_dates,
    resolve_transport_crew_schedule_conflict,
    replace_passenger_in_driver_schedule,
    create_driver_vehicle_assignment,
    create_housing_assignment,
    create_worker_project_assignment,
    add_route_passenger,
    add_route_stop,
    cancel_housing_assignment,
    cancel_transport_route,
    cancel_worker_project_assignment,
    create_transport_route,
    delete_driver_vehicle_assignment_draft,
    edit_driver_vehicle_assignment_draft,
    delete_housing_assignment_draft,
    delete_transport_route_draft,
    delete_worker_project_assignment_draft,
    edit_route_stop,
    edit_housing_assignment_draft,
    publish_housing_assignment,
    publish_driver_vehicle_assignment,
    publish_transport_route,
    publish_worker_project_assignment,
    set_worker_driving_license,
    sync_worker_schedule_transport,
    schedule_housing_check_out,
    reschedule_future_housing_assignment,
)
from .services.timekeeping import (
    cancel_scheduled_shift,
    confirm_work_time_entry,
    create_scheduled_shift,
    delete_scheduled_shift_draft,
    edit_work_time_entry,
    publish_scheduled_shift,
    replace_scheduled_shift,
    request_work_time_correction,
)
from .services.worker_requests import decide_worker_request
from .services.pipeline import (
    approve_application,
    decline_application,
    request_application_clarification,
    transition_connection,
)
from .services.documents import (
    DOCUMENT_TYPE_KEYS,
    create_document_request_package,
    review_document_request_package,
)
from .services.registries import (
    create_housing_place,
    create_housing_room,
    create_housing_site,
    create_vehicle,
    create_work_project,
    create_worksite,
    create_project,
    update_project,
    create_project_schedule_template,
    delete_housing_room,
)
from .services.organizations import (
    create_membership_invitation,
    grant_worker_access_scope,
    revoke_worker_access_scope,
)
from .services.project_crews import (
    mark_worker_schedule_days_off,
    release_project_crew_member_days,
    restore_project_crew_member_days,
    restore_worker_schedule_days_off,
)
from .services.conversations import (
    mark_conversation_read,
    open_manager_conversation_for_staff,
    require_conversation_access,
    send_text_message,
)
from .permission_groups import TEAM_PERMISSION_GROUPS, permission_codes_for_group_ids


@login_required(login_url="employer:login")
def workspace_home(request):
    """First protected staff screen for JobHub Support.

    It is deliberately read-only.  The subsequent screen will add registry
    forms and assignment controls after the layout has been reviewed.
    """

    if not is_support_feature_enabled():
        raise Http404("support_not_available")
    snapshot = workspace_snapshot(
        user=request.user,
        organization_public_id=request.GET.get("organization"),
    )
    for item in snapshot["application_rows"]:
        item["status_label"] = tr(request, item.pop("status_key"))
    for item in snapshot["worker_rows"]:
        item["stage_label"] = tr(request, item.pop("stage_key"))
        item["detail_url"] = reverse(
            "support:worker-card",
            kwargs={"connection_public_id": item["connection_id"]},
        )
        for crew in item["crew_rows"]:
            crew["crew_name"] = crew["crew_name"] or tr(
                request,
                "support_workspace_crew_fallback",
            )
            crew["project_url"] = (
                reverse(
                    "support:project-first-detail",
                    kwargs={"project_public_id": crew["project_id"]},
                )
                + f"?organization={snapshot['organization'].public_id}"
            )
    for item in snapshot["operation_cards"]:
        item["label"] = tr(request, f"support_workspace_{item['key']}_drafts")
    if snapshot["permissions"]["transport"]:
        snapshot["transport_url"] = reverse("support:transport")
    if snapshot["permissions"]["time_view"] or snapshot["permissions"]["schedule"]:
        snapshot["time_url"] = reverse("support:time")
    if snapshot["permissions"]["request_decide"]:
        snapshot["requests_url"] = (
            f"{reverse('support:worker-requests')}?organization={snapshot['organization'].public_id}"
        )
    if snapshot["permissions"]["organization_manage"]:
        snapshot["team_url"] = reverse("support:team")
    return render(request, "support/workspace.html", snapshot)


def _candidate_applications_redirect(organization, *, status_filter, workspace_view="applications"):
    query = urlencode(
        {
            "organization": organization.public_id,
            "filter": status_filter,
            "view": workspace_view,
        }
    )
    return redirect(f"{reverse('support:candidate-applications')}?{query}")


def _candidate_application_operation(request, *, snapshot):
    """Run candidate review and pipeline transitions through domain services."""

    organization = snapshot["organization"]
    action = (request.POST.get("action") or "").strip()
    selected_filter = request.POST.get("filter") or snapshot["status_filter"]
    selected_view = request.POST.get("view") or snapshot["workspace_view"]
    try:
        if action in {"application_clarify", "application_approve", "application_decline"}:
            application = get_object_or_404(
                SupportApplication.objects.select_related(
                    "vacancy__organization",
                    "candidate",
                ),
                public_id=request.POST.get("application_id"),
                vacancy__organization=organization,
            )
            note = (request.POST.get("note") or "").strip()
            if action == "application_clarify":
                if not note:
                    raise ValidationError({"note": "application_review_note_required"})
                request_application_clarification(
                    actor=request.user,
                    application=application,
                    note=note,
                )
                message_key = "support_applications_clarification_sent"
            elif action == "application_decline":
                if not note:
                    raise ValidationError({"note": "application_review_note_required"})
                decline_application(
                    actor=request.user,
                    application=application,
                    note=note,
                )
                message_key = "support_applications_declined"
            else:
                approve_application(actor=request.user, application=application)
                message_key = "support_applications_approved"
        elif action.startswith("document_package_"):
            connection = get_object_or_404(
                SupportConnection.objects.select_related("organization", "candidate"),
                public_id=request.POST.get("connection_id"),
                organization=organization,
                is_archived=False,
            )
            message_key = _perform_document_package_operation(
                request,
                organization=organization,
                connection=connection,
            )
        elif action in {
            "connection_chat",
            "connection_documents",
            "connection_coordinator",
            "connection_active_worker",
            "connection_close",
        }:
            connection = get_object_or_404(
                SupportConnection.objects.select_related("organization", "candidate"),
                public_id=request.POST.get("connection_id"),
                organization=organization,
                is_archived=False,
            )
            if action == "connection_chat":
                conversation, _ = open_manager_conversation_for_staff(
                    actor=request.user,
                    connection=connection,
                )
                conversation_url = reverse(
                    "support:conversation-detail",
                    kwargs={"conversation_public_id": conversation.public_id},
                )
                return redirect(
                    f"{conversation_url}?{urlencode({'organization': organization.public_id})}"
                )
            next_stage = {
                "connection_documents": SupportConnection.STAGE_DOCUMENTS,
                "connection_coordinator": SupportConnection.STAGE_COORDINATOR,
                "connection_active_worker": SupportConnection.STAGE_ACTIVE_WORKER,
                "connection_close": SupportConnection.STAGE_CLOSED,
            }[action]
            transition_connection(
                actor=request.user,
                connection=connection,
                next_stage=next_stage,
                reason=(request.POST.get("note") or "").strip(),
            )
            message_key = "support_applications_stage_changed"
        else:
            raise ValueError("candidate_application_operation_unknown")
    except (APIException, ValueError) as error:
        error_text = str(getattr(error, "detail", error))
        if action.startswith("document_package_"):
            message_key = _document_operation_error_key(error)
        elif "candidate_has_active_support_assignment" in error_text:
            message_key = "support_applications_error_already_employed"
        elif "unsupported_connection_transition" in error_text:
            message_key = "support_applications_error_wrong_stage"
        elif "application_review_note_required" in error_text:
            message_key = "support_applications_error_note_required"
        else:
            message_key = "support_applications_operation_error"
        messages.error(request, tr(request, message_key))
    except Exception:
        logger.exception(
            "[SUPPORT-CANDIDATE-OPERATION-ERROR] action=%s connection_id=%s",
            action,
            request.POST.get("connection_id"),
        )
        messages.error(request, tr(request, "support_applications_operation_error"))
    else:
        messages.success(request, tr(request, message_key))
    return _candidate_applications_redirect(
        organization,
        status_filter=selected_filter,
        workspace_view=selected_view,
    )


@login_required(login_url="employer:login")
def candidate_applications_workspace(request):
    """Employer queue from a vacancy questionnaire to active employment."""

    if not is_support_feature_enabled():
        raise Http404("support_not_available")
    questionnaire_filters = {
        key: (request.GET.get(key) or "").strip()
        for key in (
            "legal_status",
            "duration",
            "experience",
            "english_level",
            "license",
            "needs_housing",
            "needs_transport",
            "travelling_with_partner",
            "complete",
            "available_by",
        )
    }
    snapshot = candidate_applications_snapshot(
        user=request.user,
        organization_public_id=request.GET.get("organization"),
        status_filter=request.GET.get("filter") or "open",
        questionnaire_filters=questionnaire_filters,
        sort=request.GET.get("sort") or "newest",
        workspace_view=request.GET.get("view") or "applications",
    )
    if request.method == "POST":
        return _candidate_application_operation(request, snapshot=snapshot)

    page_language = get_lang(request)
    application_items = list(snapshot["applications"])
    known_application_ids = {item.id for item in application_items}
    for connection in snapshot["processing_connections"]:
        connection.application.connection_record = connection
        connection.application.manager_conversation = connection.manager_conversation
        connection.application.is_open = False
        connection.application.may_move_to_documents = False
        connection.application.may_move_to_coordinator = True
        connection.application.may_move_to_active_worker = False
        if connection.application_id not in known_application_ids:
            application_items.append(connection.application)
            known_application_ids.add(connection.application_id)

    for item in application_items:
        item.status_label = tr(request, f"support_application_{item.status}")
        item.language_label = item.preferred_language.upper()
        answers = item.questionnaire_answers or {}
        item.questionnaire_complete = questionnaire_is_complete(answers)
        item.questionnaire_tags = questionnaire_tags(item, language=page_language)
        item.questionnaire_sections = _candidate_questionnaire_sections(
            answers,
            language=page_language,
        )
        for section in item.questionnaire_sections:
            section["label"] = tr(
                request,
                f"support_questionnaire_section_{section['key']}",
            )
            for fact in section["facts"]:
                if not fact.get("label"):
                    fact["label"] = tr(request, f"support_questionnaire_{fact['key']}")
        for event in item.decision_events.all():
            event.action_label = tr(
                request,
                f"support_application_event_{event.action}",
            )
        if item.connection_record is not None:
            item.connection_record.stage_label = tr(
                request,
                f"support_stage_{item.connection_record.stage}",
            )
            for event in item.connection_record.stage_events.all():
                event.previous_stage_label = tr(
                    request,
                    f"support_stage_{event.previous_stage}",
                )
                event.next_stage_label = tr(
                    request,
                    f"support_stage_{event.next_stage}",
                )
            if (
                snapshot["permissions"]["workers"]
                or snapshot["permissions"]["document_request"]
            ) and item.connection_record.stage in {
                SupportConnection.STAGE_DOCUMENTS,
                SupportConnection.STAGE_COORDINATOR,
                SupportConnection.STAGE_ACTIVE_WORKER,
            }:
                item.worker_url = reverse(
                    "support:worker-card",
                    kwargs={"connection_public_id": item.connection_record.public_id},
                )
        if item.manager_conversation is not None:
            item.conversation_url = reverse(
                "support:conversation-detail",
                kwargs={"conversation_public_id": item.manager_conversation.public_id},
            )

    document_status_labels = {
        DocumentRequestPackage.STATUS_REQUESTED: tr(request, "support_documents_status_requested"),
        DocumentRequestPackage.STATUS_SENT_TO_EMPLOYER: tr(request, "support_documents_status_sent"),
        DocumentRequestPackage.STATUS_NEEDS_CORRECTION: tr(request, "support_documents_status_correction"),
        DocumentRequestPackage.STATUS_COMPLETED: tr(request, "support_documents_status_completed"),
        DocumentRequestPackage.STATUS_NOT_REQUIRED: tr(request, "support_documents_status_not_required"),
        DocumentRequestPackage.STATUS_CANCELLED: tr(request, "support_documents_status_cancelled"),
    }
    for connection in snapshot["processing_connections"]:
        connection.application_record = connection.application
        connection.stage_label = tr(request, f"support_stage_{connection.stage}")
        connection.chat_url = (
            reverse(
                "support:conversation-detail",
                kwargs={"conversation_public_id": connection.manager_conversation.public_id},
            )
            if connection.manager_conversation is not None
            else None
        )
        for package in connection.document_packages:
            labels = []
            for requested_item in package.requested_items:
                item_type = (requested_item.get("type") or "").strip()
                if item_type == "custom":
                    labels.append((requested_item.get("custom_label") or "").strip())
                else:
                    labels.append(tr(request, f"support_document_{item_type}"))
            package.requested_items_label = ", ".join(label for label in labels if label)
            package.status_label = document_status_labels[package.status]

    snapshot["workspace_url"] = (
        f"{reverse('support:workspace')}?organization={snapshot['organization'].public_id}"
    )
    snapshot["filter_base_url"] = (
        f"{reverse('support:candidate-applications')}?organization={snapshot['organization'].public_id}&view=applications"
    )
    snapshot["applications_tab_url"] = (
        f"{reverse('support:candidate-applications')}?organization={snapshot['organization'].public_id}&view=applications"
    )
    snapshot["processing_tab_url"] = (
        f"{reverse('support:candidate-applications')}?organization={snapshot['organization'].public_id}&view=processing"
    )
    snapshot["questionnaire_filter_options"] = {
        "legal_statuses": [
            (value, questionnaire_label(value, page_language)) for value in LEGAL_STATUSES
        ],
        "durations": [
            (value, questionnaire_label(value, page_language)) for value in DURATION_CHOICES
        ],
        "experience": [
            (value, questionnaire_label(value, page_language)) for value in EXPERIENCE_SECTORS
        ],
        "language_levels": [
            (value, questionnaire_label(value, page_language)) for value in LANGUAGE_LEVELS
        ],
    }
    return render(request, "support/candidate_applications_workspace.html", snapshot)


def _candidate_questionnaire_sections(answers, *, language):
    """Group screening answers into compact manager-friendly sections."""

    if not answers:
        return []

    def joined(key):
        return ", ".join(questionnaire_label(value, language) for value in answers.get(key, [])) or "—"

    def formatted_date(key):
        raw = answers.get(key)
        if not raw:
            return "—"
        try:
            return datetime.strptime(raw, "%Y-%m-%d").strftime("%d.%m.%Y")
        except (TypeError, ValueError):
            return str(raw)

    def fact(key, value, *, label=None):
        return {"key": key, "value": value, "label": label}

    yes_no = lambda value: questionnaire_label("yes" if value else "no", language)
    conditions = answers.get("work_conditions") or {}
    sections = [
        ("readiness", [
            fact("legal_status", questionnaire_label(answers.get("legal_status"), language)),
            fact("document_valid_until", formatted_date("document_valid_until")),
            fact("current_city", answers.get("current_city") or "—"),
            fact("available_from", formatted_date("available_from")),
            fact("planned_duration", questionnaire_label(answers.get("planned_duration"), language)),
        ]),
        ("experience", [
            fact("experience_sectors", joined("experience_sectors")),
            fact("experience_duration", questionnaire_label(answers.get("experience_duration"), language)),
            fact("work_countries", ", ".join(answers.get("work_countries", [])) or "—"),
            fact("last_position", answers.get("last_position") or "—"),
        ]),
        ("languages", [
            fact("english_level", questionnaire_label(answers.get("english_level"), language)),
            fact("polish_level", questionnaire_label(answers.get("polish_level"), language)),
            fact("dutch_level", questionnaire_label(answers.get("dutch_level"), language)),
        ]),
        ("driving", [
            fact("driving_license", yes_no(answers.get("has_driving_license"))),
            fact("driving_categories", joined("driving_license_categories")),
            fact("driving_license_valid_in_eu", yes_no(answers.get("driving_license_valid_in_eu"))),
            fact("driving_experience", questionnaire_label(answers.get("driving_experience"), language)),
            fact("willing_crew_driver", yes_no(answers.get("willing_crew_driver"))),
            fact("has_own_car", yes_no(answers.get("has_own_car"))),
            fact("qualifications", joined("qualifications")),
        ]),
        ("conditions", [
            *[
                fact(
                    f"condition_{key}",
                    questionnaire_label(value, language),
                    label=questionnaire_label(key, language),
                )
                for key, value in conditions.items()
            ],
            fact("shift_preferences", joined("shift_preferences")),
            fact("overtime_willing", questionnaire_label(answers.get("overtime_willing"), language)),
            fact("unavailable_dates_note", answers.get("unavailable_dates_note") or "—"),
        ]),
        ("relocation", [
            fact("needs_housing", yes_no(answers.get("needs_housing"))),
            fact("needs_transport", yes_no(answers.get("needs_transport"))),
            fact("travelling_with_partner", yes_no(answers.get("travelling_with_partner"))),
            fact("shared_room_preference", questionnaire_label(answers.get("shared_room_preference"), language)),
            fact("planned_move_in", formatted_date("planned_move_in")),
            fact("safety_policy_accepted", yes_no(answers.get("safety_policy_accepted"))),
        ]),
    ]
    return [
        {"key": key, "facts": facts}
        for key, facts in sections
        if facts
    ]


@login_required(login_url="employer:login")
def workers_workspace(request):
    """Dedicated worker list without dashboard and candidate summary blocks."""

    if not is_support_feature_enabled():
        raise Http404("support_not_available")
    snapshot = workspace_snapshot(
        user=request.user,
        organization_public_id=request.GET.get("organization"),
        worker_limit=None,
    )
    if not snapshot["permissions"]["workers"]:
        raise Http404("support_workers_not_available")
    for item in snapshot["worker_rows"]:
        item["stage_label"] = tr(request, item.pop("stage_key"))
        if item.get("driver_resource") is not None:
            item["transport_role"] = "driver_vehicle"
            item["transport_role_label"] = tr(
                request,
                "support_workspace_driver_with_vehicle",
            )
        elif item.get("has_driving_license"):
            item["transport_role"] = "driver"
            item["transport_role_label"] = tr(
                request,
                "support_workspace_driver_only",
            )
        else:
            item["transport_role"] = "passenger"
            item["transport_role_label"] = tr(
                request,
                "support_workspace_passenger",
            )
        item["detail_url"] = reverse(
            "support:worker-card",
            kwargs={"connection_public_id": item["connection_id"]},
        )
        housing_assignment = item.get("housing_assignment")
        if housing_assignment is not None:
            housing_place = housing_assignment.place
            housing_site = housing_place.room.site
            item["housing_name"] = housing_site.internal_name
            item["housing_url"] = reverse("support:housing") + "?" + urlencode(
                {
                    "organization": snapshot["organization"].public_id,
                    "site": housing_site.public_id,
                    "highlight_place": housing_place.public_id,
                }
            )
        for crew in item["crew_rows"]:
            crew["crew_name"] = crew["crew_name"] or tr(
                request,
                "support_workspace_crew_fallback",
            )
            crew["project_url"] = (
                reverse(
                    "support:project-first-detail",
                    kwargs={"project_public_id": crew["project_id"]},
                )
                + f"?organization={snapshot['organization'].public_id}"
            )
    return render(request, "support/workers_workspace.html", snapshot)


@login_required(login_url="employer:login")
def conversations_workspace(request):
    """List only Support chats assigned to the current employee."""

    if not is_support_feature_enabled():
        raise Http404("support_not_available")
    snapshot = conversation_workspace_snapshot(
        user=request.user,
        organization_public_id=request.GET.get("organization"),
    )
    for item in snapshot["conversation_rows"]:
        item["kind_label"] = tr(request, f"support_chat_kind_{item['kind']}")
        item["detail_url"] = reverse(
            "support:conversation-detail",
            kwargs={"conversation_public_id": item["conversation_id"]},
        )
    return render(request, "support/conversations.html", snapshot)


@login_required(login_url="employer:login")
def conversation_detail(request, conversation_public_id):
    """Safe browser view for one Support chat, with text messages only."""

    if not is_support_feature_enabled():
        raise Http404("support_not_available")
    snapshot = conversation_workspace_snapshot(
        user=request.user,
        organization_public_id=request.GET.get("organization"),
    )
    conversation = get_object_or_404(
        SupportConversation.objects.filter(
            organization=snapshot["organization"],
            state=SupportConversation.STATE_ACTIVE,
            members__user=request.user,
            members__left_at__isnull=True,
        )
        .prefetch_related("members__user", "messages__sender")
        .distinct(),
        public_id=conversation_public_id,
    )
    try:
        require_conversation_access(user=request.user, conversation=conversation)
    except APIException as exc:
        raise Http404("support_conversation_not_found") from exc

    detail_url = (
        f"{reverse('support:conversation-detail', kwargs={'conversation_public_id': conversation.public_id})}"
        f"?organization={snapshot['organization'].public_id}"
    )
    if request.method == "POST":
        body = (request.POST.get("body") or "").strip()
        if not body or len(body) > 1500 or "\x00" in body:
            messages.error(request, tr(request, "support_chat_message_invalid"))
        else:
            try:
                send_text_message(
                    sender=request.user,
                    conversation=conversation,
                    body=body,
                    original_language=get_lang(request),
                    client_message_id=uuid.uuid4(),
                )
            except APIException:
                messages.error(request, tr(request, "support_chat_send_error"))
            else:
                return redirect(detail_url)
    else:
        mark_conversation_read(user=request.user, conversation=conversation)

    participant_names = [
        item.user.get_full_name().strip() or item.user.username
        for item in conversation.members.all()
        if item.left_at is None and item.user_id != request.user.id
    ]
    snapshot.update(
        {
            "conversation": conversation,
            "conversation_kind_label": tr(request, f"support_chat_kind_{conversation.kind}"),
            "participant_names": participant_names,
            "messages": list(conversation.messages.select_related("sender").order_by("created_at", "id")[:500]),
        }
    )
    return render(request, "support/conversation_detail.html", snapshot)


def _aware_datetime(value, *, required=True):
    raw_value = (value or "").strip()
    if not raw_value:
        if required:
            raise ValueError("datetime_required")
        return None
    parsed = parse_datetime(raw_value)
    if parsed is None:
        raise ValueError("datetime_invalid")
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _operation_date(value, *, required=True):
    raw_value = (value or "").strip()
    if not raw_value:
        if required:
            raise ValueError("date_required")
        return None
    parsed = parse_date(raw_value)
    if parsed is None:
        raise ValueError("date_invalid")
    return parsed


def _operation_date_start(value, *, required=True):
    """Turn a date-only Support field into the local start of that day."""

    parsed = _operation_date(value, required=required)
    if parsed is None:
        return None
    return timezone.make_aware(
        datetime.combine(parsed, datetime.min.time()),
        timezone.get_current_timezone(),
    )


def _operation_time(value, *, required=True):
    raw_value = (value or "").strip()
    if not raw_value:
        if required:
            raise ValueError("time_required")
        return None
    parsed = parse_time(raw_value)
    if parsed is None:
        raise ValueError("time_invalid")
    return parsed


def _worker_card_redirect(
    connection,
    *,
    tab=None,
    month=None,
    site=None,
    transport_template=None,
    transport_crew=None,
    documents=False,
):
    """Return to the same focused worker tab after a safe POST operation."""

    if tab in {"company", "transport", "work_transport"}:
        tab = "work_transport"
    query = {}
    if tab in {"work_transport", "housing"}:
        query["tab"] = tab
    if month:
        query["month"] = month
    if site:
        query["site"] = site
    if tab == "work_transport" and transport_template:
        query["transport_template"] = transport_template
    if tab == "work_transport" and transport_crew:
        query["transport_crew"] = transport_crew
    if documents:
        query["documents"] = "1"
    base_url = reverse(
        "support:worker-card",
        kwargs={"connection_public_id": connection.public_id},
    )
    return redirect(f"{base_url}?{urlencode(query)}" if query else base_url)


def _registry_redirect(organization):
    return redirect(
        f"{reverse('support:registries')}?organization={organization.public_id}"
    )


def _validated_post(
    serializer_class,
    request,
    *,
    nullable_fields=(),
    ignored_fields=(),
    list_fields=(),
):
    """Validate a regular web form with the same rules as the public API."""

    data = request.POST.dict()
    data.pop("action", None)
    data.pop("csrfmiddlewaretoken", None)
    for field in ignored_fields:
        data.pop(field, None)
    for field in list_fields:
        data[field] = request.POST.getlist(field)
    for field in nullable_fields:
        if data.get(field) == "":
            data[field] = None
    serializer = serializer_class(data=data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def _transport_operation_error_key(error):
    """Turn expected route validation codes into an actionable web message."""

    detail = str(getattr(error, "detail", error))
    if "period_end_must_not_be_before_start" in detail:
        return "support_transport_route_end_before_start"
    if "route_outside_driver_vehicle_assignment_period" in detail:
        return "support_transport_route_outside_driver_period"
    if "driver_vehicle_assignment_already_has_active_route" in detail:
        return "support_transport_route_already_exists"
    if "transport_schedule_crew_already_exists" in detail:
        return "support_transport_crew_already_exists"
    if "driver_template_schedule_required" in detail:
        return "support_transport_crew_add_no_schedule"
    if "driver_vehicle_assignment_required" in detail:
        return "support_transport_crew_add_no_vehicle"
    if "driver_license_required" in detail:
        return "support_transport_crew_add_no_license"
    if "transport_crew_driver_unchanged" in detail:
        return "support_transport_driver_unchanged"
    if "transport_crew_driver_busy" in detail:
        return "support_transport_driver_busy"
    if "schedule_dates_required" in detail:
        return "support_transport_driver_dates_required"
    if "driver_vehicle_assignment_not_active_for_selected_dates" in detail:
        return "support_transport_driver_vehicle_dates_unavailable"
    if (
        "driver_vehicle_assignment_not_available" in detail
        or "driver_vehicle_assignment_not_published" in detail
    ):
        return "support_transport_driver_vehicle_unavailable"
    if "transport_crew_selected_day_has_no_shift" in detail:
        return "support_transport_driver_day_has_no_shift"
    if "transport_crew_vehicle_capacity_too_small" in detail:
        return "support_transport_driver_vehicle_too_small"
    if "passenger_housing_required" in detail:
        return "support_transport_passenger_requires_housing"
    if "transport_crew_full" in detail:
        return "support_transport_crew_full"
    if "driver_already_has_vehicle_assignment" in detail:
        return "support_transport_passenger_is_driver"
    if "transport_passenger_unchanged" in detail:
        return "support_transport_passenger_unchanged"
    if "connection_not_ready_for_operations" in detail:
        return "support_transport_passenger_not_ready"
    if "connection_already_in_route" in detail:
        return "support_transport_passenger_already_in_crew"
    if "driver_cannot_be_route_passenger" in detail:
        return "support_transport_passenger_same_as_driver"
    if (
        "project_schedule_template_not_available" in detail
        or "transport_crew_template_mismatch" in detail
    ):
        return "support_transport_template_unavailable"
    if "published_assignment_for_worker_required" in detail:
        return "support_transport_passenger_assignment_unavailable"
    if "work_project_capacity_reached" in detail:
        return "support_worker_work_project_capacity_reached"
    return "support_transport_operation_error"


def _worker_operation_error_key(error):
    """Expose expected work-assignment conflicts without hiding the cause."""

    detail = str(getattr(error, "detail", error))
    if "housing_place_conflicts_with_published_assignment" in detail:
        return "support_worker_housing_place_conflict"
    if "housing_worker_conflicts_with_published_assignment" in detail:
        return "support_worker_housing_worker_conflict"
    if "work_assignment_conflicts_with_published_assignment" in detail:
        return "support_worker_work_assignment_conflict"
    if "work_project_capacity_reached" in detail:
        return "support_worker_work_project_capacity_reached"
    if "project_schedule_template_break_invalid" in detail:
        return "support_worker_project_template_break_invalid"
    if "project_schedule_template_not_available" in detail:
        return "support_worker_project_template_not_available"
    if "schedule_dates_required" in detail:
        return "support_worker_schedule_dates_required"
    if "selected_schedule_days_have_no_shifts" in detail:
        return "support_worker_schedule_days_already_free"
    if "selected_schedule_days_have_no_day_off" in detail:
        return "support_worker_schedule_no_day_off"
    if "selected_schedule_days_have_no_absence" in detail:
        return "support_worker_schedule_no_absence"
    if "current_scheduled_shift_already_exists" in detail:
        return "support_worker_schedule_day_already_has_shift"
    if "driver_vehicle_assignment_required" in detail:
        return "support_transport_crew_add_no_vehicle"
    if "driver_template_schedule_required" in detail:
        return "support_transport_crew_add_no_schedule"
    if "passenger_housing_required" in detail:
        return "support_transport_crew_add_no_housing"
    if "transport_crew_full" in detail:
        return "support_transport_crew_full"
    if "published_assignment_for_selected_project_required" in detail:
        return "support_worker_quick_shift_active_assignment_required"
    if "transport_crew_override_time_required" in detail:
        return "support_crew_override_time_required"
    if "transport_crew_override_kind_invalid" in detail:
        return "support_crew_override_kind_invalid"
    if "transport_crew_has_no_participants" in detail:
        return "support_crew_override_no_participants"
    if "scheduled_shift_conflict_not_found" in detail:
        return "support_crew_conflict_not_found"
    return "support_worker_operation_error"


def _project_operation_error_key(error):
    """Keep project-template validation errors understandable in the web UI."""

    detail = str(getattr(error, "detail", error))
    if "project_schedule_template_name_already_exists" in detail:
        return "support_project_schedule_name_already_exists"
    if "project_schedule_template_name_required" in detail:
        return "support_project_schedule_name_required"
    if "time has the wrong format" in detail.lower():
        return "support_project_schedule_time_invalid"
    return "support_project_operation_error"


def _document_operation_error_key(error):
    """Return a precise message for document-request validation failures."""

    detail = str(getattr(error, "detail", error))
    if "verified_document_email_required" in detail:
        return "support_documents_error_email_required"
    if "document_type_required" in detail:
        return "support_documents_error_type_required"
    if "document_custom_label_invalid" in detail:
        return "support_documents_error_custom_label"
    return _worker_operation_error_key(error)


def _perform_document_package_operation(request, *, organization, connection):
    """Run the shared document hand-off workflow from any employer workspace."""

    action = (request.POST.get("action") or "").strip()
    if action == "document_package_create":
        requested_items = []
        for document_type in request.POST.getlist("document_type"):
            normalized = document_type.strip()
            if normalized not in DOCUMENT_TYPE_KEYS:
                raise ValueError("document_type_invalid")
            custom_label = ""
            if normalized == "custom":
                custom_label = (request.POST.get("custom_document_label") or "").strip()
                if not custom_label or len(custom_label) > 80:
                    raise ValueError("document_custom_label_invalid")
            item = {"type": normalized, "custom_label": custom_label}
            if item not in requested_items:
                requested_items.append(item)
        if not requested_items:
            raise ValueError("document_type_required")
        create_document_request_package(
            actor=request.user,
            organization=organization,
            connection=connection,
            requested_items=requested_items,
            additional_instructions=(request.POST.get("additional_instructions") or "").strip(),
        )
        return "support_document_package_created"

    if action in {
        "document_package_correction",
        "document_package_complete",
        "document_package_not_required",
        "document_package_cancel",
    }:
        package = get_object_or_404(
            DocumentRequestPackage.objects.filter(
                organization=organization,
                connection=connection,
            ).select_related("organization", "connection"),
            public_id=request.POST.get("package_id"),
        )
        data = _validated_post(
            DocumentRequestPackageDecisionSerializer,
            request,
            ignored_fields=("package_id", "connection_id", "filter", "view"),
        )
        review_document_request_package(
            actor=request.user,
            package=package,
            action={
                "document_package_correction": "needs_correction",
                "document_package_complete": "complete",
                "document_package_not_required": "not_required",
                "document_package_cancel": "cancel",
            }[action],
            manager_note=data["manager_note"],
        )
        return "support_document_package_updated"

    raise ValueError("document_package_operation_unknown")


def _worker_card_operation(request, *, snapshot):
    """Create or publish an operational draft through the existing services."""

    connection = snapshot["connection"]
    organization = snapshot["organization"]
    action = (request.POST.get("action") or "").strip()
    try:
        if action == "driving_license_set":
            set_worker_driving_license(
                actor=request.user,
                connection=connection,
                has_driving_license=request.POST.get("has_driving_license") == "1",
            )
            messages.success(request, tr(request, "support_worker_driving_license_updated"))
        elif action.startswith("document_package_"):
            message_key = _perform_document_package_operation(
                request,
                organization=organization,
                connection=connection,
            )
            messages.success(request, tr(request, message_key))
        elif action == "housing_draft":
            place = get_object_or_404(
                HousingPlace.objects.filter(room__site__organization=organization),
                public_id=request.POST.get("place_id"),
            )
            check_in_at = _operation_date_start(request.POST.get("check_in_on"))
            create_housing_assignment(
                actor=request.user,
                organization=organization,
                connection=connection,
                place=place,
                check_in_at=check_in_at,
                check_out_at=None,
            )
            messages.success(request, tr(request, "support_worker_draft_created"))
        elif action == "housing_draft_edit":
            assignment = get_object_or_404(
                HousingAssignment.objects.filter(organization=organization, connection=connection),
                public_id=request.POST.get("assignment_id"),
            )
            edit_housing_assignment_draft(
                actor=request.user,
                assignment=assignment,
                check_in_at=_operation_date_start(request.POST.get("check_in_on")),
            )
            messages.success(request, tr(request, "support_worker_housing_draft_updated"))
        elif action == "housing_draft_delete":
            assignment = get_object_or_404(
                HousingAssignment.objects.filter(organization=organization, connection=connection),
                public_id=request.POST.get("assignment_id"),
            )
            delete_housing_assignment_draft(actor=request.user, assignment=assignment)
            messages.success(request, tr(request, "support_draft_deleted"))
        elif action == "work_draft":
            project = get_object_or_404(
                WorkProject.objects.filter(organization=organization),
                public_id=request.POST.get("project_id"),
            )
            create_worker_project_assignment(
                actor=request.user,
                organization=organization,
                connection=connection,
                project=project,
                worker_role=(request.POST.get("worker_role") or "").strip(),
            )
            messages.success(request, tr(request, "support_worker_draft_created"))
        elif action == "work_draft_delete":
            assignment = get_object_or_404(
                WorkerProjectAssignment.objects.filter(
                    organization=organization,
                    connection=connection,
                ),
                public_id=request.POST.get("assignment_id"),
            )
            delete_worker_project_assignment_draft(actor=request.user, assignment=assignment)
            messages.success(request, tr(request, "support_draft_deleted"))
        elif action == "driver_vehicle_draft":
            vehicle = get_object_or_404(
                Vehicle.objects.filter(organization=organization),
                public_id=request.POST.get("vehicle_id"),
            )
            starts_on = _operation_date(request.POST.get("starts_on"))
            create_driver_vehicle_assignment(
                actor=request.user,
                organization=organization,
                driver_connection=connection,
                vehicle=vehicle,
                starts_on=starts_on,
                ends_on=None,
            )
            messages.success(request, tr(request, "support_worker_draft_created"))
        elif action == "driver_vehicle_publish":
            assignment = get_object_or_404(
                DriverVehicleAssignment.objects.filter(
                    organization=organization,
                    driver_connection=connection,
                ),
                public_id=request.POST.get("driver_vehicle_assignment_id"),
            )
            publish_driver_vehicle_assignment(actor=request.user, assignment=assignment)
            messages.success(request, tr(request, "support_transport_driver_vehicle_published"))
        elif action == "driver_vehicle_delete":
            assignment = get_object_or_404(
                DriverVehicleAssignment.objects.filter(
                    organization=organization,
                    driver_connection=connection,
                ),
                public_id=request.POST.get("driver_vehicle_assignment_id"),
            )
            delete_driver_vehicle_assignment_draft(actor=request.user, assignment=assignment)
            messages.success(request, tr(request, "support_draft_deleted"))
        elif action == "route_create":
            data = _validated_post(
                TransportRouteCreateSerializer,
                request,
                nullable_fields=("ends_on", "worksite_id", "departure_time"),
                ignored_fields=("return_tab", "return_month", "return_site"),
            )
            driver_assignment = get_object_or_404(
                DriverVehicleAssignment.objects.filter(
                    organization=organization,
                    driver_connection=connection,
                ),
                public_id=data.pop("driver_vehicle_assignment_id"),
            )
            worksite_id = data.pop("worksite_id")
            worksite = (
                get_object_or_404(
                    Worksite,
                    organization=organization,
                    public_id=worksite_id,
                )
                if worksite_id
                else None
            )
            create_transport_route(
                actor=request.user,
                organization=organization,
                driver_vehicle_assignment=driver_assignment,
                worksite=worksite,
                **data,
            )
            messages.success(request, tr(request, "support_transport_route_created"))
        elif action == "route_stop_create":
            route = get_object_or_404(
                TransportRoute.objects.filter(
                    organization=organization,
                    driver_vehicle_assignment__driver_connection=connection,
                ),
                public_id=request.POST.get("route_id"),
            )
            data = _validated_post(
                RouteStopCreateSerializer,
                request,
                nullable_fields=("housing_site_id",),
                ignored_fields=(
                    "route_id",
                    "return_tab",
                    "return_month",
                    "return_site",
                ),
            )
            housing_site_id = data.pop("housing_site_id")
            housing_site = (
                get_object_or_404(
                    HousingSite,
                    organization=organization,
                    public_id=housing_site_id,
                )
                if housing_site_id
                else None
            )
            add_route_stop(
                actor=request.user,
                route=route,
                housing_site=housing_site,
                **data,
            )
            messages.success(request, tr(request, "support_transport_stop_added"))
        elif action == "route_passenger_create":
            route = get_object_or_404(
                TransportRoute.objects.filter(
                    organization=organization,
                    driver_vehicle_assignment__driver_connection=connection,
                ),
                public_id=request.POST.get("route_id"),
            )
            data = _validated_post(
                RoutePassengerCreateSerializer,
                request,
                ignored_fields=(
                    "route_id",
                    "return_tab",
                    "return_month",
                    "return_site",
                ),
            )
            passenger_connection = get_object_or_404(
                SupportConnection,
                organization=organization,
                public_id=data.pop("connection_id"),
            )
            pickup_stop = get_object_or_404(
                RouteStop,
                route=route,
                public_id=data.pop("pickup_stop_id"),
            )
            dropoff_stop = get_object_or_404(
                RouteStop,
                route=route,
                public_id=data.pop("dropoff_stop_id"),
            )
            add_route_passenger(
                actor=request.user,
                route=route,
                connection=passenger_connection,
                pickup_stop=pickup_stop,
                dropoff_stop=dropoff_stop,
                **data,
            )
            messages.success(request, tr(request, "support_transport_passenger_added"))
        elif action == "transport_schedule_crew_create":
            driver_assignment = get_object_or_404(
                DriverVehicleAssignment.objects.select_related(
                    "driver_connection",
                    "vehicle",
                ).filter(
                    organization=organization,
                    state=DriverVehicleAssignment.STATE_PUBLISHED,
                    driver_connection__in=worker_connection_queryset_for(
                        user=request.user,
                        organization=organization,
                        queryset=SupportConnection.objects.filter(
                            organization=organization,
                            is_archived=False,
                        ),
                    ),
                ),
                public_id=request.POST.get("driver_vehicle_assignment_id"),
            )
            schedule_template = get_object_or_404(
                ProjectScheduleTemplate.objects.select_related("project__worksite"),
                project__organization=organization,
                is_active=True,
                public_id=request.POST.get("schedule_template_id"),
            )
            route = create_transport_crew_for_schedule(
                actor=request.user,
                organization=organization,
                driver_vehicle_assignment=driver_assignment,
                schedule_template=schedule_template,
            )
            messages.success(request, tr(request, "support_transport_crew_created"))
            if route.driver_vehicle_assignment.driver_connection_id == connection.id:
                request.POST = request.POST.copy()
                request.POST["return_transport_crew"] = (
                    f"{route.driver_vehicle_assignment.public_id}."
                    f"{route.schedule_template.public_id}"
                )
        elif action == "transport_schedule_passenger_add":
            work_dates = sorted(
                {_operation_date(value) for value in request.POST.getlist("work_dates")}
            )
            driver_connection = get_object_or_404(
                worker_connection_queryset_for(
                    user=request.user,
                    organization=organization,
                    queryset=SupportConnection.objects.filter(
                        organization=organization,
                        is_archived=False,
                    ),
                ),
                public_id=request.POST.get("driver_connection_id"),
            )
            passenger_connection = get_object_or_404(
                worker_connection_queryset_for(
                    user=request.user,
                    organization=organization,
                    queryset=SupportConnection.objects.filter(
                        organization=organization,
                        is_archived=False,
                    ),
                ),
                public_id=request.POST.get("passenger_connection_id"),
            )
            schedule_template = get_object_or_404(
                ProjectScheduleTemplate.objects.select_related("project__worksite"),
                project__organization=organization,
                is_active=True,
                public_id=request.POST.get("schedule_template_id"),
            )
            add_passenger_to_driver_schedule(
                actor=request.user,
                driver_connection=driver_connection,
                schedule_template=schedule_template,
                passenger_connection=passenger_connection,
                work_dates=work_dates,
                replace_conflicting_schedule=True,
                allow_driver_on_other_dates=True,
            )
            messages.success(
                request,
                tr(request, "support_transport_passenger_added_and_scheduled"),
            )
        elif action == "transport_crew_driver_replace":
            route = get_object_or_404(
                TransportRoute.objects.select_related(
                    "organization",
                    "driver_vehicle_assignment__driver_connection",
                ),
                organization=organization,
                public_id=request.POST.get("route_id"),
            )
            replacement_assignment = get_object_or_404(
                DriverVehicleAssignment.objects.select_related(
                    "driver_connection__candidate",
                    "vehicle",
                ),
                organization=organization,
                state=DriverVehicleAssignment.STATE_PUBLISHED,
                driver_connection__has_driving_license=True,
                driver_connection__is_archived=False,
                public_id=request.POST.get("replacement_driver_vehicle_assignment_id"),
            )
            work_dates = sorted(
                {_operation_date(value) for value in request.POST.getlist("work_dates")}
            )
            if not work_dates:
                raise ValidationError({"work_dates": "schedule_dates_required"})
            replace_transport_crew_resources_for_dates(
                actor=request.user,
                route=route,
                replacement_assignment=replacement_assignment,
                work_dates=work_dates,
            )
            messages.success(
                request,
                tr(request, "support_transport_driver_changed"),
            )
        elif action in {
            "transport_schedule_passenger_remove",
            "transport_schedule_passenger_replace",
        }:
            passenger_assignment = get_object_or_404(
                TransportPassengerAssignment.objects.select_related(
                    "route__driver_vehicle_assignment__driver_connection",
                    "route__schedule_template",
                    "connection",
                ),
                route__organization=organization,
                public_id=request.POST.get("passenger_assignment_id"),
            )
            if action == "transport_schedule_passenger_remove":
                work_dates = sorted(
                    {_operation_date(value) for value in request.POST.getlist("work_dates")}
                )
                remove_passenger_from_driver_schedule(
                    actor=request.user,
                    passenger_assignment=passenger_assignment,
                    work_dates=work_dates,
                )
                messages.success(
                    request,
                    tr(request, "support_transport_passenger_removed"),
                )
            else:
                work_dates = sorted(
                    {_operation_date(value) for value in request.POST.getlist("work_dates")}
                )
                replacement_connection = get_object_or_404(
                    worker_connection_queryset_for(
                        user=request.user,
                        organization=organization,
                        queryset=SupportConnection.objects.filter(
                            organization=organization,
                            is_archived=False,
                        ),
                    ),
                    public_id=request.POST.get("replacement_connection_id"),
                )
                replace_passenger_in_driver_schedule(
                    actor=request.user,
                    passenger_assignment=passenger_assignment,
                    replacement_connection=replacement_connection,
                    work_dates=work_dates,
                )
                messages.success(
                    request,
                    tr(request, "support_transport_passenger_replaced"),
                )
        elif action == "route_stop_edit":
            route = get_object_or_404(
                TransportRoute.objects.filter(
                    organization=organization,
                    driver_vehicle_assignment__driver_connection=connection,
                ),
                public_id=request.POST.get("route_id"),
            )
            stop = get_object_or_404(
                RouteStop.objects.filter(route=route),
                public_id=request.POST.get("stop_id"),
            )
            data = _validated_post(
                RouteStopCreateSerializer,
                request,
                nullable_fields=("housing_site_id",),
                ignored_fields=(
                    "route_id",
                    "stop_id",
                    "return_tab",
                    "return_month",
                    "return_site",
                ),
            )
            housing_site_id = data.pop("housing_site_id")
            housing_site = (
                get_object_or_404(
                    HousingSite,
                    organization=organization,
                    public_id=housing_site_id,
                )
                if housing_site_id
                else None
            )
            edit_route_stop(
                actor=request.user,
                stop=stop,
                kind=data["kind"],
                label=data["label"],
                housing_site=housing_site,
            )
            messages.success(request, tr(request, "support_worker_stop_updated"))
        elif action == "route_publish":
            route = get_object_or_404(
                TransportRoute.objects.filter(
                    organization=organization,
                    driver_vehicle_assignment__driver_connection=connection,
                ),
                public_id=request.POST.get("route_id"),
            )
            publish_transport_route(actor=request.user, route=route)
            messages.success(request, tr(request, "support_transport_route_published"))
        elif action == "route_cancel":
            route = get_object_or_404(
                TransportRoute.objects.filter(
                    organization=organization,
                    driver_vehicle_assignment__driver_connection=connection,
                ),
                public_id=request.POST.get("route_id"),
            )
            cancel_transport_route(actor=request.user, route=route)
            messages.success(request, tr(request, "support_transport_route_cancelled"))
        elif action == "route_delete":
            route = get_object_or_404(
                TransportRoute.objects.filter(
                    organization=organization,
                    driver_vehicle_assignment__driver_connection=connection,
                ),
                public_id=request.POST.get("route_id"),
            )
            delete_transport_route_draft(actor=request.user, route=route)
            messages.success(request, tr(request, "support_draft_deleted"))
        elif action == "scheduled_shift_create":
            work_date = _operation_date(request.POST.get("work_date"))
            starts_at = _aware_datetime(request.POST.get("starts_at"))
            ends_at = _aware_datetime(request.POST.get("ends_at"))
            create_scheduled_shift(
                actor=request.user,
                organization=organization,
                connection=connection,
                work_date=work_date,
                starts_at=starts_at,
                ends_at=ends_at,
                break_minutes=int(request.POST.get("break_minutes") or 0),
                worker_label=(request.POST.get("worker_label") or "").strip(),
                work_assignment=None,
            )
            messages.success(request, tr(request, "support_worker_draft_created"))
        elif action in {"scheduled_shift_from_template", "scheduled_shifts_from_template"}:
            raw_work_dates = request.POST.getlist("work_dates")
            # Keep the old one-day POST compatible for an already open browser
            # tab, while the new calendar sends one or several work_dates.
            if not raw_work_dates and request.POST.get("work_date"):
                raw_work_dates = [request.POST.get("work_date")]
            work_dates = sorted({_operation_date(value) for value in raw_work_dates})
            if not work_dates:
                raise ValidationError({"work_dates": "schedule_dates_required"})
            # The template is the authoritative choice.  The project selector
            # only filters its options in the browser and may be stale if a
            # manager changes both fields quickly.  Deriving the project here
            # keeps the selected template usable instead of producing a 404/500.
            template = (
                ProjectScheduleTemplate.objects.select_related("project")
                .filter(
                    public_id=request.POST.get("schedule_template_id"),
                    project__organization=organization,
                    project__is_active=True,
                    project__worksite__is_active=True,
                    is_active=True,
                )
                .first()
            )
            if template is None:
                raise ValidationError(
                    {"schedule_template": "project_schedule_template_not_available"}
                )
            project = template.project
            current_timezone = timezone.get_current_timezone()
            shift_values = []
            for work_date in work_dates:
                starts_at = timezone.make_aware(
                    datetime.combine(work_date, template.starts_at_time),
                    current_timezone,
                )
                ends_at = timezone.make_aware(
                    datetime.combine(work_date, template.ends_at_time),
                    current_timezone,
                )
                if ends_at <= starts_at:
                    ends_at += timedelta(days=1)
                shift_values.append((work_date, starts_at, ends_at))
            with transaction.atomic():
                work_assignment = (
                    WorkerProjectAssignment.objects.select_for_update()
                    .filter(
                        organization=organization,
                        connection=connection,
                        project=project,
                        state=WorkerProjectAssignment.STATE_PUBLISHED,
                    )
                    .order_by("-starts_at", "-id")
                    .first()
                )
                if work_assignment is None:
                    draft_assignment = (
                        WorkerProjectAssignment.objects.select_for_update()
                        .filter(
                            organization=organization,
                            connection=connection,
                            project=project,
                            state=WorkerProjectAssignment.STATE_DRAFT,
                        )
                        .order_by("-starts_at", "-id")
                        .first()
                    )
                    if draft_assignment is None:
                        draft_assignment = create_worker_project_assignment(
                            actor=request.user,
                            organization=organization,
                            connection=connection,
                            project=project,
                            worker_role="",
                            starts_at=shift_values[0][1],
                        )
                    work_assignment = publish_worker_project_assignment(
                        actor=request.user,
                        assignment=draft_assignment,
                        replace_conflicting_assignments=True,
                    )
                current_shifts = {}
                for item in (
                    ScheduledWorkShift.objects.select_for_update()
                    .filter(
                        organization=organization,
                        connection=connection,
                        work_date__in=work_dates,
                        state__in=(
                            ScheduledWorkShift.STATE_DRAFT,
                            ScheduledWorkShift.STATE_PUBLISHED,
                        ),
                    )
                    .order_by("work_date", "-published_at", "-created_at", "-id")
                ):
                    current_shifts.setdefault(item.work_date, []).append(item)
                replaced_count = 0
                for work_date, starts_at, ends_at in shift_values:
                    day_shifts = current_shifts.get(work_date, [])
                    had_current_shift = bool(day_shifts)
                    published_shifts = [
                        item
                        for item in day_shifts
                        if item.state == ScheduledWorkShift.STATE_PUBLISHED
                    ]
                    draft_shifts = [
                        item
                        for item in day_shifts
                        if item.state == ScheduledWorkShift.STATE_DRAFT
                    ]
                    # A date may contain legacy duplicates from the earlier
                    # one-day editor.  Drafts were never visible to the worker,
                    # so remove them.  Keep one published record as the version
                    # replaced below and cancel any extra published records.
                    for draft_shift in draft_shifts:
                        delete_scheduled_shift_draft(
                            actor=request.user,
                            shift=draft_shift,
                        )
                    current_shift = published_shifts[0] if published_shifts else None
                    for duplicate_shift in published_shifts[1:]:
                        cancel_scheduled_shift(
                            actor=request.user,
                            shift=duplicate_shift,
                        )
                    if current_shift is not None:
                        clear_existing_crew = bool(
                            current_shift.crew_id
                            and current_shift.crew.schedule_template_id != template.id
                        )
                        replace_scheduled_shift(
                            actor=request.user,
                            shift=current_shift,
                            work_date=work_date,
                            starts_at=starts_at,
                            ends_at=ends_at,
                            break_minutes=template.break_minutes,
                            worker_label="",
                            work_assignment=work_assignment,
                            replacement_state=ScheduledWorkShift.STATE_PUBLISHED,
                            schedule_template=template,
                            inherit_existing_crew=not clear_existing_crew,
                        )
                    else:
                        shift = create_scheduled_shift(
                            actor=request.user,
                            organization=organization,
                            connection=connection,
                            work_date=work_date,
                            starts_at=starts_at,
                            ends_at=ends_at,
                            break_minutes=template.break_minutes,
                            worker_label="",
                            work_assignment=work_assignment,
                            schedule_template=template,
                        )
                        publish_scheduled_shift(actor=request.user, shift=shift)
                    if had_current_shift:
                        replaced_count += 1
            # Work planning is the primary operation. Transport synchronization
            # uses legacy route data too, so a damaged old route must never
            # roll back otherwise valid published shifts. It runs in its own
            # transaction and reports a precise warning if only the automatic
            # crew attachment needs attention.
            transport_sync_warning = None
            transport_sync_detail = None
            try:
                sync_worker_schedule_transport(
                    actor=request.user,
                    organization=organization,
                    connection=connection,
                    schedule_template=template,
                    work_dates=work_dates,
                )
            except APIException as error:
                transport_sync_warning = "support_worker_schedule_saved_transport_sync_error"
                transport_sync_detail = _transport_operation_error_key(error)
                print(
                    "[SUPPORT-SCHEDULE-TRANSPORT-VALIDATION] "
                    f"connection={connection.public_id} "
                    f"template={template.public_id} "
                    f"dates={[item.isoformat() for item in work_dates]} "
                    f"detail={getattr(error, 'detail', error)}"
                )
            except Exception as error:
                transport_sync_warning = "support_worker_schedule_saved_transport_sync_error"
                print(
                    "[SUPPORT-SCHEDULE-TRANSPORT-ERROR] "
                    f"connection={connection.public_id} "
                    f"template={template.public_id} "
                    f"dates={[item.isoformat() for item in work_dates]} "
                    f"type={type(error).__name__} detail={error}"
                )
            if action == "scheduled_shift_from_template" and len(work_dates) == 1:
                message_key = (
                    "support_worker_quick_shift_replaced"
                    if replaced_count
                    else "support_worker_quick_shift_published"
                )
            else:
                message_key = (
                    "support_worker_quick_shifts_replaced"
                    if replaced_count
                    else "support_worker_quick_shifts_published"
                )
            messages.success(request, tr(request, message_key))
            if transport_sync_warning:
                warning_text = tr(request, transport_sync_warning)
                if transport_sync_detail:
                    warning_text = f"{warning_text} {tr(request, transport_sync_detail)}"
                messages.warning(request, warning_text)
        elif action == "scheduled_shifts_clear":
            work_dates = sorted(
                {_operation_date(value) for value in request.POST.getlist("work_dates")}
            )
            if not work_dates:
                raise ValidationError({"work_dates": "schedule_dates_required"})
            current_shifts = list(
                ScheduledWorkShift.objects.filter(
                    organization=organization,
                    connection=connection,
                    work_date__in=work_dates,
                    state__in=(
                        ScheduledWorkShift.STATE_DRAFT,
                        ScheduledWorkShift.STATE_PUBLISHED,
                    ),
                ).order_by("work_date", "-created_at", "-id")
            )
            if not current_shifts:
                raise ValidationError(
                    {"work_dates": "selected_schedule_days_have_no_shifts"}
                )
            project_crew_dates = sorted(
                {
                    shift.work_date
                    for shift in current_shifts
                    if shift.project_crew_member_id is not None
                }
            )
            if project_crew_dates:
                release_project_crew_member_days(
                    actor=request.user,
                    connection=connection,
                    work_dates=project_crew_dates,
                )
            for shift in current_shifts:
                if shift.project_crew_member_id is not None:
                    continue
                if shift.state == ScheduledWorkShift.STATE_DRAFT:
                    delete_scheduled_shift_draft(actor=request.user, shift=shift)
                else:
                    cancel_scheduled_shift(actor=request.user, shift=shift)
            messages.success(request, tr(request, "support_worker_quick_shifts_cleared"))
        elif action == "scheduled_shifts_day_off":
            work_dates = sorted(
                {_operation_date(value) for value in request.POST.getlist("work_dates")}
            )
            if not work_dates:
                raise ValidationError({"work_dates": "schedule_dates_required"})
            mark_worker_schedule_days_off(
                actor=request.user,
                connection=connection,
                work_dates=work_dates,
            )
            messages.success(
                request,
                tr(request, "support_worker_schedule_days_off_saved"),
            )
        elif action == "scheduled_shifts_day_off_cancel":
            work_dates = sorted(
                {_operation_date(value) for value in request.POST.getlist("work_dates")}
            )
            if not work_dates:
                raise ValidationError({"work_dates": "schedule_dates_required"})
            restore_worker_schedule_days_off(
                actor=request.user,
                connection=connection,
                work_dates=work_dates,
            )
            messages.success(
                request,
                tr(request, "support_worker_schedule_days_off_cancelled"),
            )
        elif action == "scheduled_shifts_release_cancel":
            work_dates = sorted(
                {_operation_date(value) for value in request.POST.getlist("work_dates")}
            )
            if not work_dates:
                raise ValidationError({"work_dates": "schedule_dates_required"})
            restore_project_crew_member_days(
                actor=request.user,
                connection=connection,
                work_dates=work_dates,
            )
            messages.success(
                request,
                tr(request, "support_worker_schedule_absences_cancelled"),
            )
        elif action == "crew_schedule_override":
            work_dates = sorted(
                {_operation_date(value) for value in request.POST.getlist("work_dates")}
            )
            if not work_dates:
                raise ValidationError({"work_dates": "schedule_dates_required"})
            route = get_object_or_404(
                TransportRoute.objects.filter(
                    organization=organization,
                    public_id=request.POST.get("route_id"),
                ).filter(
                    Q(driver_vehicle_assignment__driver_connection=connection)
                    | Q(passenger_assignments__connection=connection)
                ).distinct()
            )
            crew = ensure_transport_crew_for_route(actor=request.user, route=route)
            override_kind = (request.POST.get("override_kind") or "").strip()
            is_shift = override_kind == "shift"
            conflicts = apply_transport_crew_schedule_override(
                actor=request.user,
                crew=crew,
                work_dates=work_dates,
                kind=override_kind,
                starts_at_time=(
                    _operation_time(request.POST.get("override_starts_at_time"))
                    if is_shift
                    else None
                ),
                ends_at_time=(
                    _operation_time(request.POST.get("override_ends_at_time"))
                    if is_shift
                    else None
                ),
                break_minutes=int(request.POST.get("override_break_minutes") or 0),
                note=(request.POST.get("override_note") or "").strip(),
            )
            messages.success(
                request,
                tr(
                    request,
                    (
                        "support_crew_override_applied_with_conflicts"
                        if conflicts
                        else "support_crew_override_applied"
                    ),
                ),
            )
        elif action == "crew_schedule_conflict_keep":
            shift = get_object_or_404(
                ScheduledWorkShift.objects.filter(
                    organization=organization,
                    connection=connection,
                ),
                public_id=request.POST.get("shift_id"),
            )
            resolve_transport_crew_schedule_conflict(
                actor=request.user,
                keep_shift=shift,
            )
            messages.success(request, tr(request, "support_crew_conflict_resolved"))
        elif action == "scheduled_shift_publish":
            shift = get_object_or_404(
                ScheduledWorkShift.objects.filter(
                    organization=organization,
                    connection=connection,
                ),
                public_id=request.POST.get("shift_id"),
            )
            publish_scheduled_shift(actor=request.user, shift=shift)
            messages.success(request, tr(request, "support_worker_assignment_published"))
        elif action == "scheduled_shift_cancel":
            shift = get_object_or_404(
                ScheduledWorkShift.objects.filter(
                    organization=organization,
                    connection=connection,
                ),
                public_id=request.POST.get("shift_id"),
            )
            cancel_scheduled_shift(actor=request.user, shift=shift)
            messages.success(request, tr(request, "support_worker_assignment_cancelled"))
        elif action == "scheduled_shift_delete":
            shift = get_object_or_404(
                ScheduledWorkShift.objects.filter(
                    organization=organization,
                    connection=connection,
                ),
                public_id=request.POST.get("shift_id"),
            )
            delete_scheduled_shift_draft(actor=request.user, shift=shift)
            messages.success(request, tr(request, "support_draft_deleted"))
        elif action == "scheduled_shift_replace":
            shift = get_object_or_404(
                ScheduledWorkShift.objects.filter(
                    organization=organization,
                    connection=connection,
                ),
                public_id=request.POST.get("shift_id"),
            )
            replace_scheduled_shift(
                actor=request.user,
                shift=shift,
                work_date=_operation_date(request.POST.get("work_date")),
                starts_at=_aware_datetime(request.POST.get("starts_at")),
                ends_at=_aware_datetime(request.POST.get("ends_at")),
                break_minutes=int(request.POST.get("break_minutes") or 0),
                worker_label=(request.POST.get("worker_label") or "").strip(),
            )
            messages.success(request, tr(request, "support_worker_schedule_updated"))
        elif action == "publish_housing":
            assignment = get_object_or_404(
                HousingAssignment.objects.filter(
                    organization=organization,
                    connection=connection,
                ),
                public_id=request.POST.get("assignment_id"),
            )
            publish_housing_assignment(actor=request.user, assignment=assignment)
            messages.success(request, tr(request, "support_worker_assignment_published"))
        elif action == "cancel_housing":
            assignment = get_object_or_404(
                HousingAssignment.objects.filter(
                    organization=organization,
                    connection=connection,
                ),
                public_id=request.POST.get("assignment_id"),
            )
            cancel_housing_assignment(actor=request.user, assignment=assignment)
            messages.success(request, tr(request, "support_worker_assignment_cancelled"))
        elif action == "housing_check_out":
            assignment = get_object_or_404(
                HousingAssignment.objects.filter(organization=organization, connection=connection),
                public_id=request.POST.get("assignment_id"),
            )
            schedule_housing_check_out(
                actor=request.user,
                assignment=assignment,
                check_out_at=_operation_date_start(request.POST.get("check_out_on")),
            )
            messages.success(request, tr(request, "support_worker_housing_check_out_updated"))
        elif action == "publish_work":
            assignment = get_object_or_404(
                WorkerProjectAssignment.objects.filter(
                    organization=organization,
                    connection=connection,
                ),
                public_id=request.POST.get("assignment_id"),
            )
            publish_worker_project_assignment(actor=request.user, assignment=assignment)
            messages.success(request, tr(request, "support_worker_assignment_published"))
        elif action == "cancel_work":
            assignment = get_object_or_404(
                WorkerProjectAssignment.objects.filter(
                    organization=organization,
                    connection=connection,
                ),
                public_id=request.POST.get("assignment_id"),
            )
            cancel_worker_project_assignment(actor=request.user, assignment=assignment)
            messages.success(request, tr(request, "support_worker_assignment_cancelled"))
        else:
            raise ValueError("operation_unknown")
    except (APIException, ValueError) as error:
        print(
            "[SUPPORT-WORKER-VALIDATION] "
            f"action={action} detail={getattr(error, 'detail', error)}"
        )
        message_key = (
            "support_worker_housing_check_out_error"
            if action == "housing_check_out"
            else (
                _document_operation_error_key(error)
                if action.startswith("document_package_")
                else (
                    _transport_operation_error_key(error)
                    if action == "route_create"
                    or action == "transport_schedule_crew_create"
                    or action == "transport_crew_driver_replace"
                    or action.startswith("transport_schedule_passenger_")
                    else _worker_operation_error_key(error)
                )
            )
        )
        messages.error(request, tr(request, message_key))
    except Exception as error:  # Safety net: an employer action must never end on a blank 500 page.
        print(
            "[SUPPORT-WORKER-OPERATION-ERROR] "
            f"action={action} type={type(error).__name__} detail={error}"
        )
        messages.error(
            request,
            tr(
                request,
                (
                    "support_worker_schedule_apply_unexpected_error"
                    if action in {"scheduled_shift_from_template", "scheduled_shifts_from_template"}
                    else "support_worker_operation_error"
                ),
            ),
        )
    return _worker_card_redirect(
        connection,
        tab=request.POST.get("return_tab"),
        month=request.POST.get("return_month"),
        site=request.POST.get("return_site"),
        transport_template=request.POST.get("return_transport_template"),
        transport_crew=request.POST.get("return_transport_crew"),
        documents=request.POST.get("return_documents") == "1",
    )


@login_required(login_url="employer:login")
def worker_card(request, connection_public_id):
    if not is_support_feature_enabled():
        raise Http404("support_not_available")
    snapshot = worker_card_snapshot(
        user=request.user,
        connection_public_id=connection_public_id,
        calendar_month=request.GET.get("month"),
        housing_site_public_id=request.GET.get("site"),
        transport_template_public_id=request.GET.get("transport_template"),
        transport_crew_key=request.GET.get("transport_crew"),
        transport_crew_date=request.GET.get("crew_date"),
    )
    if request.method == "POST":
        return _worker_card_operation(request, snapshot=snapshot)

    for package in snapshot["document_packages"]:
        labels = []
        for item in package.requested_items:
            item_type = (item.get("type") or "").strip()
            if item_type == "custom":
                labels.append((item.get("custom_label") or "").strip())
            else:
                labels.append(tr(request, f"support_document_{item_type}"))
        package.requested_items_label = ", ".join(label for label in labels if label)
        package.status_label = {
            DocumentRequestPackage.STATUS_REQUESTED: tr(request, "support_documents_status_requested"),
            DocumentRequestPackage.STATUS_SENT_TO_EMPLOYER: tr(request, "support_documents_status_sent"),
            DocumentRequestPackage.STATUS_NEEDS_CORRECTION: tr(request, "support_documents_status_correction"),
            DocumentRequestPackage.STATUS_COMPLETED: tr(request, "support_documents_status_completed"),
            DocumentRequestPackage.STATUS_NOT_REQUIRED: tr(request, "support_documents_status_not_required"),
            DocumentRequestPackage.STATUS_CANCELLED: tr(request, "support_documents_status_cancelled"),
        }[package.status]

    for assignment in snapshot["housing_assignments"]:
        assignment.state_label = tr(request, f"support_worker_state_{assignment.state}")
    for assignment in snapshot["work_assignments"]:
        assignment.state_label = tr(request, f"support_worker_state_{assignment.state}")
    for shift in snapshot["scheduled_shifts"]:
        shift.state_label = tr(request, f"support_worker_state_{shift.state}")
    for assignment in snapshot["driver_assignments"]:
        assignment.state_label = tr(request, f"support_worker_state_{assignment.state}")
    for assignment in snapshot["passenger_routes"]:
        assignment.route.state_label = tr(
            request,
            f"support_worker_state_{assignment.route.state}",
        )
    snapshot["connection"].stage_label = tr(
        request,
        f"support_stage_{snapshot['connection'].stage}",
    )
    snapshot["open_documents"] = request.GET.get("documents") == "1"
    if (
        snapshot["open_documents"]
        and snapshot["connection"].stage == SupportConnection.STAGE_DOCUMENTS
    ):
        snapshot["workspace_url"] = (
            f"{reverse('support:candidate-applications')}?"
            + urlencode(
                {
                    "organization": snapshot["organization"].public_id,
                    "view": "processing",
                }
            )
        )
    else:
        snapshot["workspace_url"] = (
            f"{reverse('support:workers')}?organization={snapshot['organization'].public_id}"
        )
    requested_tab = (request.GET.get("tab") or "work_transport").strip()
    if requested_tab in {"company", "transport"}:
        requested_tab = "work_transport"
    visible_tabs = {
        "work_transport": (
            snapshot["permissions"]["work"]
            or snapshot["permissions"]["transport"]
        ),
        "housing": snapshot["permissions"]["housing"],
    }
    if not visible_tabs.get(requested_tab):
        requested_tab = next(
            (key for key, allowed in visible_tabs.items() if allowed),
            "work_transport",
        )
    worker_base_url = reverse(
        "support:worker-card",
        kwargs={"connection_public_id": snapshot["connection"].public_id},
    )
    selected_calendar_date = snapshot["selected_calendar_date"]
    snapshot.update(
        {
            "active_tab": requested_tab,
            "worker_base_url": worker_base_url,
            "work_transport_url": f"{worker_base_url}?tab=work_transport",
            # Compatibility aliases keep old templates, bookmarks and POST
            # return targets working while the visible UI uses one tab.
            "company_url": f"{worker_base_url}?tab=work_transport",
            "transport_tab_url": f"{worker_base_url}?tab=work_transport",
            "housing_tab_url": f"{worker_base_url}?tab=housing",
            "calendar_month": (
                selected_calendar_date.strftime("%Y-%m")
                if selected_calendar_date
                else ""
            ),
            "calendar_previous_url": (
                f"{worker_base_url}?tab=work_transport&month="
                f"{snapshot['calendar_previous_month'].strftime('%Y-%m')}"
                if snapshot["calendar_previous_month"]
                else None
            ),
            "calendar_next_url": (
                f"{worker_base_url}?tab=work_transport&month="
                f"{snapshot['calendar_next_month'].strftime('%Y-%m')}"
                if snapshot["calendar_next_month"]
                else None
            ),
            "calendar_today_url": (
                f"{worker_base_url}?tab=work_transport&month="
                f"{timezone.localdate().strftime('%Y-%m')}"
            ),
            "calendar_weekday_labels": [
                tr(request, f"support_calendar_weekday_{weekday}")
                for weekday in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
            ],
        }
    )
    for crew in snapshot["transport_crews"]:
        crew.transport_url = (
            f"{worker_base_url}?tab=work_transport&month={snapshot['calendar_month']}"
            f"&transport_crew={crew.key}"
        )
    crew_url_by_key = {
        crew.key: crew.transport_url for crew in snapshot["transport_crews"]
    }
    for day in snapshot["calendar_days"]:
        if day is None:
            continue
        crew_url = crew_url_by_key.get(day.get("transport_crew_key"))
        day["transport_url"] = (
            f"{crew_url}&crew_date={day['date'].isoformat()}" if crew_url else None
        )
        project_crew_detail = day.get("project_crew_detail")
        if project_crew_detail:
            for member in project_crew_detail["members"]:
                conversation = member.get("conversation")
                member["chat_url"] = (
                    reverse(
                        "support:conversation-detail",
                        kwargs={"conversation_public_id": conversation.public_id},
                    )
                    if conversation is not None
                    else None
                )
    for crew in snapshot["related_transport_crews"]:
        crew.transport_url = (
            f"{worker_base_url}?tab=work_transport&month={snapshot['calendar_month']}"
            f"&transport_crew={crew.key}"
        )
    for passenger in snapshot["transport_passengers"]:
        passenger.chat_url = (
            reverse(
                "support:conversation-detail",
                kwargs={
                    "conversation_public_id": passenger.chat_conversation.public_id
                },
            )
            if passenger.chat_conversation is not None
            else None
        )
    snapshot["transport_driver_chat_url"] = (
        reverse(
            "support:conversation-detail",
            kwargs={
                "conversation_public_id": snapshot["transport_driver_conversation"].public_id
            },
        )
        if snapshot["transport_driver_conversation"] is not None
        else None
    )
    return render(request, "support/worker_card.html", snapshot)


def _registry_operation(request, *, snapshot):
    """Create a registry item through a service, never by direct web writes."""

    organization = snapshot["organization"]
    action = (request.POST.get("action") or "").strip()
    try:
        if action == "housing_site_create":
            create_housing_site(
                actor=request.user,
                organization=organization,
                **_validated_post(HousingSiteCreateSerializer, request),
            )
        elif action == "housing_room_create":
            data = _validated_post(HousingRoomCreateSerializer, request)
            site = get_object_or_404(
                HousingSite,
                organization=organization,
                public_id=data.pop("site_id"),
            )
            create_housing_room(
                actor=request.user,
                organization=organization,
                site=site,
                **data,
            )
        elif action == "housing_place_create":
            data = _validated_post(HousingPlaceCreateSerializer, request)
            room = get_object_or_404(
                HousingRoom.objects.select_related("site"),
                site__organization=organization,
                public_id=data.pop("room_id"),
            )
            create_housing_place(
                actor=request.user,
                organization=organization,
                room=room,
                **data,
            )
        elif action == "worksite_create":
            create_worksite(
                actor=request.user,
                organization=organization,
                **_validated_post(WorksiteCreateSerializer, request),
            )
        elif action == "work_project_create":
            data = _validated_post(WorkProjectCreateSerializer, request)
            worksite = get_object_or_404(
                Worksite,
                organization=organization,
                public_id=data.pop("worksite_id"),
            )
            create_work_project(
                actor=request.user,
                organization=organization,
                worksite=worksite,
                **data,
            )
        elif action == "vehicle_create":
            create_vehicle(
                actor=request.user,
                organization=organization,
                **_validated_post(VehicleCreateSerializer, request),
            )
        else:
            raise ValueError("registry_operation_unknown")
    except (APIException, ValueError):
        messages.error(request, tr(request, "support_registry_operation_error"))
    else:
        messages.success(request, tr(request, "support_registry_created"))
    return _registry_redirect(organization)


@login_required(login_url="employer:login")
def registries(request):
    if not is_support_feature_enabled():
        raise Http404("support_not_available")
    snapshot = registry_snapshot(
        user=request.user,
        organization_public_id=request.GET.get("organization"),
    )
    if request.method == "POST":
        return _registry_operation(request, snapshot=snapshot)
    return redirect(
        f"{reverse('support:workspace')}?organization={snapshot['organization'].public_id}"
    )


def _housing_redirect(organization, *, site=None):
    query = {"organization": organization.public_id}
    if site is not None:
        query["site"] = site.public_id
    return redirect(f"{reverse('support:housing')}?{urlencode(query)}")


def _housing_error_key(error):
    detail = str(getattr(error, "detail", error))
    if "housing_place_conflicts_with_published_assignment" in detail:
        return "support_housing_error_place_conflict"
    if "housing_worker_conflicts_with_published_assignment" in detail:
        return "support_housing_error_worker_conflict"
    if "period_end_must_be_after_start" in detail:
        return "support_housing_error_date_order"
    if "housing_assignment_already_started" in detail:
        return "support_housing_error_stay_started"
    if "housing_room_has_active_assignments" in detail:
        return "support_housing_error_room_in_use"
    if "housing_room_label_already_exists" in detail:
        return "support_housing_error_room_exists"
    if "housing_site_internal_name_already_exists" in detail:
        return "support_housing_error_site_exists"
    return "support_housing_error_generic"


@login_required(login_url="employer:login")
def housing_workspace(request):
    """Organization-wide housing management, independent of a worker card."""

    if not is_support_feature_enabled():
        raise Http404("support_not_available")
    housing_path = reverse("support:housing")
    referrer = request.META.get("HTTP_REFERER", "")
    if referrer and url_has_allowed_host_and_scheme(
        referrer,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        referrer_parts = urlsplit(referrer)
        if referrer_parts.path != housing_path:
            return_url = referrer_parts.path or "/"
            if referrer_parts.query:
                return_url = f"{return_url}?{referrer_parts.query}"
            request.session["support_housing_return_url"] = return_url
    snapshot = housing_workspace_snapshot(
        user=request.user,
        organization_public_id=request.GET.get("organization"),
        housing_site_public_id=request.GET.get("site"),
    )
    organization = snapshot["organization"]
    selected_site = snapshot["selected_housing_site"]
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        redirect_site = selected_site
        try:
            if action == "housing_site_create":
                redirect_site = create_housing_site(
                    actor=request.user,
                    organization=organization,
                    **_validated_post(HousingSiteCreateSerializer, request),
                )
                success_key = "support_housing_site_created"
            elif action == "housing_room_create":
                data = _validated_post(HousingRoomCreateSerializer, request)
                site = get_object_or_404(
                    HousingSite,
                    organization=organization,
                    public_id=data.pop("site_id"),
                )
                create_housing_room(
                    actor=request.user,
                    organization=organization,
                    site=site,
                    **data,
                )
                redirect_site = site
                success_key = "support_housing_room_created"
            elif action == "housing_room_delete":
                room = get_object_or_404(
                    HousingRoom.objects.select_related("site"),
                    site__organization=organization,
                    public_id=request.POST.get("room_id"),
                )
                redirect_site = room.site
                delete_housing_room(
                    actor=request.user,
                    organization=organization,
                    room=room,
                )
                success_key = "support_housing_room_deleted"
            elif action in {"housing_assign", "housing_draft_create"}:
                place = get_object_or_404(
                    HousingPlace.objects.select_related("room__site"),
                    room__site__organization=organization,
                    public_id=request.POST.get("place_id"),
                    is_active=True,
                )
                redirect_site = place.room.site
                connection = get_object_or_404(
                    worker_connection_queryset_for(
                        user=request.user,
                        organization=organization,
                        queryset=SupportConnection.objects.filter(
                            is_archived=False,
                            stage__in=(
                                SupportConnection.STAGE_COORDINATOR,
                                SupportConnection.STAGE_ACTIVE_WORKER,
                            ),
                        ),
                    ),
                    public_id=request.POST.get("connection_id"),
                )
                check_in_at = _operation_date_start(request.POST.get("check_in_on"))
                with transaction.atomic():
                    assignment = create_housing_assignment(
                        actor=request.user,
                        organization=organization,
                        connection=connection,
                        place=place,
                        check_in_at=check_in_at,
                    )
                    if action == "housing_assign":
                        publish_housing_assignment(
                            actor=request.user,
                            assignment=assignment,
                        )
                success_key = (
                    "support_housing_assignment_created"
                    if action == "housing_assign"
                    else "support_housing_draft_created"
                )
            elif action == "housing_publish":
                assignment = get_object_or_404(
                    HousingAssignment.objects.select_related("place__room__site"),
                    organization=organization,
                    public_id=request.POST.get("assignment_id"),
                )
                redirect_site = assignment.place.room.site
                publish_housing_assignment(actor=request.user, assignment=assignment)
                success_key = "support_housing_assignment_created"
            elif action == "housing_draft_delete":
                assignment = get_object_or_404(
                    HousingAssignment.objects.select_related("place__room__site"),
                    organization=organization,
                    public_id=request.POST.get("assignment_id"),
                )
                redirect_site = assignment.place.room.site
                delete_housing_assignment_draft(actor=request.user, assignment=assignment)
                success_key = "support_housing_draft_deleted"
            elif action == "housing_check_out":
                assignment = get_object_or_404(
                    HousingAssignment.objects.select_related("place__room__site"),
                    organization=organization,
                    public_id=request.POST.get("assignment_id"),
                )
                redirect_site = assignment.place.room.site
                schedule_housing_check_out(
                    actor=request.user,
                    assignment=assignment,
                    check_out_at=_operation_date_start(request.POST.get("check_out_on")),
                )
                success_key = "support_housing_checkout_saved"
            elif action == "housing_queue_edit":
                assignment = get_object_or_404(
                    HousingAssignment.objects.select_related("place__room__site"),
                    organization=organization,
                    public_id=request.POST.get("assignment_id"),
                )
                redirect_site = assignment.place.room.site
                reschedule_future_housing_assignment(
                    actor=request.user,
                    assignment=assignment,
                    check_in_at=_operation_date_start(request.POST.get("check_in_on")),
                    check_out_at=_operation_date_start(
                        request.POST.get("check_out_on"), required=False
                    ),
                )
                success_key = "support_housing_queue_updated"
            else:
                raise ValueError("housing_operation_unknown")
        except (APIException, ValueError) as exc:
            messages.error(request, tr(request, _housing_error_key(exc)))
        else:
            messages.success(request, tr(request, success_key))
        return _housing_redirect(organization, site=redirect_site)

    snapshot["workspace_url"] = (
        f"{reverse('support:workspace')}?organization={organization.public_id}"
    )
    snapshot["housing_return_url"] = request.session.get(
        "support_housing_return_url",
        snapshot["workspace_url"],
    )
    snapshot["today"] = timezone.localdate()
    highlight_place_id = (request.GET.get("highlight_place") or "").strip()
    highlighted_place = None
    if highlight_place_id and selected_site is not None:
        for room in snapshot["selected_housing_rooms"]:
            for place in room.places_for_layout:
                if str(place.public_id) == highlight_place_id:
                    highlighted_place = place
                    place.is_highlighted = True
                    room.has_highlighted_place = True
                    break
            if highlighted_place is not None:
                break
    snapshot["highlighted_place"] = highlighted_place
    for site in snapshot["housing_sites"]:
        site.housing_url = (
            f"{reverse('support:housing')}?organization={organization.public_id}"
            f"&site={site.public_id}"
        )
    return render(request, "support/housing_workspace.html", snapshot)


def _projects_redirect(
    organization,
    *,
    project=None,
    calendar_month=None,
    project_crew_key=None,
):
    query = {"organization": organization.public_id}
    if calendar_month:
        query["month"] = calendar_month
    if project_crew_key:
        query["crew"] = project_crew_key
    if project is not None:
        return redirect(
            f"{reverse('support:project-detail', kwargs={'project_public_id': project.public_id})}"
            f"?{urlencode(query)}"
        )
    return redirect(f"{reverse('support:projects')}?{urlencode(query)}")


@login_required(login_url="employer:login")
def projects_workspace(request, project_public_id=None):
    """Employer directory of work projects and their operational details."""

    if not is_support_feature_enabled():
        raise Http404("support_not_available")
    snapshot = projects_snapshot(
        user=request.user,
        organization_public_id=request.GET.get("organization"),
        project_public_id=project_public_id,
        calendar_month=request.GET.get("month"),
        project_crew_key=request.GET.get("crew"),
    )
    organization = snapshot["organization"]
    selected_project = snapshot["selected_project"]
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        try:
            if action == "project_create":
                data = _validated_post(
                    ProjectCreateSerializer,
                    request,
                    nullable_fields=("ends_on",),
                )
                project = create_project(
                    actor=request.user,
                    organization=organization,
                    **data,
                )
                messages.success(request, tr(request, "support_project_created"))
                return _projects_redirect(organization, project=project)
            if selected_project is None:
                raise ValueError("project_operation_requires_selected_project")
            if action == "project_update":
                data = _validated_post(
                    ProjectCreateSerializer,
                    request,
                    nullable_fields=("ends_on",),
                )
                project = update_project(
                    actor=request.user,
                    project=selected_project,
                    **data,
                )
                messages.success(request, tr(request, "support_project_updated"))
                return _projects_redirect(
                    organization,
                    project=project,
                    calendar_month=request.POST.get("return_month"),
                )
            if action == "project_schedule_template_create":
                data = _validated_post(
                    ProjectScheduleTemplateCreateSerializer,
                    request,
                )
                create_project_schedule_template(
                    actor=request.user,
                    project=selected_project,
                    **data,
                )
                messages.success(request, tr(request, "support_project_schedule_created"))
                return _projects_redirect(
                    organization,
                    project=selected_project,
                    calendar_month=request.POST.get("return_month"),
                )
            if action == "transport_schedule_passenger_add":
                driver_connection = get_object_or_404(
                    worker_connection_queryset_for(
                        user=request.user,
                        organization=organization,
                        queryset=SupportConnection.objects.filter(
                            organization=organization,
                            is_archived=False,
                        ),
                    ),
                    public_id=request.POST.get("driver_connection_id"),
                )
                passenger_connection = get_object_or_404(
                    worker_connection_queryset_for(
                        user=request.user,
                        organization=organization,
                        queryset=SupportConnection.objects.filter(
                            organization=organization,
                            is_archived=False,
                        ),
                    ),
                    public_id=request.POST.get("passenger_connection_id"),
                )
                schedule_template = get_object_or_404(
                    ProjectScheduleTemplate.objects.select_related("project__worksite"),
                    project=selected_project,
                    is_active=True,
                    public_id=request.POST.get("schedule_template_id"),
                )
                add_passenger_to_driver_schedule(
                    actor=request.user,
                    driver_connection=driver_connection,
                    schedule_template=schedule_template,
                    passenger_connection=passenger_connection,
                )
                messages.success(
                    request,
                    tr(request, "support_transport_passenger_added_and_scheduled"),
                )
                return _projects_redirect(
                    organization,
                    project=selected_project,
                    project_crew_key=request.POST.get("return_project_crew"),
                )
            if action in {
                "transport_schedule_passenger_remove",
                "transport_schedule_passenger_replace",
            }:
                passenger_assignment = get_object_or_404(
                    TransportPassengerAssignment.objects.select_related(
                        "route__driver_vehicle_assignment__driver_connection",
                        "route__schedule_template",
                        "connection",
                    ),
                    route__organization=organization,
                    route__schedule_template__project=selected_project,
                    public_id=request.POST.get("passenger_assignment_id"),
                )
                if action == "transport_schedule_passenger_remove":
                    remove_passenger_from_driver_schedule(
                        actor=request.user,
                        passenger_assignment=passenger_assignment,
                    )
                    messages.success(
                        request,
                        tr(request, "support_transport_passenger_removed"),
                    )
                else:
                    replacement_connection = get_object_or_404(
                        worker_connection_queryset_for(
                            user=request.user,
                            organization=organization,
                            queryset=SupportConnection.objects.filter(
                                organization=organization,
                                is_archived=False,
                            ),
                        ),
                        public_id=request.POST.get("replacement_connection_id"),
                    )
                    replace_passenger_in_driver_schedule(
                        actor=request.user,
                        passenger_assignment=passenger_assignment,
                        replacement_connection=replacement_connection,
                    )
                    messages.success(
                        request,
                        tr(request, "support_transport_passenger_replaced"),
                    )
                return _projects_redirect(
                    organization,
                    project=selected_project,
                    project_crew_key=request.POST.get("return_project_crew"),
                )
            raise ValueError("project_operation_unknown")
        except (APIException, ValueError) as error:
            print(
                "[SUPPORT-PROJECT-VALIDATION] "
                f"action={action} detail={getattr(error, 'detail', error)}"
            )
            error_key = (
                _transport_operation_error_key(error)
                if action.startswith("transport_schedule_passenger_")
                else _project_operation_error_key(error)
            )
            messages.error(request, tr(request, error_key))
            return _projects_redirect(
                organization,
                project=selected_project,
                calendar_month=request.POST.get("return_month"),
                project_crew_key=request.POST.get("return_project_crew"),
            )
    snapshot["workspace_url"] = (
        f"{reverse('support:workspace')}?organization={organization.public_id}"
    )
    snapshot["project_list_url"] = (
        f"{reverse('support:projects')}?organization={organization.public_id}"
    )
    if selected_project is not None:
        project_base_url = reverse(
            "support:project-detail",
            kwargs={"project_public_id": selected_project.public_id},
        )
        for crew in snapshot["project_crews"]:
            crew.project_url = (
                f"{project_base_url}?{urlencode({'organization': organization.public_id, 'crew': crew.key})}"
            )
            crew.driver_chat_url = (
                reverse(
                    "support:conversation-detail",
                    kwargs={
                        "conversation_public_id": crew.driver_conversation.public_id
                    },
                )
                if crew.driver_conversation is not None
                else None
            )
            for passenger in crew.passengers:
                passenger.chat_url = (
                    reverse(
                        "support:conversation-detail",
                        kwargs={
                            "conversation_public_id": (
                                passenger.chat_conversation.public_id
                            )
                        },
                    )
                    if passenger.chat_conversation is not None
                    else None
                )
    return render(request, "support/projects_workspace.html", snapshot)


def _transport_redirect(organization):
    return redirect(
        f"{reverse('support:transport')}?organization={organization.public_id}"
    )


def _transport_operation(request, *, snapshot):
    """Build one route through the same transactional services used by API."""

    organization = snapshot["organization"]
    action = (request.POST.get("action") or "").strip()
    success_key = None
    try:
        if action == "route_create":
            data = _validated_post(
                TransportRouteCreateSerializer,
                request,
                nullable_fields=("ends_on", "worksite_id", "departure_time"),
            )
            driver_assignment = get_object_or_404(
                DriverVehicleAssignment,
                organization=organization,
                public_id=data.pop("driver_vehicle_assignment_id"),
            )
            worksite_id = data.pop("worksite_id")
            worksite = (
                get_object_or_404(
                    Worksite,
                    organization=organization,
                    public_id=worksite_id,
                )
                if worksite_id
                else None
            )
            create_transport_route(
                actor=request.user,
                organization=organization,
                driver_vehicle_assignment=driver_assignment,
                worksite=worksite,
                **data,
            )
            success_key = "support_transport_route_created"
        elif action == "route_stop_create":
            route = get_object_or_404(
                TransportRoute,
                organization=organization,
                public_id=request.POST.get("route_id"),
            )
            data = _validated_post(
                RouteStopCreateSerializer,
                request,
                nullable_fields=("housing_site_id",),
                ignored_fields=("route_id",),
            )
            housing_site_id = data.pop("housing_site_id")
            housing_site = (
                get_object_or_404(
                    HousingSite,
                    organization=organization,
                    public_id=housing_site_id,
                )
                if housing_site_id
                else None
            )
            add_route_stop(
                actor=request.user,
                route=route,
                housing_site=housing_site,
                **data,
            )
            success_key = "support_transport_stop_added"
        elif action == "route_stop_edit":
            route = get_object_or_404(
                TransportRoute,
                organization=organization,
                public_id=request.POST.get("route_id"),
            )
            stop = get_object_or_404(
                RouteStop,
                route=route,
                public_id=request.POST.get("stop_id"),
            )
            data = _validated_post(
                RouteStopCreateSerializer,
                request,
                nullable_fields=("housing_site_id",),
                ignored_fields=("route_id", "stop_id"),
            )
            housing_site_id = data.pop("housing_site_id")
            housing_site = (
                get_object_or_404(
                    HousingSite,
                    organization=organization,
                    public_id=housing_site_id,
                )
                if housing_site_id
                else None
            )
            edit_route_stop(
                actor=request.user,
                stop=stop,
                kind=data["kind"],
                label=data["label"],
                housing_site=housing_site,
            )
            success_key = "support_worker_stop_updated"
        elif action == "route_passenger_create":
            route = get_object_or_404(
                TransportRoute,
                organization=organization,
                public_id=request.POST.get("route_id"),
            )
            data = _validated_post(
                RoutePassengerCreateSerializer,
                request,
                ignored_fields=("route_id",),
            )
            connection = get_object_or_404(
                SupportConnection,
                organization=organization,
                public_id=data.pop("connection_id"),
            )
            pickup_stop = get_object_or_404(
                RouteStop,
                route=route,
                public_id=data.pop("pickup_stop_id"),
            )
            dropoff_stop = get_object_or_404(
                RouteStop,
                route=route,
                public_id=data.pop("dropoff_stop_id"),
            )
            add_route_passenger(
                actor=request.user,
                route=route,
                connection=connection,
                pickup_stop=pickup_stop,
                dropoff_stop=dropoff_stop,
                **data,
            )
            success_key = "support_transport_passenger_added"
        elif action == "route_publish":
            route = get_object_or_404(
                TransportRoute,
                organization=organization,
                public_id=request.POST.get("route_id"),
            )
            publish_transport_route(actor=request.user, route=route)
            success_key = "support_transport_route_published"
        elif action == "route_cancel":
            route = get_object_or_404(
                TransportRoute,
                organization=organization,
                public_id=request.POST.get("route_id"),
            )
            cancel_transport_route(actor=request.user, route=route)
            success_key = "support_transport_route_cancelled"
        elif action == "route_delete":
            route = get_object_or_404(
                TransportRoute,
                organization=organization,
                public_id=request.POST.get("route_id"),
            )
            delete_transport_route_draft(actor=request.user, route=route)
            success_key = "support_draft_deleted"
        else:
            raise ValueError("transport_operation_unknown")
    except (APIException, ValueError) as error:
        messages.error(request, tr(request, _transport_operation_error_key(error)))
    else:
        messages.success(request, tr(request, success_key))
    return _transport_redirect(organization)


@login_required(login_url="employer:login")
def transport_workspace(request):
    if not is_support_feature_enabled():
        raise Http404("support_not_available")
    snapshot = transport_workspace_snapshot(
        user=request.user,
        organization_public_id=request.GET.get("organization"),
    )
    if request.method == "POST":
        return _transport_operation(request, snapshot=snapshot)
    for route in snapshot["routes"]:
        route.state_label = tr(request, f"support_worker_state_{route.state}")
        for stop in route.stops_for_builder:
            stop.kind_label = tr(request, f"support_transport_stop_{stop.kind}")
    snapshot["workspace_url"] = (
        f"{reverse('support:workspace')}?organization={snapshot['organization'].public_id}"
    )
    return render(request, "support/transport_workspace.html", snapshot)


@login_required(login_url="employer:login")
def fleet_workspace(request):
    if not is_support_feature_enabled():
        raise Http404("support_not_available")
    snapshot = fleet_snapshot(
        user=request.user,
        organization_public_id=request.GET.get("organization"),
        vehicle_public_id=request.GET.get("vehicle"),
    )
    organization = snapshot["organization"]
    if request.method == "POST":
        action = (request.POST.get("action") or "driver_vehicle_assign").strip()
        redirect_vehicle_id = request.POST.get("vehicle_id", "")
        try:
            if action in {
                "driver_vehicle_assign",
                "driver_vehicle_draft_create",
                "driver_vehicle_draft_edit",
                "driver_vehicle_draft_publish",
            } and ProjectCrewResourceAssignment.objects.filter(
                crew__organization=organization,
                crew__state="active",
                vehicle__public_id=request.POST.get("vehicle_id"),
                starts_on__lte=timezone.localdate(),
            ).filter(
                Q(ends_on__isnull=True) | Q(ends_on__gte=timezone.localdate())
            ).exists():
                raise ValueError("vehicle_managed_by_project_crew")
            if action == "vehicle_create":
                vehicle = create_vehicle(
                    actor=request.user,
                    organization=organization,
                    **_validated_post(VehicleCreateSerializer, request),
                )
                redirect_vehicle_id = str(vehicle.public_id)
                success_key = "support_registry_created"
            elif action == "driver_vehicle_draft_publish":
                assignment = get_object_or_404(
                    DriverVehicleAssignment,
                    organization=organization,
                    public_id=request.POST.get("assignment_id"),
                )
                publish_driver_vehicle_assignment(
                    actor=request.user,
                    assignment=assignment,
                    excluded_passenger_public_ids=request.POST.getlist(
                        "exclude_passenger_ids"
                    ),
                )
                success_key = "support_transport_driver_vehicle_published"
            elif action == "driver_vehicle_draft_delete":
                assignment = get_object_or_404(
                    DriverVehicleAssignment,
                    organization=organization,
                    public_id=request.POST.get("assignment_id"),
                )
                delete_driver_vehicle_assignment_draft(actor=request.user, assignment=assignment)
                success_key = "support_draft_deleted"
            else:
                data = _validated_post(
                    DriverVehicleAssignmentCreateSerializer,
                    request,
                    ignored_fields=("assignment_id",),
                )
                vehicle = get_object_or_404(
                    Vehicle, organization=organization, public_id=data.pop("vehicle_id")
                )
                driver = get_object_or_404(
                    SupportConnection, organization=organization, public_id=data.pop("driver_connection_id")
                )
                eligible_ids = {item.id for item in snapshot["eligible_drivers"]}
                if driver.id not in eligible_ids:
                    raise ValueError("driver_license_not_verified")
                if action == "driver_vehicle_assign":
                    # The fleet screen is an operational control: selecting a
                    # driver must become active immediately.  Keep creation and
                    # publication in one transaction so a failed publication
                    # cannot leave another invisible draft behind.
                    with transaction.atomic():
                        for stale_draft in list(
                            DriverVehicleAssignment.objects.select_for_update().filter(
                                organization=organization,
                                vehicle=vehicle,
                                state=DriverVehicleAssignment.STATE_DRAFT,
                            )
                        ):
                            delete_driver_vehicle_assignment_draft(
                                actor=request.user,
                                assignment=stale_draft,
                            )
                        assignment = create_driver_vehicle_assignment(
                            actor=request.user,
                            organization=organization,
                            driver_connection=driver,
                            vehicle=vehicle,
                            **data,
                        )
                        publish_driver_vehicle_assignment(
                            actor=request.user,
                            assignment=assignment,
                            excluded_passenger_public_ids=request.POST.getlist(
                                "exclude_passenger_ids"
                            ),
                        )
                    success_key = "support_transport_driver_vehicle_published"
                elif action == "driver_vehicle_draft_edit":
                    assignment = get_object_or_404(
                        DriverVehicleAssignment,
                        organization=organization,
                        vehicle=vehicle,
                        public_id=request.POST.get("assignment_id"),
                    )
                    edit_driver_vehicle_assignment_draft(
                        actor=request.user,
                        assignment=assignment,
                        driver_connection=driver,
                        **data,
                    )
                    success_key = "support_fleet_draft_updated"
                elif action == "driver_vehicle_draft_create":
                    create_driver_vehicle_assignment(
                        actor=request.user,
                        organization=organization,
                        driver_connection=driver,
                        vehicle=vehicle,
                        **data,
                    )
                    success_key = "support_fleet_draft_created"
                else:
                    raise ValueError("fleet_operation_unknown")
        except (APIException, ValueError) as error:
            detail = str(getattr(error, "detail", error))
            message_key = (
                "support_registry_operation_error"
                if action == "vehicle_create"
                else "support_fleet_managed_by_project_error"
                if "vehicle_managed_by_project_crew" in detail
                else "support_fleet_delete_error"
                if action == "driver_vehicle_draft_delete"
                else "support_fleet_capacity_error"
                if action in {"driver_vehicle_draft_publish", "driver_vehicle_assign"}
                and "driver_crew_capacity_exceeded" in detail
                else "support_fleet_publish_error"
                if action in {"driver_vehicle_draft_publish", "driver_vehicle_assign"}
                else "support_fleet_edit_error"
                if action == "driver_vehicle_draft_edit"
                else "support_fleet_operation_error"
            )
            messages.error(
                request,
                tr(request, message_key),
            )
        else:
            messages.success(request, tr(request, success_key))
        query = urlencode({"organization": organization.public_id, "vehicle": redirect_vehicle_id})
        return redirect(f"{reverse('support:fleet')}?{query}")
    snapshot["workspace_url"] = f"{reverse('support:workspace')}?organization={organization.public_id}"
    return render(request, "support/fleet_workspace.html", snapshot)


def _team_redirect(organization, membership):
    return redirect(
        f"{reverse('support:team')}?organization={organization.public_id}"
        f"&member={membership.public_id}"
    )


def _team_list_redirect(organization):
    return redirect(
        f"{reverse('support:team')}?organization={organization.public_id}"
    )


def _team_operation(request, *, snapshot):
    organization = snapshot["organization"]
    action = (request.POST.get("action") or "").strip()
    try:
        if action == "member_invite":
            selected_groups = request.POST.getlist("permission_groups")
            allowed_group_ids = {
                item["id"] for item in snapshot["invitation_permission_groups"]
            }
            if any(group_id not in allowed_group_ids for group_id in selected_groups):
                raise ValueError("support_invitation_permission_not_allowed")
            create_membership_invitation(
                actor=request.user,
                organization=organization,
                invited_email=request.POST.get("invited_email"),
                display_role=request.POST.get("display_role"),
                permission_codes=permission_codes_for_group_ids(selected_groups),
            )
            messages.success(request, tr(request, "support_team_invitation_created"))
            return _team_list_redirect(organization)

        membership = get_object_or_404(
            OrganizationMembership,
            organization=organization,
            state=OrganizationMembership.STATE_ACTIVE,
            public_id=request.POST.get("membership_id"),
        )
        if membership.is_owner:
            raise ValueError("owner_scope_not_needed")
        if action == "scope_grant":
            connection = get_object_or_404(
                SupportConnection,
                organization=organization,
                is_archived=False,
                public_id=request.POST.get("connection_id"),
            )
            _, created = grant_worker_access_scope(
                actor=request.user,
                organization=organization,
                membership=membership,
                connection=connection,
            )
            message_key = (
                "support_team_scope_granted"
                if created
                else "support_team_scope_already_granted"
            )
        elif action == "scope_revoke":
            scope = get_object_or_404(
                WorkerAccessScope.objects.select_related("membership"),
                public_id=request.POST.get("scope_id"),
                membership=membership,
                membership__organization=organization,
            )
            revoke_worker_access_scope(actor=request.user, scope=scope)
            message_key = "support_team_scope_revoked"
        else:
            raise ValueError("team_operation_unknown")
    except (APIException, ValueError):
        message_key = (
            "support_team_invitation_error"
            if action == "member_invite"
            else "support_team_operation_error"
        )
        messages.error(request, tr(request, message_key))
    else:
        messages.success(request, tr(request, message_key))
    return _team_redirect(organization, membership)


@login_required(login_url="employer:login")
def team_management(request):
    if not is_support_feature_enabled():
        raise Http404("support_not_available")
    snapshot = team_management_snapshot(
        user=request.user,
        organization_public_id=request.GET.get("organization"),
        membership_public_id=request.GET.get("member"),
    )
    if request.method == "POST":
        return _team_operation(request, snapshot=snapshot)
    permission_group_labels = {
        group_id: tr(request, label_key)
        for group_id, label_key, _group_codes in TEAM_PERMISSION_GROUPS
    }
    for item in snapshot["invitation_permission_groups"]:
        item["label"] = permission_group_labels[item["id"]]
    for invitation in snapshot["pending_invitations"]:
        invitation.permission_group_labels = [
            permission_group_labels[group_id]
            for group_id in invitation.permission_group_ids
        ]
    snapshot["workspace_url"] = (
        f"{reverse('support:workspace')}?organization={snapshot['organization'].public_id}"
    )
    return render(request, "support/team_management.html", snapshot)


def _time_redirect(organization, *, request, date_from, date_to):
    query_data = {
        "organization": organization.public_id,
        "date_from": date_from,
        "date_to": date_to,
    }
    for key in ("project", "crew", "worker", "status"):
        value = (request.POST.get(key) or "").strip()
        if value:
            query_data[key] = value
    query = urlencode(query_data)
    return redirect(f"{reverse('support:time')}?{query}")


def _time_operation(request, *, snapshot):
    """Run web forms through the same timekeeping services as the API."""

    organization = snapshot["organization"]
    action = (request.POST.get("action") or "").strip()
    selected_date_from = request.POST.get("date_from") or snapshot["date_from"].isoformat()
    selected_date_to = request.POST.get("date_to") or snapshot["date_to"].isoformat()
    ignored_fields = ("date_from", "date_to", "project", "crew", "worker", "status")
    visible_entry_ids = {
        str(entry.public_id)
        for entry in snapshot["entries"]
    }

    def visible_entry_or_404():
        entry_id = (request.POST.get("entry_id") or "").strip()
        if entry_id not in visible_entry_ids:
            raise Http404("support_time_entry_not_found")
        return get_object_or_404(
            WorkTimeEntry,
            organization=organization,
            public_id=entry_id,
        )

    try:
        if action == "scheduled_shift_draft":
            data = _validated_post(
                ScheduledWorkShiftCreateSerializer,
                request,
                ignored_fields=ignored_fields,
            )
            connection = get_object_or_404(
                SupportConnection,
                organization=organization,
                public_id=data.pop("connection_id"),
            )
            data.pop("work_assignment_id", None)
            create_scheduled_shift(
                actor=request.user,
                organization=organization,
                connection=connection,
                work_assignment=None,
                **data,
            )
            message_key = "support_time_shift_draft_created"
        elif action == "scheduled_shift_publish":
            shift = get_object_or_404(
                ScheduledWorkShift,
                organization=organization,
                public_id=request.POST.get("shift_id"),
            )
            publish_scheduled_shift(actor=request.user, shift=shift)
            message_key = "support_time_shift_published"
        elif action == "scheduled_shift_cancel":
            shift = get_object_or_404(
                ScheduledWorkShift,
                organization=organization,
                public_id=request.POST.get("shift_id"),
            )
            cancel_scheduled_shift(actor=request.user, shift=shift)
            message_key = "support_time_shift_cancelled"
        elif action == "scheduled_shift_delete":
            shift = get_object_or_404(
                ScheduledWorkShift,
                organization=organization,
                public_id=request.POST.get("shift_id"),
            )
            delete_scheduled_shift_draft(actor=request.user, shift=shift)
            message_key = "support_draft_deleted"
        elif action == "time_entry_confirm":
            entry = visible_entry_or_404()
            confirm_work_time_entry(actor=request.user, entry=entry)
            message_key = "support_time_entry_confirmed"
        elif action == "time_entries_confirm_bulk":
            if not snapshot["permissions"]["time_review"]:
                raise ValueError("time_bulk_confirm_not_allowed")
            entry_ids = [value for value in request.POST.getlist("entry_ids") if value]
            selected_entries = list(
                WorkTimeEntry.objects.filter(
                    organization=organization,
                    public_id__in=entry_ids,
                    status=WorkTimeEntry.STATUS_SUBMITTED,
                )
            )
            if (
                not entry_ids
                or not set(entry_ids).issubset(visible_entry_ids)
                or len(selected_entries) != len(set(entry_ids))
            ):
                raise ValueError("time_bulk_confirm_invalid_selection")
            with transaction.atomic():
                for selected_entry in selected_entries:
                    confirm_work_time_entry(actor=request.user, entry=selected_entry)
            message_key = "support_time_entries_confirmed_bulk"
        elif action == "time_entry_correction":
            data = _validated_post(
                WorkTimeEntryCorrectionSerializer,
                request,
                ignored_fields=ignored_fields + ("entry_id",),
            )
            entry = visible_entry_or_404()
            request_work_time_correction(
                actor=request.user,
                entry=entry,
                reason=data["reason"],
            )
            message_key = "support_time_entry_correction_requested"
        elif action == "time_entry_edit":
            data = _validated_post(
                WorkTimeEntryStaffEditSerializer,
                request,
                ignored_fields=ignored_fields + ("entry_id",),
            )
            entry = visible_entry_or_404()
            edit_work_time_entry(actor=request.user, entry=entry, **data)
            message_key = "support_time_entry_edited"
        else:
            raise ValueError("time_operation_unknown")
    except (APIException, ValueError):
        messages.error(request, tr(request, "support_time_operation_error"))
    else:
        messages.success(request, tr(request, message_key))
    return _time_redirect(
        organization,
        request=request,
        date_from=selected_date_from,
        date_to=selected_date_to,
    )


def _timekeeping_csv(request, snapshot):
    if not snapshot["permissions"]["time_export"]:
        raise Http404("support_time_export_not_found")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="jobhub-timesheet-{snapshot["date_from"]}.csv"'
    )
    response.write("\ufeff")
    writer = csv.writer(response, delimiter=";")
    writer.writerow([
        tr(request, "support_time_worker"),
        tr(request, "support_time_project"),
        tr(request, "support_time_crew"),
        tr(request, "support_time_work_date"),
        tr(request, "support_time_planned"),
        tr(request, "support_time_actual"),
        tr(request, "support_time_break"),
        tr(request, "support_time_total_minutes"),
        tr(request, "support_time_total_decimal"),
        tr(request, "support_time_status"),
        tr(request, "support_time_confirmed_by"),
    ])
    export_all = request.GET.get("export_scope") == "all"
    for entry in snapshot["entries"]:
        if not export_all and entry.status != WorkTimeEntry.STATUS_CONFIRMED:
            continue
        writer.writerow([
            entry.worker_display_name,
            entry.project_label,
            entry.crew_label,
            entry.work_date.isoformat(),
            _time_range_label(entry.scheduled_shift) if entry.scheduled_shift else "",
            f"{timezone.localtime(entry.started_at):%H:%M}-{timezone.localtime(entry.ended_at):%H:%M}",
            entry.break_minutes,
            entry.worked_minutes,
            entry.decimal_hours_label,
            tr(request, f"support_time_status_{entry.status}"),
            entry.confirmed_by.get_username() if entry.confirmed_by else "",
        ])
    return response


def _time_range_label(shift):
    starts_at = getattr(shift, "starts_at", None) or shift.started_at
    ends_at = getattr(shift, "ends_at", None) or shift.ended_at
    return f"{timezone.localtime(starts_at):%H:%M}-{timezone.localtime(ends_at):%H:%M}"


@login_required(login_url="employer:login")
def timekeeping_workspace(request):
    if not is_support_feature_enabled():
        raise Http404("support_not_available")
    snapshot = timekeeping_snapshot(
        user=request.user,
        organization_public_id=request.GET.get("organization"),
        anchor=request.GET.get("anchor"),
        date_from=request.GET.get("date_from"),
        date_to=request.GET.get("date_to"),
        project_id=request.GET.get("project"),
        crew_id=request.GET.get("crew"),
        worker_id=request.GET.get("worker"),
        status_filter=request.GET.get("status"),
    )
    if request.method == "POST":
        return _time_operation(request, snapshot=snapshot)
    if request.GET.get("export") == "csv":
        return _timekeeping_csv(request, snapshot)
    conversation_by_connection = {
        item.connection_id: item
        for item in SupportConversation.objects.filter(
            organization=snapshot["organization"],
            connection_id__in=[entry.connection_id for entry in snapshot["entries"]],
            kind=SupportConversation.KIND_MANAGER,
            state=SupportConversation.STATE_ACTIVE,
            members__user=request.user,
            members__left_at__isnull=True,
        ).distinct()
    }
    for entry in snapshot["entries"]:
        entry.status_label = tr(request, f"support_time_status_{entry.status}")
        entry.worker_url = reverse(
            "support:worker-card",
            kwargs={"connection_public_id": entry.connection.public_id},
        )
        conversation = conversation_by_connection.get(entry.connection_id)
        entry.conversation_url = (
            reverse(
                "support:conversation-detail",
                kwargs={"conversation_public_id": conversation.public_id},
            )
            if conversation
            else ""
        )
        entry.actual_range_label = _time_range_label(entry)
        entry.planned_range_label = (
            _time_range_label(entry.scheduled_shift) if entry.scheduled_shift else "—"
        )
        for revision in entry.revisions.all():
            revision.status_label = tr(
                request, f"support_time_status_{revision.status_after}"
            )
            revision.actor_label = revision.actor.get_username() if revision.actor else "—"
    for row in snapshot["worker_rows"]:
        row.worker_url = reverse(
            "support:worker-card",
            kwargs={"connection_public_id": row.worker.public_id},
        )
        for cell in row.cells:
            for shift in cell.shifts:
                shift.range_label = _time_range_label(shift)
            if cell.entry:
                cell.entry.status_label = tr(
                    request, f"support_time_status_{cell.entry.status}"
                )
    snapshot["status_filter_choices"] = [
        (value, tr(request, f"support_time_status_{value}"))
        for value, _label in snapshot["status_choices"]
    ]
    snapshot["workspace_url"] = (
        f"{reverse('support:workspace')}?organization={snapshot['organization'].public_id}"
    )
    return render(request, "support/timekeeping_workspace.html", snapshot)


def _worker_requests_redirect(organization, *, status_filter):
    query = urlencode(
        {
            "organization": organization.public_id,
            "filter": status_filter,
        }
    )
    return redirect(f"{reverse('support:worker-requests')}?{query}")


def _worker_request_operation(request, *, snapshot):
    """Review one request through the same service used by the protected API."""

    organization = snapshot["organization"]
    action = (request.POST.get("action") or "").strip()
    selected_filter = request.POST.get("filter") or snapshot["status_filter"]
    try:
        if action not in {"request_clarify", "request_approve", "request_decline"}:
            raise ValueError("worker_request_operation_unknown")
        data = _validated_post(
            WorkerRequestDecisionSerializer,
            request,
            ignored_fields=("request_id", "filter"),
        )
        allowed_connections = worker_connection_queryset_for(
            user=request.user,
            organization=organization,
            queryset=SupportConnection.objects.filter(is_archived=False),
        )
        item = get_object_or_404(
            WorkerRequest.objects.select_related("connection"),
            organization=organization,
            connection__in=allowed_connections,
            public_id=request.POST.get("request_id"),
        )
        service_action = {
            "request_clarify": "clarify",
            "request_approve": "approve",
            "request_decline": "decline",
        }[action]
        decide_worker_request(
            actor=request.user,
            request=item,
            action=service_action,
            manager_note=data["manager_note"],
        )
        message_key = {
            "clarify": "support_requests_clarification_sent",
            "approve": "support_requests_approved",
            "decline": "support_requests_declined",
        }[service_action]
    except (APIException, ValueError):
        messages.error(request, tr(request, "support_requests_operation_error"))
    else:
        messages.success(request, tr(request, message_key))
    return _worker_requests_redirect(organization, status_filter=selected_filter)


@login_required(login_url="employer:login")
def worker_requests_workspace(request):
    if not is_support_feature_enabled():
        raise Http404("support_not_available")
    snapshot = worker_requests_snapshot(
        user=request.user,
        organization_public_id=request.GET.get("organization"),
        status_filter=request.GET.get("filter") or "open",
    )
    if request.method == "POST":
        return _worker_request_operation(request, snapshot=snapshot)
    for item in snapshot["requests"]:
        item.type_label = tr(request, f"support_request_type_{item.request_type}")
        item.status_label = tr(request, f"support_request_status_{item.status}")
        item.worker_url = (
            reverse(
                "support:worker-card",
                kwargs={"connection_public_id": item.connection.public_id},
            )
            if snapshot["permissions"]["workers"]
            else None
        )
    snapshot["workspace_url"] = (
        f"{reverse('support:workspace')}?organization={snapshot['organization'].public_id}"
    )
    snapshot["filter_base_url"] = (
        f"{reverse('support:worker-requests')}?organization={snapshot['organization'].public_id}"
    )
    return render(request, "support/worker_requests_workspace.html", snapshot)
