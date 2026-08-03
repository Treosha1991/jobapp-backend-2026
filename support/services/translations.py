"""On-demand translation boundary for Support message text.

No external translation provider is selected for JobHub Support yet.  This
service therefore provides the access checks, cache model, and failure behavior
without silently sending private chat text to an unapproved third party.
"""

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from support.models import SupportMessageTranslation


class TranslationProviderNotConfigured(Exception):
    pass


def _configured_provider_name():
    return (getattr(settings, "SUPPORT_TRANSLATION_PROVIDER", "") or "").strip().lower()


def _translate_text(*, source_text, source_language, target_language):
    """Provider seam kept closed until a reviewed provider is configured.

    Adding a provider here requires a separate privacy, retention, and data
    processing review.  This function intentionally does not reuse the public
    web translation endpoint used by legacy vacancy screens.
    """

    provider = _configured_provider_name()
    if provider in {"", "disabled"}:
        raise TranslationProviderNotConfigured()
    raise TranslationProviderNotConfigured()


def request_message_translation(*, message, requested_by, target_language):
    target_language = (target_language or "").strip().lower()
    if target_language not in {"ru", "en", "pl", "uk"}:
        raise ValueError("unsupported_support_language")
    if target_language == message.original_language:
        return {
            "state": "original",
            "translated_text": message.body,
            "target_language": target_language,
            "provider": "original",
            "created_at": message.created_at,
        }

    provider_unavailable = False
    with transaction.atomic():
        existing = (
            SupportMessageTranslation.objects.select_for_update()
            .filter(message=message, target_language=target_language)
            .first()
        )
        if existing is not None and existing.status == SupportMessageTranslation.STATUS_READY:
            return {
                "state": "ready",
                "translated_text": existing.translated_text,
                "target_language": existing.target_language,
                "provider": existing.provider,
                "created_at": existing.created_at,
            }
        try:
            translated_text, provider, provider_version = _translate_text(
                source_text=message.body,
                source_language=message.original_language,
                target_language=target_language,
            )
        except TranslationProviderNotConfigured:
            if existing is None:
                SupportMessageTranslation.objects.create(
                    message=message,
                    target_language=target_language,
                    status=SupportMessageTranslation.STATUS_FAILED,
                    error_code="translation_provider_not_configured",
                    requested_by=requested_by,
                )
            else:
                existing.status = SupportMessageTranslation.STATUS_FAILED
                existing.error_code = "translation_provider_not_configured"
                existing.requested_by = requested_by
                existing.save(
                    update_fields=["status", "error_code", "requested_by", "updated_at"]
                )
            # Commit the safe failure record before returning the 409 response.
            # Raising inside this atomic block would roll that record back.
            provider_unavailable = True

        if provider_unavailable:
            result = None
        elif existing is None:
            existing = SupportMessageTranslation.objects.create(
                message=message,
                target_language=target_language,
                translated_text=translated_text,
                provider=provider,
                provider_version=provider_version,
                status=SupportMessageTranslation.STATUS_READY,
                requested_by=requested_by,
            )
        elif existing is not None:
            existing.translated_text = translated_text
            existing.provider = provider
            existing.provider_version = provider_version
            existing.status = SupportMessageTranslation.STATUS_READY
            existing.error_code = ""
            existing.requested_by = requested_by
            existing.save(
                update_fields=[
                    "translated_text",
                    "provider",
                    "provider_version",
                    "status",
                    "error_code",
                    "requested_by",
                    "updated_at",
                ]
            )
        if not provider_unavailable:
            result = {
                "state": "ready",
                "translated_text": existing.translated_text,
                "target_language": existing.target_language,
                "provider": existing.provider,
                "created_at": existing.created_at,
            }
    if provider_unavailable:
        raise TranslationProviderNotConfigured()
    return result
