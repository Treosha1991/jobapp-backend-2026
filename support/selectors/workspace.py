"""Data for the first employer Support workspace screen.

This module deliberately returns only values already allowed by the current
membership.  Views and templates must not construct broader querysets and then
hide columns, because that would still expose data through a direct URL.
"""

from datetime import timedelta

from django.http import Http404
from django.utils import timezone
from django.utils.dateparse import parse_date

from support.models import (
    DocumentRequestPackage,
    DriverVehicleAssignment,
    HousingAssignment,
    HousingPlace,
    HousingRoom,
    HousingSite,
    OrganizationMembership,
    ScheduledWorkShift,
    SupportApplication,
    SupportConnection,
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
    DOCUMENT_REQUEST,
    HOUSING_MANAGE,
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
    worker_connection_queryset_for,
)


def _display_name(user):
    return user.get_full_name().strip() or user.username


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


def worker_card_snapshot(*, user, connection_public_id):
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

    work_assignments = []
    work_projects = []
    if permissions["work"]:
        work_assignments = list(
            WorkerProjectAssignment.objects.filter(connection=connection)
            .select_related("project__worksite")
            .order_by("-starts_at", "-id")
        )
        work_projects = list(
            WorkProject.objects.filter(
                organization=organization,
                is_active=True,
                worksite__is_active=True,
            )
            .select_related("worksite")
            .order_by("internal_name", "id")
        )

    driver_assignments = []
    passenger_routes = []
    vehicles = []
    if permissions["transport"]:
        driver_assignments = list(
            DriverVehicleAssignment.objects.filter(driver_connection=connection)
            .select_related("vehicle")
            .order_by("-starts_on", "-id")
        )
        passenger_routes = list(
            TransportPassengerAssignment.objects.filter(connection=connection)
            .select_related("route__driver_vehicle_assignment__vehicle")
            .order_by("-route__starts_on", "-id")
        )
        vehicles = list(
            Vehicle.objects.filter(organization=organization, is_active=True).order_by(
                "internal_name", "id"
            )
        )

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
        "work_assignments": work_assignments,
        "work_projects": work_projects,
        "driver_assignments": driver_assignments,
        "passenger_routes": passenger_routes,
        "vehicles": vehicles,
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
    now = timezone.now()
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
            state=DriverVehicleAssignment.STATE_DRAFT,
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
        route.passenger_choices = [
            item
            for item in workers
            if item.id != route.driver_vehicle_assignment.driver_connection_id
            and item.id not in passenger_connection_ids
        ]
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
