"""Worker-owned project-first workspace snapshot.

This read model deliberately starts from ``ProjectCrewShiftMember`` and the
project-first roster tables.  It never falls back to legacy route or shift
template data, and it exposes other crew members only as a display name and a
role for a day on which the requesting worker belongs to (or is absent from)
that crew.
"""

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Prefetch, Q, Sum
from django.utils import timezone

from jobs.avatar_utils import avatar_public_url

from support.models import (
    Announcement,
    AnnouncementAcknowledgement,
    DocumentRequestPackage,
    HousingAssignment,
    ProjectCrew,
    ProjectCrewMemberAbsence,
    ProjectCrewPassenger,
    ProjectCrewResourceAssignment,
    ProjectCrewShift,
    ProjectCrewShiftMember,
    SupportConversation,
    SupportConversationMember,
    TaskAssignment,
    WorkerRequest,
    WorkerScheduleDayOff,
    WorkerTask,
    WorkTimeEntry,
)
from support.permission_codes import CHAT_MANAGE
from support.permissions import has_permission
from support.services.conversations import find_private_manager_conversation
from support.services.entitlements import (
    support_access_snapshot_for,
    users_with_active_support_access,
)
from support.services.timekeeping import worker_time_entry_access


def _display_name(user):
    full_name = user.get_full_name().strip()
    return full_name or user.username or user.email


def _avatar_url(user):
    profile = getattr(user, "profile", None)
    return avatar_public_url(getattr(profile, "avatar_key", ""))


def _identity_payload(user):
    return {
        "first_name": (user.first_name or "").strip(),
        "last_name": (user.last_name or "").strip(),
        "display_name": _display_name(user),
        "avatar_url": _avatar_url(user),
    }


def _duration_label(minutes):
    return f"{minutes // 60}:{minutes % 60:02d}"


def _decimal_hours(minutes):
    value = (Decimal(minutes) / Decimal("60")).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return str(value)


def _address(worksite):
    return ", ".join(
        filter(
            None,
            [
                worksite.street,
                worksite.building,
                worksite.postal_code,
                worksite.city,
            ],
        )
    )


def _project_payload(project):
    return {
        "id": str(project.public_id),
        "name": project.worker_visible_name or project.internal_name,
        "address": _address(project.worksite),
        "instructions": project.instructions or project.worksite.instructions,
    }


def _crew_payload(crew):
    return {
        "id": str(crew.public_id),
        "name": crew.internal_name,
    }


def _vehicle_payload(vehicle):
    if vehicle is None:
        return None
    return {
        "id": str(vehicle.public_id),
        "name": vehicle.internal_name,
        "registration_identifier": vehicle.registration_identifier,
        "seat_capacity": vehicle.seat_capacity,
    }


def _member_queryset(*, organization):
    return (
        ProjectCrewShiftMember.objects.filter(
            connection__organization=organization,
        )
        .select_related(
            "connection__candidate",
            "connection__candidate__profile",
            "vehicle",
        )
        .order_by("role", "connection__candidate__first_name", "id")
    )


def _worker_shift_queryset(connection):
    return (
        ProjectCrewShift.objects.filter(
            state=ProjectCrewShift.STATE_PUBLISHED,
            members__connection=connection,
            crew__organization=connection.organization,
        )
        .select_related("crew__project__worksite")
        .prefetch_related(
            Prefetch(
                "members",
                queryset=_member_queryset(organization=connection.organization),
            )
        )
        .distinct()
        .order_by("work_date", "starts_at", "id")
    )


def _active_period(item, work_date):
    return item.starts_on <= work_date and (
        item.ends_on is None or item.ends_on >= work_date
    )


def _roster_role(*, connection, crew, work_date, resources, passengers):
    if any(
        item.crew_id == crew.id
        and item.driver_connection_id == connection.id
        and _active_period(item, work_date)
        for item in resources
    ):
        return ProjectCrewShiftMember.ROLE_DRIVER
    if any(
        item.crew_id == crew.id
        and item.connection_id == connection.id
        and _active_period(item, work_date)
        for item in passengers
    ):
        return ProjectCrewShiftMember.ROLE_PASSENGER
    return None


def _shift_payload(
    shift,
    *,
    connection,
    roster_resources=(),
    roster_passengers=(),
):
    members = list(shift.members.all())
    own_member = next(
        (item for item in members if item.connection_id == connection.id),
        None,
    )
    driver_member = next(
        (
            item
            for item in members
            if item.role == ProjectCrewShiftMember.ROLE_DRIVER
        ),
        None,
    )
    own_role = (
        own_member.role
        if own_member is not None
        else _roster_role(
            connection=connection,
            crew=shift.crew,
            work_date=shift.work_date,
            resources=roster_resources,
            passengers=roster_passengers,
        )
    )
    return {
        "id": str(shift.public_id),
        "date": shift.work_date.isoformat(),
        "starts_at": timezone.localtime(shift.starts_at).isoformat(),
        "ends_at": timezone.localtime(shift.ends_at).isoformat(),
        "break_minutes": shift.break_minutes,
        "project": _project_payload(shift.crew.project),
        "crew": _crew_payload(shift.crew),
        "own_role": own_role,
        "effective_vehicle": _vehicle_payload(
            driver_member.vehicle if driver_member is not None else None
        ),
        "effective_driver": (
            _identity_payload(driver_member.connection.candidate)
            if driver_member is not None
            else None
        ),
        "crew_members": [
            {
                **_identity_payload(item.connection.candidate),
                "role": item.role,
            }
            for item in members
        ],
    }


def _week_shift_payload(shift, *, connection, chat_access_by_user):
    payload = _shift_payload(shift, connection=connection)
    payload["crew_members"] = [
        {
            "connection_id": str(item.connection.public_id),
            **_identity_payload(item.connection.candidate),
            "role": item.role,
            "is_self": item.connection_id == connection.id,
            "can_open_chat": bool(
                item.connection_id != connection.id
                and not item.connection.is_archived
                and item.connection.stage != item.connection.STAGE_CLOSED
                and chat_access_by_user.get(item.connection.candidate_id, False)
            ),
        }
        for item in shift.members.all()
    ]
    return payload


def _current_assignment_from_shift(shift, *, connection, basis):
    payload = _shift_payload(shift, connection=connection)
    driver_member = next(
        (
            item
            for item in shift.members.all()
            if item.role == ProjectCrewShiftMember.ROLE_DRIVER
        ),
        None,
    )
    return {
        "basis": basis,
        "work_date": payload["date"],
        "project": payload["project"],
        "crew": payload["crew"],
        "role": payload["own_role"],
        "vehicle": payload["effective_vehicle"],
        "driver": (
            _identity_payload(driver_member.connection.candidate)
            if driver_member is not None
            else None
        ),
    }


def _permanent_assignment(connection, *, today):
    resource = (
        ProjectCrewResourceAssignment.objects.filter(
            driver_connection=connection,
            starts_on__lte=today,
            crew__state=ProjectCrew.STATE_ACTIVE,
            crew__organization=connection.organization,
            crew__project__is_active=True,
            crew__project__starts_on__lte=today,
        )
        .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=today))
        .filter(
            Q(crew__project__ends_on__isnull=True)
            | Q(crew__project__ends_on__gte=today)
        )
        .select_related(
            "crew__project__worksite",
            "driver_connection__candidate",
            "driver_connection__candidate__profile",
            "vehicle",
        )
        .order_by("-starts_on", "-id")
        .first()
    )
    passenger = (
        ProjectCrewPassenger.objects.filter(
            connection=connection,
            starts_on__lte=today,
            crew__state=ProjectCrew.STATE_ACTIVE,
            crew__organization=connection.organization,
            crew__project__is_active=True,
            crew__project__starts_on__lte=today,
        )
        .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=today))
        .filter(
            Q(crew__project__ends_on__isnull=True)
            | Q(crew__project__ends_on__gte=today)
        )
        .select_related("crew__project__worksite")
        .order_by("-starts_on", "-id")
        .first()
    )
    choices = [item for item in (resource, passenger) if item is not None]
    if not choices:
        return None
    roster_item = max(
        choices,
        key=lambda item: (
            item.starts_on,
            isinstance(item, ProjectCrewResourceAssignment),
            item.id,
        ),
    )
    role = (
        ProjectCrewShiftMember.ROLE_DRIVER
        if isinstance(roster_item, ProjectCrewResourceAssignment)
        else ProjectCrewShiftMember.ROLE_PASSENGER
    )
    active_resource = (
        roster_item
        if role == ProjectCrewShiftMember.ROLE_DRIVER
        else (
            ProjectCrewResourceAssignment.objects.filter(
                crew=roster_item.crew,
                starts_on__lte=today,
            )
            .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=today))
            .select_related(
                "driver_connection__candidate",
                "driver_connection__candidate__profile",
                "vehicle",
            )
            .order_by("-starts_on", "-id")
            .first()
        )
    )
    return {
        "basis": "permanent_roster",
        "work_date": None,
        "project": _project_payload(roster_item.crew.project),
        "crew": _crew_payload(roster_item.crew),
        "role": role,
        "vehicle": _vehicle_payload(
            active_resource.vehicle if active_resource is not None else None
        ),
        "driver": (
            _identity_payload(active_resource.driver_connection.candidate)
            if active_resource is not None
            else None
        ),
    }


def _housing_payload(assignment):
    if assignment is None:
        return None
    site = assignment.place.room.site
    return {
        "id": str(assignment.public_id),
        "site_id": str(site.public_id),
        "site_name": site.internal_name,
        "address": ", ".join(
            filter(
                None,
                [site.street, site.building, site.postal_code, site.city],
            )
        ),
        "room_label": assignment.place.room.label,
        "place_label": assignment.place.label,
        "check_in": assignment.check_in_at,
        "check_out": assignment.check_out_at,
        "rules": site.rules_text,
        "contact": {
            "name": site.contact_name,
            "phone": site.contact_phone,
        },
    }


def _time_entry_payload(entry):
    if entry is None:
        return None
    return {
        "id": str(entry.public_id),
        "status": entry.status,
        "worked_minutes": entry.worked_minutes,
        "worked_duration": _duration_label(entry.worked_minutes),
        "decimal_hours": str(entry.decimal_hours),
    }


def _time_total(*, connection, date_from, date_to):
    worked_minutes = (
        WorkTimeEntry.objects.filter(
            connection=connection,
            organization=connection.organization,
            work_date__range=(date_from, date_to),
        ).aggregate(total=Sum("worked_minutes"))["total"]
        or 0
    )
    planned_minutes = 0
    planned_shifts = (
        ProjectCrewShift.objects.filter(
            state=ProjectCrewShift.STATE_PUBLISHED,
            members__connection=connection,
            crew__organization=connection.organization,
            work_date__range=(date_from, date_to),
        )
        .distinct()
        .values_list("starts_at", "ends_at", "break_minutes")
    )
    for starts_at, ends_at, break_minutes in planned_shifts:
        duration = int((ends_at - starts_at).total_seconds() // 60)
        planned_minutes += max(0, duration - break_minutes)
    return {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "planned_minutes": planned_minutes,
        "planned_duration": _duration_label(planned_minutes),
        "planned_decimal_hours": _decimal_hours(planned_minutes),
        "worked_minutes": worked_minutes,
        "worked_duration": _duration_label(worked_minutes),
        "decimal_hours": _decimal_hours(worked_minutes),
    }


def _manager_conversation(connection):
    if not connection.assigned_manager_id or not connection.assigned_manager.user_id:
        return None
    return find_private_manager_conversation(
        organization=connection.organization,
        worker=connection.candidate,
        manager=connection.assigned_manager.user,
    )


def _unread_counts(connection):
    memberships = list(
        SupportConversationMember.objects.filter(
            user=connection.candidate,
            left_at__isnull=True,
            conversation__organization=connection.organization,
            conversation__state=SupportConversation.STATE_ACTIVE,
        ).select_related("conversation")
    )
    unread_conversations = 0
    unread_messages = 0
    for membership in memberships:
        messages = membership.conversation.messages.exclude(
            sender=connection.candidate
        )
        if membership.last_read_at is not None:
            messages = messages.filter(created_at__gt=membership.last_read_at)
        count = messages.count()
        if count:
            unread_conversations += 1
            unread_messages += count
    return unread_conversations, unread_messages


def _attention_payload(connection, *, now):
    document_requests = DocumentRequestPackage.objects.filter(
        connection=connection,
        organization=connection.organization,
        status__in=(
            DocumentRequestPackage.STATUS_REQUESTED,
            DocumentRequestPackage.STATUS_NEEDS_CORRECTION,
        ),
    ).count()
    worker_requests = WorkerRequest.objects.filter(
        connection=connection,
        organization=connection.organization,
        status=WorkerRequest.STATUS_NEEDS_CLARIFICATION,
    ).count()
    tasks = TaskAssignment.objects.filter(
        connection=connection,
        task__organization=connection.organization,
        task__state=WorkerTask.STATE_PUBLISHED,
        status__in=(TaskAssignment.STATUS_NEW, TaskAssignment.STATUS_RETURNED),
    ).count()
    announcements = (
        AnnouncementAcknowledgement.objects.filter(
            connection=connection,
            announcement__organization=connection.organization,
            announcement__state=Announcement.STATE_PUBLISHED,
            announcement__requires_acknowledgement=True,
            acknowledged_at__isnull=True,
        )
        .filter(
            Q(announcement__expires_at__isnull=True)
            | Q(announcement__expires_at__gt=now)
        )
        .count()
    )
    time_entries = WorkTimeEntry.objects.filter(
        connection=connection,
        organization=connection.organization,
        status__in=(
            WorkTimeEntry.STATUS_CORRECTION_REQUESTED,
            WorkTimeEntry.STATUS_MANAGER_ADJUSTED,
        ),
    ).count()
    unread_conversations, unread_messages = _unread_counts(connection)
    values = {
        "document_requests": document_requests,
        "worker_requests": worker_requests,
        "tasks": tasks,
        "announcements": announcements,
        "time_entries": time_entries,
        "unread_conversations": unread_conversations,
        "unread_messages": unread_messages,
    }
    values["total"] = sum(
        values[key]
        for key in (
            "document_requests",
            "worker_requests",
            "tasks",
            "announcements",
            "time_entries",
            "unread_conversations",
        )
    )
    return values


def worker_workspace_week_snapshot(*, connection, selected_date):
    """Return exactly one ISO week owned by the authenticated worker.

    Crew membership is taken only from each published project-first shift on
    which this exact connection is an actual member.  A client therefore
    cannot reuse a connection or shift UUID to discover another crew/day.
    """

    now = timezone.now()
    today = timezone.localdate()
    week_start = selected_date - timedelta(days=selected_date.weekday())
    week_end = week_start + timedelta(days=6)
    iso_year, iso_week, _ = selected_date.isocalendar()

    week_shifts = list(
        _worker_shift_queryset(connection).filter(
            work_date__range=(week_start, week_end),
        )
    )
    shifts_by_date = {}
    candidate_users = {}
    for shift in week_shifts:
        shifts_by_date.setdefault(shift.work_date, []).append(shift)
        for item in shift.members.all():
            candidate_users.setdefault(
                item.connection.candidate_id,
                item.connection.candidate,
            )
    peer_user_ids = {
        user_id
        for user_id in candidate_users
        if user_id != connection.candidate_id
    }
    active_peer_user_ids = users_with_active_support_access(
        peer_user_ids,
        at_time=now,
    )
    chat_access_by_user = {
        user_id: user_id in active_peer_user_ids for user_id in peer_user_ids
    }

    day_off_dates = set(
        WorkerScheduleDayOff.objects.filter(
            connection=connection,
            organization=connection.organization,
            work_date__range=(week_start, week_end),
        ).values_list("work_date", flat=True)
    )
    absence_dates = set(
        ProjectCrewMemberAbsence.objects.filter(
            connection=connection,
            organization=connection.organization,
            crew__organization=connection.organization,
            work_date__range=(week_start, week_end),
        ).values_list("work_date", flat=True)
    )
    time_entries = {
        item.work_date: item
        for item in WorkTimeEntry.objects.filter(
            connection=connection,
            organization=connection.organization,
            work_date__range=(week_start, week_end),
        ).order_by("work_date", "id")
    }

    days = []
    for offset in range(7):
        work_date = week_start + timedelta(days=offset)
        shifts = shifts_by_date.get(work_date, [])
        access_shift = max(
            shifts,
            key=lambda item: (item.ends_at, item.id),
            default=None,
        )
        entry = time_entries.get(work_date)
        days.append(
            {
                "date": work_date.isoformat(),
                "is_today": work_date == today,
                "is_selected": work_date == selected_date,
                "day_off": work_date in day_off_dates,
                "absence": work_date in absence_dates,
                "shift": (
                    _week_shift_payload(
                        shifts[0],
                        connection=connection,
                        chat_access_by_user=chat_access_by_user,
                    )
                    if shifts
                    else None
                ),
                "shifts": [
                    _week_shift_payload(
                        shift,
                        connection=connection,
                        chat_access_by_user=chat_access_by_user,
                    )
                    for shift in shifts
                ],
                "time_entry": _time_entry_payload(entry),
                "time_entry_access": worker_time_entry_access(
                    scheduled_shift=access_shift,
                    entry=entry,
                    now=now,
                ),
            }
        )

    return {
        "generated_at": now,
        "today": today.isoformat(),
        "selected_date": selected_date.isoformat(),
        "week": {
            "iso_year": iso_year,
            "number": iso_week,
            "starts_on": week_start.isoformat(),
            "ends_on": week_end.isoformat(),
        },
        "connection": {
            "id": str(connection.public_id),
            "stage": connection.stage,
        },
        "organization": {
            "id": str(connection.organization.public_id),
            "display_name": connection.organization.display_name,
        },
        "worker": {
            **_identity_payload(connection.candidate),
            "has_driving_license": connection.has_driving_license,
        },
        "days": days,
    }


def worker_workspace_snapshot(*, connection, selected_month):
    """Return one safe, authoritative worker workspace month."""

    now = timezone.now()
    today = timezone.localdate()
    month_start = selected_month.replace(day=1)
    month_end = month_start.replace(
        day=monthrange(month_start.year, month_start.month)[1]
    )

    month_shifts = list(
        _worker_shift_queryset(connection).filter(
            work_date__range=(month_start, month_end)
        )
    )
    today_shift = _worker_shift_queryset(connection).filter(work_date=today).first()
    next_shift = _worker_shift_queryset(connection).filter(work_date__gt=today).first()

    absences = list(
        ProjectCrewMemberAbsence.objects.filter(
            connection=connection,
            organization=connection.organization,
            crew__organization=connection.organization,
            work_date__range=(month_start, month_end),
        )
        .select_related("crew__project__worksite")
        .order_by("work_date", "id")
    )
    absence_pairs = {(item.crew_id, item.work_date) for item in absences}
    absence_shifts = []
    if absence_pairs:
        absence_shifts = [
            shift
            for shift in ProjectCrewShift.objects.filter(
                state=ProjectCrewShift.STATE_PUBLISHED,
                crew__organization=connection.organization,
                crew_id__in={crew_id for crew_id, _ in absence_pairs},
                work_date__range=(month_start, month_end),
            )
            .select_related("crew__project__worksite")
            .prefetch_related(
                Prefetch(
                    "members",
                    queryset=_member_queryset(
                        organization=connection.organization
                    ),
                )
            )
            .order_by("work_date", "starts_at", "id")
            if (shift.crew_id, shift.work_date) in absence_pairs
        ]
    relevant_crews = {
        shift.crew_id: shift.crew for shift in [*month_shifts, *absence_shifts]
    }
    roster_resources = list(
        ProjectCrewResourceAssignment.objects.filter(
            crew_id__in=relevant_crews,
            starts_on__lte=month_end,
        )
        .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=month_start))
        .select_related(
            "driver_connection__candidate",
            "driver_connection__candidate__profile",
            "vehicle",
        )
    )
    roster_passengers = list(
        ProjectCrewPassenger.objects.filter(
            crew_id__in=relevant_crews,
            connection=connection,
            starts_on__lte=month_end,
        ).filter(Q(ends_on__isnull=True) | Q(ends_on__gte=month_start))
    )

    shift_by_date = {item.work_date: item for item in absence_shifts}
    shift_by_date.update({item.work_date: item for item in month_shifts})
    worker_shift_dates = {item.work_date for item in month_shifts}
    day_off_dates = set(
        WorkerScheduleDayOff.objects.filter(
            connection=connection,
            organization=connection.organization,
            work_date__range=(month_start, month_end),
        ).values_list("work_date", flat=True)
    )
    absence_dates = {item.work_date for item in absences}
    time_entries = {
        item.work_date: item
        for item in WorkTimeEntry.objects.filter(
            connection=connection,
            organization=connection.organization,
            work_date__range=(month_start, month_end),
        ).order_by("work_date", "id")
    }

    calendar_days = []
    for day_number in range(1, month_end.day + 1):
        work_date = date(month_start.year, month_start.month, day_number)
        shift = shift_by_date.get(work_date)
        calendar_days.append(
            {
                "date": work_date.isoformat(),
                "shift": (
                    _shift_payload(
                        shift,
                        connection=connection,
                        roster_resources=roster_resources,
                        roster_passengers=roster_passengers,
                    )
                    if shift is not None
                    else None
                ),
                "day_off": work_date in day_off_dates,
                "absence": work_date in absence_dates,
                "time_entry": _time_entry_payload(time_entries.get(work_date)),
                "time_entry_access": worker_time_entry_access(
                    scheduled_shift=(
                        shift if work_date in worker_shift_dates else None
                    ),
                    entry=time_entries.get(work_date),
                    now=now,
                ),
            }
        )

    current_assignment = None
    if today_shift is not None:
        current_assignment = _current_assignment_from_shift(
            today_shift,
            connection=connection,
            basis="today_shift",
        )
    elif next_shift is not None:
        current_assignment = _current_assignment_from_shift(
            next_shift,
            connection=connection,
            basis="next_shift",
        )
    else:
        current_assignment = _permanent_assignment(connection, today=today)

    housing_items = list(
        HousingAssignment.objects.filter(
            connection=connection,
            organization=connection.organization,
            place__room__site__organization=connection.organization,
            state=HousingAssignment.STATE_PUBLISHED,
        )
        .select_related("place__room__site")
        .order_by("check_in_at", "id")
    )
    current_housing = next(
        (
            item
            for item in housing_items
            if item.check_in_at <= now
            and (item.check_out_at is None or item.check_out_at > now)
        ),
        None,
    )
    upcoming_housing = [item for item in housing_items if item.check_in_at > now]

    manager_conversation = _manager_conversation(connection)
    assigned_manager = connection.assigned_manager
    can_create_manager_conversation = bool(
        manager_conversation is None
        and assigned_manager is not None
        and assigned_manager.is_active
        and connection.stage != connection.STAGE_CLOSED
        and has_permission(
            user=assigned_manager.user,
            organization=connection.organization,
            permission_code=CHAT_MANAGE,
        )
    )
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    return {
        "generated_at": now,
        "today": today.isoformat(),
        "month": month_start.strftime("%Y-%m"),
        "connection": {
            "id": str(connection.public_id),
            "stage": connection.stage,
        },
        "organization": {
            "id": str(connection.organization.public_id),
            "display_name": connection.organization.display_name,
        },
        "worker": {
            **_identity_payload(connection.candidate),
            "has_driving_license": connection.has_driving_license,
            "stage": connection.stage,
        },
        "current_assignment": current_assignment,
        "housing": {
            "current": _housing_payload(current_housing),
            "upcoming": [_housing_payload(item) for item in upcoming_housing[:10]],
        },
        "calendar_days": calendar_days,
        "today_shift": (
            _shift_payload(today_shift, connection=connection)
            if today_shift is not None
            else None
        ),
        "next_shift": (
            _shift_payload(next_shift, connection=connection)
            if next_shift is not None
            else None
        ),
        "manager_conversation_id": (
            str(manager_conversation.public_id)
            if manager_conversation is not None
            else None
        ),
        "can_open_manager_chat": bool(
            manager_conversation is not None or can_create_manager_conversation
        ),
        "attention": _attention_payload(connection, now=now),
        "time_summary": {
            "month": _time_total(
                connection=connection,
                date_from=month_start,
                date_to=month_end,
            ),
            "current_week": _time_total(
                connection=connection,
                date_from=week_start,
                date_to=week_end,
            ),
        },
    }
