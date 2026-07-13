from copy import deepcopy
import json

from django import template
from django.utils.safestring import mark_safe
from django.utils.translation import get_language

from jobs.web_choice_labels import (
    CATEGORY_LABELS,
    COUNTRY_LABELS,
    EMPLOYMENT_LABELS,
    EXPERIENCE_LABELS,
    HOUSING_LABELS,
    SALARY_TAX_LABELS,
    SOURCE_LABELS,
)


register = template.Library()


TEXT = {
    "en": {
        "console": "Operator console",
        "dashboard": "Dashboard",
        "vacancies": "Vacancies",
        "complaints": "Complaints",
        "users": "Users",
        "menu": "Menu",
        "language": "Language",
        "daily_work": "Daily work",
        "daily_work_note": "The queues you use most often.",
        "all_tools": "All tools",
        "all_tools_note": "Open a group only when you need it.",
        "recent_activity": "Recent activity",
        "no_activity": "No recent operator actions.",
        "open": "Open",
        "add": "Add",
        "view_only": "View only",
        "editable": "Editable",
        "tools": "tools",
        "workspace": "Moderation workspace",
        "workspace_note": "Review vacancies, resolve reports, and manage access from one compact console.",
        "dashboard_breadcrumb": "Moderation and support",
        "moderation": "Moderation and safety",
        "moderation_note": "Vacancies, complaints, blocks, deletion requests, and review history.",
        "communication": "Chats and notifications",
        "communication_note": "Conversations, reports, devices, and notification delivery.",
        "people": "People and access",
        "people_note": "Users, profiles, verification, permissions, and login tokens.",
        "commerce": "Economy and subscriptions",
        "commerce_note": "Wallets, products, purchases, subscriptions, and contact access.",
        "publishing": "Publishing and reach",
        "publishing_note": "JobHub publishing permissions, alerts, and employer-side tools.",
        "other": "Other tools",
        "other_note": "Less frequently used administration tools.",
        "show_more": "More",
        "show_less": "Less",
        "filters": "Filters",
        "close": "Close",
        "empty": "No records found.",
    },
    "ru": {
        "console": "Панель оператора",
        "dashboard": "Главная",
        "vacancies": "Вакансии",
        "complaints": "Жалобы",
        "users": "Пользователи",
        "menu": "Меню",
        "language": "Язык",
        "daily_work": "Рабочие разделы",
        "daily_work_note": "Основные очереди для ежедневной работы.",
        "all_tools": "Все инструменты",
        "all_tools_note": "Открывайте только нужную группу — без длинного списка на экране.",
        "recent_activity": "Последние действия",
        "no_activity": "Последних действий пока нет.",
        "open": "Открыть",
        "add": "Добавить",
        "view_only": "Просмотр",
        "editable": "Редактирование",
        "tools": "инструментов",
        "workspace": "Рабочее место модератора",
        "workspace_note": "Проверяйте вакансии, разбирайте жалобы и управляйте доступом в одной компактной панели.",
        "dashboard_breadcrumb": "Модерация и поддержка",
        "moderation": "Модерация и безопасность",
        "moderation_note": "Вакансии, жалобы, блокировки, удаление аккаунтов и история проверок.",
        "communication": "Чаты и уведомления",
        "communication_note": "Диалоги, жалобы на чаты, устройства и доставка уведомлений.",
        "people": "Пользователи и доступ",
        "people_note": "Аккаунты, профили, проверки, права и токены входа.",
        "commerce": "Экономика и подписки",
        "commerce_note": "Кошельки, товары, покупки, подписки и открытие контактов.",
        "publishing": "Публикации и охват",
        "publishing_note": "Разрешения JobHub, оповещения о вакансиях и инструменты работодателей.",
        "other": "Другие инструменты",
        "other_note": "Редко используемые административные разделы.",
        "show_more": "Ещё",
        "show_less": "Скрыть",
        "filters": "Фильтры",
        "close": "Закрыть",
        "empty": "Записи не найдены.",
    },
}


MODEL_LABELS = {
    "en": {
        "Vacancy": "Vacancies",
        "VacancyModerationAttempt": "Moderation attempts",
        "Complaint": "Complaints",
        "ComplaintActionLog": "Complaint history",
        "AccountDeletionRequest": "Account deletion",
        "UserBlock": "User blocks",
        "UnlockRequest": "Unlock requests",
        "VacancyReview": "Vacancy reviews",
        "ChatConversation": "Conversations",
        "ChatMessage": "Chat messages",
        "ChatReport": "Chat reports",
        "PushDevice": "Push devices",
        "ModeratorNotificationDelivery": "Moderator notifications",
        "VacancyAlertSubscription": "Vacancy alerts",
        "VacancyAlertDelivery": "Alert delivery",
        "User": "Users",
        "Group": "Permission groups",
        "UserProfile": "User profiles",
        "PhoneVerification": "Phone verification",
        "PhoneVerificationAttempt": "Phone verification attempts",
        "EmailVerification": "Email verification",
        "TokenProxy": "Access tokens",
        "EconomyConfig": "Economy settings",
        "UserWallet": "User wallets",
        "WalletTransaction": "Wallet transactions",
        "StoreProduct": "Store products",
        "PurchaseRecord": "Purchases",
        "UserMonetizationProfile": "Monetization profiles",
        "EmployerSubscription": "Employer subscriptions",
        "UnlockedContact": "Unlocked contacts",
        "VacancyContactAccessPolicy": "Contact access rules",
        "EmployerBoardPublishingAuthorization": "JobHub publishing permissions",
        "EmployerBoardPublishingEvent": "Publishing history",
    },
    "ru": {
        "Vacancy": "Вакансии",
        "VacancyModerationAttempt": "Попытки модерации",
        "Complaint": "Жалобы",
        "ComplaintActionLog": "История жалоб",
        "AccountDeletionRequest": "Удаление аккаунтов",
        "UserBlock": "Блокировки пользователей",
        "UnlockRequest": "Запросы на разблокировку",
        "VacancyReview": "Отзывы о вакансиях",
        "ChatConversation": "Диалоги",
        "ChatMessage": "Сообщения чатов",
        "ChatReport": "Жалобы на чаты",
        "PushDevice": "Push-устройства",
        "ModeratorNotificationDelivery": "Уведомления модераторов",
        "VacancyAlertSubscription": "Оповещения о вакансиях",
        "VacancyAlertDelivery": "Доставка оповещений",
        "User": "Пользователи",
        "Group": "Группы доступа",
        "UserProfile": "Профили пользователей",
        "PhoneVerification": "Проверка телефонов",
        "PhoneVerificationAttempt": "Попытки проверки телефона",
        "EmailVerification": "Проверка email",
        "TokenProxy": "Токены доступа",
        "EconomyConfig": "Настройки экономики",
        "UserWallet": "Кошельки пользователей",
        "WalletTransaction": "Операции кошельков",
        "StoreProduct": "Товары магазина",
        "PurchaseRecord": "Покупки",
        "UserMonetizationProfile": "Профили монетизации",
        "EmployerSubscription": "Подписки работодателей",
        "UnlockedContact": "Открытые контакты",
        "VacancyContactAccessPolicy": "Правила доступа к контактам",
        "EmployerBoardPublishingAuthorization": "Разрешения на публикации JobHub",
        "EmployerBoardPublishingEvent": "История публикаций",
    },
}


# Short object names used after action verbs in change-form headings.  Django's
# default titles are assembled from model metadata, which produces awkward
# combinations once the surrounding chrome is translated in JavaScript.
MODEL_FORM_LABELS = {
    "en": {
        "Vacancy": "vacancy",
        "VacancyModerationAttempt": "moderation attempt",
        "Complaint": "complaint",
        "ComplaintActionLog": "complaint history entry",
        "AccountDeletionRequest": "account deletion request",
        "UserBlock": "user block",
        "UnlockRequest": "unlock request",
        "VacancyReview": "vacancy review",
        "ChatConversation": "conversation",
        "ChatMessage": "chat message",
        "ChatReport": "chat report",
        "PushDevice": "push device",
        "ModeratorNotificationDelivery": "moderator notification",
        "VacancyAlertSubscription": "vacancy alert",
        "VacancyAlertDelivery": "alert delivery",
        "User": "user",
        "Group": "permission group",
        "UserProfile": "user profile",
        "PhoneVerification": "phone verification",
        "PhoneVerificationAttempt": "phone verification attempt",
        "EmailVerification": "email verification",
        "TokenProxy": "access token",
        "EconomyConfig": "economy setting",
        "UserWallet": "user wallet",
        "WalletTransaction": "wallet transaction",
        "StoreProduct": "store product",
        "PurchaseRecord": "purchase",
        "UserMonetizationProfile": "monetization profile",
        "EmployerSubscription": "employer subscription",
        "UnlockedContact": "unlocked contact",
        "VacancyContactAccessPolicy": "contact access rule",
        "EmployerBoardPublishingAuthorization": "JobHub publishing permission",
        "EmployerBoardPublishingEvent": "publishing history entry",
    },
    "ru": {
        "Vacancy": "вакансию",
        "VacancyModerationAttempt": "попытку модерации",
        "Complaint": "жалобу",
        "ComplaintActionLog": "запись истории жалобы",
        "AccountDeletionRequest": "запрос на удаление аккаунта",
        "UserBlock": "блокировку пользователя",
        "UnlockRequest": "запрос на разблокировку",
        "VacancyReview": "отзыв о вакансии",
        "ChatConversation": "диалог",
        "ChatMessage": "сообщение чата",
        "ChatReport": "жалобу на чат",
        "PushDevice": "push-устройство",
        "ModeratorNotificationDelivery": "уведомление модератора",
        "VacancyAlertSubscription": "оповещение о вакансии",
        "VacancyAlertDelivery": "доставку оповещения",
        "User": "пользователя",
        "Group": "группу доступа",
        "UserProfile": "профиль пользователя",
        "PhoneVerification": "проверку телефона",
        "PhoneVerificationAttempt": "попытку проверки телефона",
        "EmailVerification": "проверку email",
        "TokenProxy": "токен доступа",
        "EconomyConfig": "настройку экономики",
        "UserWallet": "кошелёк пользователя",
        "WalletTransaction": "операцию кошелька",
        "StoreProduct": "товар магазина",
        "PurchaseRecord": "покупку",
        "UserMonetizationProfile": "профиль монетизации",
        "EmployerSubscription": "подписку работодателя",
        "UnlockedContact": "открытый контакт",
        "VacancyContactAccessPolicy": "правило доступа к контактам",
        "EmployerBoardPublishingAuthorization": "разрешение на публикации JobHub",
        "EmployerBoardPublishingEvent": "запись истории публикаций",
    },
}


SECTION_DEFINITIONS = [
    {
        "key": "moderation",
        "title_key": "moderation",
        "description_key": "moderation_note",
        "short": "MS",
        "accent": "green",
        "models": {
            "Vacancy",
            "VacancyModerationAttempt",
            "Complaint",
            "ComplaintActionLog",
            "AccountDeletionRequest",
            "UserBlock",
            "UnlockRequest",
            "VacancyReview",
        },
    },
    {
        "key": "communication",
        "title_key": "communication",
        "description_key": "communication_note",
        "short": "CN",
        "accent": "cyan",
        "models": {
            "ChatConversation",
            "ChatMessage",
            "ChatReport",
            "PushDevice",
            "ModeratorNotificationDelivery",
            "VacancyAlertSubscription",
            "VacancyAlertDelivery",
        },
    },
    {
        "key": "people",
        "title_key": "people",
        "description_key": "people_note",
        "short": "PA",
        "accent": "slate",
        "models": {
            "User",
            "Group",
            "UserProfile",
            "PhoneVerification",
            "PhoneVerificationAttempt",
            "EmailVerification",
            "TokenProxy",
        },
    },
    {
        "key": "commerce",
        "title_key": "commerce",
        "description_key": "commerce_note",
        "short": "EC",
        "accent": "amber",
        "models": {
            "EconomyConfig",
            "UserWallet",
            "WalletTransaction",
            "StoreProduct",
            "PurchaseRecord",
            "UserMonetizationProfile",
            "EmployerSubscription",
            "UnlockedContact",
            "VacancyContactAccessPolicy",
        },
    },
    {
        "key": "publishing",
        "title_key": "publishing",
        "description_key": "publishing_note",
        "short": "PR",
        "accent": "blue",
        "models": {
            "EmployerBoardPublishingAuthorization",
            "EmployerBoardPublishingEvent",
        },
    },
]

FEATURED_MODELS = ("Vacancy", "Complaint", "User", "ChatReport")

MODEL_LOOKUP = {}
for definition in SECTION_DEFINITIONS:
    for model_name in definition["models"]:
        MODEL_LOOKUP[model_name] = definition["key"]


def _language():
    return "ru" if (get_language() or "en").lower().startswith("ru") else "en"


def _text(key):
    language = _language()
    return TEXT.get(language, TEXT["en"]).get(key, TEXT["en"].get(key, key))


def _model_label(object_name, fallback=""):
    language = _language()
    return MODEL_LABELS.get(language, {}).get(
        object_name,
        MODEL_LABELS["en"].get(object_name, fallback or object_name),
    )


def _model_form_label(object_name, fallback=""):
    language = _language()
    return MODEL_FORM_LABELS.get(language, {}).get(
        object_name,
        MODEL_FORM_LABELS["en"].get(object_name, fallback or object_name),
    )


def _clone_sections():
    sections = []
    for definition in SECTION_DEFINITIONS:
        section = deepcopy(definition)
        section["title"] = _text(section.pop("title_key"))
        section["description"] = _text(section.pop("description_key"))
        section["models"] = []
        sections.append(section)
    return sections


def _localized_model(app, model):
    item = dict(model)
    item["app_label"] = app.get("app_label")
    item["app_name"] = app.get("name")
    item["display_name"] = _model_label(model.get("object_name"), model.get("name", ""))
    return item


@register.simple_tag
def operator_text(key):
    return _text(key)


@register.simple_tag
def operator_choice_labels_json():
    language = _language()
    payload = {
        "countries": COUNTRY_LABELS.get(language, COUNTRY_LABELS["en"]),
        "categories": CATEGORY_LABELS.get(language, CATEGORY_LABELS["en"]),
        "employment": EMPLOYMENT_LABELS.get(language, EMPLOYMENT_LABELS["en"]),
        "experience": EXPERIENCE_LABELS.get(language, EXPERIENCE_LABELS["en"]),
        "housing": HOUSING_LABELS.get(language, HOUSING_LABELS["en"]),
        "source": SOURCE_LABELS.get(language, SOURCE_LABELS["en"]),
        "salaryTax": SALARY_TAX_LABELS.get(language, SALARY_TAX_LABELS["en"]),
        # Django renders changelist filters with the model's default (English)
        # labels.  Keep the source labels alongside the selected locale so the
        # operator UI can translate those rendered values without guessing.
        "english": {
            "countries": COUNTRY_LABELS["en"],
            "categories": CATEGORY_LABELS["en"],
            "employment": EMPLOYMENT_LABELS["en"],
            "experience": EXPERIENCE_LABELS["en"],
            "housing": HOUSING_LABELS["en"],
            "source": SOURCE_LABELS["en"],
            "salaryTax": SALARY_TAX_LABELS["en"],
        },
    }
    # The dictionaries are application constants, not user-controlled content.
    return mark_safe(json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))


@register.filter
def operator_model_label(object_name):
    return _model_label(str(object_name), str(object_name))


@register.filter
def operator_model_form_label(object_name):
    return _model_form_label(str(object_name), str(object_name))


@register.simple_tag
def operator_sections(app_list):
    sections = _clone_sections()
    section_map = {section["key"]: section for section in sections}
    extras = {
        "key": "other",
        "title": _text("other"),
        "description": _text("other_note"),
        "short": "OT",
        "accent": "violet",
        "models": [],
    }

    for app in app_list:
        for model in app.get("models", []):
            item = _localized_model(app, model)
            key = MODEL_LOOKUP.get(model.get("object_name"))
            if key:
                section_map[key]["models"].append(item)
            else:
                extras["models"].append(item)

    result = [section for section in sections if section["models"]]
    if extras["models"]:
        result.append(extras)
    return result


@register.simple_tag
def operator_featured_models(app_list):
    found = {}
    for app in app_list:
        for model in app.get("models", []):
            object_name = model.get("object_name")
            if object_name in FEATURED_MODELS:
                found[object_name] = _localized_model(app, model)
    return [found[name] for name in FEATURED_MODELS if name in found]


@register.filter
def count_admin_models(app_list):
    return sum(len(app.get("models", [])) for app in app_list)
