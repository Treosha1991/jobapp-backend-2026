"""Server-side translation for published JobHub content.

This module intentionally serves only public vacancy wording and Support
announcements.  It must never be used for private chats or documents.
"""

from __future__ import annotations

from hashlib import sha256
from html import unescape

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import ContentTranslationUsageMonth, VacancyTranslation


SUPPORTED_TARGET_LANGUAGES = frozenset({"ru", "en", "pl", "uk"})
GOOGLE_CLOUD_TRANSLATE_URL = "https://translation.googleapis.com/language/translate/v2"


class ContentTranslationUnavailable(Exception):
    """The approved provider has not been enabled or cannot answer safely."""


class ContentTranslationBudgetExceeded(Exception):
    """The JobHub-owned monthly safety limit has been reached."""


def source_fingerprint(*parts: str) -> str:
    normalized = "\x1f".join((part or "").strip() for part in parts)
    return sha256(normalized.encode("utf-8")).hexdigest()


def _provider_is_configured() -> bool:
    return (
        getattr(settings, "JOBHUB_CONTENT_TRANSLATION_PROVIDER", "disabled")
        == "google_cloud"
        and bool(getattr(settings, "GOOGLE_CLOUD_TRANSLATION_API_KEY", ""))
    )


def _reserve_characters(character_count: int) -> None:
    if character_count <= 0:
        return
    limit = max(
        0,
        int(getattr(settings, "JOBHUB_CONTENT_TRANSLATION_MONTHLY_CHARACTER_LIMIT", 0)),
    )
    month = timezone.localdate().replace(day=1)
    usage, _ = ContentTranslationUsageMonth.objects.select_for_update().get_or_create(
        month=month
    )
    if limit <= 0 or usage.character_count + character_count > limit:
        raise ContentTranslationBudgetExceeded()
    usage.character_count += character_count
    usage.save(update_fields=["character_count", "updated_at"])


def _google_cloud_translate(*, texts: list[str], source_language: str, target_language: str):
    payload = {"q": texts, "target": target_language, "format": "text"}
    if source_language and source_language != "auto":
        payload["source"] = source_language
    try:
        response = requests.post(
            GOOGLE_CLOUD_TRANSLATE_URL,
            params={"key": settings.GOOGLE_CLOUD_TRANSLATION_API_KEY},
            json=payload,
            timeout=12,
        )
    except requests.RequestException as exc:
        raise ContentTranslationUnavailable() from exc
    if response.status_code != 200:
        raise ContentTranslationUnavailable()
    try:
        translated_items = response.json()["data"]["translations"]
        translated = [unescape(item["translatedText"]).strip() for item in translated_items]
    except (KeyError, TypeError, ValueError) as exc:
        raise ContentTranslationUnavailable() from exc
    if len(translated) != len(texts) or any(not text for text in translated):
        raise ContentTranslationUnavailable()
    detected_source_language = ""
    if translated_items:
        detected_source_language = str(
            translated_items[0].get("detectedSourceLanguage", "")
        ).strip().lower()
    return translated, detected_source_language, "google_cloud", "v2"


def translate_content_texts(*, texts: list[str], source_language: str, target_language: str):
    """Translate a bounded set of non-private text fields in one API request.

    The call and the character reservation share a transaction.  If Google
    does not return a valid translation, the usage reservation is rolled back.
    """

    target_language = (target_language or "").strip().lower()
    source_language = (source_language or "auto").strip().lower()
    normalized = [(text or "").strip() for text in texts]
    if target_language not in SUPPORTED_TARGET_LANGUAGES:
        raise ValueError("unsupported_translation_target_language")
    if not normalized or any(not text for text in normalized):
        raise ValueError("translation_text_required")
    if not _provider_is_configured():
        raise ContentTranslationUnavailable()
    with transaction.atomic():
        _reserve_characters(sum(len(text) for text in normalized))
        return _google_cloud_translate(
            texts=normalized,
            source_language=source_language,
            target_language=target_language,
        )


def request_vacancy_translation(*, vacancy, target_language: str):
    """Translate and cache public vacancy fields for one supported app locale."""

    target_language = (target_language or "").strip().lower()
    if target_language not in SUPPORTED_TARGET_LANGUAGES:
        raise ValueError("unsupported_translation_target_language")
    fingerprint = source_fingerprint(vacancy.title, vacancy.city, vacancy.description)
    with transaction.atomic():
        existing = (
            VacancyTranslation.objects.select_for_update()
            .filter(vacancy=vacancy, target_language=target_language)
            .first()
        )
        if (
            existing is not None
            and existing.status == VacancyTranslation.STATUS_READY
            and existing.source_fingerprint == fingerprint
        ):
            return {
                "state": "ready",
                "title": existing.title,
                "city": existing.city,
                "description": existing.description,
                "target_language": target_language,
                "source_language": existing.detected_source_language,
                "provider": existing.provider,
            }
        try:
            translated, detected_source, provider, provider_version = translate_content_texts(
                texts=[vacancy.title, vacancy.city, vacancy.description],
                source_language="auto",
                target_language=target_language,
            )
        except (ContentTranslationUnavailable, ContentTranslationBudgetExceeded):
            if existing is None:
                VacancyTranslation.objects.create(
                    vacancy=vacancy,
                    target_language=target_language,
                    source_fingerprint=fingerprint,
                    status=VacancyTranslation.STATUS_FAILED,
                    error_code="translation_unavailable",
                )
            else:
                existing.status = VacancyTranslation.STATUS_FAILED
                existing.error_code = "translation_unavailable"
                existing.source_fingerprint = fingerprint
                existing.save(
                    update_fields=[
                        "status",
                        "error_code",
                        "source_fingerprint",
                        "updated_at",
                    ]
                )
            raise

        defaults = {
            "source_fingerprint": fingerprint,
            "detected_source_language": detected_source,
            "title": translated[0],
            "city": translated[1],
            "description": translated[2],
            "provider": provider,
            "provider_version": provider_version,
            "status": VacancyTranslation.STATUS_READY,
            "error_code": "",
        }
        if existing is None:
            existing = VacancyTranslation.objects.create(
                vacancy=vacancy,
                target_language=target_language,
                **defaults,
            )
        else:
            for field, value in defaults.items():
                setattr(existing, field, value)
            existing.save(update_fields=[*defaults, "updated_at"])
    return {
        "state": "ready",
        "title": existing.title,
        "city": existing.city,
        "description": existing.description,
        "target_language": target_language,
        "source_language": existing.detected_source_language,
        "provider": existing.provider,
    }
