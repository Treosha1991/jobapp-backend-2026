from django.contrib.auth.models import User

from .models import ModeratorNotificationDelivery, PushDevice
from .push_gateway import send_push_message


VALID_DELIVERY_STATUSES = {"sent", "failed", "skipped_not_configured"}


def _normalized(value):
    return (value or "").strip()


def _localized_title(lang):
    lang = _normalized(lang).lower()
    if lang.startswith("ru"):
        return "????? ???????? ?? ?????????"
    if lang.startswith("uk"):
        return "???? ???????? ?? ?????????"
    if lang.startswith("pl"):
        return "Nowa oferta do moderacji"
    return "New vacancy for moderation"


def _localized_body(lang, vacancy):
    lang = _normalized(lang).lower()
    title = _normalized(getattr(vacancy, "title", ""))
    city = _normalized(getattr(vacancy, "city", ""))
    if title and city:
        vacancy_text = f"{title} ? {city}"
    else:
        vacancy_text = title or city or f"#{vacancy.id}"

    if lang.startswith("ru"):
        return f"????????? ??????????: {vacancy_text}"
    if lang.startswith("uk"):
        return f"????????? ??????????: {vacancy_text}"
    if lang.startswith("pl"):
        return f"Sprawd? publikacj?: {vacancy_text}"
    return f"Review publication: {vacancy_text}"


def _aggregate_status(results):
    statuses = [item[0] for item in results]
    if "sent" in statuses:
        return "sent"
    if statuses and all(status == "skipped_not_configured" for status in statuses):
        return "skipped_not_configured"
    return "failed"


def notify_moderators_about_pending_vacancy(vacancy):
    summary = {
        "moderators": 0,
        "devices": 0,
        "already_delivered": 0,
        "sent": 0,
        "failed": 0,
        "skipped_no_device": 0,
        "skipped_not_configured": 0,
    }

    moderators = User.objects.filter(is_staff=True, is_active=True).order_by("id")
    for moderator in moderators:
        summary["moderators"] += 1
        if ModeratorNotificationDelivery.objects.filter(
            user=moderator,
            vacancy=vacancy,
            kind="vacancy_pending",
        ).exists():
            summary["already_delivered"] += 1
            continue

        devices = list(
            PushDevice.objects.filter(user=moderator, is_active=True)
            .order_by("-last_seen_at", "-id")
        )
        if not devices:
            ModeratorNotificationDelivery.objects.create(
                user=moderator,
                vacancy=vacancy,
                kind="vacancy_pending",
                status="skipped_no_device",
            )
            summary["skipped_no_device"] += 1
            continue

        results = []
        for device in devices:
            summary["devices"] += 1
            lang = _normalized(device.app_language)
            status, provider_message_id, error_text = send_push_message(
                token=device.token,
                title=_localized_title(lang),
                body=_localized_body(lang, vacancy),
                data={
                    "type": "moderation_vacancy_pending",
                    "vacancy_id": vacancy.id,
                },
            )
            if status not in VALID_DELIVERY_STATUSES:
                status = "failed"
                error_text = error_text or "invalid_push_status"
            results.append((status, provider_message_id or "", error_text or "", device))

        aggregate_status = _aggregate_status(results)
        first_device = results[0][3]
        provider_message_ids = [item[1].strip() for item in results if item[1].strip()]
        errors = [item[2].strip() for item in results if item[2].strip()]
        platforms = sorted({(item[3].platform or "").strip() for item in results if item[3].platform})

        ModeratorNotificationDelivery.objects.create(
            user=moderator,
            vacancy=vacancy,
            kind="vacancy_pending",
            status=aggregate_status,
            device_platform=",".join(platforms)[:20],
            device_token_tail=(first_device.token or "")[-8:],
            provider_message_id=",".join(provider_message_ids)[:255],
            error_text=" | ".join(errors)[:2000],
        )

        if aggregate_status in summary:
            summary[aggregate_status] += 1
        else:
            summary["failed"] += 1

    return summary
