"""Stable vocabulary and presentation helpers for candidate questionnaire v2."""

from datetime import date


QUESTIONNAIRE_VERSION = "support-questionnaire-v2"

LEGAL_STATUSES = (
    "visa_free",
    "polish_work_visa",
    "other_eu_visa",
    "temporary_residence",
    "permanent_residence",
    "eu_citizen",
    "other",
    "needs_consultation",
)
DURATION_CHOICES = ("under_1m", "1_3m", "3_6m", "6_12m", "over_1y", "permanent", "unsure")
EXPERIENCE_SECTORS = (
    "warehouse",
    "manufacturing",
    "food_industry",
    "agriculture",
    "greenhouse",
    "construction",
    "cleaning",
    "logistics",
    "driver",
    "machine_operator",
    "no_experience",
    "other",
)
EXPERIENCE_DURATIONS = ("none", "under_6m", "6_12m", "1_3y", "over_3y")
LANGUAGE_LEVELS = ("none", "words", "instructions", "conversation", "fluent")
DRIVING_CATEGORIES = ("B", "BE", "C", "CE", "D")
DRIVING_EXPERIENCE = ("none", "under_1y", "1_3y", "over_3y")
QUALIFICATIONS = (
    "forklift",
    "reach_truck",
    "aerial_platform",
    "tractor",
    "excavator",
    "welding",
    "electrical",
    "other",
)
WORK_CONDITIONS = (
    "standing",
    "repetitive",
    "lifting",
    "cold",
    "outdoor",
    "night",
    "long_shift",
    "height",
)
CONDITION_ANSWERS = ("yes", "no", "discuss")
SHIFT_PREFERENCES = ("day", "evening", "night", "weekend", "rotating")
THREE_WAY_ANSWERS = ("yes", "no", "discuss")

_LABELS_RU = {
    "visa_free": "Безвизовый режим", "polish_work_visa": "Польская рабочая виза",
    "other_eu_visa": "Виза другой страны ЕС", "temporary_residence": "Временный ВНЖ",
    "permanent_residence": "Постоянный ВНЖ", "eu_citizen": "Гражданство ЕС",
    "other": "Другое", "needs_consultation": "Нужна консультация",
    "under_1m": "До 1 месяца", "1_3m": "1–3 месяца", "3_6m": "3–6 месяцев",
    "6_12m": "6–12 месяцев", "over_1y": "Более года", "permanent": "Постоянно",
    "unsure": "Не определился", "warehouse": "Склад", "manufacturing": "Производство",
    "food_industry": "Пищевая промышленность", "agriculture": "Сельское хозяйство",
    "greenhouse": "Теплицы", "construction": "Строительство", "cleaning": "Уборка",
    "logistics": "Логистика", "driver": "Водитель", "machine_operator": "Оператор техники",
    "no_experience": "Без опыта", "none": "Нет", "under_6m": "До 6 месяцев",
    "6_12m": "6–12 месяцев", "1_3y": "1–3 года", "over_3y": "Более 3 лет",
    "words": "Отдельные слова", "instructions": "Понимает рабочие инструкции",
    "conversation": "Простой разговор", "fluent": "Свободно", "forklift": "Погрузчик",
    "reach_truck": "Ричтрак", "aerial_platform": "Подъёмная платформа", "tractor": "Трактор",
    "excavator": "Экскаватор", "welding": "Сварочные допуски", "electrical": "Электродопуски",
    "day": "Дневные", "evening": "Вечерние", "night": "Ночные", "weekend": "Выходные",
    "rotating": "Сменный график", "yes": "Да", "no": "Нет", "discuss": "Обсудить",
    "standing": "Работа стоя", "repetitive": "Повторяющиеся операции", "lifting": "Подъём тяжестей",
    "cold": "Работа в холоде", "outdoor": "Работа на улице", "long_shift": "Длинная смена",
    "height": "Работа на высоте", "under_1y": "До 1 года", "over_1y": "Более 1 года",
}

_LABELS_EN = {
    "visa_free": "Visa-free stay", "polish_work_visa": "Polish work visa",
    "other_eu_visa": "Visa issued by another EU country", "temporary_residence": "Temporary residence",
    "permanent_residence": "Permanent residence", "eu_citizen": "EU citizenship",
    "other": "Other", "needs_consultation": "Needs consultation", "under_1m": "Under 1 month",
    "1_3m": "1–3 months", "3_6m": "3–6 months", "6_12m": "6–12 months",
    "over_1y": "Over 1 year", "permanent": "Permanent", "unsure": "Not decided",
    "warehouse": "Warehouse", "manufacturing": "Manufacturing", "food_industry": "Food industry",
    "agriculture": "Agriculture", "greenhouse": "Greenhouse", "construction": "Construction",
    "cleaning": "Cleaning", "logistics": "Logistics", "driver": "Driver",
    "machine_operator": "Machine operator", "no_experience": "No experience", "none": "None",
    "under_6m": "Under 6 months", "1_3y": "1–3 years", "over_3y": "Over 3 years",
    "words": "Basic words", "instructions": "Understands work instructions",
    "conversation": "Basic conversation", "fluent": "Fluent", "forklift": "Forklift",
    "reach_truck": "Reach truck", "aerial_platform": "Aerial platform", "tractor": "Tractor",
    "excavator": "Excavator", "welding": "Welding certificate", "electrical": "Electrical certificate",
    "day": "Day", "evening": "Evening", "night": "Night", "weekend": "Weekend",
    "rotating": "Rotating", "yes": "Yes", "no": "No", "discuss": "Discuss",
    "standing": "Standing work", "repetitive": "Repetitive work", "lifting": "Lifting",
    "cold": "Cold environment", "outdoor": "Outdoor work", "long_shift": "Long shift",
    "height": "Work at height", "under_1y": "Under 1 year",
}

# Polish and Ukrainian labels cover the complete fixed vocabulary used by the
# candidate and manager. Unknown future codes remain visible as safe fallbacks.
_LABELS_PL = {
    **_LABELS_EN,
    "visa_free": "Ruch bezwizowy", "polish_work_visa": "Polska wiza pracownicza",
    "other_eu_visa": "Wiza innego kraju UE", "temporary_residence": "Pobyt czasowy",
    "permanent_residence": "Pobyt stały", "eu_citizen": "Obywatelstwo UE",
    "other": "Inne", "needs_consultation": "Potrzebna konsultacja", "under_1m": "Do 1 miesiąca",
    "1_3m": "1–3 miesiące", "3_6m": "3–6 miesięcy", "6_12m": "6–12 miesięcy",
    "over_1y": "Ponad rok", "permanent": "Na stałe", "unsure": "Jeszcze nie wiem",
    "warehouse": "Magazyn", "manufacturing": "Produkcja", "food_industry": "Przemysł spożywczy",
    "agriculture": "Rolnictwo", "greenhouse": "Szklarnie", "construction": "Budownictwo",
    "cleaning": "Sprzątanie", "logistics": "Logistyka", "driver": "Kierowca",
    "machine_operator": "Operator maszyn", "no_experience": "Bez doświadczenia", "none": "Brak",
    "under_6m": "Do 6 miesięcy", "1_3y": "1–3 lata", "over_3y": "Ponad 3 lata",
    "words": "Pojedyncze słowa", "instructions": "Rozumie instrukcje w pracy",
    "conversation": "Prosta rozmowa", "fluent": "Biegle", "forklift": "Wózek widłowy",
    "reach_truck": "Reach truck", "yes": "Tak", "no": "Nie", "discuss": "Do omówienia",
    "day": "Dzienna", "evening": "Wieczorna", "night": "Nocna", "weekend": "Weekend",
    "rotating": "Zmianowa",
}

_LABELS_UK = {
    **_LABELS_RU,
    "visa_free": "Безвізовий режим", "polish_work_visa": "Польська робоча віза",
    "other_eu_visa": "Віза іншої країни ЄС", "temporary_residence": "Тимчасовий ВНП",
    "permanent_residence": "Постійний ВНП", "eu_citizen": "Громадянство ЄС",
    "other": "Інше", "needs_consultation": "Потрібна консультація", "under_1m": "До 1 місяця",
    "1_3m": "1–3 місяці", "3_6m": "3–6 місяців", "6_12m": "6–12 місяців",
    "over_1y": "Понад рік", "permanent": "Постійно", "unsure": "Ще не вирішив",
    "warehouse": "Склад", "manufacturing": "Виробництво", "food_industry": "Харчова промисловість",
    "agriculture": "Сільське господарство", "greenhouse": "Теплиці", "construction": "Будівництво",
    "cleaning": "Прибирання", "logistics": "Логістика", "driver": "Водій",
    "machine_operator": "Оператор техніки", "no_experience": "Без досвіду", "none": "Немає",
    "yes": "Так", "no": "Ні", "discuss": "Обговорити", "day": "Денні",
    "evening": "Вечірні", "night": "Нічні", "weekend": "Вихідні", "rotating": "Змінний графік",
}

_LABELS = {"ru": _LABELS_RU, "en": _LABELS_EN, "pl": _LABELS_PL, "uk": _LABELS_UK}


def label(value, language="ru"):
    """Return a manager-friendly label; codes remain readable as a safe fallback."""

    # The employer cabinet already translates its surrounding UI. Keeping the
    # stable answer meaning visible is preferable to hiding a new/unknown code.
    return _LABELS.get(language, _LABELS_RU).get(value, value or "—")


def questionnaire_is_complete(answers):
    required = (
        "adult_confirmed", "legal_status", "current_city", "available_from",
        "planned_duration", "experience_sectors", "experience_duration",
        "english_level", "polish_level", "dutch_level", "work_conditions",
        "shift_preferences", "needs_housing", "needs_transport",
        "safety_policy_accepted",
    )
    return bool(answers) and all(key in answers and answers[key] not in (None, "", []) for key in required)


def application_matches_filters(application, filters):
    answers = application.questionnaire_answers or {}
    if filters.get("legal_status") and answers.get("legal_status") != filters["legal_status"]:
        return False
    if filters.get("duration") and answers.get("planned_duration") != filters["duration"]:
        return False
    if filters.get("experience") and filters["experience"] not in answers.get("experience_sectors", []):
        return False
    if filters.get("license"):
        categories = answers.get("driving_license_categories", [])
        if filters["license"] == "any":
            if not answers.get("has_driving_license"):
                return False
        elif filters["license"] not in categories:
            return False
    for key in ("needs_housing", "needs_transport", "travelling_with_partner"):
        requested = filters.get(key)
        if requested in {"yes", "no"} and bool(answers.get(key)) != (requested == "yes"):
            return False
    if filters.get("complete") == "yes" and not questionnaire_is_complete(answers):
        return False
    if filters.get("complete") == "no" and questionnaire_is_complete(answers):
        return False
    available_by = filters.get("available_by")
    if available_by:
        try:
            if date.fromisoformat(answers.get("available_from", "")) > date.fromisoformat(available_by):
                return False
        except (TypeError, ValueError):
            return False
    minimum_language = filters.get("english_level")
    if minimum_language:
        rank = {value: index for index, value in enumerate(LANGUAGE_LEVELS)}
        if rank.get(answers.get("english_level"), -1) < rank.get(minimum_language, 0):
            return False
    return True


def questionnaire_tags(application, language="ru"):
    answers = application.questionnaire_answers or {}
    texts = {
        "ru": ("Готов", "Права", "есть", "Погрузчик", "Нужно жильё", "Нужен транспорт", "Семейная пара", "Нужно уточнение"),
        "en": ("Available", "Licence", "yes", "Forklift", "Needs housing", "Needs transport", "Couple", "Needs clarification"),
        "pl": ("Gotowy", "Prawo jazdy", "tak", "Wózek widłowy", "Potrzebuje mieszkania", "Potrzebuje transportu", "Para", "Wymaga wyjaśnienia"),
        "uk": ("Готовий", "Права", "є", "Навантажувач", "Потрібне житло", "Потрібен транспорт", "Сімейна пара", "Потрібне уточнення"),
    }[language if language in _LABELS else "ru"]
    tags = []
    if answers.get("legal_status"):
        tags.append(label(answers["legal_status"], language))
    if answers.get("available_from"):
        tags.append(f"{texts[0]}: {answers['available_from']}")
    if answers.get("planned_duration"):
        tags.append(label(answers["planned_duration"], language))
    if answers.get("english_level") and answers["english_level"] != "none":
        tags.append(f"EN: {label(answers['english_level'], language)}")
    if answers.get("has_driving_license"):
        categories = ", ".join(answers.get("driving_license_categories", []))
        tags.append(f"{texts[1]}: {categories or texts[2]}")
    if "forklift" in answers.get("qualifications", []):
        tags.append(texts[3])
    if answers.get("needs_housing"):
        tags.append(texts[4])
    if answers.get("needs_transport"):
        tags.append(texts[5])
    if answers.get("travelling_with_partner"):
        tags.append(texts[6])
    if not questionnaire_is_complete(answers):
        tags.append(texts[7])
    return tags
