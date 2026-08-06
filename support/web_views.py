import uuid
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from urllib.parse import urlencode
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.exceptions import APIException, ValidationError

from jobs.web_i18n import get_lang, tr

from .feature_flags import is_support_feature_enabled
from .permissions import worker_connection_queryset_for
from .models import (
    DocumentRequestPackage,
    DriverVehicleAssignment,
    HousingAssignment,
    HousingPlace,
    HousingRoom,
    HousingSite,
    OrganizationMembership,
    ProjectScheduleTemplate,
    RouteStop,
    ScheduledWorkShift,
    SupportConnection,
    SupportConversation,
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
    registry_snapshot,
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
from .services.operations import (
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
    schedule_housing_check_out,
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
)
from .services.organizations import (
    create_membership_invitation,
    grant_worker_access_scope,
    revoke_worker_access_scope,
)
from .services.conversations import (
    mark_conversation_read,
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
    for item in snapshot["operation_cards"]:
        item["label"] = tr(request, f"support_workspace_{item['key']}_drafts")
    if snapshot["operation_cards"]:
        snapshot["registry_url"] = reverse("support:registries")
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


def _worker_card_redirect(connection, *, tab=None, month=None, site=None):
    """Return to the same focused worker tab after a safe POST operation."""

    query = {}
    if tab in {"company", "transport", "housing"}:
        query["tab"] = tab
    if month:
        query["month"] = month
    if site:
        query["site"] = site
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
    return "support_transport_operation_error"


def _worker_operation_error_key(error):
    """Expose expected work-assignment conflicts without hiding the cause."""

    detail = str(getattr(error, "detail", error))
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
    if "current_scheduled_shift_already_exists" in detail:
        return "support_worker_schedule_day_already_has_shift"
    if "published_assignment_for_selected_project_required" in detail:
        return "support_worker_quick_shift_active_assignment_required"
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
        elif action == "document_package_create":
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
            messages.success(request, tr(request, "support_document_package_created"))
        elif action in {"document_package_correction", "document_package_complete", "document_package_not_required", "document_package_cancel"}:
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
                ignored_fields=("package_id",),
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
            messages.success(request, tr(request, "support_document_package_updated"))
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
                current_shifts = {
                    item.work_date: item
                    for item in ScheduledWorkShift.objects.select_for_update()
                    .filter(
                        organization=organization,
                        connection=connection,
                        work_date__in=work_dates,
                        state__in=(
                            ScheduledWorkShift.STATE_DRAFT,
                            ScheduledWorkShift.STATE_PUBLISHED,
                        ),
                    )
                    .order_by("work_date", "-created_at", "-id")
                }
                replaced_count = 0
                for work_date, starts_at, ends_at in shift_values:
                    current_shift = current_shifts.get(work_date)
                    if current_shift is not None:
                        replace_scheduled_shift(
                            actor=request.user,
                            shift=current_shift,
                            work_date=work_date,
                            starts_at=starts_at,
                            ends_at=ends_at,
                            break_minutes=template.break_minutes,
                            worker_label=(template.worker_label or template.name).strip(),
                            work_assignment=work_assignment,
                            replacement_state=ScheduledWorkShift.STATE_PUBLISHED,
                        )
                        replaced_count += 1
                    else:
                        shift = create_scheduled_shift(
                            actor=request.user,
                            organization=organization,
                            connection=connection,
                            work_date=work_date,
                            starts_at=starts_at,
                            ends_at=ends_at,
                            break_minutes=template.break_minutes,
                            worker_label=(template.worker_label or template.name).strip(),
                            work_assignment=work_assignment,
                        )
                        publish_scheduled_shift(actor=request.user, shift=shift)
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
            for shift in current_shifts:
                cancel_scheduled_shift(actor=request.user, shift=shift)
            messages.success(request, tr(request, "support_worker_quick_shifts_cleared"))
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
        message_key = (
            "support_worker_housing_check_out_error"
            if action == "housing_check_out"
            else (
                _transport_operation_error_key(error)
                if action == "route_create"
                else _worker_operation_error_key(error)
            )
        )
        messages.error(request, tr(request, message_key))
    except Exception as error:  # Safety net: an employer action must never end on a blank 500 page.
        print(
            "[SUPPORT-WORKER-OPERATION-ERROR] "
            f"action={action} type={type(error).__name__} detail={error}"
        )
        messages.error(request, tr(request, "support_worker_operation_error"))
    return _worker_card_redirect(
        connection,
        tab=request.POST.get("return_tab"),
        month=request.POST.get("return_month"),
        site=request.POST.get("return_site"),
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
    snapshot["workspace_url"] = (
        f"{reverse('support:workspace')}?organization={snapshot['organization'].public_id}"
    )
    requested_tab = (request.GET.get("tab") or "company").strip()
    visible_tabs = {
        "company": snapshot["permissions"]["work"],
        "transport": snapshot["permissions"]["transport"],
        "housing": snapshot["permissions"]["housing"],
    }
    if not visible_tabs.get(requested_tab):
        requested_tab = next(
            (key for key, allowed in visible_tabs.items() if allowed),
            "company",
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
            "company_url": f"{worker_base_url}?tab=company",
            "transport_tab_url": f"{worker_base_url}?tab=transport",
            "housing_tab_url": f"{worker_base_url}?tab=housing",
            "calendar_month": (
                selected_calendar_date.strftime("%Y-%m")
                if selected_calendar_date
                else ""
            ),
            "calendar_previous_url": (
                f"{worker_base_url}?tab=company&month="
                f"{snapshot['calendar_previous_month'].strftime('%Y-%m')}"
                if snapshot["calendar_previous_month"]
                else None
            ),
            "calendar_next_url": (
                f"{worker_base_url}?tab=company&month="
                f"{snapshot['calendar_next_month'].strftime('%Y-%m')}"
                if snapshot["calendar_next_month"]
                else None
            ),
        }
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
    snapshot["workspace_url"] = (
        f"{reverse('support:workspace')}?organization={snapshot['organization'].public_id}"
    )
    return render(request, "support/registries.html", snapshot)


def _projects_redirect(organization, *, project=None, calendar_month=None):
    query = {"organization": organization.public_id}
    if calendar_month:
        query["month"] = calendar_month
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
            raise ValueError("project_operation_unknown")
        except (APIException, ValueError) as error:
            messages.error(request, tr(request, _project_operation_error_key(error)))
            return _projects_redirect(
                organization,
                project=selected_project,
                calendar_month=request.POST.get("return_month"),
            )
    snapshot["workspace_url"] = (
        f"{reverse('support:workspace')}?organization={organization.public_id}"
    )
    snapshot["project_list_url"] = (
        f"{reverse('support:projects')}?organization={organization.public_id}"
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
    snapshot["registry_url"] = (
        f"{reverse('support:registries')}?organization={snapshot['organization'].public_id}"
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
        action = (request.POST.get("action") or "driver_vehicle_draft_create").strip()
        try:
            if action == "driver_vehicle_draft_publish":
                assignment = get_object_or_404(
                    DriverVehicleAssignment,
                    organization=organization,
                    public_id=request.POST.get("assignment_id"),
                )
                publish_driver_vehicle_assignment(actor=request.user, assignment=assignment)
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
                if action == "driver_vehicle_draft_edit":
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
        except (APIException, ValueError):
            message_key = (
                "support_fleet_delete_error"
                if action == "driver_vehicle_draft_delete"
                else "support_fleet_publish_error"
                if action == "driver_vehicle_draft_publish"
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
        query = urlencode({"organization": organization.public_id, "vehicle": request.POST.get("vehicle_id", "")})
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


def _time_redirect(organization, *, date_from, date_to):
    query = urlencode(
        {
            "organization": organization.public_id,
            "date_from": date_from,
            "date_to": date_to,
        }
    )
    return redirect(f"{reverse('support:time')}?{query}")


def _time_operation(request, *, snapshot):
    """Run web forms through the same timekeeping services as the API."""

    organization = snapshot["organization"]
    action = (request.POST.get("action") or "").strip()
    selected_date_from = request.POST.get("date_from") or snapshot["date_from"].isoformat()
    selected_date_to = request.POST.get("date_to") or snapshot["date_to"].isoformat()
    ignored_fields = ("date_from", "date_to")
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
            entry = get_object_or_404(
                WorkTimeEntry,
                organization=organization,
                public_id=request.POST.get("entry_id"),
            )
            confirm_work_time_entry(actor=request.user, entry=entry)
            message_key = "support_time_entry_confirmed"
        elif action == "time_entry_correction":
            data = _validated_post(
                WorkTimeEntryCorrectionSerializer,
                request,
                ignored_fields=ignored_fields + ("entry_id",),
            )
            entry = get_object_or_404(
                WorkTimeEntry,
                organization=organization,
                public_id=request.POST.get("entry_id"),
            )
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
            entry = get_object_or_404(
                WorkTimeEntry,
                organization=organization,
                public_id=request.POST.get("entry_id"),
            )
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
        date_from=selected_date_from,
        date_to=selected_date_to,
    )


@login_required(login_url="employer:login")
def timekeeping_workspace(request):
    if not is_support_feature_enabled():
        raise Http404("support_not_available")
    snapshot = timekeeping_snapshot(
        user=request.user,
        organization_public_id=request.GET.get("organization"),
        date_from=request.GET.get("date_from"),
        date_to=request.GET.get("date_to"),
    )
    if request.method == "POST":
        return _time_operation(request, snapshot=snapshot)
    for entry in snapshot["entries"]:
        entry.status_label = tr(request, f"support_time_status_{entry.status}")
        entry.worker_url = reverse(
            "support:worker-card",
            kwargs={"connection_public_id": entry.connection.public_id},
        )
    for shift in snapshot["scheduled_shifts"]:
        shift.state_label = tr(request, f"support_worker_state_{shift.state}")
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
