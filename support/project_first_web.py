"""Isolated employer preview for the project-first crew workflow.

The legacy Support workspace remains the default.  This module is reachable
only when the dedicated project-first feature switch is enabled and all write
operations delegate to ``support.services.project_crews``.
"""

from calendar import monthrange
from datetime import date
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Prefetch, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_time
from rest_framework.exceptions import APIException, ValidationError

from jobs.web_i18n import get_lang

from .feature_flags import is_project_first_workspace_enabled
from .models import (
    ProjectCrew,
    ProjectCrewPassenger,
    ProjectCrewResourceAssignment,
    ProjectCrewShift,
    ProjectCrewShiftMember,
    SupportConnection,
    Vehicle,
    WorkProject,
)
from .permissions import has_unrestricted_worker_access, worker_connection_queryset_for
from .selectors.workspace import _permissions_for, _select_membership
from .services.project_crews import (
    PASSENGER_SCOPE_FUTURE,
    PASSENGER_SCOPE_SELECTED,
    assign_project_crew_passenger,
    create_project_crew,
    publish_project_crew_shifts,
    release_project_crew_shifts,
    remove_project_crew_passenger,
    replace_project_crew_driver,
)


COPY = {
    "ru": {
        "preview": "Новый кабинет · тестовый режим",
        "title": "Проекты и экипажи",
        "subtitle": "Основное управление работой, транспортом и составом экипажей — на странице проекта.",
        "projects": "Проекты",
        "open": "Открыть",
        "back": "Все проекты",
        "crews": "Экипажи",
        "crew": "Экипаж",
        "no_crews": "У проекта пока нет экипажей.",
        "create_crew": "Добавить первый экипаж",
        "name": "Название экипажа",
        "driver": "Водитель",
        "vehicle": "Автомобиль",
        "start_date": "Дата начала",
        "create": "Создать экипаж",
        "schedule": "График экипажа",
        "dates": "Даты",
        "shift_start": "Начало",
        "shift_end": "Окончание",
        "break": "Пауза, минут",
        "publish": "Опубликовать выбранные дни",
        "release": "Освободить выбранные дни",
        "passengers": "Пассажиры",
        "add_passenger": "Добавить пассажира",
        "scope": "Применить",
        "future": "Ко всем будущим опубликованным дням",
        "selected": "Только к выбранным дням",
        "remove": "Исключить",
        "replace_driver": "Сменить водителя",
        "new_driver": "Новый водитель из пассажиров",
        "replace": "Сменить",
        "no_projects": "Активных проектов пока нет.",
        "no_shifts": "Опубликованных дней пока нет.",
        "no_passengers": "Пассажиров пока нет.",
        "seats": "Места",
        "project_address": "Адрес",
        "created": "Экипаж создан.",
        "shifts_saved": "График экипажа сохранён.",
        "shifts_released": "Выбранные дни освобождены.",
        "passenger_added": "Пассажир добавлен.",
        "passenger_removed": "Пассажир исключён.",
        "driver_replaced": "Водитель экипажа изменён.",
        "choose_dates": "Выберите один или несколько опубликованных дней.",
    },
    "en": {
        "preview": "New workspace · preview",
        "title": "Projects and crews",
        "subtitle": "Manage work, transport and crew composition from the project page.",
        "projects": "Projects", "open": "Open", "back": "All projects", "crews": "Crews", "crew": "Crew",
        "no_crews": "This project has no crews yet.", "create_crew": "Add the first crew", "name": "Crew name",
        "driver": "Driver", "vehicle": "Vehicle", "start_date": "Start date", "create": "Create crew",
        "schedule": "Crew schedule", "dates": "Dates", "shift_start": "Start", "shift_end": "End",
        "break": "Break, minutes", "publish": "Publish selected days", "release": "Release selected days",
        "passengers": "Passengers", "add_passenger": "Add passenger", "scope": "Apply to",
        "future": "All future published days", "selected": "Selected days only", "remove": "Remove",
        "replace_driver": "Replace driver", "new_driver": "New driver from passengers", "replace": "Replace",
        "no_projects": "There are no active projects yet.", "no_shifts": "There are no published days yet.",
        "no_passengers": "There are no passengers yet.", "seats": "Seats", "project_address": "Address",
        "created": "Crew created.", "shifts_saved": "Crew schedule saved.",
        "shifts_released": "Selected days released.", "passenger_added": "Passenger added.",
        "passenger_removed": "Passenger removed.", "driver_replaced": "Crew driver replaced.",
        "choose_dates": "Select one or more published days.",
    },
    "pl": {
        "preview": "Nowy panel · tryb testowy", "title": "Projekty i ekipy",
        "subtitle": "Zarządzaj pracą, transportem i składem ekipy na stronie projektu.",
        "projects": "Projekty", "open": "Otwórz", "back": "Wszystkie projekty", "crews": "Ekipy", "crew": "Ekipa",
        "no_crews": "Ten projekt nie ma jeszcze ekip.", "create_crew": "Dodaj pierwszą ekipę", "name": "Nazwa ekipy",
        "driver": "Kierowca", "vehicle": "Samochód", "start_date": "Data rozpoczęcia", "create": "Utwórz ekipę",
        "schedule": "Grafik ekipy", "dates": "Daty", "shift_start": "Początek", "shift_end": "Koniec",
        "break": "Przerwa, minuty", "publish": "Opublikuj wybrane dni", "release": "Zwolnij wybrane dni",
        "passengers": "Pasażerowie", "add_passenger": "Dodaj pasażera", "scope": "Zastosuj do",
        "future": "Wszystkich przyszłych opublikowanych dni", "selected": "Tylko wybranych dni", "remove": "Usuń",
        "replace_driver": "Zmień kierowcę", "new_driver": "Nowy kierowca z pasażerów", "replace": "Zmień",
        "no_projects": "Nie ma jeszcze aktywnych projektów.", "no_shifts": "Nie ma jeszcze opublikowanych dni.",
        "no_passengers": "Nie ma jeszcze pasażerów.", "seats": "Miejsca", "project_address": "Adres",
        "created": "Ekipa została utworzona.", "shifts_saved": "Grafik ekipy został zapisany.",
        "shifts_released": "Wybrane dni zostały zwolnione.", "passenger_added": "Pasażer został dodany.",
        "passenger_removed": "Pasażer został usunięty.", "driver_replaced": "Kierowca ekipy został zmieniony.",
        "choose_dates": "Wybierz co najmniej jeden opublikowany dzień.",
    },
    "uk": {
        "preview": "Новий кабінет · тестовий режим", "title": "Проєкти та екіпажі",
        "subtitle": "Керуйте роботою, транспортом і складом екіпажів на сторінці проєкту.",
        "projects": "Проєкти", "open": "Відкрити", "back": "Усі проєкти", "crews": "Екіпажі", "crew": "Екіпаж",
        "no_crews": "У проєкту ще немає екіпажів.", "create_crew": "Додати перший екіпаж", "name": "Назва екіпажу",
        "driver": "Водій", "vehicle": "Автомобіль", "start_date": "Дата початку", "create": "Створити екіпаж",
        "schedule": "Графік екіпажу", "dates": "Дати", "shift_start": "Початок", "shift_end": "Кінець",
        "break": "Перерва, хвилин", "publish": "Опублікувати вибрані дні", "release": "Звільнити вибрані дні",
        "passengers": "Пасажири", "add_passenger": "Додати пасажира", "scope": "Застосувати до",
        "future": "Усіх майбутніх опублікованих днів", "selected": "Лише вибраних днів", "remove": "Виключити",
        "replace_driver": "Змінити водія", "new_driver": "Новий водій з пасажирів", "replace": "Змінити",
        "no_projects": "Активних проєктів поки немає.", "no_shifts": "Опублікованих днів поки немає.",
        "no_passengers": "Пасажирів поки немає.", "seats": "Місця", "project_address": "Адреса",
        "created": "Екіпаж створено.", "shifts_saved": "Графік екіпажу збережено.",
        "shifts_released": "Вибрані дні звільнено.", "passenger_added": "Пасажира додано.",
        "passenger_removed": "Пасажира виключено.", "driver_replaced": "Водія екіпажу змінено.",
        "choose_dates": "Виберіть один або кілька опублікованих днів.",
    },
}


def _display_name(connection):
    user = connection.candidate
    return user.get_full_name().strip() or user.email or user.username


def _copy(request):
    return COPY.get(get_lang(request), COPY["ru"])


def _validation_message(error):
    detail = getattr(error, "detail", None)
    if isinstance(detail, dict):
        message = detail.get("message")
        if message:
            return str(message)
        for value in detail.values():
            if isinstance(value, (list, tuple)) and value:
                return str(value[0])
            if value:
                return str(value)
    if detail:
        return str(detail)
    return str(error)


def _selected_organization(request):
    memberships, membership = _select_membership(
        user=request.user,
        organization_public_id=request.GET.get("organization") or request.POST.get("organization"),
    )
    permissions = _permissions_for(user=request.user, organization=membership.organization)
    if not permissions["schedule"] or not permissions["transport"]:
        raise Http404("project_first_workspace_not_found")
    # The preview renders whole crews.  Until per-crew read scopes are added,
    # only the owner/deputy with unrestricted worker access may enter it; this
    # prevents a scoped manager from learning names outside their assignment.
    if not has_unrestricted_worker_access(
        user=request.user,
        organization=membership.organization,
    ):
        raise Http404("project_first_workspace_not_found")
    return memberships, membership, permissions


def _workspace_url(organization, *, project=None, month=None):
    query = {"organization": organization.public_id}
    if month:
        query["month"] = month
    if project is None:
        name = "support:project-first"
        kwargs = {}
    else:
        name = "support:project-first-detail"
        kwargs = {"project_public_id": project.public_id}
    return f"{reverse(name, kwargs=kwargs)}?{urlencode(query)}"


def _parse_dates(request):
    parsed = []
    for raw in request.POST.getlist("work_dates"):
        value = parse_date(raw)
        if value is not None:
            parsed.append(value)
    return parsed


def _scoped_connections(request, organization):
    return worker_connection_queryset_for(
        user=request.user,
        organization=organization,
        queryset=SupportConnection.objects.filter(
            organization=organization,
            is_archived=False,
        ).select_related("candidate"),
    )


def _project_context(request, organization, project):
    today = timezone.localdate()
    connections = list(_scoped_connections(request, organization).order_by("candidate__first_name", "candidate__last_name", "id"))
    for item in connections:
        item.display_name = _display_name(item)

    crews = list(
        ProjectCrew.objects.filter(project=project, state=ProjectCrew.STATE_ACTIVE)
        .prefetch_related(
            Prefetch(
                "resource_assignments",
                queryset=ProjectCrewResourceAssignment.objects.select_related(
                    "driver_connection__candidate", "vehicle"
                ).order_by("-starts_on", "-id"),
            ),
            Prefetch(
                "passenger_assignments",
                queryset=ProjectCrewPassenger.objects.filter(ends_on__isnull=True)
                .select_related("connection__candidate")
                .order_by("connection__candidate__first_name", "id"),
            ),
            Prefetch(
                "calendar_shifts",
                queryset=ProjectCrewShift.objects.filter(state=ProjectCrewShift.STATE_PUBLISHED)
                .prefetch_related("members")
                .order_by("work_date"),
            ),
        )
        .order_by("internal_name", "id")
    )
    used_vehicle_ids = set(
        ProjectCrewResourceAssignment.objects.filter(
            crew__organization=organization,
            ends_on__isnull=True,
        ).values_list("vehicle_id", flat=True)
    )
    used_driver_ids = set(
        ProjectCrewResourceAssignment.objects.filter(
            crew__organization=organization,
            ends_on__isnull=True,
        ).values_list("driver_connection_id", flat=True)
    )
    for crew in crews:
        crew.current_resource = next(
            (
                resource for resource in crew.resource_assignments.all()
                if resource.starts_on <= today and (resource.ends_on is None or resource.ends_on >= today)
            ),
            crew.resource_assignments.all()[0] if crew.resource_assignments.all() else None,
        )
        if crew.current_resource:
            crew.current_resource.driver_name = _display_name(crew.current_resource.driver_connection)
        crew.open_passengers = list(crew.passenger_assignments.all())
        for passenger in crew.open_passengers:
            passenger.display_name = _display_name(passenger.connection)
        crew.published_shifts = list(crew.calendar_shifts.all())
        crew.occupied = 1 + len(crew.open_passengers)
        crew.passenger_driver_options = [
            item for item in crew.open_passengers if item.connection.has_driving_license
        ]

    available_drivers = [
        item for item in connections
        if item.has_driving_license and item.id not in used_driver_ids
    ]
    available_vehicles = list(
        Vehicle.objects.filter(organization=organization, is_active=True)
        .exclude(id__in=used_vehicle_ids)
        .order_by("internal_name", "registration_identifier")
    )
    return {
        "crews": crews,
        "connections": connections,
        "available_drivers": available_drivers,
        "available_vehicles": available_vehicles,
    }


def _handle_action(request, *, organization, project, copy):
    action = (request.POST.get("action") or "").strip()
    crew = None
    if request.POST.get("crew_id"):
        crew = get_object_or_404(
            ProjectCrew,
            organization=organization,
            project=project,
            public_id=request.POST.get("crew_id"),
            state=ProjectCrew.STATE_ACTIVE,
        )
    if action == "crew_create":
        driver = get_object_or_404(
            _scoped_connections(request, organization),
            public_id=request.POST.get("driver_id"),
            has_driving_license=True,
        )
        vehicle = get_object_or_404(
            Vehicle,
            organization=organization,
            public_id=request.POST.get("vehicle_id"),
            is_active=True,
        )
        starts_on = parse_date(request.POST.get("starts_on")) or timezone.localdate()
        create_project_crew(
            actor=request.user,
            organization=organization,
            project=project,
            driver_connection=driver,
            vehicle=vehicle,
            internal_name=(request.POST.get("internal_name") or "").strip(),
            starts_on=starts_on,
        )
        return copy["created"]
    if crew is None:
        raise ValidationError({"message": "Select a crew."})
    if action == "shifts_publish":
        publish_project_crew_shifts(
            actor=request.user,
            crew=crew,
            work_dates=_parse_dates(request),
            starts_at_time=parse_time(request.POST.get("starts_at_time") or ""),
            ends_at_time=parse_time(request.POST.get("ends_at_time") or ""),
            break_minutes=request.POST.get("break_minutes") or 0,
        )
        return copy["shifts_saved"]
    if action == "shifts_release":
        release_project_crew_shifts(
            actor=request.user,
            crew=crew,
            work_dates=_parse_dates(request),
        )
        return copy["shifts_released"]
    if action in {"passenger_add", "passenger_remove"}:
        connection = get_object_or_404(
            _scoped_connections(request, organization),
            public_id=request.POST.get("connection_id"),
        )
        scope = request.POST.get("scope") or PASSENGER_SCOPE_FUTURE
        kwargs = {
            "actor": request.user,
            "crew": crew,
            "connection": connection,
            "scope": scope,
            "selected_dates": _parse_dates(request),
            "effective_on": parse_date(request.POST.get("effective_on")) or timezone.localdate(),
        }
        if action == "passenger_add":
            assign_project_crew_passenger(**kwargs)
            return copy["passenger_added"]
        remove_project_crew_passenger(**kwargs)
        return copy["passenger_removed"]
    if action == "driver_replace":
        new_driver = get_object_or_404(
            _scoped_connections(request, organization),
            public_id=request.POST.get("driver_id"),
            has_driving_license=True,
        )
        replace_project_crew_driver(
            actor=request.user,
            crew=crew,
            new_driver_connection=new_driver,
            effective_on=parse_date(request.POST.get("effective_on")) or timezone.localdate(),
        )
        return copy["driver_replaced"]
    raise ValidationError({"message": "Unknown project crew operation."})


@login_required(login_url="employer:login")
def project_first_workspace(request, project_public_id=None):
    if not is_project_first_workspace_enabled():
        raise Http404("project_first_workspace_not_available")
    memberships, membership, permissions = _selected_organization(request)
    organization = membership.organization
    copy = _copy(request)
    projects = list(
        WorkProject.objects.filter(organization=organization, is_active=True)
        .select_related("worksite")
        .annotate(
            crew_count=Count(
                "project_crews",
                filter=Q(project_crews__state=ProjectCrew.STATE_ACTIVE),
                distinct=True,
            ),
            shift_count=Count(
                "project_crews__calendar_shifts",
                filter=Q(project_crews__calendar_shifts__state=ProjectCrewShift.STATE_PUBLISHED),
                distinct=True,
            ),
        )
        .order_by("internal_name", "id")
    )
    project = None
    if project_public_id is not None:
        project = get_object_or_404(
            WorkProject.objects.select_related("worksite"),
            organization=organization,
            public_id=project_public_id,
            is_active=True,
        )
        if request.method == "POST":
            try:
                success = _handle_action(
                    request,
                    organization=organization,
                    project=project,
                    copy=copy,
                )
            except (APIException, ValueError) as error:
                messages.error(request, _validation_message(error))
            else:
                messages.success(request, success)
            return redirect(_workspace_url(organization, project=project))

    context = {
        "pf": copy,
        "organization": organization,
        "memberships": memberships,
        "membership": membership,
        "permissions": permissions,
        "projects": projects,
        "project": project,
        "project_list_url": _workspace_url(organization),
        "today": timezone.localdate(),
    }
    if project is not None:
        context.update(_project_context(request, organization, project))
    return render(request, "support/project_first_workspace.html", context)
