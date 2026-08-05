"""Data for the first employer Support workspace screen.

This module deliberately returns only values already allowed by the current
membership.  Views and templates must not construct broader querysets and then
hide columns, because that would still expose data through a direct URL.
"""

from calendar import monthrange
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from django.http import Http404
from django.db.models import Prefetch, Q
from django.utils import timezone
from django.utils.dateparse import parse_date

from support.models import (
    DocumentRequestPackage,
    DriverVehicleAssignment,
    HousingAssignment,
    HousingPlace,
    HousingRoom,
    HousingSite,
    MembershipInvitation,
    OrganizationMembership,
    ProjectScheduleTemplate,
    RouteStop,
    ScheduledWorkShift,
    SupportApplication,
    SupportConnection,
    SupportConversation,
    TransportPassengerAssignment,
    TransportRoute,
    Vehicle,
    WorkerProjectAssignment,
    WorkProject,
    Worksite,
    WorkTimeEntry,
    WorkerRequest,
)
from support.permission_codes import (
    CHAT_MANAGE,
    DOCUMENT_REQUEST,
    HOUSING_MANAGE,
    MEMBER_DELEGATE_PERMISSIONS,
    MEMBER_INVITE,
    ORGANIZATION_MANAGE,
    PIPELINE_REVIEW,
    SCHEDULE_MANAGE,
    TIME_EDIT,
    TIME_REVIEW,
    TIME_VIEW,
    TRANSPORT_MANAGE,
    WORKER_VIEW,
    REQUEST_DECIDE,
)
from support.permissions import (
    has_permission,
    has_unrestricted_worker_access,
    has_worker_connection_access,
    may_delegate_permission,
    worker_connection_queryset_for,
)
from support.permission_groups import TEAM_PERMISSION_GROUPS


def _display_name(user):
    return user.get_full_name().strip() or user.username


def _date_period_overlaps(*, starts_field, ends_field, starts_on, ends_on):
    """Return a query for date ranges that overlap the supplied period."""

    if ends_on is None:
        return Q(**{f"{ends_field}__isnull": True}) | Q(
            **{f"{ends_field}__gt": starts_on}
        )
    return Q(**{f"{starts_field}__lte": ends_on}) & (
        Q(**{f"{ends_field}__isnull": True})
        | Q(**{f"{ends_field}__gt": starts_on})
    )


def _datetime_periods_overlap(*, starts_at, ends_at, other_starts_at, other_ends_at):
    """Return whether two half-open datetime periods overlap."""

    return (
        (ends_at is None or other_starts_at < ends_at)
        and (other_ends_at is None or other_ends_at > starts_at)
    )


def _select_membership(*, user, organization_public_id):
    memberships = list(
        OrganizationMembership.objects.filter(
            user=user,
            state=OrganizationMembership.STATE_ACTIVE,
            organization__status="active",
        )
        .select_related("organization")
        .order_by("organization__display_name", "id")
    )
    if not memberships:
        raise Http404("support_workspace_not_found")

    if organization_public_id:
        membership = next(
            (
                item
                for item in memberships
                if str(item.organization.public_id) == str(organization_public_id)
            ),
            None,
        )
        if membership is None:
            raise Http404("support_workspace_not_found")
        return memberships, membership
    return memberships, memberships[0]


def _permissions_for(*, user, organization):
    return {
        "chat_manage": has_permission(
            user=user, organization=organization, permission_code=CHAT_MANAGE
        ),
        "pipeline": has_permission(
            user=user, organization=organization, permission_code=PIPELINE_REVIEW
        ),
        "organization_manage": has_permission(
            user=user,
            organization=organization,
            permission_code=ORGANIZATION_MANAGE,
        ),
        "workers": has_permission(
            user=user, organization=organization, permission_code=WORKER_VIEW
        ),
        "housing": has_permission(
            user=user, organization=organization, permission_code=HOUSING_MANAGE
        ),
        "work": has_permission(
            user=user, organization=organization, permission_code=SCHEDULE_MANAGE
        ),
        "schedule": has_permission(
            user=user, organization=organization, permission_code=SCHEDULE_MANAGE
        ),
        "transport": has_permission(
            user=user, organization=organization, permission_code=TRANSPORT_MANAGE
        ),
        "time_view": has_permission(
            user=user, organization=organization, permission_code=TIME_VIEW
        ),
        "time_review": has_permission(
            user=user, organization=organization, permission_code=TIME_REVIEW
        ),
        "time_edit": has_permission(
            user=user, organization=organization, permission_code=TIME_EDIT
        ),
        "request_decide": has_permission(
            user=user, organization=organization, permission_code=REQUEST_DECIDE
        ),
        "document_request": has_permission(
            user=user, organization=organization, permission_code=DOCUMENT_REQUEST
        ),
    }


def worker_requests_snapshot(*, user, organization_public_id=None, status_filter="open"):
    """Return the staff request queue already reduced to their worker scope."""

    memberships, membership = _select_membership(
        user=user,
        organization_public_id=organization_public_id,
    )
    organization = membership.organization
    permissions = _permissions_for(user=user, organization=organization)
    if not permissions["request_decide"]:
        raise Http404("support_worker_requests_not_found")

    allowed_connections = worker_connection_queryset_for(
        user=user,
        organization=organization,
        queryset=SupportConnection.objects.filter(is_archived=False),
    )
    request_queryset = WorkerRequest.objects.filter(
        organization=organization,
        connection__in=allowed_connections,
    )
    supported_filters = {
        "open": (
            WorkerRequest.STATUS_SUBMITTED,
            WorkerRequest.STATUS_NEEDS_CLARIFICATION,
        ),
        "urgent": (WorkerRequest.STATUS_SUBMITTED,),
        "all": None,
    }
    normalized_filter = (status_filter or "open").strip()
    if normalized_filter not in supported_filters:
        raise Http404("support_worker_requests_invalid_filter")
    statuses = supported_filters[normalized_filter]
    if statuses is not None:
        request_queryset = request_queryset.filter(status__in=statuses)
    if normalized_filter == "urgent":
        request_queryset = request_queryset.filter(
            request_type=WorkerRequest.TYPE_UNABLE_TODAY,
        )

    requests = list(
        request_queryset.select_related(
            "connection__candidate",
            "connection__vacancy",
            "reviewed_by",
        ).order_by(
            "-submitted_at",
            "-created_at",
            "-id",
        )[:250]
    )
    for item in requests:
        item.worker_display_name = _display_name(item.connection.candidate)
        item.is_open = item.status in {
            WorkerRequest.STATUS_SUBMITTED,
            WorkerRequest.STATUS_NEEDS_CLARIFICATION,
        }
        item.is_urgent_open = (
            item.request_type == WorkerRequest.TYPE_UNABLE_TODAY
            and item.status == WorkerRequest.STATUS_SUBMITTED
        )
    requests.sort(key=lambda item: not item.is_urgent_open)
    if requests:
        connection_ids = {item.connection_id for item in requests}
        earliest_request_day = min(item.starts_on for item in requests)
        latest_request_day = max(item.ends_on for item in requests)
        shifts_by_connection = {}
        for shift in ScheduledWorkShift.objects.filter(
            organization=organization,
            connection_id__in=connection_ids,
            state=ScheduledWorkShift.STATE_PUBLISHED,
            work_date__range=(earliest_request_day, latest_request_day),
        ).order_by("work_date", "starts_at", "id"):
            shifts_by_connection.setdefault(shift.connection_id, []).append(shift)
        for item in requests:
            affected_shifts = [
                shift
                for shift in shifts_by_connection.get(item.connection_id, [])
                if item.starts_on <= shift.work_date <= item.ends_on
            ]
            item.affected_shift_count = len(affected_shifts)
            # A reviewer who does not manage schedules still needs to know
            # that a conflict exists, but does not receive the shift times.
            item.affected_shifts = affected_shifts if permissions["schedule"] else []
    return {
        "organization": organization,
        "membership": membership,
        "memberships": memberships,
        "permissions": permissions,
        "status_filter": normalized_filter,
        "requests": requests,
    }


def workspace_snapshot(*, user, organization_public_id=None):
    """Return a safe snapshot for the first staff workspace page."""

    memberships, membership = _select_membership(
        user=user,
        organization_public_id=organization_public_id,
    )
    organization = membership.organization
    permissions = _permissions_for(user=user, organization=organization)

    application_rows = []
    application_count = 0
    if permissions["pipeline"]:
        applications = (
            SupportApplication.objects.filter(
                vacancy__organization=organization,
                status__in=(
                    SupportApplication.STATUS_SUBMITTED,
                    SupportApplication.STATUS_UNDER_REVIEW,
                ),
            )
            .select_related("candidate", "vacancy")
            .order_by("submitted_at", "id")
        )
        application_count = applications.count()
        application_rows = [
            {
                "candidate_name": _display_name(item.candidate),
                "vacancy_title": item.vacancy.internal_title,
                "status_key": f"support_application_{item.status}",
                "submitted_at": item.submitted_at,
            }
            for item in applications[:8]
        ]

    worker_rows = []
    worker_count = 0
    if permissions["workers"]:
        connections = worker_connection_queryset_for(
            user=user,
            organization=organization,
            queryset=(
                SupportConnection.objects.filter(
                    is_archived=False,
                    stage__in=(
                        SupportConnection.STAGE_COORDINATOR,
                        SupportConnection.STAGE_ACTIVE_WORKER,
                    ),
                )
                .select_related("candidate", "vacancy")
                .order_by("-updated_at", "-id")
            ),
        )
        worker_count = connections.count()
        connection_ids = list(connections.values_list("id", flat=True)[:8])
        published_housing_ids = set(
            HousingAssignment.objects.filter(
                connection_id__in=connection_ids,
                state=HousingAssignment.STATE_PUBLISHED,
            ).values_list("connection_id", flat=True)
        )
        published_work_ids = set(
            WorkerProjectAssignment.objects.filter(
                connection_id__in=connection_ids,
                state=WorkerProjectAssignment.STATE_PUBLISHED,
            ).values_list("connection_id", flat=True)
        )
        published_passenger_ids = set(
            TransportPassengerAssignment.objects.filter(
                connection_id__in=connection_ids,
                route__state=TransportRoute.STATE_PUBLISHED,
            ).values_list("connection_id", flat=True)
        )
        published_driver_ids = set(
            DriverVehicleAssignment.objects.filter(
                driver_connection_id__in=connection_ids,
                state=DriverVehicleAssignment.STATE_PUBLISHED,
            ).values_list("driver_connection_id", flat=True)
        )
        worker_rows = [
            {
                "connection_id": str(item.public_id),
                "candidate_name": _display_name(item.candidate),
                "vacancy_title": item.vacancy.internal_title,
                "stage_key": f"support_stage_{item.stage}",
                "housing_ready": item.id in published_housing_ids,
                "work_ready": item.id in published_work_ids,
                # The dashboard says only whether transport is assigned. It
                # never exposes a route, address, passengers or vehicle here.
                "transport_ready": item.id in published_passenger_ids
                or item.id in published_driver_ids,
            }
            for item in connections[:8]
        ]

    operation_cards = []
    if permissions["housing"]:
        operation_cards.append(
            {
                "key": "housing",
                "count": HousingAssignment.objects.filter(
                    organization=organization,
                    state=HousingAssignment.STATE_DRAFT,
                ).count(),
            }
        )
    if permissions["work"]:
        operation_cards.append(
            {
                "key": "work",
                "count": WorkerProjectAssignment.objects.filter(
                    organization=organization,
                    state=WorkerProjectAssignment.STATE_DRAFT,
                ).count(),
            }
        )
    if permissions["transport"]:
        operation_cards.append(
            {
                "key": "transport",
                "count": TransportRoute.objects.filter(
                    organization=organization,
                    state=TransportRoute.STATE_DRAFT,
                    reservation_expires_at__gt=timezone.now(),
                ).count(),
            }
        )

    return {
        "organization": organization,
        "membership": membership,
        "memberships": memberships,
        "permissions": permissions,
        "application_count": application_count,
        "application_rows": application_rows,
        "worker_count": worker_count,
        "worker_rows": worker_rows,
        "operation_cards": operation_cards,
    }


def conversation_workspace_snapshot(*, user, organization_public_id=None):
    """Return only Support conversations explicitly assigned to this staff user.

    A company-wide membership alone is not enough to list a chat.  This keeps
    manager, coordinator and group conversations private until the employer
    adds the employee as a conversation member.
    """

    memberships, membership = _select_membership(
        user=user,
        organization_public_id=organization_public_id,
    )
    organization = membership.organization
    permissions = _permissions_for(user=user, organization=organization)
    if not permissions["chat_manage"]:
        raise Http404("support_conversations_not_found")

    conversations = list(
        SupportConversation.objects.filter(
            organization=organization,
            state=SupportConversation.STATE_ACTIVE,
            members__user=user,
            members__left_at__isnull=True,
        )
        .prefetch_related("members__user")
        .distinct()
        .order_by("-updated_at", "-id")[:100]
    )
    rows = []
    for conversation in conversations:
        participant_names = [
            _display_name(item.user)
            for item in conversation.members.all()
            if item.left_at is None and item.user_id != user.id
        ]
        rows.append(
            {
                "conversation_id": str(conversation.public_id),
                "kind": conversation.kind,
                "title": conversation.title,
                "participants": participant_names,
                "updated_at": conversation.updated_at,
            }
        )
    return {
        "organization": organization,
        "membership": membership,
        "memberships": memberships,
        "permissions": permissions,
        "conversation_rows": rows,
    }


def projects_snapshot(*, user, organization_public_id=None, project_public_id=None, calendar_month=None):
    """Return the employer-facing project directory without exposing other firms."""

    memberships, membership = _select_membership(
        user=user,
        organization_public_id=organization_public_id,
    )
    organization = membership.organization
    permissions = _permissions_for(user=user, organization=organization)
    if not permissions["work"]:
        raise Http404("support_projects_not_found")

    projects = list(
        WorkProject.objects.filter(organization=organization, is_active=True)
        .select_related("worksite")
        .order_by("internal_name", "id")
    )
    now = timezone.now()
    for project in projects:
        active_assignments = WorkerProjectAssignment.objects.filter(
            project=project,
            state=WorkerProjectAssignment.STATE_PUBLISHED,
        ).filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
        project.filled_places = active_assignments.count()
        project.draft_places = WorkerProjectAssignment.objects.filter(
            project=project,
            state=WorkerProjectAssignment.STATE_DRAFT,
        ).count()
        project.address_label = " · ".join(
            item
            for item in (
                project.worksite.city,
                project.worksite.street,
                project.worksite.building,
            )
            if item
        )

    selected_project = None
    if project_public_id:
        selected_project = next(
            (item for item in projects if str(item.public_id) == str(project_public_id)),
            None,
        )
        if selected_project is None:
            raise Http404("support_project_not_found")

    templates = []
    workers = []
    routes = []
    calendar_days = []
    selected_calendar_month = None
    calendar_previous_month = None
    calendar_next_month = None
    if selected_project is not None:
        templates = list(
            ProjectScheduleTemplate.objects.filter(
                project=selected_project,
                is_active=True,
            ).order_by("name", "id")
        )
        allowed_connections = worker_connection_queryset_for(
            user=user,
            organization=organization,
            queryset=SupportConnection.objects.filter(is_archived=False),
        )
        workers = list(
            WorkerProjectAssignment.objects.filter(
                project=selected_project,
                connection__in=allowed_connections,
                state=WorkerProjectAssignment.STATE_PUBLISHED,
            )
            .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
            .select_related("connection__candidate")
            .order_by("connection__candidate__first_name", "connection__candidate__last_name", "id")
        )
        for item in workers:
            item.worker_display_name = _display_name(item.connection.candidate)

        routes = list(
            TransportRoute.objects.filter(
                organization=organization,
                worksite=selected_project.worksite,
                state=TransportRoute.STATE_PUBLISHED,
            )
            .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=timezone.localdate()))
            .select_related(
                "driver_vehicle_assignment__vehicle",
                "driver_vehicle_assignment__driver_connection__candidate",
            )
            .order_by("starts_on", "departure_time", "id")
        )
        for route in routes:
            route.driver_display_name = _display_name(
                route.driver_vehicle_assignment.driver_connection.candidate
            )

        try:
            year, month = [int(item) for item in (calendar_month or "").split("-", 1)]
            selected_calendar_month = date(year, month, 1)
        except (TypeError, ValueError):
            today = timezone.localdate()
            selected_calendar_month = date(today.year, today.month, 1)
        selected_month_end = date(
            selected_calendar_month.year,
            selected_calendar_month.month,
            monthrange(selected_calendar_month.year, selected_calendar_month.month)[1],
        )
        template_by_date = {}
        for template in templates:
            for raw_value in template.calendar_dates:
                selected_date = parse_date(raw_value) if isinstance(raw_value, str) else None
                if selected_date is None or not (
                    selected_calendar_month <= selected_date <= selected_month_end
                ):
                    continue
                template_by_date.setdefault(selected_date, []).append(template)
        first_weekday, days_in_month = monthrange(
            selected_calendar_month.year,
            selected_calendar_month.month,
        )
        calendar_days = [None] * first_weekday
        for day_number in range(1, days_in_month + 1):
            current_date = date(
                selected_calendar_month.year,
                selected_calendar_month.month,
                day_number,
            )
            calendar_days.append(
                {
                    "date": current_date,
                    "templates": template_by_date.get(current_date, []),
                    "is_today": current_date == timezone.localdate(),
                }
            )
        calendar_previous_month = (
            date(selected_calendar_month.year - 1, 12, 1)
            if selected_calendar_month.month == 1
            else date(selected_calendar_month.year, selected_calendar_month.month - 1, 1)
        )
        calendar_next_month = (
            date(selected_calendar_month.year + 1, 1, 1)
            if selected_calendar_month.month == 12
            else date(selected_calendar_month.year, selected_calendar_month.month + 1, 1)
        )

    return {
        "organization": organization,
        "membership": membership,
        "memberships": memberships,
        "permissions": permissions,
        "projects": projects,
        "selected_project": selected_project,
        "schedule_templates": templates,
        "workers": workers,
        "routes": routes,
        "calendar_days": calendar_days,
        "selected_calendar_month": selected_calendar_month,
        "calendar_previous_month": calendar_previous_month,
        "calendar_next_month": calendar_next_month,
    }


def worker_card_snapshot(*, user, connection_public_id, calendar_month=None, housing_site_public_id=None):
    """Return one worker card without expanding access beyond its organization.

    A specialized coordinator may open the card with their operational right
    even before the later AccessScope model is added.  Such a coordinator sees
    only the section needed for their own operation, not every assignment.
    """

    connection = (
        SupportConnection.objects.filter(
            public_id=connection_public_id,
            is_archived=False,
            organization__status="active",
            organization__memberships__user=user,
            organization__memberships__state=OrganizationMembership.STATE_ACTIVE,
        )
        .select_related("organization", "candidate", "vacancy")
        .distinct()
        .first()
    )
    if connection is None:
        raise Http404("support_worker_not_found")
    organization = connection.organization
    permissions = _permissions_for(user=user, organization=organization)
    if not any(
        (
            permissions["workers"],
            permissions["housing"],
            permissions["work"],
            permissions["transport"],
            permissions["document_request"],
        )
    ):
        raise Http404("support_worker_not_found")
    if not has_worker_connection_access(
        user=user,
        organization=organization,
        connection=connection,
    ):
        raise Http404("support_worker_not_found")

    housing_assignments = []
    housing_places = []
    housing_sites = []
    selected_housing_site = None
    selected_housing_rooms = []
    if permissions["housing"]:
        housing_assignments = list(
            HousingAssignment.objects.filter(connection=connection)
            .select_related("place__room__site")
            .order_by("-check_in_at", "-id")
        )
        housing_places = list(
            HousingPlace.objects.filter(
                room__site__organization=organization,
                is_active=True,
                room__is_active=True,
                room__site__is_active=True,
            )
            .select_related("room__site")
            .order_by("room__site__internal_name", "room__label", "label", "id")
        )
        housing_sites = list(
            HousingSite.objects.filter(
                organization=organization,
                is_active=True,
            ).order_by("internal_name", "id")
        )
        if housing_site_public_id:
            selected_housing_site = next(
                (
                    item
                    for item in housing_sites
                    if str(item.public_id) == str(housing_site_public_id)
                ),
                None,
            )
            if selected_housing_site is None:
                raise Http404("support_housing_site_not_found")
        elif housing_sites:
            selected_housing_site = housing_sites[0]

        if selected_housing_site is not None:
            # A housing manager needs a plan of every current or upcoming
            # placement. Names are still limited to workers in the manager's
            # own scope; an assignment outside that scope is never exposed as
            # a name.
            allowed_worker_ids = set(
                worker_connection_queryset_for(
                    user=user,
                    organization=organization,
                    queryset=SupportConnection.objects.filter(is_archived=False),
                ).values_list("id", flat=True)
            )
            selected_housing_rooms = list(
                HousingRoom.objects.filter(
                    site=selected_housing_site,
                    is_active=True,
                )
                .prefetch_related("places")
                .order_by("label", "id")
            )
            now = timezone.now()
            assignments_by_place_id = {}
            for assignment in (
                HousingAssignment.objects.filter(
                    place__room__site=selected_housing_site,
                    state__in=(
                        HousingAssignment.STATE_DRAFT,
                        HousingAssignment.STATE_PUBLISHED,
                    ),
                )
                .filter(
                    Q(check_out_at__isnull=True) | Q(check_out_at__gt=now)
                )
                .select_related("connection__candidate")
                .order_by("-state", "check_in_at", "id")
            ):
                assignments_by_place_id.setdefault(assignment.place_id, []).append(assignment)
            for room in selected_housing_rooms:
                room.places_for_layout = []
                for place in room.places.all():
                    assignments = assignments_by_place_id.get(place.id, [])
                    place.occupancy_state = (
                        "free"
                        if not assignments
                        else "occupied"
                    )
                    place.assignments_for_layout = []
                    for assignment in assignments:
                        assignment.layout_label = (
                            _display_name(assignment.connection.candidate)
                            if assignment.connection_id in allowed_worker_ids
                            else None
                        )
                        assignment.is_selected_worker_assignment = (
                            assignment.connection_id == connection.id
                        )
                        place.assignments_for_layout.append(assignment)
                    place.is_selected_worker_place = any(
                        item.is_selected_worker_assignment
                        for item in place.assignments_for_layout
                    )
                    room.places_for_layout.append(place)

    work_assignments = []
    work_projects = []
    quick_shift_projects = []
    has_schedule_templates = False
    scheduled_shifts = []
    calendar_days = []
    selected_calendar_date = None
    calendar_previous_month = None
    calendar_next_month = None
    if permissions["work"]:
        work_assignments = list(
            WorkerProjectAssignment.objects.filter(connection=connection)
            .select_related("project__worksite")
            .prefetch_related(
                "schedule_template_selections__template",
                Prefetch(
                    "scheduled_shifts",
                    queryset=ScheduledWorkShift.objects.filter(
                        state__in=(
                            ScheduledWorkShift.STATE_DRAFT,
                            ScheduledWorkShift.STATE_PUBLISHED,
                        )
                    ).order_by("work_date", "starts_at", "id"),
                    to_attr="display_scheduled_shifts",
                ),
            )
            .order_by("-starts_at", "-id")
        )
        work_projects = list(
            WorkProject.objects.filter(
                organization=organization,
                is_active=True,
                worksite__is_active=True,
            )
            .select_related("worksite")
            .prefetch_related(
                Prefetch(
                    "schedule_templates",
                    queryset=ProjectScheduleTemplate.objects.filter(is_active=True).order_by(
                        "name", "id"
                    ),
                )
            )
            .order_by("internal_name", "id")
        )
        active_project_ids = {
            item.project_id
            for item in work_assignments
            if item.state == WorkerProjectAssignment.STATE_PUBLISHED
        }
        quick_shift_projects = [
            item
            for item in work_projects
            if item.id in active_project_ids and item.schedule_templates.all()
        ]
        has_schedule_templates = bool(quick_shift_projects)
        try:
            year, month = [int(item) for item in (calendar_month or "").split("-", 1)]
            month_start = date(year, month, 1)
        except (TypeError, ValueError):
            today = timezone.localdate()
            month_start = date(today.year, today.month, 1)
        month_end = date(
            month_start.year,
            month_start.month,
            monthrange(month_start.year, month_start.month)[1],
        )
        scheduled_shifts = list(
            ScheduledWorkShift.objects.filter(
                connection=connection,
                state__in=(
                    ScheduledWorkShift.STATE_DRAFT,
                    ScheduledWorkShift.STATE_PUBLISHED,
                ),
                work_date__range=(month_start, month_end),
            ).select_related("work_assignment__project").order_by("work_date", "starts_at", "id")
        )
        for shift in scheduled_shifts:
            shift.is_preview = False
            shift.has_conflict = False

        # A project-assignment draft has not created ScheduledWorkShift rows yet.
        # Still show its selected project templates in the calendar so the manager
        # can see the planned shifts, including a collision, before publishing.
        draft_template_previews = []
        current_timezone = timezone.get_current_timezone()
        published_work_assignments = [
            item
            for item in work_assignments
            if item.state == WorkerProjectAssignment.STATE_PUBLISHED
        ]
        for assignment in work_assignments:
            if assignment.state != WorkerProjectAssignment.STATE_DRAFT:
                continue
            assignment_has_conflict = any(
                _datetime_periods_overlap(
                    starts_at=assignment.starts_at,
                    ends_at=assignment.ends_at,
                    other_starts_at=other.starts_at,
                    other_ends_at=other.ends_at,
                )
                for other in published_work_assignments
            )
            for selection in assignment.schedule_template_selections.all():
                template = selection.template
                for raw_date in template.calendar_dates:
                    work_date = parse_date(raw_date) if isinstance(raw_date, str) else None
                    if work_date is None or not (month_start <= work_date <= month_end):
                        continue
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
                    if starts_at < assignment.starts_at or (
                        assignment.ends_at is not None and ends_at > assignment.ends_at
                    ):
                        continue
                    draft_template_previews.append(
                        SimpleNamespace(
                            work_date=work_date,
                            starts_at=starts_at,
                            ends_at=ends_at,
                            break_minutes=template.break_minutes,
                            worker_label=template.worker_label or template.name,
                            state=WorkerProjectAssignment.STATE_DRAFT,
                            is_preview=True,
                            has_conflict=False,
                            has_assignment_conflict=assignment_has_conflict,
                        )
                    )

        calendar_shifts = sorted(
            [*scheduled_shifts, *draft_template_previews],
            key=lambda item: (item.work_date, item.starts_at, item.ends_at),
        )
        for index, shift in enumerate(calendar_shifts):
            if getattr(shift, "has_assignment_conflict", False):
                shift.has_conflict = True
            for other in calendar_shifts[index + 1 :]:
                if other.starts_at >= shift.ends_at:
                    break
                if shift.starts_at < other.ends_at and other.starts_at < shift.ends_at:
                    shift.has_conflict = True
                    other.has_conflict = True
        shifts_by_date = {}
        for shift in calendar_shifts:
            shifts_by_date.setdefault(shift.work_date, []).append(shift)
        first_weekday, days_in_month = monthrange(month_start.year, month_start.month)
        calendar_days = [None] * first_weekday
        for day_number in range(1, days_in_month + 1):
            current_date = date(month_start.year, month_start.month, day_number)
            calendar_days.append(
                {
                    "date": current_date,
                    "shifts": shifts_by_date.get(current_date, []),
                    "has_published": any(
                        item.state == ScheduledWorkShift.STATE_PUBLISHED
                        for item in shifts_by_date.get(current_date, [])
                    ),
                    "has_draft": any(
                        item.state == ScheduledWorkShift.STATE_DRAFT
                        for item in shifts_by_date.get(current_date, [])
                    ),
                    "has_conflict": any(
                        item.has_conflict
                        for item in shifts_by_date.get(current_date, [])
                    ),
                    "has_assignment_conflict": any(
                        getattr(item, "has_assignment_conflict", False)
                        for item in shifts_by_date.get(current_date, [])
                    ),
                    "is_today": current_date == timezone.localdate(),
                }
            )
        selected_calendar_date = month_start
        calendar_previous_month = (
            date(month_start.year - 1, 12, 1)
            if month_start.month == 1
            else date(month_start.year, month_start.month - 1, 1)
        )
        calendar_next_month = (
            date(month_start.year + 1, 1, 1)
            if month_start.month == 12
            else date(month_start.year, month_start.month + 1, 1)
        )

    driver_assignments = []
    driver_routes = []
    passenger_routes = []
    vehicles = []
    worksites = []
    if permissions["transport"]:
        driver_assignments = list(
            DriverVehicleAssignment.objects.filter(
                driver_connection=connection,
                state__in=(
                    DriverVehicleAssignment.STATE_DRAFT,
                    DriverVehicleAssignment.STATE_PUBLISHED,
                ),
            )
            .select_related("vehicle")
            .order_by("-starts_on", "-id")
        )
        driver_routes = list(
            TransportRoute.objects.filter(
                driver_vehicle_assignment__driver_connection=connection,
                state__in=(
                    TransportRoute.STATE_DRAFT,
                    TransportRoute.STATE_PUBLISHED,
                ),
            )
            .select_related(
                "driver_vehicle_assignment__vehicle",
                "worksite",
            )
            .prefetch_related(
                "stops",
                "passenger_assignments__connection__candidate",
            )
            .order_by("-starts_on", "-id")
        )
        passenger_routes = list(
            TransportPassengerAssignment.objects.filter(
                connection=connection,
                route__state__in=(
                    TransportRoute.STATE_DRAFT,
                    TransportRoute.STATE_PUBLISHED,
                ),
            )
            .select_related(
                "route__driver_vehicle_assignment__vehicle",
                "route__driver_vehicle_assignment__driver_connection__candidate",
                "pickup_stop",
                "dropoff_stop",
            )
            .order_by("-route__starts_on", "-id")
        )
        vehicles = list(
            Vehicle.objects.filter(organization=organization, is_active=True).order_by(
                "internal_name", "id"
            )
        )
        worksites = list(
            Worksite.objects.filter(organization=organization, is_active=True).order_by(
                "internal_name", "id"
            )
        )
        transport_workers = list(
            worker_connection_queryset_for(
                user=user,
                organization=organization,
                queryset=SupportConnection.objects.filter(
                    is_archived=False,
                    stage__in=(
                        SupportConnection.STAGE_COORDINATOR,
                        SupportConnection.STAGE_ACTIVE_WORKER,
                    ),
                ).select_related("candidate", "vacancy"),
            ).order_by("candidate__first_name", "candidate__last_name", "candidate__username")
        )
        transport_worker_ids = {item.id for item in transport_workers}
        now = timezone.now()
        current_housing_by_connection_id = {}
        for assignment in (
            HousingAssignment.objects.filter(
                organization=organization,
                connection_id__in=transport_worker_ids,
                state__in=(HousingAssignment.STATE_DRAFT, HousingAssignment.STATE_PUBLISHED),
                check_in_at__lte=now,
            )
            .filter(Q(check_out_at__isnull=True) | Q(check_out_at__gt=now))
            .select_related("place__room__site")
            .order_by("-check_in_at", "-id")
        ):
            current_housing_by_connection_id.setdefault(assignment.connection_id, assignment)
        current_work_by_connection_id = {}
        for assignment in (
            WorkerProjectAssignment.objects.filter(
                organization=organization,
                connection_id__in=transport_worker_ids,
                state__in=(
                    WorkerProjectAssignment.STATE_DRAFT,
                    WorkerProjectAssignment.STATE_PUBLISHED,
                ),
                starts_at__lte=now,
            )
            .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
            .select_related("project")
            .order_by("-starts_at", "-id")
        ):
            current_work_by_connection_id.setdefault(assignment.connection_id, assignment)
        for route in driver_routes:
            route.passengers_for_driver = list(route.passenger_assignments.all())
            route.occupied_seat_count = min(
                route.driver_vehicle_assignment.vehicle.seat_capacity,
                1 + len(route.passengers_for_driver),
            )
            route.free_seat_count = max(
                0,
                route.driver_vehicle_assignment.vehicle.seat_capacity
                - route.occupied_seat_count,
            )
            route.is_reservation_active = (
                route.state == TransportRoute.STATE_DRAFT
                and route.reservation_expires_at is not None
                and route.reservation_expires_at > timezone.now()
            )
            route.stops_for_builder = list(route.stops.all())
            route.pickup_stops = [
                item for item in route.stops_for_builder if item.kind == RouteStop.KIND_PICKUP
            ]
            route.dropoff_stops = [
                item for item in route.stops_for_builder if item.kind == RouteStop.KIND_DROPOFF
            ]
            route.next_stop_sequence = len(route.stops_for_builder) + 1
            route.next_boarding_order = len(route.passengers_for_driver) + 1
            assigned_passenger_ids = {
                item.connection_id for item in route.passengers_for_driver
            }
            active_passenger_rows = TransportPassengerAssignment.objects.filter(
                route__organization=organization,
                route__state__in=(
                    TransportRoute.STATE_DRAFT,
                    TransportRoute.STATE_PUBLISHED,
                ),
            ).exclude(route=route).filter(
                _date_period_overlaps(
                    starts_field="route__starts_on",
                    ends_field="route__ends_on",
                    starts_on=route.starts_on,
                    ends_on=route.ends_on,
                )
            )
            reserved_passenger_ids = set(
                active_passenger_rows.filter(
                    route__state=TransportRoute.STATE_DRAFT,
                    route__reservation_expires_at__gt=now,
                ).values_list("connection_id", flat=True)
            )
            published_passenger_ids = set(
                active_passenger_rows.filter(
                    route__state=TransportRoute.STATE_PUBLISHED,
                ).values_list("connection_id", flat=True)
            )
            active_driver_rows = DriverVehicleAssignment.objects.filter(
                organization=organization,
                state__in=(
                    DriverVehicleAssignment.STATE_DRAFT,
                    DriverVehicleAssignment.STATE_PUBLISHED,
                ),
            ).filter(
                _date_period_overlaps(
                    starts_field="starts_on",
                    ends_field="ends_on",
                    starts_on=route.starts_on,
                    ends_on=route.ends_on,
                )
            )
            reserved_driver_ids = set(
                active_driver_rows.filter(
                    state=DriverVehicleAssignment.STATE_DRAFT,
                ).values_list("driver_connection_id", flat=True)
            )
            published_driver_ids = set(
                active_driver_rows.filter(
                    state=DriverVehicleAssignment.STATE_PUBLISHED,
                ).values_list("driver_connection_id", flat=True)
            )
            occupied_elsewhere_ids = (
                reserved_passenger_ids
                | published_passenger_ids
                | reserved_driver_ids
                | published_driver_ids
            )
            route.passenger_candidates = []
            for item in transport_workers:
                if (
                    item.id == route.driver_vehicle_assignment.driver_connection_id
                    or item.id in assigned_passenger_ids
                ):
                    continue
                housing = current_housing_by_connection_id.get(item.id)
                work = current_work_by_connection_id.get(item.id)
                item.transport_housing_site_id = (
                    str(housing.place.room.site.public_id) if housing else ""
                )
                item.transport_housing_label = (
                    f"{housing.place.room.label} · {housing.place.label}" if housing else ""
                )
                item.transport_company_label = (
                    work.project.worker_visible_name if work else ""
                )
                if item.id in reserved_passenger_ids or item.id in reserved_driver_ids:
                    item.transport_filter = "draft"
                elif (
                    item.id in published_passenger_ids
                    or item.id in published_driver_ids
                    or item.stage == SupportConnection.STAGE_ACTIVE_WORKER
                ):
                    item.transport_filter = "working"
                else:
                    item.transport_filter = "free"
                item.transport_can_board = item.id not in occupied_elsewhere_ids
                item.transport_option_label = _display_name(item.candidate)
                if item.transport_housing_label:
                    item.transport_option_label += f" · {item.transport_housing_label}"
                if item.transport_company_label:
                    item.transport_option_label += f" · {item.transport_company_label}"
                route.passenger_candidates.append(item)
        for passenger_assignment in passenger_routes:
            passenger_assignment.is_selected_worker = True
        routes_by_driver_assignment_id = {
            route.driver_vehicle_assignment_id: route
            for route in driver_routes
            if route.state == TransportRoute.STATE_PUBLISHED
            or route.is_reservation_active
        }
        for assignment in driver_assignments:
            assignment.route_for_driver = routes_by_driver_assignment_id.get(assignment.id)

    document_packages = []
    if permissions["document_request"]:
        document_packages = list(
            DocumentRequestPackage.objects.filter(connection=connection)
            .select_related("account_reference", "created_by", "reviewed_by")
            .order_by("-updated_at", "-id")
        )

    return {
        "organization": organization,
        "connection": connection,
        "candidate_name": _display_name(connection.candidate),
        "permissions": permissions,
        "can_operate": connection.stage
        in {
            SupportConnection.STAGE_COORDINATOR,
            SupportConnection.STAGE_ACTIVE_WORKER,
        },
        "housing_assignments": housing_assignments,
        "housing_places": housing_places,
        "housing_sites": housing_sites,
        "selected_housing_site": selected_housing_site,
        "selected_housing_rooms": selected_housing_rooms,
        "work_assignments": work_assignments,
        "work_projects": work_projects,
        "quick_shift_projects": quick_shift_projects,
        "has_schedule_templates": has_schedule_templates,
        "scheduled_shifts": scheduled_shifts,
        "calendar_days": calendar_days,
        "selected_calendar_date": selected_calendar_date,
        "calendar_previous_month": calendar_previous_month,
        "calendar_next_month": calendar_next_month,
        "driver_assignments": driver_assignments,
        "driver_routes": driver_routes,
        "passenger_routes": passenger_routes,
        "vehicles": vehicles,
        "worksites": worksites,
        "document_packages": document_packages,
    }


def registry_snapshot(*, user, organization_public_id=None):
    """Return only employer registries the current staff member may manage.

    Registry data includes operational addresses and vehicle identifiers, so it
    is intentionally unavailable to worker-only staff accounts.  This is a
    server-side restriction, not merely a hidden web section.
    """

    memberships, membership = _select_membership(
        user=user,
        organization_public_id=organization_public_id,
    )
    organization = membership.organization
    permissions = _permissions_for(user=user, organization=organization)
    if not any((permissions["housing"], permissions["work"], permissions["transport"])):
        raise Http404("support_registry_not_found")

    housing_sites = []
    housing_rooms = []
    if permissions["housing"]:
        housing_sites = list(
            HousingSite.objects.filter(organization=organization).order_by(
                "internal_name", "id"
            )
        )
        housing_rooms = list(
            HousingRoom.objects.filter(site__organization=organization)
            .select_related("site")
            .prefetch_related("places")
            .order_by("site__internal_name", "label", "id")
        )

    worksites = []
    work_projects = []
    if permissions["work"]:
        worksites = list(
            Worksite.objects.filter(organization=organization).order_by(
                "internal_name", "id"
            )
        )
        work_projects = list(
            WorkProject.objects.filter(organization=organization)
            .select_related("worksite")
            .order_by("internal_name", "id")
        )

    vehicles = []
    if permissions["transport"]:
        vehicles = list(
            Vehicle.objects.filter(organization=organization).order_by(
                "internal_name", "id"
            )
        )

    return {
        "organization": organization,
        "membership": membership,
        "memberships": memberships,
        "permissions": permissions,
        "housing_sites": housing_sites,
        "housing_rooms": housing_rooms,
        "worksites": worksites,
        "work_projects": work_projects,
        "vehicles": vehicles,
    }


def transport_workspace_snapshot(*, user, organization_public_id=None):
    """Return the transport-only staff workspace for one organization.

    A transport employee needs worker names only to build a route.  The query
    deliberately excludes personal profiles, document data and housing street
    addresses; a stop can refer to an internal housing-site name instead.
    """

    memberships, membership = _select_membership(
        user=user,
        organization_public_id=organization_public_id,
    )
    organization = membership.organization
    permissions = _permissions_for(user=user, organization=organization)
    if not permissions["transport"]:
        raise Http404("support_transport_not_found")

    workers_queryset = worker_connection_queryset_for(
        user=user,
        organization=organization,
        queryset=SupportConnection.objects.filter(
            is_archived=False,
            stage__in=(
                SupportConnection.STAGE_COORDINATOR,
                SupportConnection.STAGE_ACTIVE_WORKER,
            ),
        ).select_related("candidate", "vacancy"),
    )
    workers = list(
        workers_queryset.order_by(
            "candidate__first_name", "candidate__last_name", "candidate__username"
        )
    )
    worker_ids = {item.id for item in workers}
    now = timezone.now()
    current_housing_by_connection_id = {}
    for assignment in (
        HousingAssignment.objects.filter(
            organization=organization,
            connection_id__in=worker_ids,
            state__in=(HousingAssignment.STATE_DRAFT, HousingAssignment.STATE_PUBLISHED),
            check_in_at__lte=now,
        )
        .filter(Q(check_out_at__isnull=True) | Q(check_out_at__gt=now))
        .select_related("place__room__site")
        .order_by("-check_in_at", "-id")
    ):
        current_housing_by_connection_id.setdefault(assignment.connection_id, assignment)
    current_work_by_connection_id = {}
    for assignment in (
        WorkerProjectAssignment.objects.filter(
            organization=organization,
            connection_id__in=worker_ids,
            state__in=(
                WorkerProjectAssignment.STATE_DRAFT,
                WorkerProjectAssignment.STATE_PUBLISHED,
            ),
            starts_at__lte=now,
        )
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now))
        .select_related("project")
        .order_by("-starts_at", "-id")
    ):
        current_work_by_connection_id.setdefault(assignment.connection_id, assignment)
    routes = list(
        TransportRoute.objects.filter(
            organization=organization,
            state__in=(TransportRoute.STATE_DRAFT, TransportRoute.STATE_PUBLISHED),
        )
        .select_related(
            "driver_vehicle_assignment__driver_connection__candidate",
            "driver_vehicle_assignment__vehicle",
            "worksite",
        )
        .prefetch_related(
            "stops__housing_site",
            "passenger_assignments__connection__candidate",
        )
        .order_by("state", "-starts_on", "-id")[:20]
    )
    routes = [
        route
        for route in routes
        if route.driver_vehicle_assignment.driver_connection_id in worker_ids
        and all(
            passenger.connection_id in worker_ids
            for passenger in route.passenger_assignments.all()
        )
    ]
    route_driver_assignment_ids = [
        item.driver_vehicle_assignment_id
        for item in routes
        if item.state == TransportRoute.STATE_PUBLISHED
        or (
            item.reservation_expires_at is not None
            and item.reservation_expires_at > now
        )
    ]
    driver_assignments = list(
        DriverVehicleAssignment.objects.filter(
            organization=organization,
            state__in=(
                DriverVehicleAssignment.STATE_DRAFT,
                DriverVehicleAssignment.STATE_PUBLISHED,
            ),
            driver_connection_id__in=worker_ids,
        )
        .exclude(id__in=route_driver_assignment_ids)
        .select_related("driver_connection__candidate", "vehicle")
        .order_by("starts_on", "id")
    )
    housing_sites = list(
        HousingSite.objects.filter(organization=organization, is_active=True).order_by(
            "internal_name", "id"
        )
    )
    worksites = list(
        Worksite.objects.filter(organization=organization, is_active=True).order_by(
            "internal_name", "id"
        )
    )
    for route in routes:
        route.is_reservation_active = (
            route.state == TransportRoute.STATE_DRAFT
            and route.reservation_expires_at is not None
            and route.reservation_expires_at > now
        )
        route.stops_for_builder = list(route.stops.all())
        route.pickup_stops = [
            item
            for item in route.stops_for_builder
            if item.kind == "pickup"
        ]
        route.dropoff_stops = [
            item
            for item in route.stops_for_builder
            if item.kind == "dropoff"
        ]
        route.passengers_for_builder = list(route.passenger_assignments.all())
        passenger_connection_ids = {
            item.connection_id for item in route.passengers_for_builder
        }
        active_passenger_rows = TransportPassengerAssignment.objects.filter(
            route__organization=organization,
            route__state__in=(
                TransportRoute.STATE_DRAFT,
                TransportRoute.STATE_PUBLISHED,
            ),
        ).exclude(route=route).filter(
            _date_period_overlaps(
                starts_field="route__starts_on",
                ends_field="route__ends_on",
                starts_on=route.starts_on,
                ends_on=route.ends_on,
            )
        )
        reserved_passenger_ids = set(
            active_passenger_rows.filter(
                route__state=TransportRoute.STATE_DRAFT,
                route__reservation_expires_at__gt=now,
            ).values_list("connection_id", flat=True)
        )
        published_passenger_ids = set(
            active_passenger_rows.filter(
                route__state=TransportRoute.STATE_PUBLISHED,
            ).values_list("connection_id", flat=True)
        )
        active_driver_rows = DriverVehicleAssignment.objects.filter(
            organization=organization,
            state__in=(
                DriverVehicleAssignment.STATE_DRAFT,
                DriverVehicleAssignment.STATE_PUBLISHED,
            ),
        ).filter(
            _date_period_overlaps(
                starts_field="starts_on",
                ends_field="ends_on",
                starts_on=route.starts_on,
                ends_on=route.ends_on,
            )
        )
        reserved_driver_ids = set(
            active_driver_rows.filter(
                state=DriverVehicleAssignment.STATE_DRAFT,
            ).values_list("driver_connection_id", flat=True)
        )
        published_driver_ids = set(
            active_driver_rows.filter(
                state=DriverVehicleAssignment.STATE_PUBLISHED,
            ).values_list("driver_connection_id", flat=True)
        )
        occupied_elsewhere_ids = (
            reserved_passenger_ids
            | published_passenger_ids
            | reserved_driver_ids
            | published_driver_ids
        )
        route.passenger_candidates = []
        for item in workers:
            if (
                item.id == route.driver_vehicle_assignment.driver_connection_id
                or item.id in passenger_connection_ids
            ):
                continue
            housing = current_housing_by_connection_id.get(item.id)
            work = current_work_by_connection_id.get(item.id)
            item.transport_housing_site_id = (
                str(housing.place.room.site.public_id) if housing else ""
            )
            item.transport_housing_label = (
                f"{housing.place.room.label} · {housing.place.label}" if housing else ""
            )
            item.transport_company_label = (
                work.project.worker_visible_name if work and work.project else ""
            )
            if item.id in reserved_passenger_ids or item.id in reserved_driver_ids:
                item.transport_filter = "draft"
            elif (
                item.id in published_passenger_ids
                or item.id in published_driver_ids
                or item.stage == SupportConnection.STAGE_ACTIVE_WORKER
            ):
                item.transport_filter = "working"
            else:
                item.transport_filter = "free"
            item.transport_can_board = item.id not in occupied_elsewhere_ids
            item.transport_option_label = _display_name(item.candidate)
            if item.transport_housing_label:
                item.transport_option_label += f" · {item.transport_housing_label}"
            if item.transport_company_label:
                item.transport_option_label += f" · {item.transport_company_label}"
            route.passenger_candidates.append(item)
        # The API keeps its original name while the employer template uses the
        # clearer `passenger_candidates` name for the same filtered list.
        route.passenger_choices = route.passenger_candidates
        route.available_seat_count = max(
            0,
            route.driver_vehicle_assignment.vehicle.seat_capacity
            - 1
            - len(route.passengers_for_builder),
        )
        route.next_boarding_order = len(route.passengers_for_builder) + 1

    return {
        "organization": organization,
        "membership": membership,
        "memberships": memberships,
        "permissions": permissions,
        "routes": routes,
        "driver_assignments": driver_assignments,
        "worker_choices": workers,
        "housing_sites": housing_sites,
        "worksites": worksites,
        "vehicles": list(
            Vehicle.objects.filter(organization=organization, is_active=True).order_by(
                "internal_name", "id"
            )
        ),
    }


def fleet_snapshot(*, user, organization_public_id=None, vehicle_public_id=None):
    """Return a transport-only fleet view without exposing worker documents."""

    memberships, membership = _select_membership(
        user=user,
        organization_public_id=organization_public_id,
    )
    organization = membership.organization
    permissions = _permissions_for(user=user, organization=organization)
    if not permissions["transport"]:
        raise Http404("support_fleet_not_found")

    workers = list(
        worker_connection_queryset_for(
            user=user,
            organization=organization,
            queryset=SupportConnection.objects.filter(
                is_archived=False,
                stage__in=(
                    SupportConnection.STAGE_COORDINATOR,
                    SupportConnection.STAGE_ACTIVE_WORKER,
                ),
            ).select_related("candidate"),
        ).order_by("candidate__first_name", "candidate__last_name", "candidate__username")
    )
    for worker in workers:
        worker.has_verified_driving_license = worker.has_driving_license
        worker.display_name = _display_name(worker.candidate)
    eligible_drivers = [item for item in workers if item.has_verified_driving_license]

    today = timezone.localdate()
    vehicles = list(
        Vehicle.objects.filter(organization=organization)
        .prefetch_related(
            "driver_assignments__driver_connection__candidate",
            "driver_assignments__routes__passenger_assignments",
        )
        .order_by("internal_name", "id")
    )
    for vehicle in vehicles:
        assignments = list(vehicle.driver_assignments.all())
        assignments.sort(key=lambda item: (item.starts_on, item.id), reverse=True)
        vehicle.assignments_for_fleet = assignments
        active = [
            item for item in assignments
            if item.state in (DriverVehicleAssignment.STATE_DRAFT, DriverVehicleAssignment.STATE_PUBLISHED)
            and item.starts_on <= today
            and (item.ends_on is None or item.ends_on > today)
        ]
        active.sort(key=lambda item: (item.state == DriverVehicleAssignment.STATE_PUBLISHED, item.starts_on), reverse=True)
        vehicle.current_assignment = active[0] if active else None
        vehicle.current_route = None
        if vehicle.current_assignment:
            routes = [
                route for route in vehicle.current_assignment.routes.all()
                if route.state in (TransportRoute.STATE_DRAFT, TransportRoute.STATE_PUBLISHED)
            ]
            routes.sort(key=lambda item: (item.state == TransportRoute.STATE_PUBLISHED, item.starts_on), reverse=True)
            vehicle.current_route = routes[0] if routes else None
        passenger_count = (
            vehicle.current_route.passenger_assignments.count()
            if vehicle.current_route is not None
            else 0
        )
        vehicle.free_seat_count = max(0, vehicle.seat_capacity - (1 if vehicle.current_assignment else 0) - passenger_count)
        vehicle.occupancy_label = f"{vehicle.free_seat_count}/{vehicle.seat_capacity}"

    selected_vehicle = next(
        (item for item in vehicles if str(item.public_id) == str(vehicle_public_id)),
        vehicles[0] if vehicles else None,
    )
    if vehicle_public_id and selected_vehicle is None:
        raise Http404("support_vehicle_not_found")
    return {
        "organization": organization,
        "membership": membership,
        "memberships": memberships,
        "permissions": permissions,
        "vehicles": vehicles,
        "selected_vehicle": selected_vehicle,
        "eligible_drivers": eligible_drivers,
    }


def team_management_snapshot(*, user, organization_public_id=None, membership_public_id=None):
    """Return a server-filtered staff and worker-scope management snapshot."""

    memberships, viewer_membership = _select_membership(
        user=user,
        organization_public_id=organization_public_id,
    )
    organization = viewer_membership.organization
    permissions = _permissions_for(user=user, organization=organization)
    if not permissions["organization_manage"]:
        raise Http404("support_team_management_not_found")

    staff_memberships = list(
        OrganizationMembership.objects.filter(
            organization=organization,
            state=OrganizationMembership.STATE_ACTIVE,
        )
        .select_related("user")
        .prefetch_related("worker_access_scopes")
        .order_by("is_owner", "user__first_name", "user__last_name", "user__username")
    )
    selected_membership = None
    if membership_public_id:
        selected_membership = next(
            (
                item
                for item in staff_memberships
                if str(item.public_id) == str(membership_public_id)
            ),
            None,
        )
        if selected_membership is None:
            raise Http404("support_team_management_not_found")
    else:
        selected_membership = next(
            (item for item in staff_memberships if not item.is_owner),
            staff_memberships[0] if staff_memberships else None,
        )

    active_workers = list(
        SupportConnection.objects.filter(
            organization=organization,
            is_archived=False,
            stage__in=(
                SupportConnection.STAGE_COORDINATOR,
                SupportConnection.STAGE_ACTIVE_WORKER,
            ),
        )
        .select_related("candidate", "vacancy")
        .order_by("candidate__first_name", "candidate__last_name", "candidate__username")
    )
    selected_scopes = []
    selected_scope_connection_ids = set()
    if selected_membership is not None:
        selected_scopes = list(
            selected_membership.worker_access_scopes.filter(is_active=True)
            .select_related("connection__candidate", "connection__vacancy")
            .order_by(
                "connection__candidate__first_name",
                "connection__candidate__last_name",
                "connection__candidate__username",
            )
        )
        selected_scope_connection_ids = {
            item.connection_id for item in selected_scopes
        }

    for item in staff_memberships:
        item.display_name = _display_name(item.user)
        item.active_scope_count = sum(
            1 for scope in item.worker_access_scopes.all() if scope.is_active
        )
    for item in active_workers:
        item.display_name = _display_name(item.candidate)
        item.scope_label = f"{item.display_name} · {item.vacancy.internal_title}"
    for item in selected_scopes:
        item.connection.display_name = _display_name(item.connection.candidate)
        item.connection.scope_label = (
            f"{item.connection.display_name} · {item.connection.vacancy.internal_title}"
        )

    can_invite_staff = has_permission(
        user=user,
        organization=organization,
        permission_code=MEMBER_INVITE,
    )
    can_delegate_permissions = (
        viewer_membership.is_owner
        or has_permission(
            user=user,
            organization=organization,
            permission_code=MEMBER_DELEGATE_PERMISSIONS,
        )
    )
    invitation_permission_groups = []
    if can_invite_staff:
        for group_id, label_key, group_codes in TEAM_PERMISSION_GROUPS:
            if viewer_membership.is_owner or (
                can_delegate_permissions
                and all(
                    may_delegate_permission(
                        user=user,
                        organization=organization,
                        permission_code=code,
                    )
                    for code in group_codes
                )
            ):
                invitation_permission_groups.append(
                    {
                        "id": group_id,
                        "label_key": label_key,
                    }
                )

    pending_invitations = list(
        MembershipInvitation.objects.filter(
            organization=organization,
            state=MembershipInvitation.STATUS_PENDING,
        )
        .prefetch_related("permission_grants")
        .order_by("-created_at", "-id")
    )
    group_codes_by_id = {
        group_id: set(group_codes)
        for group_id, _label_key, group_codes in TEAM_PERMISSION_GROUPS
    }
    for invitation in pending_invitations:
        invitation_codes = {
            grant.permission_code for grant in invitation.permission_grants.all()
        }
        invitation.permission_group_ids = [
            group_id
            for group_id, group_codes in group_codes_by_id.items()
            if group_codes.issubset(invitation_codes)
        ]

    return {
        "organization": organization,
        "membership": viewer_membership,
        "memberships": memberships,
        "permissions": permissions,
        "staff_memberships": staff_memberships,
        "selected_membership": selected_membership,
        "selected_has_full_access": (
            selected_membership is not None
            and has_unrestricted_worker_access(
                user=selected_membership.user,
                organization=organization,
            )
        ),
        "selected_scopes": selected_scopes,
        "available_workers": [
            item for item in active_workers if item.id not in selected_scope_connection_ids
        ],
        "can_invite_staff": can_invite_staff,
        "invitation_permission_groups": invitation_permission_groups,
        "pending_invitations": pending_invitations,
    }


def _time_query_date(value, *, fallback, key):
    raw_value = (value or "").strip()
    if not raw_value:
        return fallback
    parsed = parse_date(raw_value)
    if parsed is None:
        raise Http404(f"support_time_invalid_{key}")
    return parsed


def timekeeping_snapshot(*, user, organization_public_id=None, date_from=None, date_to=None):
    """Return one scope-filtered ledger and schedule workspace for staff."""

    memberships, membership = _select_membership(
        user=user,
        organization_public_id=organization_public_id,
    )
    organization = membership.organization
    permissions = _permissions_for(user=user, organization=organization)
    if not (permissions["time_view"] or permissions["schedule"]):
        raise Http404("support_timekeeping_not_found")

    today = timezone.localdate()
    selected_date_from = _time_query_date(
        date_from,
        fallback=today - timedelta(days=6),
        key="date_from",
    )
    selected_date_to = _time_query_date(
        date_to,
        fallback=today,
        key="date_to",
    )
    if (
        selected_date_to < selected_date_from
        or (selected_date_to - selected_date_from).days > 62
    ):
        raise Http404("support_timekeeping_invalid_range")

    allowed_connections = worker_connection_queryset_for(
        user=user,
        organization=organization,
        queryset=SupportConnection.objects.filter(
            is_archived=False,
            stage__in=(
                SupportConnection.STAGE_COORDINATOR,
                SupportConnection.STAGE_ACTIVE_WORKER,
            ),
        ),
    )
    workers = list(
        allowed_connections.select_related("candidate", "vacancy").order_by(
            "candidate__first_name",
            "candidate__last_name",
            "candidate__username",
        )
    )
    for worker in workers:
        worker.display_name = _display_name(worker.candidate)
        worker.time_label = f"{worker.display_name} · {worker.vacancy.internal_title}"

    entries = []
    totals = {"worked_minutes": 0, "entry_count": 0}
    if permissions["time_view"]:
        entries = list(
            WorkTimeEntry.objects.filter(
                organization=organization,
                connection__in=allowed_connections,
                work_date__range=(selected_date_from, selected_date_to),
            )
            .select_related(
                "connection__candidate",
                "connection__vacancy",
                "scheduled_shift",
                "confirmed_by",
            )
            .order_by(
                "-work_date",
                "connection__candidate__last_name",
                "connection__candidate__first_name",
                "id",
            )
        )
        totals = {
            "worked_minutes": sum(item.worked_minutes for item in entries),
            "entry_count": len(entries),
        }
        for entry in entries:
            entry.worker_display_name = _display_name(entry.connection.candidate)
            entry.duration_label = (
                f"{entry.worked_minutes // 60}:{entry.worked_minutes % 60:02d}"
            )
            entry.decimal_hours_label = f"{entry.decimal_hours:.2f}"

    scheduled_shifts = []
    if permissions["schedule"]:
        scheduled_shifts = list(
            ScheduledWorkShift.objects.filter(
                organization=organization,
                connection__in=allowed_connections,
                work_date__range=(selected_date_from, selected_date_to),
            )
            .select_related("connection__candidate", "connection__vacancy")
            .order_by("work_date", "starts_at", "id")
        )
        for shift in scheduled_shifts:
            shift.worker_display_name = _display_name(shift.connection.candidate)

    totals["duration_label"] = (
        f"{totals['worked_minutes'] // 60}:{totals['worked_minutes'] % 60:02d}"
    )
    totals["decimal_hours_label"] = f"{totals['worked_minutes'] / 60:.2f}"
    return {
        "organization": organization,
        "membership": membership,
        "memberships": memberships,
        "permissions": permissions,
        "date_from": selected_date_from,
        "date_to": selected_date_to,
        "workers": workers,
        "entries": entries,
        "scheduled_shifts": scheduled_shifts,
        "totals": totals,
    }
