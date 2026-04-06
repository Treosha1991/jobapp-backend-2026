from django.db.models import Avg, Count
from django.utils import timezone

from .models import VacancyReview, WalletTransaction
from .review_presets import (
    MAX_REVIEW_PRESET_SELECTIONS,
    REVIEW_BUTTON_DELAY,
    REVIEW_EDIT_WINDOW,
    REVIEW_PRESET_CHOICES,
    normalize_review_preset_codes,
)
from .service_sources import is_service_board_user


def _round_rating(value):
    if value is None:
        return None
    return round(float(value), 1)


def get_employer_review_summary(employer):
    if not employer:
        return {
            "average_rating": None,
            "reviews_count": 0,
            "rating_counts": {str(score): 0 for score in range(1, 6)},
        }

    qs = VacancyReview.objects.filter(employer=employer)
    aggregate = qs.aggregate(
        average_rating=Avg("rating"),
        reviews_count=Count("id"),
    )
    rating_counts = {str(score): 0 for score in range(1, 6)}
    for item in qs.values("rating").annotate(count=Count("id")):
        rating = int(item.get("rating") or 0)
        if 1 <= rating <= 5:
            rating_counts[str(rating)] = int(item.get("count") or 0)

    return {
        "average_rating": _round_rating(aggregate.get("average_rating")),
        "reviews_count": int(aggregate.get("reviews_count") or 0),
        "rating_counts": rating_counts,
    }


def get_vacancy_review_preset_counts(vacancy):
    counts = {code: 0 for code, _ in REVIEW_PRESET_CHOICES}
    total_reviews = 0
    average_rating = None
    if not vacancy:
        return {
            "total_reviews": 0,
            "average_rating": None,
            "presets": [{"code": code, "count": 0} for code, _ in REVIEW_PRESET_CHOICES],
        }

    qs = VacancyReview.objects.filter(vacancy=vacancy)
    aggregate = qs.aggregate(
        total_reviews=Count("id"),
        average_rating=Avg("rating"),
    )
    total_reviews = int(aggregate.get("total_reviews") or 0)
    average_rating = _round_rating(aggregate.get("average_rating"))

    for review in qs.only("preset_codes"):
        try:
            normalized_codes = normalize_review_preset_codes(
                list(getattr(review, "preset_codes", []) or []),
                max_selections=MAX_REVIEW_PRESET_SELECTIONS,
            )
        except ValueError:
            normalized_codes = []
        for code in normalized_codes:
            counts[code] += 1

    return {
        "total_reviews": total_reviews,
        "average_rating": average_rating,
        "presets": [{"code": code, "count": counts[code]} for code, _ in REVIEW_PRESET_CHOICES],
    }


def _first_contact_unlock_at(user, vacancy):
    if not getattr(user, "is_authenticated", False) or vacancy is None:
        return None
    return (
        WalletTransaction.objects.filter(
            user=user,
            kind="contact_unlock",
            related_vacancy=vacancy,
        )
        .order_by("created_at", "id")
        .values_list("created_at", flat=True)
        .first()
    )


def serialize_vacancy_review(review):
    if review is None:
        return None
    return {
        "id": review.id,
        "rating": int(review.rating or 0),
        "preset_codes": normalize_review_preset_codes(
            list(getattr(review, "preset_codes", []) or []),
            max_selections=MAX_REVIEW_PRESET_SELECTIONS,
        ),
        "created_at": review.created_at,
        "updated_at": review.updated_at,
    }


def build_vacancy_review_state(user, vacancy, *, now=None):
    current_time = now or timezone.now()
    empty = {
        "eligible": False,
        "can_submit": False,
        "can_edit": False,
        "can_delete": False,
        "button_state": "hidden",
        "first_contact_opened_at": None,
        "review_available_from": None,
        "review_deadline": None,
        "delay_remaining_seconds": 0,
        "window_remaining_seconds": 0,
        "review": None,
    }
    if not getattr(user, "is_authenticated", False):
        return empty
    if vacancy is None or vacancy.created_by_id == getattr(user, "id", None):
        return empty
    if getattr(user, "is_staff", False):
        return empty
    if is_service_board_user(getattr(vacancy, "created_by", None)):
        return empty

    first_unlock_at = _first_contact_unlock_at(user, vacancy)
    if first_unlock_at is None:
        return empty

    review_available_from = first_unlock_at + REVIEW_BUTTON_DELAY
    review_deadline = review_available_from + REVIEW_EDIT_WINDOW
    review = VacancyReview.objects.filter(reviewer=user, vacancy=vacancy).first()

    if current_time < review_available_from:
        delay_remaining = int((review_available_from - current_time).total_seconds())
        return {
            **empty,
            "button_state": "pending",
            "first_contact_opened_at": first_unlock_at,
            "review_available_from": review_available_from,
            "review_deadline": review_deadline,
            "delay_remaining_seconds": max(0, delay_remaining),
            "review": serialize_vacancy_review(review),
        }

    if current_time >= review_deadline:
        return {
            **empty,
            "button_state": "expired",
            "first_contact_opened_at": first_unlock_at,
            "review_available_from": review_available_from,
            "review_deadline": review_deadline,
            "review": serialize_vacancy_review(review),
        }

    remaining = int((review_deadline - current_time).total_seconds())
    has_review = review is not None
    return {
        "eligible": True,
        "can_submit": True,
        "can_edit": has_review,
        "can_delete": has_review,
        "button_state": "edit" if has_review else "create",
        "first_contact_opened_at": first_unlock_at,
        "review_available_from": review_available_from,
        "review_deadline": review_deadline,
        "delay_remaining_seconds": 0,
        "window_remaining_seconds": max(0, remaining),
        "review": serialize_vacancy_review(review),
    }


def save_vacancy_review(*, user, vacancy, rating, preset_codes, now=None):
    current_time = now or timezone.now()
    state = build_vacancy_review_state(user, vacancy, now=current_time)
    if not state["can_submit"]:
        raise ValueError("review_submission_not_available")

    normalized_presets = normalize_review_preset_codes(preset_codes)
    if not normalized_presets:
        raise ValueError("review_presets_required")

    rating_value = int(rating or 0)
    if rating_value < 1 or rating_value > 5:
        raise ValueError("review_rating_invalid")

    review, created = VacancyReview.objects.update_or_create(
        reviewer=user,
        vacancy=vacancy,
        defaults={
            "employer": vacancy.created_by,
            "rating": rating_value,
            "preset_codes": normalized_presets,
        },
    )
    return review, created


def delete_vacancy_review(*, user, vacancy, now=None):
    current_time = now or timezone.now()
    state = build_vacancy_review_state(user, vacancy, now=current_time)
    if not state["can_delete"]:
        raise ValueError("review_delete_not_available")
    review = VacancyReview.objects.filter(reviewer=user, vacancy=vacancy).first()
    if review is None:
        raise ValueError("review_not_found")
    review.delete()
    return True


def get_employer_review_records_for_moderator(employer):
    if not employer:
        return []
    items = []
    qs = (
        VacancyReview.objects.filter(employer=employer)
        .select_related("vacancy", "reviewer", "reviewer__profile")
        .order_by("-updated_at", "-id")
    )
    for review in qs:
        reviewer_profile = getattr(review.reviewer, "profile", None)
        reviewer_name = (getattr(reviewer_profile, "nickname", "") or "").strip()
        if not reviewer_name:
            reviewer_name = (review.reviewer.username or "").strip() or f"User #{review.reviewer_id}"
        items.append(
            {
                "id": review.id,
                "vacancy_id": review.vacancy_id,
                "vacancy_title": (getattr(review.vacancy, "title", "") or "").strip(),
                "rating": int(review.rating or 0),
                "preset_codes": normalize_review_preset_codes(
                    list(getattr(review, "preset_codes", []) or []),
                    max_selections=MAX_REVIEW_PRESET_SELECTIONS,
                ),
                "reviewer_name": reviewer_name,
                "updated_at": review.updated_at,
            }
        )
    return items
