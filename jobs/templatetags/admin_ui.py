from copy import deepcopy

from django import template


register = template.Library()


SECTION_DEFINITIONS = [
    {
        "key": "workflow",
        "title": "Moderation and safety",
        "description": "Everything that needs review, intervention, or escalation.",
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
        },
    },
    {
        "key": "growth",
        "title": "Marketplace and reach",
        "description": "Subscriptions, alerts, unlocks, and employer-side relationships.",
        "short": "MR",
        "accent": "blue",
        "models": {
            "EmployerSubscription",
            "UnlockedContact",
            "VacancyAlertSubscription",
            "VacancyAlertDelivery",
        },
    },
    {
        "key": "identity",
        "title": "People and access",
        "description": "Users, profiles, verification flows, devices, and access control.",
        "short": "PA",
        "accent": "slate",
        "models": {
            "User",
            "Group",
            "UserProfile",
            "PhoneVerification",
            "EmailVerification",
            "PushDevice",
            "TokenProxy",
        },
    },
]

FEATURED_MODELS = ("Vacancy", "Complaint", "User", "VacancyModerationAttempt")

MODEL_LOOKUP = {}
for definition in SECTION_DEFINITIONS:
    for model_name in definition["models"]:
        MODEL_LOOKUP[model_name] = definition["key"]


def _clone_sections():
    sections = []
    for definition in SECTION_DEFINITIONS:
        section = deepcopy(definition)
        section["models"] = []
        sections.append(section)
    return sections


@register.simple_tag
def operator_sections(app_list):
    sections = _clone_sections()
    section_map = {section["key"]: section for section in sections}
    extras = {
        "key": "other",
        "title": "Other tools",
        "description": "Less frequent admin tools that still belong in reach.",
        "short": "OT",
        "accent": "amber",
        "models": [],
    }

    for app in app_list:
        for model in app.get("models", []):
            item = dict(model)
            item["app_label"] = app.get("app_label")
            item["app_name"] = app.get("name")
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
                item = dict(model)
                item["app_label"] = app.get("app_label")
                found[object_name] = item
    return [found[name] for name in FEATURED_MODELS if name in found]


@register.filter
def count_admin_models(app_list):
    return sum(len(app.get("models", [])) for app in app_list)
