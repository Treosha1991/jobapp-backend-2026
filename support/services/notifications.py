"""Safe, durable delivery for JobHub Support notifications.

Phone notifications are deliberately less detailed than the in-app target.  A
push can say that something changed, but never include a message body, address,
document code, money amount, or other sensitive information.
"""

from django.db import transaction
from django.utils import timezone

from jobs.models import PushDevice
from jobs.push_gateway import send_push_message

from support.models import InAppNotification, NotificationOutbox, PushDelivery


_SUPPORTED_LANGUAGES = frozenset({"ru", "en", "pl", "uk"})

_NOTIFICATION_COPY = {
    "application.approved": {
        "ru": ("JobHub Support", "Ваша заявка одобрена. Откройте Support."),
        "en": ("JobHub Support", "Your application was approved. Open Support."),
        "pl": ("JobHub Support", "Twoja aplikacja została zaakceptowana. Otwórz Support."),
        "uk": ("JobHub Support", "Вашу заявку схвалено. Відкрийте Support."),
    },
    "connection.stage_changed": {
        "ru": ("JobHub Support", "Ваш статус в Support изменился."),
        "en": ("JobHub Support", "Your Support status has changed."),
        "pl": ("JobHub Support", "Twój status Support uległ zmianie."),
        "uk": ("JobHub Support", "Ваш статус у Support змінився."),
    },
    "conversation.message": {
        "ru": ("JobHub Support", "Новое сообщение в JobHub Support."),
        "en": ("JobHub Support", "New message in JobHub Support."),
        "pl": ("JobHub Support", "Nowa wiadomość w JobHub Support."),
        "uk": ("JobHub Support", "Нове повідомлення в JobHub Support."),
    },
    "support.access_changed": {
        "ru": ("JobHub Support", "Изменился доступ к JobHub Support."),
        "en": ("JobHub Support", "Your JobHub Support access has changed."),
        "pl": ("JobHub Support", "Twój dostęp do JobHub Support uległ zmianie."),
        "uk": ("JobHub Support", "Ваш доступ до JobHub Support змінився."),
    },
    "housing.assignment_published": {
        "ru": ("JobHub Support", "Ваша информация о жилье обновлена."),
        "en": ("JobHub Support", "Your accommodation information was updated."),
        "pl": ("JobHub Support", "Twoje informacje o zakwaterowaniu zostały zaktualizowane."),
        "uk": ("JobHub Support", "Вашу інформацію про житло оновлено."),
    },
    "work.assignment_published": {
        "ru": ("JobHub Support", "Ваша информация о работе обновлена."),
        "en": ("JobHub Support", "Your work information was updated."),
        "pl": ("JobHub Support", "Twoje informacje o pracy zostały zaktualizowane."),
        "uk": ("JobHub Support", "Вашу інформацію про роботу оновлено."),
    },
    "transport.route_published": {
        "ru": ("JobHub Support", "Ваша информация о транспорте обновлена."),
        "en": ("JobHub Support", "Your transport information was updated."),
        "pl": ("JobHub Support", "Twoje informacje o transporcie zostały zaktualizowane."),
        "uk": ("JobHub Support", "Вашу інформацію про транспорт оновлено."),
    },
    "transport.assignment_published": {
        "ru": ("JobHub Support", "Ваша информация о транспорте обновлена."),
        "en": ("JobHub Support", "Your transport information was updated."),
        "pl": ("JobHub Support", "Twoje informacje o transporcie zostały zaktualizowane."),
        "uk": ("JobHub Support", "Вашу інформацію про транспорт оновлено."),
    },
    "schedule.shift_published": {
        "ru": ("JobHub Support", "Ваш график работы обновлён."),
        "en": ("JobHub Support", "Your work schedule was updated."),
        "pl": ("JobHub Support", "Twój grafik pracy został zaktualizowany."),
        "uk": ("JobHub Support", "Ваш графік роботи оновлено."),
    },
    "time.entry_changed": {
        "ru": ("JobHub Support", "В вашей записи рабочих часов есть изменение."),
        "en": ("JobHub Support", "Your work-time record was changed."),
        "pl": ("JobHub Support", "Twoja ewidencja czasu pracy została zmieniona."),
        "uk": ("JobHub Support", "Ваш запис робочого часу змінено."),
    },
    "worker_request.urgent_submitted": {
        "ru": ("JobHub Support", "Срочный запрос работника требует внимания."),
        "en": ("JobHub Support", "An urgent worker request needs attention."),
        "pl": ("JobHub Support", "Pilny wniosek pracownika wymaga uwagi."),
        "uk": ("JobHub Support", "Терміновий запит працівника потребує уваги."),
    },
    "worker_task.published": {
        "ru": ("JobHub Support", "Вам назначена новая задача."),
        "en": ("JobHub Support", "A new task was assigned to you."),
        "pl": ("JobHub Support", "Przypisano Ci nowe zadanie."),
        "uk": ("JobHub Support", "Вам призначено нове завдання."),
    },
    "worker_task.status_changed": {
        "ru": ("JobHub Support", "Статус вашей задачи изменён."),
        "en": ("JobHub Support", "The status of your task changed."),
        "pl": ("JobHub Support", "Status Twojego zadania został zmieniony."),
        "uk": ("JobHub Support", "Статус вашого завдання змінено."),
    },
    "announcement.published": {
        "ru": ("JobHub Support", "Для вас опубликовано новое объявление."),
        "en": ("JobHub Support", "A new announcement was published for you."),
        "pl": ("JobHub Support", "Opublikowano dla Ciebie nowe ogłoszenie."),
        "uk": ("JobHub Support", "Для вас опубліковано нове оголошення."),
    },
}


def _localized_copy(notification_code, language):
    variants = _NOTIFICATION_COPY.get(notification_code)
    if variants is None:
        raise ValueError("unsupported_support_notification_code")
    normalized_language = (language or "").strip().lower()
    if normalized_language not in _SUPPORTED_LANGUAGES:
        normalized_language = "en"
    return variants[normalized_language]


def _safe_context(context):
    """Keep the outbox safe even when a future caller passes extra metadata."""

    if not context:
        return {}
    if not isinstance(context, dict):
        raise ValueError("support_notification_context_must_be_mapping")
    allowed = {"stage"}
    extra = set(context) - allowed
    if extra:
        raise ValueError("unsafe_support_notification_context")
    return {key: str(value)[:64] for key, value in context.items()}


def enqueue_support_notification(
    *,
    organization,
    recipient,
    notification_code,
    target_kind,
    target_public_id,
    target_key,
    collapse_key,
    dedupe_key,
    push_requested=True,
    context=None,
):
    """Create a delivery event and notification-center item in one transaction.

    Call this inside the business transaction.  The asynchronous-looking
    on-commit dispatch is safe without Celery for the pilot: the durable outbox
    is already present if FCM is unavailable and may be retried by command.
    """

    if notification_code not in _NOTIFICATION_COPY:
        raise ValueError("unsupported_support_notification_code")
    if not target_key or not collapse_key or not dedupe_key:
        raise ValueError("support_notification_target_and_dedupe_required")

    outbox, created = NotificationOutbox.objects.get_or_create(
        dedupe_key=dedupe_key,
        defaults={
            "organization": organization,
            "recipient": recipient,
            "notification_code": notification_code,
            "target_kind": target_kind,
            "target_public_id": target_public_id,
            "target_key": target_key,
            "collapse_key": collapse_key,
            "push_requested": bool(push_requested),
            "safe_context": _safe_context(context),
        },
    )
    if created:
        InAppNotification.objects.create(outbox=outbox, recipient=recipient)
        transaction.on_commit(lambda: dispatch_outbox_entry(outbox_public_id=outbox.public_id))
    return outbox, created


def _delivery_error_code(error_text):
    value = (error_text or "").strip().lower()
    if not value:
        return "unknown"
    if "not_configured" in value or "credentials_missing" in value:
        return "provider_not_configured"
    if "unregistered" in value or "not found" in value:
        return "device_not_registered"
    return "provider_send_failed"


def _refresh_outbox_status(outbox):
    states = list(outbox.push_deliveries.values_list("status", flat=True))
    now = timezone.now()
    if not outbox.push_requested or not states or set(states) == {PushDelivery.STATUS_SKIPPED}:
        status = NotificationOutbox.STATUS_SKIPPED
        delivered_at = None
    elif PushDelivery.STATUS_SENT in states:
        status = NotificationOutbox.STATUS_DELIVERED
        delivered_at = now
    elif PushDelivery.STATUS_PENDING in states:
        status = NotificationOutbox.STATUS_PENDING
        delivered_at = None
    else:
        status = NotificationOutbox.STATUS_FAILED
        delivered_at = None
    outbox.status = status
    outbox.delivered_at = delivered_at
    outbox.save(update_fields=["status", "delivered_at", "updated_at"])


def dispatch_outbox_entry(*, outbox_public_id):
    """Deliver one event idempotently to each active device.

    If the configured FCM provider is unavailable, the in-app notification
    remains available and the event is marked skipped.  Retrying never creates
    duplicate InAppNotification rows or duplicate successful device records.
    """

    with transaction.atomic():
        outbox = (
            NotificationOutbox.objects.select_for_update()
            .select_related("recipient")
            .filter(public_id=outbox_public_id)
            .first()
        )
        if outbox is None:
            return {"state": "missing", "devices": 0}
        if outbox.status == NotificationOutbox.STATUS_DELIVERED:
            return {"state": "already_delivered", "devices": 0}
        outbox.status = NotificationOutbox.STATUS_DELIVERING
        outbox.attempt_count += 1
        outbox.last_attempt_at = timezone.now()
        outbox.save(update_fields=["status", "attempt_count", "last_attempt_at", "updated_at"])
        if not outbox.push_requested:
            outbox.status = NotificationOutbox.STATUS_SKIPPED
            outbox.save(update_fields=["status", "updated_at"])
            return {"state": "push_not_requested", "devices": 0}
        devices = list(
            PushDevice.objects.filter(user=outbox.recipient, is_active=True).order_by(
                "-last_seen_at", "-id"
            )
        )

    if not devices:
        with transaction.atomic():
            locked = NotificationOutbox.objects.select_for_update().get(pk=outbox.pk)
            locked.status = NotificationOutbox.STATUS_SKIPPED
            locked.save(update_fields=["status", "updated_at"])
        return {"state": "no_active_device", "devices": 0}

    summary = {"state": "processed", "devices": len(devices), "sent": 0, "failed": 0, "skipped": 0}
    for device in devices:
        with transaction.atomic():
            delivery, _ = PushDelivery.objects.select_for_update().get_or_create(
                outbox=outbox,
                device=device,
                defaults={
                    "device_platform": (device.platform or "").strip(),
                    "device_token_tail": (device.token or "")[-10:],
                    "native_notification_tag": f"jobhub:{outbox.target_key}",
                },
            )
            if delivery.status == PushDelivery.STATUS_SENT:
                continue

        title, body = _localized_copy(outbox.notification_code, device.app_language)
        push_status, provider_message_id, error_text = send_push_message(
            token=device.token,
            platform=device.platform,
            title=title,
            body=body,
            data={
                "type": "support_notification",
                "notification_code": outbox.notification_code,
                "notification_event_id": str(outbox.public_id),
                "notification_namespace": outbox.notification_namespace,
                "notification_target": outbox.target_key,
                "target_kind": outbox.target_kind,
                "target_id": str(outbox.target_public_id),
            },
            collapse_key=outbox.collapse_key,
            notification_tag=f"jobhub:{outbox.target_key}",
        )
        if push_status == "sent":
            delivery_status = PushDelivery.STATUS_SENT
            summary["sent"] += 1
        elif push_status == "skipped_not_configured":
            delivery_status = PushDelivery.STATUS_SKIPPED
            summary["skipped"] += 1
        else:
            delivery_status = PushDelivery.STATUS_FAILED
            summary["failed"] += 1
        with transaction.atomic():
            delivery = PushDelivery.objects.select_for_update().get(pk=delivery.pk)
            delivery.status = delivery_status
            delivery.provider_message_id = (provider_message_id or "").strip()[:255]
            delivery.error_code = _delivery_error_code(error_text)
            delivery.attempted_at = timezone.now()
            delivery.save(
                update_fields=[
                    "status",
                    "provider_message_id",
                    "error_code",
                    "attempted_at",
                    "updated_at",
                ]
            )
    with transaction.atomic():
        locked = NotificationOutbox.objects.select_for_update().get(pk=outbox.pk)
        _refresh_outbox_status(locked)
    return summary


def dispatch_pending_outbox(*, limit=100):
    pending = list(
        NotificationOutbox.objects.filter(
            status__in=[
                NotificationOutbox.STATUS_PENDING,
                NotificationOutbox.STATUS_FAILED,
            ]
        )
        .order_by("created_at", "id")
        .values_list("public_id", flat=True)[:limit]
    )
    return [dispatch_outbox_entry(outbox_public_id=public_id) for public_id in pending]
