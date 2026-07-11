from .models import PushDevice
from .push_gateway import send_push_message


PERMANENT_TOKEN_ERRORS = (
    "Requested entity was not found",
    "registration-token-not-registered",
    "UNREGISTERED",
)


def _normalized(value):
    return (value or "").strip()


def _localized_title(language, sender_name):
    language = _normalized(language).lower()
    sender_name = _normalized(sender_name) or "JobHub"
    if language.startswith("ru"):
        return f"Новое сообщение от {sender_name}"
    if language.startswith("uk"):
        return f"Нове повідомлення від {sender_name}"
    if language.startswith("pl"):
        return f"Nowa wiadomość od {sender_name}"
    return f"New message from {sender_name}"


def _notification_body(message_text):
    text = " ".join(_normalized(message_text).split())
    if len(text) <= 100:
        return text
    return f"{text[:99].rstrip()}…"


def notify_user_about_chat_message(message, *, recipient, sender_name):
    """Send a new-message notification to every active device of the recipient."""

    summary = {
        "devices": 0,
        "sent": 0,
        "failed": 0,
        "skipped_not_configured": 0,
        "skipped_no_device": 0,
    }
    devices = list(
        PushDevice.objects.filter(user=recipient, is_active=True).order_by(
            "-last_seen_at", "-id"
        )
    )
    if not devices:
        summary["skipped_no_device"] = 1
        return summary

    for device in devices:
        summary["devices"] += 1
        push_status, _, error_text = send_push_message(
            token=device.token,
            platform=device.platform,
            title=_localized_title(device.app_language, sender_name),
            body=_notification_body(message.body),
            data={
                "type": "chat_message",
                "conversation_id": message.conversation_id,
                "message_id": message.id,
            },
        )
        if push_status == "sent":
            summary["sent"] += 1
            continue
        if push_status == "skipped_not_configured":
            summary["skipped_not_configured"] += 1
            continue

        summary["failed"] += 1
        if any(marker in (error_text or "") for marker in PERMANENT_TOKEN_ERRORS):
            device.is_active = False
            device.save(update_fields=["is_active", "last_seen_at"])
        print(
            "[CHAT-PUSH-DEVICE-FAILED] "
            f"conversation={message.conversation_id} "
            f"message={message.id} "
            f"user={recipient.id} "
            f"platform={(device.platform or '').strip()} "
            f"token_tail={(device.token or '')[-8:]} "
            f"error={error_text or 'unknown'}"
        )

    return summary
