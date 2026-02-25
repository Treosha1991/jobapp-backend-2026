from django.db.models import Q

from .models import PushDevice, VacancyAlertDelivery, VacancyAlertSubscription
from .push_gateway import send_push_message


def _normalized(value):
    return (value or "").strip()


def _build_subscription_queryset(vacancy):
    qs = VacancyAlertSubscription.objects.filter(enabled=True).select_related("user")
    qs = qs.exclude(user_id=vacancy.created_by_id)
    qs = qs.exclude(user__outgoing_blocks__blocked_user=vacancy.created_by)

    if _normalized(vacancy.country):
        qs = qs.filter(Q(country="") | Q(country=vacancy.country))
    if _normalized(vacancy.category):
        qs = qs.filter(Q(category="") | Q(category=vacancy.category))
    if _normalized(vacancy.employment_type):
        qs = qs.filter(Q(employment_type="") | Q(employment_type=vacancy.employment_type))
    if _normalized(vacancy.housing_type):
        qs = qs.filter(Q(housing_type="") | Q(housing_type=vacancy.housing_type))
    return qs.distinct()


def _city_matches(subscription_city, vacancy_city):
    sub_city = _normalized(subscription_city).lower()
    vacancy_city = _normalized(vacancy_city).lower()
    if not sub_city:
        return True
    if not vacancy_city:
        return False
    return sub_city in vacancy_city


def _localized_title(lang):
    lang = (lang or "").strip().lower()
    if lang.startswith("ru"):
        return "Новая вакансия по вашему фильтру"
    if lang.startswith("uk"):
        return "Нова вакансія за вашим фільтром"
    if lang.startswith("pl"):
        return "Nowa oferta wg Twojego filtra"
    return "New vacancy for your filters"


def _localized_body(lang, vacancy):
    location_parts = [_normalized(vacancy.country), _normalized(vacancy.city)]
    location = ", ".join([x for x in location_parts if x])
    title = _normalized(vacancy.title)
    if location and title:
        return f"{title} — {location}"
    if title:
        return title
    if location:
        return location
    return f"Vacancy #{vacancy.id}"


def preview_vacancy_alerts(vacancy):
    base_qs = _build_subscription_queryset(vacancy)
    matched_subscriptions = [sub for sub in base_qs if _city_matches(sub.city, vacancy.city)]
    matched_user_ids = [sub.user_id for sub in matched_subscriptions]
    already_delivered_user_ids = set(
        VacancyAlertDelivery.objects.filter(vacancy=vacancy, user_id__in=matched_user_ids).values_list("user_id", flat=True)
    )

    with_device = 0
    without_device = 0
    ready_to_send = 0
    for sub in matched_subscriptions:
        if sub.user_id in already_delivered_user_ids:
            continue
        has_device = PushDevice.objects.filter(user_id=sub.user_id, is_active=True).exists()
        if has_device:
            with_device += 1
            ready_to_send += 1
        else:
            without_device += 1

    return {
        "matched_subscriptions": len(matched_subscriptions),
        "already_delivered": len(already_delivered_user_ids),
        "with_device": with_device,
        "without_device": without_device,
        "ready_to_send": ready_to_send,
    }


def dispatch_vacancy_alerts(vacancy):
    summary = {
        "matched_subscriptions": 0,
        "already_delivered": 0,
        "sent": 0,
        "failed": 0,
        "skipped_no_device": 0,
        "skipped_not_configured": 0,
    }

    subscriptions = [sub for sub in _build_subscription_queryset(vacancy) if _city_matches(sub.city, vacancy.city)]
    summary["matched_subscriptions"] = len(subscriptions)

    for sub in subscriptions:
        user = sub.user
        if VacancyAlertDelivery.objects.filter(user=user, vacancy=vacancy).exists():
            summary["already_delivered"] += 1
            continue

        device = (
            PushDevice.objects.filter(user=user, is_active=True)
            .order_by("-last_seen_at")
            .first()
        )
        if not device:
            VacancyAlertDelivery.objects.create(
                user=user,
                subscription=sub,
                vacancy=vacancy,
                status="skipped_no_device",
            )
            summary["skipped_no_device"] += 1
            continue

        lang = _normalized(device.app_language)
        status, provider_message_id, error_text = send_push_message(
            token=device.token,
            title=_localized_title(lang),
            body=_localized_body(lang, vacancy),
            data={
                "type": "vacancy_alert",
                "vacancy_id": vacancy.id,
            },
        )

        if status not in {"sent", "failed", "skipped_not_configured"}:
            status = "failed"
            error_text = error_text or "invalid_push_status"

        VacancyAlertDelivery.objects.create(
            user=user,
            subscription=sub,
            vacancy=vacancy,
            status=status,
            device_platform=(device.platform or "").strip(),
            device_token_tail=(device.token or "")[-8:],
            provider_message_id=(provider_message_id or "").strip(),
            error_text=(error_text or "").strip(),
        )

        if status in summary:
            summary[status] += 1
        else:
            summary["failed"] += 1

    return summary
