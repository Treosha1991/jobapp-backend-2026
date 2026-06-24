from django.contrib.auth.models import User

from .models import ModeratorNotificationDelivery, PushDevice
from .push_gateway import send_push_message


def _normalized(value):
    return (value or "").strip()


def _localized_title(lang):
    lang = _normalized(lang).lower()
    if lang.startswith("ru"):
        return "Новая вакансия на модерации"
    if lang.startswith("uk"):
        return "Нова вакансія на модерації"
    if lang.startswith("pl"):
        return "Nowa oferta do moderacji"
    return "New vacancy for moderation"


def _localized_body(lang, vacancy):
    lang = _normalized(lang).lower()
    title = _normalized(getattr(vacancy, "title", ""))
    city = _normalized(getattr(vacancy, "city", ""))
    if title and city:
        vacancy_text = f"{title} — {city}"
    else:
        vacancy_text = title or city or f"#{vacancy.id}"

    if lang.startswith("ru"):
        return f"Проверьте публикацию: {vacancy_text}"
    if lang.startswith("uk"):
        return f"Перевірте публікацію: {vacancy_text}"
    if lang.startswith("pl"):
        return f"Sprawdź publikację: {vacancy_text}"
    return f"Review publication: {vacancy_text}"


def notify_moderators_about_pending_vacancy(vacancy):
    summary = {
        "moderators": 0,
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

        device = (
            PushDevice.objects.filter(user=moderator, is_active=True)
            .order_by("-last_seen_at")
            .first()
        )
        if not device:
            ModeratorNotificationDelivery.objects.create(
                user=moderator,
                vacancy=vacancy,
                kind="vacancy_pending",
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
                "type": "moderation_vacancy_pending",
                "vacancy_id": vacancy.id,
            },
        )

        if status not in {"sent", "failed", "skipped_not_configured"}:
            status = "failed"
            error_text = error_text or "invalid_push_status"

        ModeratorNotificationDelivery.objects.create(
            user=moderator,
            vacancy=vacancy,
            kind="vacancy_pending",
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
