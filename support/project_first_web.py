"""Isolated employer preview for the project-first crew workflow.

The legacy Support workspace remains the default.  This module is reachable
only when the dedicated project-first feature switch is enabled and all write
operations delegate to ``support.services.project_crews``.
"""

from calendar import monthrange
from datetime import date
from urllib.parse import urlencode

from django.conf import settings
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
    DriverVehicleAssignment,
    ProjectCrew,
    ProjectCrewDriverSubstitution,
    ProjectCrewMemberAbsence,
    ProjectCrewPassenger,
    ProjectCrewResourceAssignment,
    ProjectCrewShift,
    ProjectCrewShiftMember,
    SupportConnection,
    Vehicle,
    WorkerScheduleDayOff,
    WorkProject,
)
from .permissions import has_unrestricted_worker_access, worker_connection_queryset_for
from .selectors.workspace import _permissions_for, _select_membership
from .services.project_crews import (
    PASSENGER_SCOPE_FUTURE,
    PASSENGER_SCOPE_SELECTED,
    assign_project_crew_passenger,
    assign_project_crew_substitute_driver,
    create_project_crew,
    publish_project_crew_shifts,
    project_crew_substitute_driver_candidates,
    release_project_crew_shifts,
    remove_project_crew_passenger,
    replace_project_crew_driver,
)
from .services.project_first_reset import (
    ProjectFirstResetError,
    build_project_first_reset_plan,
    execute_project_first_reset,
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
        "create_crew": "Добавить экипаж",
        "name": "Название экипажа",
        "driver": "Водитель",
        "vehicle": "Автомобиль",
        "start_date": "Дата начала",
        "create": "Создать экипаж",
        "schedule": "График экипажа",
        "schedule_hint": "Один клик показывает день, двойной — выбирает его для изменения.",
        "month_navigation": "Управление месяцем",
        "today": "Сегодня",
        "selected_days": "Выбрано дней",
        "clear_selection": "Снять выбор",
        "month_shifts": "Список смен этого месяца",
        "published": "Опубликовано",
        "dates": "Даты",
        "shift_start": "Начало",
        "shift_end": "Окончание",
        "break": "Пауза, минут",
        "publish": "Опубликовать выбранные дни",
        "release": "Освободить выбранные дни",
        "passengers": "Пассажиры",
        "add_passenger": "Добавить пассажира",
        "scope": "Применить",
        "future": "На весь график экипажа",
        "selected": "Только к выбранным дням",
        "select_dates_hint": "Выделите даты в календаре.",
        "absent_dates": "Отсутствует",
        "day_off": "Выходной",
        "driver_missing": "Нет водителя",
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
        "substitute_driver": "Подменный водитель",
        "assign_substitute": "Подменить водителя",
        "substitute_on": "Подменный водитель на",
        "substitute_dates_hint": "Выберите в календаре только даты отсутствия основного водителя.",
        "substitute_assigned": "Подменный водитель назначен.",
        "substitution_history": "История подмен",
        "substitution_active": "Активна",
        "substitution_replaced": "Заменена",
        "substitution_cancelled": "Отменена",
        "error_substitution_requires_driver_absence": "Подмену можно назначить только на даты отсутствия основного водителя.",
        "error_substitute_driver_unavailable": "Выбранный водитель занят или недоступен хотя бы в одну из выбранных дат.",
        "error_substitution_date_in_past": "Подменного водителя нельзя назначить на прошедшую дату.",
        "choose_dates": "Выберите один или несколько опубликованных дней.",
        "reset_plan": "План очистки",
        "reset_title": "Предварительный план очистки staging",
        "reset_subtitle": "Это только отчёт. Сейчас никакие данные не изменяются и не удаляются.",
        "delete_group": "Будет удалено",
        "preserve_group": "Будет сохранено",
        "confirmation_phrase": "Фраза подтверждения",
        "report_updated": "Отчёт рассчитан по текущему состоянию базы данных.",
        "apply_reset": "Выполнить подтверждённую очистку",
        "reset_complete": "Очистка завершена. Работники, жильё и автопарк сохранены.",
        "reset_guard_disabled": "Серверная защита очистки выключена.",
        "reset_confirmation_invalid": "Фраза подтверждения не совпадает.",
    },
    "en": {
        "preview": "New workspace · preview",
        "title": "Projects and crews",
        "subtitle": "Manage work, transport and crew composition from the project page.",
        "projects": "Projects", "open": "Open", "back": "All projects", "crews": "Crews", "crew": "Crew",
        "no_crews": "This project has no crews yet.", "create_crew": "Add crew", "name": "Crew name",
        "driver": "Driver", "vehicle": "Vehicle", "start_date": "Start date", "create": "Create crew",
        "schedule": "Crew schedule", "dates": "Dates", "shift_start": "Start", "shift_end": "End",
        "schedule_hint": "Click once to view a day; double-click to select it for changes.",
        "month_navigation": "Month navigation", "today": "Today", "selected_days": "Selected days",
        "clear_selection": "Clear selection", "month_shifts": "This month's shifts", "published": "Published",
        "break": "Break, minutes", "publish": "Publish selected days", "release": "Release selected days",
        "passengers": "Passengers", "add_passenger": "Add passenger", "scope": "Apply to",
        "future": "Entire crew schedule", "selected": "Selected days only",
        "select_dates_hint": "Select dates in the calendar.", "remove": "Remove",
        "absent_dates": "Absent",
        "day_off": "Day off",
        "driver_missing": "No driver",
        "replace_driver": "Replace driver", "new_driver": "New driver from passengers", "replace": "Replace",
        "no_projects": "There are no active projects yet.", "no_shifts": "There are no published days yet.",
        "no_passengers": "There are no passengers yet.", "seats": "Seats", "project_address": "Address",
        "created": "Crew created.", "shifts_saved": "Crew schedule saved.",
        "shifts_released": "Selected days released.", "passenger_added": "Passenger added.",
        "passenger_removed": "Passenger removed.", "driver_replaced": "Crew driver replaced.",
        "substitute_driver": "Substitute driver", "assign_substitute": "Assign substitute driver",
        "substitute_on": "Substitute driver on", "substitute_dates_hint": "Select only dates when the primary driver is absent.",
        "substitute_assigned": "Substitute driver assigned.",
        "substitution_history": "Substitution history",
        "substitution_active": "Active",
        "substitution_replaced": "Replaced",
        "substitution_cancelled": "Cancelled",
        "error_substitution_requires_driver_absence": "A substitute can be assigned only when the primary driver is absent.",
        "error_substitute_driver_unavailable": "The selected driver is busy or unavailable on at least one selected date.",
        "error_substitution_date_in_past": "A substitute driver cannot be assigned to a past date.",
        "choose_dates": "Select one or more published days.",
        "reset_plan": "Reset plan", "reset_title": "Staging reset preview",
        "reset_subtitle": "This is a read-only report. No data is changed or deleted now.",
        "delete_group": "Will be deleted", "preserve_group": "Will be preserved",
        "confirmation_phrase": "Confirmation phrase",
        "report_updated": "The report was calculated from the current database state.",
        "apply_reset": "Apply confirmed reset", "reset_complete": "Reset complete. Workers, housing and fleet were preserved.",
        "reset_guard_disabled": "The server-side reset guard is disabled.",
        "reset_confirmation_invalid": "The confirmation phrase does not match.",
    },
    "pl": {
        "preview": "Nowy panel · tryb testowy", "title": "Projekty i ekipy",
        "subtitle": "Zarządzaj pracą, transportem i składem ekipy na stronie projektu.",
        "projects": "Projekty", "open": "Otwórz", "back": "Wszystkie projekty", "crews": "Ekipy", "crew": "Ekipa",
        "no_crews": "Ten projekt nie ma jeszcze ekip.", "create_crew": "Dodaj ekipę", "name": "Nazwa ekipy",
        "driver": "Kierowca", "vehicle": "Samochód", "start_date": "Data rozpoczęcia", "create": "Utwórz ekipę",
        "schedule": "Grafik ekipy", "dates": "Daty", "shift_start": "Początek", "shift_end": "Koniec",
        "schedule_hint": "Jedno kliknięcie pokazuje dzień, podwójne wybiera go do zmiany.",
        "month_navigation": "Nawigacja miesiąca", "today": "Dzisiaj", "selected_days": "Wybrane dni",
        "clear_selection": "Wyczyść wybór", "month_shifts": "Zmiany w tym miesiącu", "published": "Opublikowano",
        "break": "Przerwa, minuty", "publish": "Opublikuj wybrane dni", "release": "Zwolnij wybrane dni",
        "passengers": "Pasażerowie", "add_passenger": "Dodaj pasażera", "scope": "Zastosuj do",
        "future": "Całego grafiku ekipy", "selected": "Tylko wybranych dni",
        "select_dates_hint": "Wybierz daty w kalendarzu.", "remove": "Usuń",
        "absent_dates": "Nieobecny/a",
        "day_off": "Dzień wolny",
        "driver_missing": "Brak kierowcy",
        "replace_driver": "Zmień kierowcę", "new_driver": "Nowy kierowca z pasażerów", "replace": "Zmień",
        "no_projects": "Nie ma jeszcze aktywnych projektów.", "no_shifts": "Nie ma jeszcze opublikowanych dni.",
        "no_passengers": "Nie ma jeszcze pasażerów.", "seats": "Miejsca", "project_address": "Adres",
        "created": "Ekipa została utworzona.", "shifts_saved": "Grafik ekipy został zapisany.",
        "shifts_released": "Wybrane dni zostały zwolnione.", "passenger_added": "Pasażer został dodany.",
        "passenger_removed": "Pasażer został usunięty.", "driver_replaced": "Kierowca ekipy został zmieniony.",
        "substitute_driver": "Kierowca zastępczy", "assign_substitute": "Wyznacz kierowcę zastępczego",
        "substitute_on": "Kierowca zastępczy na", "substitute_dates_hint": "Wybierz tylko dni nieobecności głównego kierowcy.",
        "substitute_assigned": "Kierowca zastępczy został wyznaczony.",
        "substitution_history": "Historia zastępstw",
        "substitution_active": "Aktywne",
        "substitution_replaced": "Zmienione",
        "substitution_cancelled": "Anulowane",
        "error_substitution_requires_driver_absence": "Zastępstwo można wyznaczyć tylko na dni nieobecności głównego kierowcy.",
        "error_substitute_driver_unavailable": "Wybrany kierowca jest zajęty lub niedostępny co najmniej jednego wybranego dnia.",
        "error_substitution_date_in_past": "Nie można wyznaczyć kierowcy zastępczego na minioną datę.",
        "choose_dates": "Wybierz co najmniej jeden opublikowany dzień.",
        "reset_plan": "Plan czyszczenia", "reset_title": "Podgląd czyszczenia staging",
        "reset_subtitle": "To tylko raport do odczytu. Żadne dane nie są teraz zmieniane ani usuwane.",
        "delete_group": "Zostanie usunięte", "preserve_group": "Zostanie zachowane",
        "confirmation_phrase": "Fraza potwierdzająca",
        "report_updated": "Raport obliczono na podstawie bieżącego stanu bazy danych.",
        "apply_reset": "Wykonaj potwierdzone czyszczenie", "reset_complete": "Czyszczenie zakończone. Pracownicy, mieszkania i flota zostały zachowane.",
        "reset_guard_disabled": "Serwerowa blokada czyszczenia jest wyłączona.",
        "reset_confirmation_invalid": "Fraza potwierdzająca nie pasuje.",
    },
    "uk": {
        "preview": "Новий кабінет · тестовий режим", "title": "Проєкти та екіпажі",
        "subtitle": "Керуйте роботою, транспортом і складом екіпажів на сторінці проєкту.",
        "projects": "Проєкти", "open": "Відкрити", "back": "Усі проєкти", "crews": "Екіпажі", "crew": "Екіпаж",
        "no_crews": "У проєкту ще немає екіпажів.", "create_crew": "Додати екіпаж", "name": "Назва екіпажу",
        "driver": "Водій", "vehicle": "Автомобіль", "start_date": "Дата початку", "create": "Створити екіпаж",
        "schedule": "Графік екіпажу", "dates": "Дати", "shift_start": "Початок", "shift_end": "Кінець",
        "schedule_hint": "Один клік показує день, подвійний вибирає його для зміни.",
        "month_navigation": "Керування місяцем", "today": "Сьогодні", "selected_days": "Вибрано днів",
        "clear_selection": "Зняти вибір", "month_shifts": "Зміни цього місяця", "published": "Опубліковано",
        "break": "Перерва, хвилин", "publish": "Опублікувати вибрані дні", "release": "Звільнити вибрані дні",
        "passengers": "Пасажири", "add_passenger": "Додати пасажира", "scope": "Застосувати до",
        "future": "Усього графіка екіпажу", "selected": "Лише вибраних днів",
        "select_dates_hint": "Виберіть дати в календарі.", "remove": "Виключити",
        "absent_dates": "Відсутній/я",
        "day_off": "Вихідний",
        "driver_missing": "Немає водія",
        "replace_driver": "Змінити водія", "new_driver": "Новий водій з пасажирів", "replace": "Змінити",
        "no_projects": "Активних проєктів поки немає.", "no_shifts": "Опублікованих днів поки немає.",
        "no_passengers": "Пасажирів поки немає.", "seats": "Місця", "project_address": "Адреса",
        "created": "Екіпаж створено.", "shifts_saved": "Графік екіпажу збережено.",
        "shifts_released": "Вибрані дні звільнено.", "passenger_added": "Пасажира додано.",
        "passenger_removed": "Пасажира виключено.", "driver_replaced": "Водія екіпажу змінено.",
        "substitute_driver": "Підмінний водій", "assign_substitute": "Призначити підмінного водія",
        "substitute_on": "Підмінний водій на", "substitute_dates_hint": "Виберіть лише дні відсутності основного водія.",
        "substitute_assigned": "Підмінного водія призначено.",
        "substitution_history": "Історія підмін",
        "substitution_active": "Активна",
        "substitution_replaced": "Замінена",
        "substitution_cancelled": "Скасована",
        "error_substitution_requires_driver_absence": "Підміну можна призначити лише на дні відсутності основного водія.",
        "error_substitute_driver_unavailable": "Вибраний водій зайнятий або недоступний щонайменше в один із вибраних днів.",
        "error_substitution_date_in_past": "Підмінного водія не можна призначити на минулу дату.",
        "choose_dates": "Виберіть один або кілька опублікованих днів.",
        "reset_plan": "План очищення", "reset_title": "Попередній план очищення staging",
        "reset_subtitle": "Це лише звіт для читання. Зараз дані не змінюються і не видаляються.",
        "delete_group": "Буде видалено", "preserve_group": "Буде збережено",
        "confirmation_phrase": "Фраза підтвердження",
        "report_updated": "Звіт розраховано за поточним станом бази даних.",
        "apply_reset": "Виконати підтверджене очищення", "reset_complete": "Очищення завершено. Працівники, житло й автопарк збережено.",
        "reset_guard_disabled": "Серверний захист очищення вимкнено.",
        "reset_confirmation_invalid": "Фраза підтвердження не збігається.",
    },
}


PROJECT_CREW_ERROR_COPY = {
    "ru": {
        "error_work_dates_required": "Выберите хотя бы один день в календаре.",
        "error_shift_time_required": "Укажите время начала и окончания смены.",
        "error_break_minutes_invalid": "Проверьте продолжительность паузы в минутах.",
        "error_worker_not_in_organization": "Работник не относится к выбранной фирме.",
        "error_worker_archived": "Выбранный работник находится в архиве.",
        "error_project_not_in_organization": "Проект не относится к выбранной фирме.",
        "error_vehicle_not_available": "Выбранный автомобиль недоступен в этой фирме.",
        "error_driver_licence_not_confirmed": "У работника нет подтверждённой отметки о водительском удостоверении.",
        "error_crew_driver_missing": "На выбранную дату у экипажа нет водителя.",
        "error_crew_resource_missing": "На одну из выбранных дат у экипажа нет основного водителя или автомобиля.",
        "error_crew_shift_missing": "На одну из выбранных дат нет опубликованной смены экипажа.",
        "error_crew_capacity_exceeded": "В автомобиле недостаточно свободных мест.",
        "error_worker_drives_other_crew": "В выбранную дату работник уже является водителем другого экипажа.",
        "error_worker_day_off": "На выбранную дату у работника отмечен выходной.",
        "error_worker_absent_from_crew": "На выбранную дату работник отмечен отсутствующим в этом экипаже.",
        "error_worker_is_crew_driver": "Водителя этого экипажа нельзя назначить его пассажиром.",
        "error_driver_shift_conflict": "У водителя уже есть другая смена в выбранное время.",
        "error_legacy_driver_or_vehicle_already_assigned": "Водитель или автомобиль всё ещё занят в прежнем транспортном назначении.",
        "error_driver_or_vehicle_already_assigned": "Водитель или автомобиль уже закреплён за другим экипажем.",
        "error_passenger_scope_invalid": "Выберите: весь график экипажа или только отмеченные дни.",
        "error_selected_schedule_days_have_no_shifts": "В выбранных днях нет смен, которые можно изменить.",
        "error_replacement_driver_not_in_crew": "Нового постоянного водителя можно выбрать только из пассажиров этого экипажа.",
        "error_replacement_driver_shift_conflict": "У нового водителя есть пересекающаяся смена в другом экипаже.",
    },
    "en": {
        "error_work_dates_required": "Select at least one calendar date.",
        "error_shift_time_required": "Enter the shift start and end time.",
        "error_break_minutes_invalid": "Check the break duration in minutes.",
        "error_worker_not_in_organization": "The worker does not belong to the selected company.",
        "error_worker_archived": "The selected worker is archived.",
        "error_project_not_in_organization": "The project does not belong to the selected company.",
        "error_vehicle_not_available": "The selected vehicle is unavailable in this company.",
        "error_driver_licence_not_confirmed": "The worker has no confirmed driving-licence mark.",
        "error_crew_driver_missing": "The crew has no driver on the selected date.",
        "error_crew_resource_missing": "The crew has no primary driver or vehicle on one selected date.",
        "error_crew_shift_missing": "There is no published crew shift on one selected date.",
        "error_crew_capacity_exceeded": "The vehicle has insufficient free seats.",
        "error_worker_drives_other_crew": "The worker already drives another crew on the selected date.",
        "error_worker_day_off": "The worker has a day off on the selected date.",
        "error_worker_absent_from_crew": "The worker is marked absent from this crew on the selected date.",
        "error_worker_is_crew_driver": "This crew's driver cannot be assigned as its passenger.",
        "error_driver_shift_conflict": "The driver already has another overlapping shift.",
        "error_legacy_driver_or_vehicle_already_assigned": "The driver or vehicle is still occupied by a legacy transport assignment.",
        "error_driver_or_vehicle_already_assigned": "The driver or vehicle is already assigned to another crew.",
        "error_passenger_scope_invalid": "Choose the entire crew schedule or selected days only.",
        "error_selected_schedule_days_have_no_shifts": "The selected days contain no shifts to change.",
        "error_replacement_driver_not_in_crew": "A permanent replacement driver must be a passenger of this crew.",
        "error_replacement_driver_shift_conflict": "The new driver has an overlapping shift in another crew.",
    },
    "pl": {
        "error_work_dates_required": "Wybierz co najmniej jeden dzień w kalendarzu.",
        "error_shift_time_required": "Podaj godzinę rozpoczęcia i zakończenia zmiany.",
        "error_break_minutes_invalid": "Sprawdź długość przerwy w minutach.",
        "error_worker_not_in_organization": "Pracownik nie należy do wybranej firmy.",
        "error_worker_archived": "Wybrany pracownik znajduje się w archiwum.",
        "error_project_not_in_organization": "Projekt nie należy do wybranej firmy.",
        "error_vehicle_not_available": "Wybrany samochód nie jest dostępny w tej firmie.",
        "error_driver_licence_not_confirmed": "Pracownik nie ma potwierdzonego prawa jazdy.",
        "error_crew_driver_missing": "Ekipa nie ma kierowcy w wybranym dniu.",
        "error_crew_resource_missing": "W jednym z wybranych dni ekipa nie ma głównego kierowcy lub samochodu.",
        "error_crew_shift_missing": "W jednym z wybranych dni nie ma opublikowanej zmiany ekipy.",
        "error_crew_capacity_exceeded": "W samochodzie brakuje wolnych miejsc.",
        "error_worker_drives_other_crew": "W wybranym dniu pracownik prowadzi już inną ekipę.",
        "error_worker_day_off": "W wybranym dniu pracownik ma dzień wolny.",
        "error_worker_absent_from_crew": "W wybranym dniu pracownik jest oznaczony jako nieobecny w tej ekipie.",
        "error_worker_is_crew_driver": "Kierowca tej ekipy nie może być jej pasażerem.",
        "error_driver_shift_conflict": "Kierowca ma już inną nakładającą się zmianę.",
        "error_legacy_driver_or_vehicle_already_assigned": "Kierowca lub samochód jest nadal zajęty w poprzednim przypisaniu transportowym.",
        "error_driver_or_vehicle_already_assigned": "Kierowca lub samochód jest już przypisany do innej ekipy.",
        "error_passenger_scope_invalid": "Wybierz cały grafik ekipy albo tylko zaznaczone dni.",
        "error_selected_schedule_days_have_no_shifts": "W wybranych dniach nie ma zmian do edycji.",
        "error_replacement_driver_not_in_crew": "Nowego stałego kierowcę można wybrać tylko spośród pasażerów tej ekipy.",
        "error_replacement_driver_shift_conflict": "Nowy kierowca ma nakładającą się zmianę w innej ekipie.",
    },
    "uk": {
        "error_work_dates_required": "Виберіть хоча б один день у календарі.",
        "error_shift_time_required": "Вкажіть час початку й завершення зміни.",
        "error_break_minutes_invalid": "Перевірте тривалість перерви у хвилинах.",
        "error_worker_not_in_organization": "Працівник не належить до вибраної фірми.",
        "error_worker_archived": "Вибраний працівник перебуває в архіві.",
        "error_project_not_in_organization": "Проєкт не належить до вибраної фірми.",
        "error_vehicle_not_available": "Вибраний автомобіль недоступний у цій фірмі.",
        "error_driver_licence_not_confirmed": "Працівник не має підтвердженої відмітки про водійське посвідчення.",
        "error_crew_driver_missing": "На вибрану дату екіпаж не має водія.",
        "error_crew_resource_missing": "На одну з вибраних дат екіпаж не має основного водія або автомобіля.",
        "error_crew_shift_missing": "На одну з вибраних дат немає опублікованої зміни екіпажу.",
        "error_crew_capacity_exceeded": "В автомобілі недостатньо вільних місць.",
        "error_worker_drives_other_crew": "На вибрану дату працівник уже є водієм іншого екіпажу.",
        "error_worker_day_off": "На вибрану дату працівник має вихідний.",
        "error_worker_absent_from_crew": "На вибрану дату працівник позначений відсутнім у цьому екіпажі.",
        "error_worker_is_crew_driver": "Водія цього екіпажу не можна призначити його пасажиром.",
        "error_driver_shift_conflict": "Водій уже має іншу зміну, що перетинається в часі.",
        "error_legacy_driver_or_vehicle_already_assigned": "Водій або автомобіль досі зайнятий у попередньому транспортному призначенні.",
        "error_driver_or_vehicle_already_assigned": "Водій або автомобіль уже закріплений за іншим екіпажем.",
        "error_passenger_scope_invalid": "Виберіть увесь графік екіпажу або лише позначені дні.",
        "error_selected_schedule_days_have_no_shifts": "У вибраних днях немає змін, які можна редагувати.",
        "error_replacement_driver_not_in_crew": "Нового постійного водія можна вибрати лише з пасажирів цього екіпажу.",
        "error_replacement_driver_shift_conflict": "Новий водій має зміну, що перетинається, в іншому екіпажі.",
    },
}


RESET_OBJECT_COPY = {
    "ru": {
        "project_crews": "Новые тестовые экипажи", "transport_crews": "Старые экипажи",
        "scheduled_work_shifts": "Плановые смены",
        "transport_passenger_assignments": "Пассажиры старых маршрутов",
        "route_stops": "Остановки старых маршрутов", "transport_routes": "Маршруты",
        "driver_vehicle_assignments": "Назначения водителей на автомобили",
        "worker_project_assignments": "Назначения работников на проекты",
        "project_schedule_templates": "Шаблоны графиков проектов", "work_projects": "Проекты",
        "worksites": "Объекты работы", "scheduled_shift_batches": "Пакеты смен",
        "shift_templates": "Старые шаблоны смен", "workers": "Работники",
        "housing_sites": "Объекты жилья", "housing_rooms": "Комнаты",
        "housing_places": "Места в комнатах", "housing_assignments": "Заселения",
        "vehicles": "Автомобили", "work_time_entries": "Фактические записи рабочего времени",
    },
    "en": {
        "project_crews": "New preview crews", "transport_crews": "Legacy crews",
        "scheduled_work_shifts": "Planned shifts",
        "transport_passenger_assignments": "Legacy route passengers",
        "route_stops": "Legacy route stops", "transport_routes": "Routes",
        "driver_vehicle_assignments": "Driver/vehicle assignments",
        "worker_project_assignments": "Worker/project assignments",
        "project_schedule_templates": "Project schedule templates", "work_projects": "Projects",
        "worksites": "Worksites", "scheduled_shift_batches": "Shift batches",
        "shift_templates": "Legacy shift templates", "workers": "Workers",
        "housing_sites": "Housing sites", "housing_rooms": "Rooms",
        "housing_places": "Room places", "housing_assignments": "Housing assignments",
        "vehicles": "Vehicles", "work_time_entries": "Factual work-time entries",
    },
    "pl": {
        "project_crews": "Nowe ekipy testowe", "transport_crews": "Stare ekipy",
        "scheduled_work_shifts": "Planowane zmiany",
        "transport_passenger_assignments": "Pasażerowie starych tras",
        "route_stops": "Przystanki starych tras", "transport_routes": "Trasy",
        "driver_vehicle_assignments": "Przypisania kierowców do pojazdów",
        "worker_project_assignments": "Przypisania pracowników do projektów",
        "project_schedule_templates": "Szablony grafików projektów", "work_projects": "Projekty",
        "worksites": "Miejsca pracy", "scheduled_shift_batches": "Pakiety zmian",
        "shift_templates": "Stare szablony zmian", "workers": "Pracownicy",
        "housing_sites": "Obiekty mieszkalne", "housing_rooms": "Pokoje",
        "housing_places": "Miejsca w pokojach", "housing_assignments": "Zakwaterowania",
        "vehicles": "Pojazdy", "work_time_entries": "Faktyczne wpisy czasu pracy",
    },
    "uk": {
        "project_crews": "Нові тестові екіпажі", "transport_crews": "Старі екіпажі",
        "scheduled_work_shifts": "Заплановані зміни",
        "transport_passenger_assignments": "Пасажири старих маршрутів",
        "route_stops": "Зупинки старих маршрутів", "transport_routes": "Маршрути",
        "driver_vehicle_assignments": "Призначення водіїв на автомобілі",
        "worker_project_assignments": "Призначення працівників на проєкти",
        "project_schedule_templates": "Шаблони графіків проєктів", "work_projects": "Проєкти",
        "worksites": "Об'єкти роботи", "scheduled_shift_batches": "Пакети змін",
        "shift_templates": "Старі шаблони змін", "workers": "Працівники",
        "housing_sites": "Об'єкти житла", "housing_rooms": "Кімнати",
        "housing_places": "Місця в кімнатах", "housing_assignments": "Заселення",
        "vehicles": "Автомобілі", "work_time_entries": "Фактичні записи робочого часу",
    },
}


def _display_name(connection):
    user = connection.candidate
    return user.get_full_name().strip() or user.email or user.username


def _copy(request):
    language = get_lang(request)
    if language not in COPY:
        language = "ru"
    return {**COPY[language], **PROJECT_CREW_ERROR_COPY[language]}


def _validation_message(error, copy=None):
    detail = getattr(error, "detail", None)
    if isinstance(detail, dict):
        code = detail.get("code")
        if code and copy:
            localized = copy.get(f"error_{code}")
            if localized:
                return localized
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


def _selected_month(request):
    raw = (request.GET.get("month") or request.POST.get("return_month") or "").strip()
    try:
        selected = date.fromisoformat(f"{raw}-01") if raw else timezone.localdate().replace(day=1)
    except ValueError:
        selected = timezone.localdate().replace(day=1)
    return selected.replace(day=1)


def _shift_month(value, offset):
    month_index = value.year * 12 + value.month - 1 + offset
    return date(month_index // 12, month_index % 12 + 1, 1)


def _crew_calendar(crew, *, selected_month, today, driver_absence_dates=None):
    driver_absence_dates = set(driver_absence_dates or ())
    shifts = [
        shift
        for shift in crew.calendar_shifts.all()
        if shift.state == ProjectCrewShift.STATE_PUBLISHED
        and shift.work_date.year == selected_month.year
        and shift.work_date.month == selected_month.month
    ]
    shifts_by_day = {shift.work_date: shift for shift in shifts}
    first_weekday, days_in_month = monthrange(selected_month.year, selected_month.month)
    days = [None] * first_weekday
    for number in range(1, days_in_month + 1):
        work_date = selected_month.replace(day=number)
        shift = shifts_by_day.get(work_date)
        has_no_driver = bool(
            shift
            and not any(
                member.role == ProjectCrewShiftMember.ROLE_DRIVER
                for member in shift.members.all()
            )
        )
        days.append(
            {
                "date": work_date,
                "shift": shift,
                "is_today": work_date == today,
                "has_published": shift is not None,
                "has_no_driver": has_no_driver,
                "is_driver_absence": work_date in driver_absence_dates,
            }
        )
    return days, shifts


def _scoped_connections(request, organization):
    return worker_connection_queryset_for(
        user=request.user,
        organization=organization,
        queryset=SupportConnection.objects.filter(
            organization=organization,
            is_archived=False,
        ).select_related("candidate"),
    )


def _project_context(request, organization, project, *, selected_month):
    today = timezone.localdate()
    copy = _copy(request)
    selected_month_end = selected_month.replace(
        day=monthrange(selected_month.year, selected_month.month)[1]
    )
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
                .prefetch_related(
                    Prefetch(
                        "members",
                        queryset=ProjectCrewShiftMember.objects.select_related(
                            "connection__candidate",
                        ).order_by("role", "connection__candidate__first_name", "id"),
                    )
                )
                .order_by("work_date"),
            ),
            Prefetch(
                "member_absences",
                queryset=ProjectCrewMemberAbsence.objects.filter(
                    work_date__range=(selected_month, selected_month_end),
                ).select_related("connection__candidate").order_by(
                    "work_date", "connection_id"
                ),
            ),
            Prefetch(
                "driver_substitutions",
                queryset=ProjectCrewDriverSubstitution.objects.select_related(
                    "primary_driver_connection__candidate",
                    "substitute_driver_connection__candidate",
                    "vehicle",
                ).order_by("-work_date", "-created_at", "-id"),
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
    legacy_resources = DriverVehicleAssignment.objects.filter(
        organization=organization,
        state__in=(
            DriverVehicleAssignment.STATE_DRAFT,
            DriverVehicleAssignment.STATE_PUBLISHED,
        ),
    ).filter(Q(ends_on__isnull=True) | Q(ends_on__gte=today))
    used_vehicle_ids.update(
        legacy_resources.values_list("vehicle_id", flat=True)
    )
    used_driver_ids.update(
        legacy_resources.values_list("driver_connection_id", flat=True)
    )
    crew_shift_dates = {
        shift.work_date
        for crew in crews
        for shift in crew.calendar_shifts.all()
    }
    days_off_by_connection = {}
    if crew_shift_dates:
        for connection_id, work_date in WorkerScheduleDayOff.objects.filter(
            organization=organization,
            connection__in=connections,
            work_date__in=crew_shift_dates,
        ).values_list("connection_id", "work_date"):
            days_off_by_connection.setdefault(connection_id, set()).add(work_date)
    for crew in crews:
        absence_dates_by_connection = {}
        for absence in crew.member_absences.all():
            absence_dates_by_connection.setdefault(
                absence.connection_id,
                set(),
            ).add(absence.work_date)
        crew.current_resource = next(
            (
                resource for resource in crew.resource_assignments.all()
                if resource.starts_on <= today and (resource.ends_on is None or resource.ends_on >= today)
            ),
            crew.resource_assignments.all()[0] if crew.resource_assignments.all() else None,
        )
        if crew.current_resource:
            crew.current_resource.driver_name = _display_name(crew.current_resource.driver_connection)
            driver_days_off = sorted(
                days_off_by_connection.get(
                    crew.current_resource.driver_connection_id,
                    set(),
                )
                & {shift.work_date for shift in crew.calendar_shifts.all()}
            )
            crew.current_resource.day_off_dates_label = ", ".join(
                item.strftime("%d.%m") for item in driver_days_off
            )
            driver_absence_dates = sorted(
                absence_dates_by_connection.get(
                    crew.current_resource.driver_connection_id,
                    set(),
                )
            )
            crew.current_resource.absence_dates_label = ", ".join(
                item.strftime("%d.%m") for item in driver_absence_dates
            )
            crew.driver_absence_dates = driver_absence_dates
            crew.future_driver_absence_dates = [
                item for item in driver_absence_dates if item >= today
            ]
            crew.driver_absence_date_values = {
                item.isoformat() for item in driver_absence_dates
            }
        else:
            crew.driver_absence_dates = []
            crew.future_driver_absence_dates = []
            crew.driver_absence_date_values = set()

        active_substitutions = [
            item
            for item in crew.driver_substitutions.all()
            if item.state == ProjectCrewDriverSubstitution.STATE_ACTIVE
            and item.work_date >= today
        ]
        substitution_groups = {}
        for substitution in active_substitutions:
            group = substitution_groups.setdefault(
                substitution.substitute_driver_connection_id,
                {
                    "connection": substitution.substitute_driver_connection,
                    "display_name": _display_name(
                        substitution.substitute_driver_connection
                    ),
                    "work_dates": [],
                },
            )
            group["work_dates"].append(substitution.work_date)
        crew.current_substitution_groups = []
        for group in substitution_groups.values():
            group["work_dates"] = sorted(set(group["work_dates"]))
            group["dates_label"] = ", ".join(
                item.strftime("%d.%m") for item in group["work_dates"]
            )
            crew.current_substitution_groups.append(group)
        substitution_state_labels = {
            ProjectCrewDriverSubstitution.STATE_ACTIVE: copy["substitution_active"],
            ProjectCrewDriverSubstitution.STATE_REPLACED: copy["substitution_replaced"],
            ProjectCrewDriverSubstitution.STATE_CANCELLED: copy["substitution_cancelled"],
        }
        crew.substitution_history = [
            {
                "work_date": item.work_date,
                "primary_driver_name": _display_name(
                    item.primary_driver_connection
                ),
                "substitute_driver_name": _display_name(
                    item.substitute_driver_connection
                ),
                "vehicle": item.vehicle,
                "state_label": substitution_state_labels[item.state],
            }
            for item in crew.driver_substitutions.all()
        ]
        crew.open_passengers = list(crew.passenger_assignments.all())
        roster_by_connection = {
            passenger.connection_id: passenger for passenger in crew.open_passengers
        }
        member_dates = {}
        member_connections = {}
        for shift in crew.calendar_shifts.all():
            for member in shift.members.all():
                if member.role != ProjectCrewShiftMember.ROLE_PASSENGER:
                    continue
                member_connections[member.connection_id] = member.connection
                member_dates.setdefault(member.connection_id, []).append(shift.work_date)
        crew.display_passengers = []
        for connection_id in sorted(
            set(roster_by_connection) | set(member_connections),
            key=lambda item: _display_name(
                roster_by_connection[item].connection
                if item in roster_by_connection
                else member_connections[item]
            ),
        ):
            connection = (
                roster_by_connection[connection_id].connection
                if connection_id in roster_by_connection
                else member_connections[connection_id]
            )
            dates = sorted(set(member_dates.get(connection_id, [])))
            day_off_dates = sorted(
                days_off_by_connection.get(connection_id, set())
                & {shift.work_date for shift in crew.calendar_shifts.all()}
            )
            excluded_dates = sorted(
                absence_dates_by_connection.get(connection_id, set())
                - set(day_off_dates)
            )
            roster_entry = roster_by_connection.get(connection_id)
            if roster_entry is not None and not excluded_dates:
                # Compatibility for data created before explicit absence
                # records existed. New releases always use the records above.
                assigned_dates = set(dates)
                excluded_dates = [
                    shift.work_date
                    for shift in crew.calendar_shifts.all()
                    if shift.work_date >= roster_entry.starts_on
                    and (
                        roster_entry.ends_on is None
                        or shift.work_date <= roster_entry.ends_on
                    )
                    and shift.work_date not in assigned_dates
                    and shift.work_date not in day_off_dates
                ]
            crew.display_passengers.append(
                {
                    "connection": connection,
                    "display_name": _display_name(connection),
                    "scope": (
                        PASSENGER_SCOPE_FUTURE
                        if connection_id in roster_by_connection
                        else PASSENGER_SCOPE_SELECTED
                    ),
                    "work_dates": dates,
                    "dates_label": ", ".join(item.strftime("%d.%m") for item in dates),
                    "excluded_dates": excluded_dates,
                    "excluded_dates_label": ", ".join(
                        item.strftime("%d.%m") for item in excluded_dates
                    ),
                    "day_off_dates": day_off_dates,
                    "day_off_dates_label": ", ".join(
                        item.strftime("%d.%m") for item in day_off_dates
                    ),
                }
            )
        # A passenger assigned only to particular calendar days must remain in
        # the picker so the employer can add the same person to other selected
        # days later. Only the permanent/future roster and the current driver
        # are unavailable here.
        unavailable_passenger_ids = set(roster_by_connection)
        if crew.current_resource:
            unavailable_passenger_ids.add(
                crew.current_resource.driver_connection_id
            )
        crew.available_passengers = [
            connection
            for connection in connections
            if connection.id not in unavailable_passenger_ids
        ]
        crew.published_shifts = list(crew.calendar_shifts.all())
        crew.calendar_days, crew.month_shifts = _crew_calendar(
            crew,
            selected_month=selected_month,
            today=today,
            driver_absence_dates=crew.driver_absence_dates,
        )
        crew.schedule_example = (
            crew.month_shifts[0]
            if crew.month_shifts
            else (crew.published_shifts[0] if crew.published_shifts else None)
        )
        crew.occupied = 1 + max(
            (shift.members.filter(role=ProjectCrewShiftMember.ROLE_PASSENGER).count()
             for shift in crew.calendar_shifts.all()),
            default=len(crew.open_passengers),
        )
        crew.passenger_driver_options = [
            item for item in crew.display_passengers if item["connection"].has_driving_license
        ]
        substitute_options = {}
        for work_date in crew.future_driver_absence_dates:
            try:
                available = project_crew_substitute_driver_candidates(
                    crew=crew,
                    work_dates=[work_date],
                    candidate_connections=connections,
                )
            except ValidationError:
                available = []
            for candidate in available:
                option = substitute_options.setdefault(
                    candidate.id,
                    {
                        "connection": candidate,
                        "display_name": _display_name(candidate),
                        "is_current_crew_passenger": candidate.is_current_crew_passenger,
                        "work_dates": [],
                    },
                )
                option["work_dates"].append(work_date)
        crew.substitute_driver_options = sorted(
            substitute_options.values(),
            key=lambda item: (
                not item["is_current_crew_passenger"],
                item["display_name"].casefold(),
                item["connection"].id,
            ),
        )
        for option in crew.substitute_driver_options:
            option["available_dates_value"] = ",".join(
                item.isoformat() for item in sorted(set(option["work_dates"]))
            )

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
        effective_on = parse_date(request.POST.get("effective_on") or "") or timezone.localdate()
        if action == "passenger_add" and scope == PASSENGER_SCOPE_FUTURE:
            # "Entire crew schedule" starts with the crew's first resource
            # assignment, so existing published days and later days use the
            # same passenger roster without requiring calendar selection.
            effective_on = (
                crew.resource_assignments.order_by("starts_on")
                .values_list("starts_on", flat=True)
                .first()
                or effective_on
            )
        kwargs = {
            "actor": request.user,
            "crew": crew,
            "connection": connection,
            "scope": scope,
            "selected_dates": _parse_dates(request),
            "effective_on": effective_on,
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
    if action == "driver_substitute":
        substitute = get_object_or_404(
            _scoped_connections(request, organization),
            public_id=request.POST.get("driver_id"),
            has_driving_license=True,
        )
        assign_project_crew_substitute_driver(
            actor=request.user,
            crew=crew,
            substitute_driver_connection=substitute,
            work_dates=_parse_dates(request),
        )
        return copy["substitute_assigned"]
    raise ValidationError({"message": "Unknown project crew operation."})


@login_required(login_url="employer:login")
def project_first_workspace(request, project_public_id=None):
    if not is_project_first_workspace_enabled():
        raise Http404("project_first_workspace_not_available")
    memberships, membership, permissions = _selected_organization(request)
    organization = membership.organization
    copy = _copy(request)
    selected_month = _selected_month(request)
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
                messages.error(request, _validation_message(error, copy))
            else:
                messages.success(request, success)
            return redirect(
                _workspace_url(
                    organization,
                    project=project,
                    month=(request.POST.get("return_month") or selected_month.strftime("%Y-%m")),
                )
            )

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
        "selected_month": selected_month,
        "calendar_month": selected_month.strftime("%Y-%m"),
        "calendar_weekday_labels": {
            "ru": ("ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"),
            "en": ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"),
            "pl": ("PN", "WT", "ŚR", "CZW", "PT", "SOB", "ND"),
            "uk": ("ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "НД"),
        }.get(get_lang(request), ("ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС")),
        "reset_plan_url": f"{reverse('support:project-first-reset-plan')}?{urlencode({'organization': organization.public_id})}",
    }
    if project is not None:
        context.update(
            {
                "calendar_previous_url": _workspace_url(
                    organization,
                    project=project,
                    month=_shift_month(selected_month, -1).strftime("%Y-%m"),
                ),
                "calendar_next_url": _workspace_url(
                    organization,
                    project=project,
                    month=_shift_month(selected_month, 1).strftime("%Y-%m"),
                ),
                "calendar_today_url": _workspace_url(
                    organization,
                    project=project,
                    month=timezone.localdate().strftime("%Y-%m"),
                ),
            }
        )
        context.update(
            _project_context(
                request,
                organization,
                project,
                selected_month=selected_month,
            )
        )
    return render(request, "support/project_first_workspace.html", context)


@login_required(login_url="employer:login")
def project_first_reset_plan(request):
    """Show the organization-specific cutover report without changing data."""

    if not is_project_first_workspace_enabled():
        raise Http404("project_first_workspace_not_available")
    memberships, membership, permissions = _selected_organization(request)
    organization = membership.organization
    copy = _copy(request)
    lang = get_lang(request)
    object_copy = RESET_OBJECT_COPY.get(lang, RESET_OBJECT_COPY["ru"])
    include_work_time = True
    plan = build_project_first_reset_plan(
        organization,
        include_work_time=include_work_time,
    )
    if request.method == "POST":
        if not membership.is_owner or not request.user.is_staff:
            raise Http404("project_first_reset_not_available")
        if not getattr(settings, "SUPPORT_PROJECT_FIRST_RESET_ALLOWED", False):
            messages.error(request, copy["reset_guard_disabled"])
        elif request.POST.get("confirmation", "") != plan["confirmation"]:
            messages.error(request, copy["reset_confirmation_invalid"])
        else:
            try:
                execute_project_first_reset(
                    organization=organization,
                    actor=request.user,
                    include_work_time=include_work_time,
                )
            except ProjectFirstResetError as error:
                messages.error(request, str(error))
            else:
                messages.success(request, copy["reset_complete"])
        return redirect(
            f"{reverse('support:project-first-reset-plan')}?"
            f"{urlencode({'organization': organization.public_id})}"
        )
    context = {
        "pf": copy,
        "organization": organization,
        "memberships": memberships,
        "membership": membership,
        "permissions": permissions,
        "delete_items": [
            {"key": key, "label": object_copy[key], "count": count}
            for key, count in plan["delete_counts"].items()
        ],
        "preserve_items": [
            {"key": key, "label": object_copy[key], "count": count}
            for key, count in plan["preserve_counts"].items()
        ],
        "confirmation": plan["confirmation"],
        "can_apply_reset": (
            membership.is_owner
            and request.user.is_staff
            and getattr(settings, "SUPPORT_PROJECT_FIRST_RESET_ALLOWED", False)
        ),
        "project_list_url": _workspace_url(organization),
    }
    return render(request, "support/project_first_reset_plan.html", context)
