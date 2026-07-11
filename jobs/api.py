from math import ceil
from hmac import compare_digest
import logging
import secrets
from datetime import datetime, time, timedelta, timezone as datetime_timezone
import requests

from django.utils import timezone
from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Case, Count, Exists, IntegerField, Max, OuterRef, Q, Value, When
from django.db.models.functions import Coalesce
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from .alerts import dispatch_vacancy_alerts, preview_vacancy_alerts
from .avatar_utils import avatar_public_url
from .country_choices import normalize_audience_country_codes
from .driver_licenses import normalize_driver_license_categories
from .economy import (
    EconomyActionRequiredError,
    InsufficientCreditsError,
    apply_vacancy_submission_action,
    apply_store_product_purchase,
    build_contact_access_state,
    build_vacancy_submission_state,
    ensure_free_contact_policy,
    get_economy_config,
    get_or_create_contact_policy,
    get_or_create_monetization_profile,
    get_or_create_wallet,
    grant_credits,
    is_employer_profile_visible_for_vacancy,
    set_wallet_balances,
    spend_credits,
    unlock_vacancy_contacts,
)
from .google_play import (
    GooglePlayNotConfiguredError,
    GooglePlayVerificationError,
    verify_google_play_product_purchase,
    verify_google_play_subscription_purchase,
)
from .models import (
    Complaint,
    ComplaintActionLog,
    EconomyConfig,
    EmployerSubscription,
    PurchaseRecord,
    PushDevice,
    StoreProduct,
    UserBlock,
    UserMonetizationProfile,
    UserWallet,
    Vacancy,
    VacancyAlertSubscription,
    VacancyContactAccessPolicy,
    VacancyModerationAttempt,
    UserProfile,
    UnlockedContact,
    VacancyReview,
    WalletTransaction,
)
from .monetization import CONTACT_ACCESS_DURATION_MINUTES_DEFAULT
from .moderation_notifications import notify_moderators_about_pending_vacancy
from .service_sources import (
    SERVICE_BOARD_USERNAME,
    service_board_meta_for_user,
    is_service_board_user,
)
from .review_presets import REVIEW_PRESET_CHOICES
from .reviews import (
    build_vacancy_review_state,
    delete_vacancy_review,
    get_employer_review_records_for_moderator,
    get_employer_review_summary,
    save_vacancy_review,
)
from .serializers import (
    ApplePurchaseCompleteSerializer,
    ComplaintListSerializer,
    EconomyConfigSerializer,
    PushDeviceRegisterSerializer,
    GooglePlayPurchaseCompleteSerializer,
    StoreProductSerializer,
    UserMonetizationProfileSerializer,
    UserWalletSerializer,
    VacancyAlertSubscriptionSerializer,
    VacancyContactSerializer,
    InternalVacancyImportSerializer,
    WalletTransactionSerializer,
    VacancyListSerializer,
    VacancyModerationSerializer,
    VacancyModerationDetailSerializer,
    VacancyDetailSerializer,
    VacancyCreateSerializer,
    VacancyMineSerializer,
)
from .text_filters import censor_minimal, contains_link


VACANCY_LIVE_WINDOW = timedelta(days=30)
OWNER_RESUME_FIRST_COOLDOWN = timedelta(seconds=5)
OWNER_RESUME_REPEAT_COOLDOWN = timedelta(minutes=5)
OWNER_RESUME_REPEAT_THRESHOLD = 2
OWNER_MODERATION_RESUBMIT_MIN_INTERVAL = timedelta(minutes=30)
OWNER_MODERATION_RESUBMIT_MAX_PER_DAY = 3

APPLE_VERIFY_RECEIPT_PRODUCTION_URL = "https://buy.itunes.apple.com/verifyReceipt"
APPLE_VERIFY_RECEIPT_SANDBOX_URL = "https://sandbox.itunes.apple.com/verifyReceipt"
APPLE_VERIFY_RECEIPT_TIMEOUT_SECONDS = 20
logger = logging.getLogger(__name__)


class AppConfigAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        return Response(
            {
                "latest_android_version": settings.JOBHUB_LATEST_ANDROID_VERSION,
                "latest_android_build": settings.JOBHUB_LATEST_ANDROID_BUILD,
                "latest_ios_version": settings.JOBHUB_LATEST_IOS_VERSION,
                "latest_ios_build": settings.JOBHUB_LATEST_IOS_BUILD,
                "android_store_url": settings.JOBHUB_ANDROID_STORE_URL,
                "ios_store_url": settings.JOBHUB_IOS_STORE_URL,
            }
        )


def _notify_moderators_about_pending_vacancy_safe(vacancy):
    try:
        summary = notify_moderators_about_pending_vacancy(vacancy)
        print(f"[MODERATION-PUSH] vacancy={vacancy.id} summary={summary}")
    except Exception as exc:
        print(f"[MODERATION-PUSH-ERROR] vacancy={vacancy.id}: {exc}")


class AppleIAPNotConfiguredError(Exception):
    code = "apple_iap_not_configured"


class AppleIAPVerificationError(Exception):
    def __init__(self, code, *, detail="", payload=None):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code
        self.payload = payload or {}


def _apple_store_product_id(product):
    metadata = product.metadata or {}
    explicit = (metadata.get("ios_store_product_id") or "").strip()
    if explicit:
        return explicit
    bundle_id = settings.APPLE_IAP_BUNDLE_ID.strip()
    if not bundle_id:
        return ""
    store_product_id = (product.store_product_id or "").strip()
    if store_product_id.startswith(f"{bundle_id}."):
        return store_product_id
    if product.product_type == "employer_subscription":
        return f"{bundle_id}.employer_subscription"
    if product.product_type == "seeker_subscription":
        return f"{bundle_id}.seeker_subscription"
    code = (product.code or "").strip()
    if not code:
        return ""
    return f"{bundle_id}.{code}"


def _post_apple_verify_receipt(url, *, receipt_data, include_shared_secret=False):
    payload = {
        "receipt-data": receipt_data,
        "exclude-old-transactions": False,
    }
    shared_secret = settings.APPLE_IAP_SHARED_SECRET.strip()
    if include_shared_secret and not shared_secret:
        raise AppleIAPVerificationError(
            "apple_shared_secret_missing",
            detail="Apple shared secret is required for subscription receipt verification.",
        )
    if include_shared_secret:
        payload["password"] = shared_secret
    try:
        response = requests.post(
            url,
            json=payload,
            timeout=APPLE_VERIFY_RECEIPT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise AppleIAPVerificationError(
            "apple_receipt_request_failed",
            detail="Could not reach Apple receipt verification.",
        ) from exc
    except ValueError as exc:
        raise AppleIAPVerificationError(
            "apple_receipt_invalid_response",
            detail="Apple receipt verification returned an invalid response.",
        ) from exc


def _apple_receipt_status_error_code(status_code):
    if status_code == 21004:
        return "apple_shared_secret_invalid"
    return "apple_receipt_invalid"


def _verify_apple_receipt(receipt_data, *, requires_shared_secret=False):
    if not settings.APPLE_IAP_BUNDLE_ID.strip():
        raise AppleIAPNotConfiguredError()

    verified_payload = _post_apple_verify_receipt(
        APPLE_VERIFY_RECEIPT_PRODUCTION_URL,
        receipt_data=receipt_data,
        include_shared_secret=requires_shared_secret,
    )
    status_code = int(verified_payload.get("status") or 0)
    if status_code == 21007:
        verified_payload = _post_apple_verify_receipt(
            APPLE_VERIFY_RECEIPT_SANDBOX_URL,
            receipt_data=receipt_data,
            include_shared_secret=requires_shared_secret,
        )
        status_code = int(verified_payload.get("status") or 0)
    elif status_code == 21008:
        verified_payload = _post_apple_verify_receipt(
            APPLE_VERIFY_RECEIPT_PRODUCTION_URL,
            receipt_data=receipt_data,
            include_shared_secret=requires_shared_secret,
        )
        status_code = int(verified_payload.get("status") or 0)

    if status_code != 0:
        raise AppleIAPVerificationError(
            _apple_receipt_status_error_code(status_code),
            detail=f"Apple receipt verification failed with status {status_code}.",
            payload=verified_payload,
        )

    receipt = verified_payload.get("receipt") or {}
    bundle_id = (receipt.get("bundle_id") or "").strip()
    expected_bundle_id = settings.APPLE_IAP_BUNDLE_ID.strip()
    if expected_bundle_id and bundle_id and bundle_id != expected_bundle_id:
        raise AppleIAPVerificationError(
            "apple_receipt_bundle_mismatch",
            detail="Apple receipt bundle ID does not match the app bundle ID.",
            payload=verified_payload,
        )

    return verified_payload


def _apple_receipt_items(verified_payload):
    items = []
    latest_items = verified_payload.get("latest_receipt_info")
    if isinstance(latest_items, list):
        items.extend(item for item in latest_items if isinstance(item, dict))
    receipt = verified_payload.get("receipt") or {}
    in_app_items = receipt.get("in_app")
    if isinstance(in_app_items, list):
        items.extend(item for item in in_app_items if isinstance(item, dict))
    return items


def _apple_receipt_item_sort_key(item):
    for key in ("expires_date_ms", "purchase_date_ms", "original_purchase_date_ms"):
        raw = (item.get(key) or "").strip()
        if raw.isdigit():
            return int(raw)
    return 0


def _find_apple_receipt_item(*, verified_payload, expected_product_id, purchase_id=""):
    matching = [
        item for item in _apple_receipt_items(verified_payload)
        if (item.get("product_id") or "").strip() == expected_product_id
    ]
    if not matching:
        raise AppleIAPVerificationError(
            "apple_receipt_product_not_found",
            detail="The Apple receipt does not contain the expected product.",
            payload=verified_payload,
        )

    raw_purchase_id = (purchase_id or "").strip()
    if raw_purchase_id:
        exact = [
            item for item in matching
            if raw_purchase_id in {
                (item.get("transaction_id") or "").strip(),
                (item.get("original_transaction_id") or "").strip(),
            }
        ]
        if exact:
            matching = exact

    matching.sort(key=_apple_receipt_item_sort_key, reverse=True)
    return matching[0]


def _is_subscription_store_product(product):
    return product.product_type in {"employer_subscription", "seeker_subscription"}


def _apple_receipt_item_expires_at(item):
    raw_ms = (item.get("expires_date_ms") or "").strip()
    if raw_ms.isdigit():
        return datetime.fromtimestamp(int(raw_ms) / 1000, tz=datetime_timezone.utc)
    raw = (item.get("expires_date") or "").strip()
    parsed = parse_datetime(raw) if raw else None
    if parsed and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, datetime_timezone.utc)
    return parsed


def _google_subscription_expires_at(payload, *, subscription_id):
    line_items = payload.get("lineItems") or []
    for item in line_items:
        if (item.get("productId") or "").strip() != subscription_id:
            continue
        raw = (item.get("expiryTime") or "").strip()
        parsed = parse_datetime(raw) if raw else None
        if parsed and timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, datetime_timezone.utc)
        return parsed
    return None


def _parse_driver_license_filter_value(raw_value):
    raw = (raw_value or "").strip()
    if not raw:
        return []
    try:
        parsed = normalize_driver_license_categories(
            [part for part in raw.split(",") if part.strip()]
        )
        return parsed
    except ValueError:
        return None


def _parse_audience_country_filter_value(raw_value):
    raw = (raw_value or "").strip()
    if not raw:
        return []
    try:
        parsed = normalize_audience_country_codes(
            [part for part in raw.split(",") if part.strip()]
        )
        return parsed
    except ValueError:
        return None


def _filter_by_driver_license_categories(queryset, categories):
    if not categories:
        return queryset
    license_query = Q()
    for code in categories:
        license_query |= Q(driver_license_categories__contains=f"|{code}|")
    return queryset.filter(license_query)


def _filter_by_audience_country_codes(queryset, codes):
    if not codes:
        return queryset
    audience_query = Q()
    for code in codes:
        audience_query |= Q(audience_country_codes__contains=f"|{code}|")
    return queryset.filter(audience_query)


def _filter_visible_vacancies(queryset, *, now=None):
    current_time = now or timezone.now()
    visible_ids = [
        vacancy.id
        for vacancy in queryset
        if is_employer_profile_visible_for_vacancy(vacancy, now=current_time)
    ]
    return queryset.filter(id__in=visible_ids)


def _economy_overview_payload(user):
    config = get_economy_config()
    wallet = get_or_create_wallet(user)
    profile = get_or_create_monetization_profile(user)
    now = timezone.now()
    wallet_data = UserWalletSerializer(wallet).data
    profile_data = UserMonetizationProfileSerializer(profile).data
    config_data = EconomyConfigSerializer(config).data
    products = (
        StoreProduct.objects.filter(is_active=True)
        .order_by("sort_order", "id")
    )
    products_data = StoreProductSerializer(products, many=True).data

    employer_daily_remaining = int(config.employer_daily_free_submissions_limit or 0)
    if profile.has_employer_subscription(now):
        if profile.employer_daily_submission_date == now.date():
            employer_daily_remaining = max(
                0,
                employer_daily_remaining - int(profile.employer_daily_submissions_used or 0),
            )
    else:
        employer_daily_remaining = 0

    free_create_remaining = max(
        0,
        int(config.free_create_ad_submissions_limit or 0)
        - int(profile.free_create_ad_submissions_used or 0),
    )
    free_edit_remaining = max(
        0,
        int(config.free_edit_ad_resubmissions_limit or 0)
        - int(profile.free_edit_ad_resubmissions_used or 0),
    )

    return {
        "wallet": wallet_data,
        "profile": profile_data,
        "config": config_data,
        "quotas": {
            "free_create_ad_submissions_remaining": free_create_remaining,
            "free_edit_ad_resubmissions_remaining": free_edit_remaining,
            "employer_daily_free_submissions_remaining": employer_daily_remaining,
        },
        "products": {
            "credit_packs": [
                item for item in products_data if item["product_type"] == "credits"
            ],
            "employer_subscriptions": [
                item
                for item in products_data
                if item["product_type"] == "employer_subscription"
            ],
            "seeker_subscriptions": [
                item
                for item in products_data
                if item["product_type"] == "seeker_subscription"
            ],
        },
    }


def _purchase_transaction_id_from_payload(verified_payload, *, fallback_transaction_id, purchase_token):
    verified_payload = verified_payload or {}
    for candidate in [
        verified_payload.get("latestSuccessfulOrderId"),
        verified_payload.get("latestOrderId"),
        verified_payload.get("orderId"),
        fallback_transaction_id,
        purchase_token,
    ]:
        normalized = (candidate or "").strip()
        if normalized:
            return normalized
    raise ValidationError("purchase_transaction_id_required")


def _transliterate_ru_uk_to_latin(value):
    """
    Lightweight transliteration for RU/UK queries.
    Used only as a search fallback to improve multilingual city lookup.
    """
    src = (value or "").strip()
    if not src:
        return ""

    table = {
        "а": "a", "б": "b", "в": "v", "г": "g", "ґ": "g", "д": "d", "е": "e", "ё": "e",
        "є": "ie", "ж": "zh", "з": "z", "и": "i", "і": "i", "ї": "yi", "й": "y",
        "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s",
        "т": "t", "у": "u", "ў": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
        "ш": "sh", "щ": "shch", "ь": "", "ъ": "", "ы": "y", "э": "e", "ю": "yu", "я": "ya",
    }

    out = []
    for ch in src:
        lower = ch.lower()
        mapped = table.get(lower)
        if mapped is None:
            out.append(ch.lower())
        else:
            out.append(mapped)
    return "".join(out).strip()


class VacancyListAPIView(generics.ListAPIView):
    serializer_class = VacancyListSerializer

    def get_queryset(self):
        current_time = timezone.now()
        qs = Vacancy.objects.filter(
            is_approved=True,
            is_paused_by_owner=False,
            is_deleted_by_moderator=False,
            expires_at__gt=current_time
        ).select_related(
            "contact_access_policy",
            "created_by",
            "created_by__profile",
        ).order_by("-published_at")

        country = self.request.query_params.get("country")
        city = self.request.query_params.get("city")
        city_code = (self.request.query_params.get("city_code") or "").strip().lower()
        category = self.request.query_params.get("category")
        employment_type = self.request.query_params.get("employment_type")
        source = self.request.query_params.get("source")
        housing_type = self.request.query_params.get("housing_type")
        audience_countries = _parse_audience_country_filter_value(
            self.request.query_params.get("audience_countries")
        )
        driver_license_categories = _parse_driver_license_filter_value(
            self.request.query_params.get("driver_license_categories")
        )
        search = self.request.query_params.get("search")
        search_alt = self.request.query_params.get("search_alt")
        subscribed = (self.request.query_params.get("subscribed") or "").strip().lower()

        if country:
            qs = qs.filter(country=country)
        if city_code and city:
            qs = qs.filter(Q(city_code=city_code) | Q(city__icontains=city))
        elif city_code:
            qs = qs.filter(city_code=city_code)
        elif city:
            qs = qs.filter(city__icontains=city)
        if category:
            qs = qs.filter(category=category)
        if employment_type:
            qs = qs.filter(employment_type=employment_type)
        if source:
            qs = qs.filter(source=source)
        if housing_type:
            qs = qs.filter(housing_type=housing_type)
        if audience_countries is None:
            return qs.none()
        if audience_countries:
            qs = _filter_by_audience_country_codes(qs, audience_countries)
        if driver_license_categories is None:
            return qs.none()
        if driver_license_categories:
            qs = _filter_by_driver_license_categories(qs, driver_license_categories)
        if subscribed in {"1", "true", "yes", "on"}:
            if not self.request.user.is_authenticated:
                return qs.none()
            qs = qs.filter(
                created_by__employer_followers__subscriber=self.request.user
            ).distinct()
        if search or search_alt:
            raw_terms = []
            if search:
                raw_terms.append(search)
            if search_alt:
                raw_terms.extend(search_alt.split("||"))

            terms = []
            seen_terms = set()
            for term in raw_terms:
                normalized = (term or "").strip()
                key = normalized.lower()
                if normalized and key not in seen_terms:
                    terms.append(normalized)
                    seen_terms.add(key)
                translit = _transliterate_ru_uk_to_latin(normalized)
                translit_key = translit.lower()
                if translit and translit_key not in seen_terms:
                    terms.append(translit)
                    seen_terms.add(translit_key)

            if terms:
                search_q = Q()
                for term in terms:
                    search_q |= Q(title__icontains=term) | Q(city__icontains=term)
                    compact = term.strip().lower()
                    if len(compact) >= 5 and " " not in compact:
                        # Fallback for close latin variants (e.g. lelistad -> lelystad).
                        prefix = compact[:3]
                        suffix = compact[-3:]
                        search_q |= (
                            (Q(title__istartswith=prefix) & Q(title__iendswith=suffix))
                            | (Q(city__istartswith=prefix) & Q(city__iendswith=suffix))
                        )
                qs = qs.filter(search_q)

        if self.request.user.is_authenticated:
            qs = qs.exclude(created_by__incoming_blocks__blocker=self.request.user)
            qs = qs.annotate(
                is_owner_subscribed=Exists(
                    EmployerSubscription.objects.filter(
                        subscriber=self.request.user,
                        employer_id=OuterRef("created_by_id"),
                    )
                )
            )

        if subscribed in {"1", "true", "yes", "on"}:
            qs = _filter_visible_vacancies(qs, now=current_time)

        return qs


def _public_vacancy_error_response(vacancy, *, now=None):
    if not vacancy:
        return Response({"error": "vacancy_not_found"}, status=status.HTTP_404_NOT_FOUND)
    if vacancy.is_deleted_by_moderator:
        return Response({"error": "vacancy_deleted"}, status=status.HTTP_410_GONE)

    current_time = now or timezone.now()
    if vacancy.is_paused_by_owner or not vacancy.is_approved or vacancy.expires_at <= current_time:
        return Response({"error": "vacancy_not_found"}, status=status.HTTP_404_NOT_FOUND)
    return None


class VacancyDetailAPIView(APIView):
    def get(self, request, pk):
        vacancy = Vacancy.objects.select_related("created_by", "created_by__profile").filter(pk=pk).first()
        if _is_moderator(request):
            if not vacancy:
                return Response({"error": "vacancy_not_found"}, status=status.HTTP_404_NOT_FOUND)
            if vacancy.is_deleted_by_moderator:
                return Response({"error": "vacancy_deleted"}, status=status.HTTP_410_GONE)
        else:
            error_response = _public_vacancy_error_response(vacancy)
            if error_response is not None:
                return error_response
        serializer = VacancyDetailSerializer(vacancy, context={"request": request})
        payload = dict(serializer.data)
        if _is_moderator(request):
            payload["employer_summary"] = _build_employer_moderation_summary(
                vacancy.created_by,
            )
        return Response(payload, status=200)


class VacancyBookmarkStatusAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    max_ids = 100

    def post(self, request):
        raw_ids = request.data.get("ids", [])
        if not isinstance(raw_ids, list):
            return Response({"error": "ids_must_be_list"}, status=status.HTTP_400_BAD_REQUEST)

        normalized_ids = []
        seen = set()
        for raw_id in raw_ids:
            try:
                vacancy_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if vacancy_id <= 0 or vacancy_id in seen:
                continue
            seen.add(vacancy_id)
            normalized_ids.append(vacancy_id)
            if len(normalized_ids) > self.max_ids:
                return Response(
                    {"error": "too_many_ids", "max_ids": self.max_ids},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if not normalized_ids:
            return Response({"count": 0, "results": []}, status=status.HTTP_200_OK)

        now = timezone.now()
        vacancies = Vacancy.objects.filter(id__in=normalized_ids).only(
            "id",
            "is_deleted_by_moderator",
            "is_paused_by_owner",
            "is_approved",
            "expires_at",
        )
        vacancies_by_id = {vacancy.id: vacancy for vacancy in vacancies}
        results = []
        for vacancy_id in normalized_ids:
            vacancy = vacancies_by_id.get(vacancy_id)
            results.append(
                {
                    "id": vacancy_id,
                    "status": (
                        _vacancy_bookmark_status(vacancy, now=now)
                        if vacancy
                        else "unavailable"
                    ),
                }
            )

        return Response(
            {"count": len(results), "results": results},
            status=status.HTTP_200_OK,
        )


class VacancyBlockOwnerAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        vacancy = Vacancy.objects.select_related("created_by").filter(pk=pk).first()
        if not vacancy or vacancy.is_deleted_by_moderator:
            return Response({"error": "vacancy_not_found"}, status=status.HTTP_404_NOT_FOUND)

        owner = vacancy.created_by
        if owner.id == request.user.id:
            return Response({"error": "cannot_block_self"}, status=status.HTTP_400_BAD_REQUEST)

        _, created = UserBlock.objects.get_or_create(
            blocker=request.user,
            blocked_user=owner,
        )
        return Response({"status": "blocked", "created": created}, status=status.HTTP_200_OK)

    def delete(self, request, pk):
        vacancy = Vacancy.objects.select_related("created_by").filter(pk=pk).first()
        if not vacancy or vacancy.is_deleted_by_moderator:
            return Response({"error": "vacancy_not_found"}, status=status.HTTP_404_NOT_FOUND)

        owner = vacancy.created_by
        if owner.id == request.user.id:
            return Response({"error": "cannot_block_self"}, status=status.HTTP_400_BAD_REQUEST)

        deleted_count, _ = UserBlock.objects.filter(
            blocker=request.user,
            blocked_user=owner,
        ).delete()
        return Response({"status": "unblocked", "deleted": bool(deleted_count)}, status=status.HTTP_200_OK)


class UserBlockListAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        blocks = (
            UserBlock.objects.filter(blocker=request.user)
            .select_related("blocked_user", "blocked_user__profile")
            .order_by("-created_at")
        )
        results = [
            {
                "blocked_user_id": block.blocked_user_id,
                "blocked_user_email": (block.blocked_user.email or "").strip(),
                "blocked_user_username": block.blocked_user.username,
                "blocked_user_nickname": _owner_nickname_or_fallback(block.blocked_user),
                "blocked_user_avatar_url": _owner_avatar_url(block.blocked_user),
                "created_at": block.created_at,
            }
            for block in blocks
        ]
        return Response({"count": len(results), "results": results}, status=status.HTTP_200_OK)


class UserBlockRemoveAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, blocked_user_id):
        deleted_count, _ = UserBlock.objects.filter(
            blocker=request.user,
            blocked_user_id=blocked_user_id,
        ).delete()
        if deleted_count == 0:
            return Response({"error": "block_not_found"}, status=status.HTTP_404_NOT_FOUND)
        return Response({"status": "unblocked"}, status=status.HTTP_200_OK)


class VacancyCreateAPIView(generics.CreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = VacancyCreateSerializer

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        # include creator_token in response for client storage
        return response

    def get_serializer_context(self):
        context = super().get_serializer_context()
        save_as_draft_raw = str(self.request.data.get("save_as_draft", "")).strip().lower()
        context["draft_mode"] = save_as_draft_raw in ("1", "true", "yes", "on")
        return context

    def perform_create(self, serializer):
        profile = UserProfile.objects.filter(user=self.request.user).first()
        if not profile or not profile.phone_verified:
            raise ValidationError({"error": "phone_verification_required"})
        is_moderator = _is_moderator(self.request)
        now = timezone.now()
        token = secrets.token_hex(32)
        save_as_draft_raw = str(self.request.data.get("save_as_draft", "")).strip().lower()
        save_as_draft = save_as_draft_raw in ("1", "true", "yes", "on")
        submission_method = (self.request.data.get("submission_method") or "").strip().lower()
        notify_moderators = False
        try:
            with transaction.atomic():
                vacancy = serializer.save(
                    created_by=self.request.user,
                    is_approved=is_moderator and not save_as_draft,
                    approved_at=now if (is_moderator and not save_as_draft) else None,
                    is_rejected=False,
                    is_paused_by_owner=False,
                    paused_by_owner_at=None,
                    rejection_reason="",
                    last_moderator_rejection_reason="",
                    moderation_baseline={},
                    is_editing=save_as_draft,
                    revision=1,
                    # Reuse this field as "submitted_at" for moderation queue ordering.
                    editing_started_at=now,
                    creator_token=token,
                    expires_at=now + VACANCY_LIVE_WINDOW,
                )
                ensure_free_contact_policy(vacancy, set_by=self.request.user)
                if not is_moderator and not save_as_draft:
                    _create_moderation_attempt(
                        vacancy,
                        trigger_type="create",
                        submitted_by=self.request.user,
                        submitted_at=now,
                    )
                    notify_moderators = True
                    apply_vacancy_submission_action(
                        self.request.user,
                        flow="create",
                        method=submission_method,
                        related_vacancy=vacancy,
                        now=now,
                    )
        except EconomyActionRequiredError as exc:
            raise ValidationError(
                {
                    "error": exc.code,
                    "submission_state": exc.state,
                }
            )
        except InsufficientCreditsError:
            raise ValidationError(
                {
                    "error": "insufficient_credits",
                    "submission_state": build_vacancy_submission_state(
                        self.request.user,
                        flow="create",
                        now=now,
                    ),
                }
            )
        if notify_moderators:
            transaction.on_commit(lambda: _notify_moderators_about_pending_vacancy_safe(vacancy))
        if vacancy.is_approved and not vacancy.is_deleted_by_moderator:
            try:
                summary = dispatch_vacancy_alerts(vacancy)
                print(f"[VACANCY-ALERTS] vacancy={vacancy.id} summary={summary}")
            except Exception as exc:
                print(f"[VACANCY-ALERTS-ERROR] vacancy={vacancy.id}: {exc}")


def _internal_import_token_from_request(request):
    header_token = (request.headers.get("X-Internal-Import-Token") or "").strip()
    if header_token:
        return header_token
    auth_header = (request.headers.get("Authorization") or "").strip()
    prefix = "Bearer "
    if auth_header.startswith(prefix):
        return auth_header[len(prefix):].strip()
    return ""


class InternalVacancyImportAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        configured_token = (settings.INTERNAL_IMPORT_TOKEN or "").strip()
        request_token = _internal_import_token_from_request(request)
        if not configured_token:
            return Response(
                {"error": "internal_import_not_configured"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not request_token or not compare_digest(request_token, configured_token):
            return Response(
                {"error": "invalid_internal_import_token"},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = InternalVacancyImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        title = (data.get("title") or "").strip()
        city = (data.get("city") or "").strip()
        phone = (data.get("phone") or "").strip()

        now = timezone.now()
        service_user, created = User.objects.get_or_create(
            username=SERVICE_BOARD_USERNAME,
            defaults={"email": ""},
        )
        if created:
            service_user.set_unusable_password()
            service_user.save(update_fields=["password"])

        duplicate = None
        if title and city and phone:
            duplicate = (
                Vacancy.objects.filter(
                    created_by=service_user,
                    title=title,
                    city=city,
                    phone=phone,
                    is_deleted_by_moderator=False,
                )
                .order_by("-id")
                .first()
            )
        if duplicate:
            return Response(
                {
                    "error": "duplicate_import",
                    "vacancy_id": duplicate.id,
                    "moderation_status": duplicate.moderation_status,
                },
                status=status.HTTP_409_CONFLICT,
            )

        source_url = (data.get("source_url") or "").strip()
        source_text = (data.get("source_text") or "").strip()
        extraction_notes = (data.get("extraction_notes") or "").strip()
        requested_moderation_status = (
            request.data.get("moderation_status") or ""
        ).strip().lower()
        create_pending = requested_moderation_status == "pending"
        extra_context = {
            "import_source": "internal_import",
            "source_url": source_url,
            "source_text": source_text,
            "extraction_notes": extraction_notes,
        }

        with transaction.atomic():
            vacancy = serializer.save(
                created_by=service_user,
                creator_token=secrets.token_hex(32),
                approved_at=None if create_pending else now,
                expires_at=now + VACANCY_LIVE_WINDOW,
                is_approved=not create_pending,
                is_rejected=False,
                rejection_reason="",
                moderation_baseline={},
                last_moderator_rejection_reason="",
                is_deleted_by_moderator=False,
                is_paused_by_owner=False,
                is_editing=False,
                editing_started_at=now if create_pending else None,
                revision=1,
            )
            if create_pending:
                _create_moderation_attempt(
                    vacancy,
                    trigger_type="create",
                    submitted_by=service_user,
                    submitted_at=now,
                    extra_context=extra_context,
                )
            VacancyContactAccessPolicy.objects.update_or_create(
                vacancy=vacancy,
                defaults={
                    "contact_unlock_mode": "ad_forever",
                    "contact_unlock_timer_hours": None,
                    "contact_unlock_price_credits": 0,
                    "contact_unlock_paid_click_limit": None,
                    "paid_window_started_at": None,
                    "set_by": service_user,
                },
            )

        if create_pending:
            transaction.on_commit(lambda: _notify_moderators_about_pending_vacancy_safe(vacancy))

        if vacancy.is_approved:
            try:
                summary = dispatch_vacancy_alerts(vacancy)
                print(f"[VACANCY-ALERTS] vacancy={vacancy.id} summary={summary}")
            except Exception as exc:
                print(f"[VACANCY-ALERTS-ERROR] vacancy={vacancy.id}: {exc}")

        return Response(
            {
                "status": "created",
                "vacancy_id": vacancy.id,
                "moderation_status": vacancy.moderation_status,
            },
            status=status.HTTP_201_CREATED,
        )


class InternalVacancyDeleteAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        configured_token = (settings.INTERNAL_IMPORT_TOKEN or "").strip()
        request_token = _internal_import_token_from_request(request)
        if not configured_token:
            return Response(
                {"error": "internal_import_not_configured"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not request_token or not compare_digest(request_token, configured_token):
            return Response(
                {"error": "invalid_internal_import_token"},
                status=status.HTTP_403_FORBIDDEN,
            )

        raw_ids = request.data.get("vacancy_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            return Response(
                {"error": "vacancy_ids_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        vacancy_ids = []
        for raw_id in raw_ids:
            try:
                vacancy_id = int(raw_id)
            except (TypeError, ValueError):
                return Response(
                    {"error": "invalid_vacancy_id", "value": raw_id},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if vacancy_id > 0:
                vacancy_ids.append(vacancy_id)

        service_user = User.objects.filter(username=SERVICE_BOARD_USERNAME).first()
        if not service_user:
            return Response({"deleted": [], "skipped": vacancy_ids}, status=status.HTTP_200_OK)

        now = timezone.now()
        deleted = []
        with transaction.atomic():
            vacancies = (
                Vacancy.objects.select_for_update()
                .filter(
                    id__in=vacancy_ids,
                    created_by=service_user,
                    is_deleted_by_moderator=False,
                )
                .order_by("id")
            )
            for vacancy in vacancies:
                vacancy.is_approved = False
                vacancy.is_rejected = True
                vacancy.is_paused_by_owner = False
                vacancy.paused_by_owner_at = None
                vacancy.is_editing = False
                vacancy.rejection_reason = "Removed by internal import cleanup"
                vacancy.moderation_baseline = {}
                vacancy.last_moderator_rejection_reason = vacancy.rejection_reason
                vacancy.editing_started_at = None
                vacancy.is_deleted_by_moderator = True
                vacancy.deleted_by_moderator_at = now
                vacancy.save(
                    update_fields=[
                        "is_approved",
                        "is_rejected",
                        "is_paused_by_owner",
                        "paused_by_owner_at",
                        "is_editing",
                        "rejection_reason",
                        "moderation_baseline",
                        "last_moderator_rejection_reason",
                        "editing_started_at",
                        "is_deleted_by_moderator",
                        "deleted_by_moderator_at",
                    ]
                )
                deleted.append(vacancy.id)

        skipped = [vacancy_id for vacancy_id in vacancy_ids if vacancy_id not in deleted]
        return Response({"deleted": deleted, "skipped": skipped}, status=status.HTTP_200_OK)


def _is_moderator(request):
    return request.user.is_authenticated and request.user.is_staff


def _auto_pause_due_owner_vacancies(owner):
    """
    Auto-pause approved live vacancies after the live window elapses
    from the latest approved publication time.
    """
    if not owner or not owner.is_authenticated:
        return 0

    now = timezone.now()
    cutoff = now - VACANCY_LIVE_WINDOW
    due_qs = (
        Vacancy.objects.filter(
            created_by=owner,
            is_approved=True,
            is_paused_by_owner=False,
            is_deleted_by_moderator=False,
        )
        .filter(published_at__lte=cutoff)
    )
    updated = due_qs.update(
        is_paused_by_owner=True,
        paused_by_owner_at=now,
    )
    return updated


def _set_vacancy_live(vacancy, *, now=None):
    current_time = now or timezone.now()
    vacancy.is_approved = True
    vacancy.approved_at = current_time
    vacancy.is_rejected = False
    vacancy.is_paused_by_owner = False
    vacancy.paused_by_owner_at = None
    vacancy.rejection_reason = ""
    vacancy.last_moderator_rejection_reason = ""
    vacancy.moderation_baseline = {}
    vacancy.is_editing = False
    vacancy.editing_started_at = None
    vacancy.published_at = current_time
    vacancy.expires_at = current_time + VACANCY_LIVE_WINDOW


def _masked_email(email):
    value = (email or "").strip()
    if not value or "@" not in value:
        return ""
    local, domain = value.split("@", 1)
    if not local:
        return f"***@{domain}"
    if len(local) == 1:
        return f"{local}***@{domain}"
    return f"{local[:2]}***@{domain}"


def _owner_nickname_or_fallback(owner):
    profile = getattr(owner, "profile", None)
    nickname = (getattr(profile, "nickname", "") or "").strip() if profile else ""
    if nickname:
        return nickname
    return f"Employer #{owner.id}"


def _owner_avatar_url(owner):
    profile = getattr(owner, "profile", None)
    avatar_key = (getattr(profile, "avatar_key", "") or "").strip() if profile else ""
    return avatar_public_url(avatar_key)


def _subscriber_count_for_owner(owner):
    owner_id = getattr(owner, "id", None)
    if not owner_id:
        return 0
    return EmployerSubscription.objects.filter(employer_id=owner_id).count()


def _count_map(rows, key_field):
    return {
        int(row[key_field]): int(row.get("total", 0) or 0)
        for row in rows
        if row.get(key_field) is not None
    }


def _build_employer_moderation_summary_map(owner_ids, *, now=None):
    owner_ids = [int(owner_id) for owner_id in owner_ids if owner_id]
    if not owner_ids:
        return {}

    now = now or timezone.now()
    owners = {
        owner.id: owner
        for owner in User.objects.filter(id__in=owner_ids).select_related("profile")
    }

    active_counts = _count_map(
        Vacancy.objects.filter(
            created_by_id__in=owner_ids,
            is_approved=True,
            is_paused_by_owner=False,
            is_deleted_by_moderator=False,
            expires_at__gt=now,
        )
        .values("created_by_id")
        .annotate(total=Count("id")),
        "created_by_id",
    )
    editing_counts = _count_map(
        Vacancy.objects.filter(
            created_by_id__in=owner_ids,
            is_editing=True,
            is_deleted_by_moderator=False,
        )
        .values("created_by_id")
        .annotate(total=Count("id")),
        "created_by_id",
    )
    rejected_counts = _count_map(
        Vacancy.objects.filter(
            created_by_id__in=owner_ids,
            is_rejected=True,
            is_deleted_by_moderator=False,
        )
        .values("created_by_id")
        .annotate(total=Count("id")),
        "created_by_id",
    )
    complaints_on_vacancies_counts = _count_map(
        Complaint.objects.filter(vacancy__created_by_id__in=owner_ids)
        .values("vacancy__created_by_id")
        .annotate(total=Count("id")),
        "vacancy__created_by_id",
    )
    complaints_submitted_counts = _count_map(
        Complaint.objects.filter(reporter_id__in=owner_ids)
        .values("reporter_id")
        .annotate(total=Count("id")),
        "reporter_id",
    )

    summary_map = {}
    for owner_id in owner_ids:
        owner = owners.get(owner_id)
        if not owner:
            continue
        payload = {
            "owner_user_id": owner.id,
            "nickname": _owner_nickname_or_fallback(owner),
            "avatar_url": _owner_avatar_url(owner),
            "active_vacancies_count": active_counts.get(owner_id, 0),
            "editing_vacancies_count": editing_counts.get(owner_id, 0),
            "rejected_vacancies_count": rejected_counts.get(owner_id, 0),
            "complaints_on_vacancies_count": complaints_on_vacancies_counts.get(
                owner_id,
                0,
            ),
            "complaints_submitted_count": complaints_submitted_counts.get(
                owner_id,
                0,
            ),
        }
        payload.update(service_board_meta_for_user(owner))
        summary_map[owner_id] = payload
    return summary_map


def _build_employer_moderation_summary(owner, *, now=None):
    if not owner:
        return {}
    return _build_employer_moderation_summary_map([owner.id], now=now).get(
        owner.id,
        {},
    )


def _vacancy_editable_snapshot(vacancy):
    return {
        "title": vacancy.title or "",
        "country": vacancy.country or "",
        "city": vacancy.city or "",
        "city_code": vacancy.city_code or "",
        "category": vacancy.category or "",
        "audience_country_codes": vacancy.audience_country_codes or "",
        "employment_type": vacancy.employment_type or "",
        "experience_required": vacancy.experience_required or "",
        "driver_license_categories": vacancy.driver_license_categories or "",
        "salary_from": vacancy.salary_from,
        "salary_to": vacancy.salary_to,
        "salary_currency": vacancy.salary_currency or "",
        "salary_tax_type": vacancy.salary_tax_type or "",
        "salary_hours_month": vacancy.salary_hours_month,
        "description": vacancy.description or "",
        "housing_type": vacancy.housing_type or "",
        "housing_cost": vacancy.housing_cost or "",
        "phone": vacancy.phone or "",
        "additional_phone": vacancy.additional_phone or "",
        "additional_phone_2": vacancy.additional_phone_2 or "",
        "additional_phone_3": vacancy.additional_phone_3 or "",
        "hide_primary_phone": bool(vacancy.hide_primary_phone),
        "whatsapp": vacancy.whatsapp or "",
        "viber": vacancy.viber or "",
        # Do not expose the legacy phone-backed Telegram field.
        "telegram": vacancy.telegram_username or "",
        "telegram_username": vacancy.telegram_username or "",
        "email": vacancy.email or "",
        "source": vacancy.source or "",
    }


def _vacancy_moderation_state_snapshot(vacancy):
    return {
        "is_approved": bool(vacancy.is_approved),
        "is_rejected": bool(vacancy.is_rejected),
        "is_paused_by_owner": bool(vacancy.is_paused_by_owner),
        "paused_by_owner_at": (
            vacancy.paused_by_owner_at.isoformat()
            if vacancy.paused_by_owner_at
            else ""
        ),
        "is_editing": bool(vacancy.is_editing),
        "rejection_reason": vacancy.rejection_reason or "",
        "last_moderator_rejection_reason": vacancy.last_moderator_rejection_reason or "",
        "moderation_baseline": vacancy.moderation_baseline or {},
    }


def _next_moderation_attempt_no(vacancy):
    max_no = vacancy.moderation_attempts.aggregate(max_no=Max("attempt_no"))["max_no"] or 0
    return int(max_no) + 1


def _create_moderation_attempt(
    vacancy,
    *,
    trigger_type,
    submitted_by=None,
    submitted_at=None,
    extra_context=None,
):
    return VacancyModerationAttempt.objects.create(
        vacancy=vacancy,
        attempt_no=_next_moderation_attempt_no(vacancy),
        trigger_type=trigger_type,
        submitted_by=submitted_by,
        submitted_at=submitted_at or timezone.now(),
        decision="pending",
        extra_context=extra_context or {},
    )


def _submission_flow_for_vacancy(vacancy):
    if vacancy.is_editing and not vacancy.moderation_attempts.exists():
        return "create"
    return "edit_resubmit"


def _resolve_latest_moderation_attempt(
    vacancy,
    *,
    decision,
    moderator=None,
    reason="",
    decided_at=None,
):
    resolved_at = decided_at or timezone.now()
    attempt = vacancy.moderation_attempts.filter(decision="pending").order_by("-attempt_no").first()
    if attempt is None:
        attempt = VacancyModerationAttempt.objects.create(
            vacancy=vacancy,
            attempt_no=_next_moderation_attempt_no(vacancy),
            trigger_type="moderator_resubmit",
            submitted_by=vacancy.created_by,
            submitted_at=resolved_at,
            decision=decision,
            decided_at=resolved_at,
            decided_by=moderator,
            rejection_reason=reason or "",
            extra_context={"auto_created": True},
        )
        return attempt

    attempt.decision = decision
    attempt.decided_at = resolved_at
    attempt.decided_by = moderator
    attempt.rejection_reason = reason or ""
    attempt.save(
        update_fields=[
            "decision",
            "decided_at",
            "decided_by",
            "rejection_reason",
        ]
    )
    return attempt


def _owner_moderation_attempts_qs(vacancy):
    return vacancy.moderation_attempts.filter(
        trigger_type__in=["edit", "restore", "resume_expired"],
        submitted_by=vacancy.created_by,
    )


def _owner_moderation_limit_payload(vacancy, *, now=None):
    current_time = now or timezone.now()
    local_today = timezone.localtime(current_time).date()
    attempts_qs = _owner_moderation_attempts_qs(vacancy)
    last_attempt = attempts_qs.order_by("-submitted_at").first()
    today_count = attempts_qs.filter(submitted_at__date=local_today).count()

    if today_count >= OWNER_MODERATION_RESUBMIT_MAX_PER_DAY:
        next_day = datetime.combine(
            local_today + timedelta(days=1),
            time.min,
            tzinfo=current_time.tzinfo,
        )
        remaining_seconds = max(int((next_day - current_time).total_seconds()), 1)
        return {
            "error": "moderation_submission_daily_limit",
            "remaining_seconds": remaining_seconds,
            "submissions_today": today_count,
        }

    if last_attempt and current_time - last_attempt.submitted_at < OWNER_MODERATION_RESUBMIT_MIN_INTERVAL:
        remaining_seconds = max(
            int((OWNER_MODERATION_RESUBMIT_MIN_INTERVAL - (current_time - last_attempt.submitted_at)).total_seconds()),
            1,
        )
        return {
            "error": "moderation_submission_cooldown",
            "remaining_seconds": remaining_seconds,
            "submissions_today": today_count,
        }

    return None


def _owner_resume_cooldown(vacancy, *, now=None):
    current_time = now or timezone.now()
    local_today = timezone.localtime(current_time).date()
    resume_day = vacancy.owner_resume_day
    count_today = vacancy.owner_resume_count_day or 0
    if resume_day != local_today:
        count_today = 0

    cooldown = OWNER_RESUME_FIRST_COOLDOWN
    if count_today >= OWNER_RESUME_REPEAT_THRESHOLD:
        cooldown = OWNER_RESUME_REPEAT_COOLDOWN
    elif count_today >= 1:
        cooldown = OWNER_RESUME_FIRST_COOLDOWN

    last_resume = vacancy.last_owner_resume_at
    if last_resume and count_today >= 1:
        delta = current_time - last_resume
        if delta < cooldown:
            return max(int((cooldown - delta).total_seconds()), 1)
    return 0


def _register_owner_resume(vacancy, *, now=None):
    current_time = now or timezone.now()
    local_today = timezone.localtime(current_time).date()
    count_today = vacancy.owner_resume_count_day or 0
    if vacancy.owner_resume_day != local_today:
        count_today = 0
    vacancy.owner_resume_day = local_today
    vacancy.owner_resume_count_day = count_today + 1
    vacancy.last_owner_resume_at = current_time


def _vacancy_bookmark_status(vacancy, *, now=None):
    current_time = now or timezone.now()
    if vacancy.is_deleted_by_moderator:
        return "deleted"
    if vacancy.is_paused_by_owner:
        return "paused"
    if vacancy.expires_at <= current_time:
        return "expired"
    if not vacancy.is_approved:
        return "unavailable"
    return "active"


def _notify_vacancy_owner_about_complaint_action(
    *,
    vacancy,
    complaint,
    action,
    moderator,
    note="",
    reject_reason="",
):
    owner_email = (getattr(vacancy.created_by, "email", "") or "").strip()
    if not owner_email:
        return False, "owner_email_missing"

    action_title = {
        "delete_forever": "deleted forever",
        "reject": "rejected",
        "restore": "restored",
    }.get(action, action)

    subject = f"JobHub moderation update for vacancy #{vacancy.id}"
    body_lines = [
        f"Vacancy ID: {vacancy.id}",
        f"Title: {vacancy.title}",
        f"Action: {action_title}",
        f"Reason from complaint: {complaint.reason}",
        f"Moderator: {moderator.email or moderator.username}",
    ]
    if reject_reason:
        body_lines.append(f"Reject reason: {reject_reason}")
    if note:
        body_lines.append(f"Note: {note}")
    body_lines.extend(
        [
            "",
            f"Support: {getattr(settings, 'SUPPORT_EMAIL', settings.DEFAULT_FROM_EMAIL)}",
        ]
    )

    try:
        send_mail(
            subject,
            "\n".join(body_lines),
            settings.DEFAULT_FROM_EMAIL,
            [owner_email],
            fail_silently=False,
        )
        return True, ""
    except Exception as exc:
        print(f"[COMPLAINT-OWNER-NOTIFY-ERROR] vacancy={vacancy.id}: {exc}")
        return False, str(exc)


def _notify_vacancy_owner_about_reject(*, vacancy, moderator, reason=""):
    owner_email = (getattr(vacancy.created_by, "email", "") or "").strip()
    if not owner_email:
        return False, "owner_email_missing"

    subject = f"JobHub moderation: vacancy #{vacancy.id} rejected"
    body_lines = [
        f"Vacancy ID: {vacancy.id}",
        f"Title: {vacancy.title}",
        f"Action: rejected",
        f"Moderator: {moderator.email or moderator.username}",
        f"Reason: {reason or '-'}",
        "",
        f"Support: {getattr(settings, 'SUPPORT_EMAIL', settings.DEFAULT_FROM_EMAIL)}",
    ]

    try:
        send_mail(
            subject,
            "\n".join(body_lines),
            settings.DEFAULT_FROM_EMAIL,
            [owner_email],
            fail_silently=False,
        )
        return True, ""
    except Exception as exc:
        print(f"[VACANCY-REJECT-NOTIFY-ERROR] vacancy={vacancy.id}: {exc}")
        return False, str(exc)


class IsModerator(permissions.BasePermission):
    def has_permission(self, request, view):
        return _is_moderator(request)


class PushDeviceAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = PushDeviceRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        token = payload["token"]
        platform = payload.get("platform") or "android"
        app_language = payload.get("app_language") or ""

        PushDevice.objects.filter(token=token).exclude(user=request.user).update(is_active=False)
        device, created = PushDevice.objects.update_or_create(
            user=request.user,
            token=token,
            defaults={
                "platform": platform,
                "app_language": app_language,
                "is_active": True,
            },
        )
        return Response(
            {
                "detail": "device_registered",
                "created": created,
                "platform": device.platform,
                "app_language": device.app_language,
                "is_active": bool(device.is_active),
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request):
        token = (request.data.get("token") or request.query_params.get("token") or "").strip()
        devices = PushDevice.objects.filter(user=request.user, is_active=True)
        if token:
            devices = devices.filter(token=token)
        updated = devices.update(is_active=False)
        return Response(
            {
                "detail": "device_deactivated",
                "updated": int(updated),
            },
            status=status.HTTP_200_OK,
        )


class VacancyAlertSubscriptionAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        subscription, _ = VacancyAlertSubscription.objects.get_or_create(user=request.user)
        serializer = VacancyAlertSubscriptionSerializer(subscription)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request):
        return self._save(request, partial=False)

    def patch(self, request):
        return self._save(request, partial=True)

    def _save(self, request, *, partial):
        subscription, _ = VacancyAlertSubscription.objects.get_or_create(user=request.user)
        serializer = VacancyAlertSubscriptionSerializer(
            subscription,
            data=request.data,
            partial=partial,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save(user=request.user)
        payload = dict(serializer.data)
        payload["detail"] = "vacancy_alert_subscription_updated"
        return Response(payload, status=status.HTTP_200_OK)


class VacancyAlertPreviewAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, vacancy_id):
        vacancy = Vacancy.objects.filter(id=vacancy_id).first()
        if not vacancy:
            return Response({"error": "vacancy_not_found"}, status=status.HTTP_404_NOT_FOUND)
        preview = preview_vacancy_alerts(vacancy)
        return Response(preview, status=status.HTTP_200_OK)


class EconomyOverviewAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(_economy_overview_payload(request.user), status=status.HTTP_200_OK)


class WalletTransactionListAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        transactions = (
            WalletTransaction.objects.filter(user=request.user)
            .select_related("related_vacancy")
            .order_by("-created_at", "-id")
        )
        paginator = PageNumberPagination()
        paginator.page_size = 30
        page = paginator.paginate_queryset(transactions, request, view=self)
        serializer = WalletTransactionSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class GooglePlayPurchaseCompleteAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = GooglePlayPurchaseCompleteSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        product = serializer.context["store_product"]
        purchase_token = payload["purchase_token"]
        raw_purchase_id = (payload.get("purchase_id") or "").strip()

        try:
            if product.product_type == "credits":
                verified_payload = verify_google_play_product_purchase(
                    product_id=(product.store_product_id or "").strip(),
                    purchase_token=purchase_token,
                )
                entitlement_expires_at = None
            else:
                subscription_id = (product.store_product_id or "").strip()
                verified_payload = verify_google_play_subscription_purchase(
                    subscription_id=subscription_id,
                    purchase_token=purchase_token,
                )
                entitlement_expires_at = _google_subscription_expires_at(
                    verified_payload,
                    subscription_id=subscription_id,
                )
        except GooglePlayNotConfiguredError as exc:
            return Response(
                {
                    "error": exc.code,
                    "message": "Google Play verification is not configured on the backend.",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except GooglePlayVerificationError as exc:
            return Response(
                {
                    "error": exc.code,
                    "message": exc.detail or exc.code,
                    "payload": exc.payload,
                },
                status=status.HTTP_409_CONFLICT,
            )

        transaction_id = _purchase_transaction_id_from_payload(
            verified_payload,
            fallback_transaction_id=raw_purchase_id,
            purchase_token=purchase_token,
        )

        merged_payload = {
            "verified_payload": verified_payload,
            "client_payload": payload.get("purchase_payload") or {},
            "verification_data": (payload.get("verification_data") or "").strip(),
            "local_verification_data": (payload.get("local_verification_data") or "").strip(),
        }

        try:
            purchase_record, created = apply_store_product_purchase(
                request.user,
                product=product,
                platform="android",
                external_transaction_id=transaction_id,
                purchase_token=purchase_token,
                payload=merged_payload,
                store_entitlement_expires_at=entitlement_expires_at,
            )
        except ValueError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "detail": "purchase_validated",
                "purchase": {
                    "id": purchase_record.id,
                    "status": purchase_record.status,
                    "created": created,
                    "product_code": product.code,
                    "product_type": product.product_type,
                    "credits_granted": purchase_record.credits_granted,
                    "entitlement_started_at": purchase_record.entitlement_started_at,
                    "entitlement_expires_at": purchase_record.entitlement_expires_at,
                    "external_transaction_id": purchase_record.external_transaction_id,
                },
                "economy": _economy_overview_payload(request.user),
            },
            status=status.HTTP_200_OK,
        )


class ApplePurchaseCompleteAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ApplePurchaseCompleteSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data
        product = serializer.context["store_product"]
        receipt_data = payload["receipt_data"]
        raw_purchase_id = (payload.get("purchase_id") or "").strip()

        expected_product_id = _apple_store_product_id(product)
        if not expected_product_id:
            return Response(
                {"error": "store_product_id_missing"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # App receipts may contain auto-renewable subscription entries even
            # when the current purchase is a consumable credit pack. Sending the
            # shared secret for every iOS receipt keeps Apple's verification
            # response consistent across mixed consumable/subscription receipts.
            verified_payload = _verify_apple_receipt(
                receipt_data,
                requires_shared_secret=True,
            )
            matched_item = _find_apple_receipt_item(
                verified_payload=verified_payload,
                expected_product_id=expected_product_id,
                purchase_id=raw_purchase_id,
            )
        except AppleIAPNotConfiguredError as exc:
            return Response(
                {
                    "error": exc.code,
                    "message": "Apple in-app purchase verification is not configured on the backend.",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except AppleIAPVerificationError as exc:
            payload_status = ""
            payload_bundle = ""
            payload_product_ids = []
            if isinstance(exc.payload, dict):
                payload_status = exc.payload.get("status", "")
                receipt = exc.payload.get("receipt") or {}
                if isinstance(receipt, dict):
                    payload_bundle = (receipt.get("bundle_id") or "").strip()
                    in_app = receipt.get("in_app") or []
                    if isinstance(in_app, list):
                        payload_product_ids.extend(
                            (item.get("product_id") or "").strip()
                            for item in in_app
                            if isinstance(item, dict)
                        )
                latest = exc.payload.get("latest_receipt_info") or []
                if isinstance(latest, list):
                    payload_product_ids.extend(
                        (item.get("product_id") or "").strip()
                        for item in latest
                        if isinstance(item, dict)
                    )
            logger.warning(
                "Apple IAP verification failed: code=%s detail=%s product_code=%s expected_product_id=%s purchase_id=%s apple_status=%s receipt_bundle=%s receipt_product_ids=%s",
                exc.code,
                exc.detail,
                product.code,
                expected_product_id,
                raw_purchase_id,
                payload_status,
                payload_bundle,
                sorted({item for item in payload_product_ids if item}),
            )
            return Response(
                {
                    "error": exc.code,
                    "message": exc.detail or exc.code,
                    "payload": exc.payload,
                },
                status=status.HTTP_409_CONFLICT,
            )

        transaction_id = (
            (matched_item.get("transaction_id") or "").strip()
            or (matched_item.get("original_transaction_id") or "").strip()
            or raw_purchase_id
        )
        if not transaction_id:
            return Response(
                {"error": "purchase_transaction_id_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        merged_payload = {
            "verified_payload": verified_payload,
            "matched_item": matched_item,
            "client_payload": payload.get("purchase_payload") or {},
            "verification_data": (payload.get("verification_data") or "").strip(),
            "local_verification_data": (payload.get("local_verification_data") or "").strip(),
        }
        entitlement_expires_at = (
            _apple_receipt_item_expires_at(matched_item)
            if _is_subscription_store_product(product)
            else None
        )

        try:
            purchase_record, created = apply_store_product_purchase(
                request.user,
                product=product,
                platform="ios",
                external_transaction_id=transaction_id,
                payload=merged_payload,
                store_entitlement_expires_at=entitlement_expires_at,
            )
        except ValueError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "detail": "purchase_validated",
                "purchase": {
                    "id": purchase_record.id,
                    "status": purchase_record.status,
                    "created": created,
                    "product_code": product.code,
                    "product_type": product.product_type,
                    "credits_granted": purchase_record.credits_granted,
                    "entitlement_started_at": purchase_record.entitlement_started_at,
                    "entitlement_expires_at": purchase_record.entitlement_expires_at,
                    "external_transaction_id": purchase_record.external_transaction_id,
                },
                "economy": _economy_overview_payload(request.user),
            },
            status=status.HTTP_200_OK,
        )


class VacancyContactAccessStateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        vacancy = Vacancy.objects.filter(pk=pk).first()
        error_response = _public_vacancy_error_response(vacancy)
        if error_response is not None:
            return error_response
        state = build_contact_access_state(request.user, vacancy)
        return Response(state, status=status.HTTP_200_OK)


class VacancySubmissionStateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        flow = (request.query_params.get("flow") or "create").strip().lower()
        vacancy_id = request.query_params.get("vacancy_id")
        effective_flow = flow
        if flow not in {"create", "edit_resubmit"}:
            return Response({"error": "invalid_submission_flow"}, status=status.HTTP_400_BAD_REQUEST)
        if flow == "edit_resubmit":
            if not vacancy_id:
                return Response({"error": "vacancy_id_required"}, status=status.HTTP_400_BAD_REQUEST)
            vacancy = Vacancy.objects.filter(pk=vacancy_id, created_by=request.user).first()
            if not vacancy:
                return Response({"error": "vacancy_not_found"}, status=status.HTTP_404_NOT_FOUND)
            if vacancy.is_deleted_by_moderator:
                return Response({"error": "vacancy_deleted"}, status=status.HTTP_410_GONE)
            effective_flow = _submission_flow_for_vacancy(vacancy)

        payload = build_vacancy_submission_state(request.user, flow=effective_flow)
        payload["effective_flow"] = effective_flow
        return Response(payload, status=status.HTTP_200_OK)


class VacancyContactAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        vacancy = Vacancy.objects.filter(pk=pk).first()
        error_response = _public_vacancy_error_response(vacancy)
        if error_response is not None:
            return error_response

        state = build_contact_access_state(request.user, vacancy)
        if not state["is_unlocked"]:
            return Response(
                {"detail": "contacts_locked", "access_state": state},
                status=403
            )

        serializer = VacancyContactSerializer(vacancy)
        return Response(serializer.data)

    def post(self, request, pk):
        vacancy = Vacancy.objects.filter(pk=pk).first()
        error_response = _public_vacancy_error_response(vacancy)
        if error_response is not None:
            return error_response

        try:
            _, access_state, _ = unlock_vacancy_contacts(
                request.user,
                vacancy,
                method=request.data.get("method"),
            )
        except EconomyActionRequiredError as exc:
            return Response(
                {"error": exc.code, "access_state": exc.state},
                status=status.HTTP_409_CONFLICT,
            )
        except InsufficientCreditsError:
            return Response(
                {
                    "error": "insufficient_credits",
                    "access_state": build_contact_access_state(request.user, vacancy),
                },
                status=status.HTTP_409_CONFLICT,
            )

        serializer = VacancyContactSerializer(vacancy)
        payload = dict(serializer.data)
        payload["access_state"] = access_state
        return Response(payload)


class VacancyReviewAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        vacancy = Vacancy.objects.select_related("created_by").filter(pk=pk).first()
        error_response = _public_vacancy_error_response(vacancy)
        if error_response is not None:
            return error_response
        return Response(
            {
                "review_state": build_vacancy_review_state(request.user, vacancy),
                "employer_review_summary": get_employer_review_summary(vacancy.created_by),
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request, pk):
        vacancy = Vacancy.objects.select_related("created_by").filter(pk=pk).first()
        error_response = _public_vacancy_error_response(vacancy)
        if error_response is not None:
            return error_response

        try:
            review, _ = save_vacancy_review(
                user=request.user,
                vacancy=vacancy,
                rating=request.data.get("rating"),
                preset_codes=request.data.get("preset_codes"),
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "review": {
                    "id": review.id,
                    "rating": int(review.rating or 0),
                    "preset_codes": list(review.preset_codes or []),
                    "created_at": review.created_at,
                    "updated_at": review.updated_at,
                },
                "review_state": build_vacancy_review_state(request.user, vacancy),
                "employer_review_summary": get_employer_review_summary(vacancy.created_by),
            },
            status=status.HTTP_200_OK,
        )

    def patch(self, request, pk):
        return self.post(request, pk)

    def delete(self, request, pk):
        vacancy = Vacancy.objects.select_related("created_by").filter(pk=pk).first()
        error_response = _public_vacancy_error_response(vacancy)
        if error_response is not None:
            return error_response
        try:
            delete_vacancy_review(user=request.user, vacancy=vacancy)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "deleted": True,
                "review_state": build_vacancy_review_state(request.user, vacancy),
                "employer_review_summary": get_employer_review_summary(vacancy.created_by),
            },
            status=status.HTTP_200_OK,
        )


class EmployerReviewModeratorListAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, owner_user_id):
        if not _is_moderator(request):
            return Response({"error": "forbidden"}, status=status.HTTP_403_FORBIDDEN)

        owner = User.objects.filter(id=owner_user_id).select_related("profile").first()
        if not owner:
            return Response({"error": "employer_not_found"}, status=status.HTTP_404_NOT_FOUND)

        return Response(
            {
                "employer": {
                    "id": owner.id,
                    "nickname": _owner_nickname_or_fallback(owner),
                    "review_summary": get_employer_review_summary(owner),
                },
                "preset_codes": [
                    {"code": code, "label": label}
                    for code, label in REVIEW_PRESET_CHOICES
                ],
                "results": get_employer_review_records_for_moderator(owner),
            },
            status=status.HTTP_200_OK,
        )


class EmployerProfileAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, owner_user_id):
        owner = (
            User.objects.filter(id=owner_user_id)
            .select_related("profile")
            .first()
        )
        if not owner:
            return Response({"error": "employer_not_found"}, status=status.HTTP_404_NOT_FOUND)

        blocked_by_owner = UserBlock.objects.filter(
            blocker=owner,
            blocked_user=request.user,
        ).exists()
        if blocked_by_owner:
            return Response({"error": "employer_not_found"}, status=status.HTTP_404_NOT_FOUND)

        is_service_board = is_service_board_user(owner)
        can_subscribe = owner.id != request.user.id and not is_service_board
        is_subscribed = False
        if can_subscribe:
            is_subscribed = EmployerSubscription.objects.filter(
                subscriber=request.user,
                employer=owner,
            ).exists()

        qs = Vacancy.objects.filter(
            created_by=owner,
            is_approved=True,
            is_paused_by_owner=False,
            is_deleted_by_moderator=False,
            expires_at__gt=timezone.now(),
        ).select_related(
            "contact_access_policy",
            "created_by",
            "created_by__profile",
        ).order_by("-published_at")

        visible_vacancies = _filter_visible_vacancies(qs)

        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(visible_vacancies, request, view=self)
        serializer = VacancyListSerializer(page, many=True)
        paginated = paginator.get_paginated_response(serializer.data).data

        employer_payload = {
            "id": owner.id,
            "nickname": _owner_nickname_or_fallback(owner),
            "profile_description": (
                (getattr(getattr(owner, "profile", None), "description", "") or "").strip()
            ),
            "email_masked": _masked_email(owner.email),
            "avatar_url": _owner_avatar_url(owner),
            "subscribers_count": _subscriber_count_for_owner(owner),
            "can_subscribe": can_subscribe,
            "is_subscribed": is_subscribed,
            "review_summary": get_employer_review_summary(owner),
            "viewer_is_staff": bool(getattr(request.user, "is_staff", False)),
        }
        employer_payload.update(service_board_meta_for_user(owner))

        return Response(
            {
                "employer": employer_payload,
                "count": paginated.get("count", 0),
                "next": paginated.get("next"),
                "previous": paginated.get("previous"),
                "results": paginated.get("results", []),
            },
            status=status.HTTP_200_OK,
        )


class EmployerSubscriptionAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, owner_user_id):
        owner = User.objects.filter(id=owner_user_id).first()
        if not owner:
            return Response({"error": "employer_not_found"}, status=status.HTTP_404_NOT_FOUND)
        if owner.id == request.user.id:
            return Response({"error": "cannot_subscribe_self"}, status=status.HTTP_400_BAD_REQUEST)
        if is_service_board_user(owner):
            return Response({"error": "cannot_subscribe_service_source"}, status=status.HTTP_400_BAD_REQUEST)

        _, created = EmployerSubscription.objects.get_or_create(
            subscriber=request.user,
            employer=owner,
        )
        return Response({"subscribed": True, "created": created}, status=status.HTTP_200_OK)

    def delete(self, request, owner_user_id):
        owner = User.objects.filter(id=owner_user_id).first()
        if not owner:
            return Response({"error": "employer_not_found"}, status=status.HTTP_404_NOT_FOUND)

        EmployerSubscription.objects.filter(
            subscriber=request.user,
            employer=owner,
        ).delete()
        return Response({"subscribed": False}, status=status.HTTP_200_OK)


class EmployerSubscriptionListAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        search = (request.query_params.get("search") or "").strip()
        qs = (
            EmployerSubscription.objects.filter(subscriber=request.user)
            .select_related("employer", "employer__profile")
            .order_by("-created_at")
        )
        if search:
            qs = qs.filter(employer__profile__nickname__icontains=search)

        paginator = PageNumberPagination()
        paginator.page_size = 20
        page = paginator.paginate_queryset(qs, request, view=self)

        results = [
            {
                "employer_id": item.employer_id,
                "nickname": _owner_nickname_or_fallback(item.employer),
                "avatar_url": _owner_avatar_url(item.employer),
                "subscribed_at": item.created_at,
            }
            for item in (page or [])
        ]

        return paginator.get_paginated_response(results)


class EmployerSearchAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        search = (request.query_params.get("search") or "").strip()
        if len(search) < 2:
            return Response(
                {"count": 0, "next": None, "previous": None, "results": []},
                status=status.HTTP_200_OK,
            )

        try:
            limit = int(request.query_params.get("limit") or 20)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 20))

        now = timezone.now()
        search_terms = [term for term in search.split() if term]
        live_vacancy_exists = Vacancy.objects.filter(
            created_by_id=OuterRef("id"),
            is_approved=True,
            is_paused_by_owner=False,
            is_deleted_by_moderator=False,
            expires_at__gt=now,
        )
        blocked_by_me = UserBlock.objects.filter(
            blocker=request.user,
            blocked_user=OuterRef("id"),
        )
        blocked_me = UserBlock.objects.filter(
            blocker_id=OuterRef("id"),
            blocked_user=request.user,
        )
        subscribed_qs = EmployerSubscription.objects.filter(
            subscriber=request.user,
            employer_id=OuterRef("id"),
        )

        qs = (
            User.objects.select_related("profile")
            .exclude(id=request.user.id)
            .annotate(
                has_live_vacancies=Exists(live_vacancy_exists),
                blocked_by_me=Exists(blocked_by_me),
                blocked_me=Exists(blocked_me),
                is_subscribed=Exists(subscribed_qs),
            )
            .filter(
                has_live_vacancies=True,
                blocked_by_me=False,
                blocked_me=False,
            )
        )

        search_q = Q()
        for term in search_terms:
            term_q = (
                Q(profile__nickname__icontains=term)
                | Q(first_name__icontains=term)
                | Q(last_name__icontains=term)
            )
            search_q = term_q if not search_q else (search_q & term_q)
        qs = qs.filter(search_q)

        qs = qs.annotate(
            search_rank=Case(
                When(profile__nickname__iexact=search, then=Value(0)),
                When(first_name__iexact=search, then=Value(1)),
                When(last_name__iexact=search, then=Value(2)),
                When(profile__nickname__istartswith=search, then=Value(3)),
                When(first_name__istartswith=search, then=Value(4)),
                When(last_name__istartswith=search, then=Value(5)),
                default=Value(9),
                output_field=IntegerField(),
            ),
        ).order_by("search_rank", "-is_subscribed", "profile__nickname", "id")

        paginator = PageNumberPagination()
        paginator.page_size = limit
        page = paginator.paginate_queryset(qs, request, view=self)

        results = [
            {
                "employer_id": owner.id,
                "nickname": _owner_nickname_or_fallback(owner),
                "avatar_url": _owner_avatar_url(owner),
                "is_subscribed": bool(getattr(owner, "is_subscribed", False)),
            }
            for owner in (page or [])
        ]

        return paginator.get_paginated_response(results)


from .models import UnlockRequest
from rest_framework import status


class VacancyUnlockRequestAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        vacancy = Vacancy.objects.filter(pk=pk).first()
        error_response = _public_vacancy_error_response(vacancy)
        if error_response is not None:
            return error_response

        # если уже открыто — сразу вернём "already_unlocked"
        if UnlockedContact.objects.filter(user=request.user, vacancy=vacancy).exists():
            return Response({"detail": "already_unlocked"}, status=200)

        unlock = UnlockRequest.create_for(request.user, vacancy)
        return Response(
            {
                "unlock_token": unlock.token,
                "expires_in_seconds": 120
            },
            status=200
        )


class VacancyUnlockConfirmAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        vacancy = Vacancy.objects.filter(pk=pk).first()
        error_response = _public_vacancy_error_response(vacancy)
        if error_response is not None:
            return error_response
        token = request.data.get("unlock_token")

        if not token:
            return Response({"error": "unlock_token required"}, status=status.HTTP_400_BAD_REQUEST)

        qs = UnlockRequest.objects.filter(
            user=request.user,
            vacancy=vacancy,
            token=token
        ).order_by("-created_at")

        if not qs.exists():
            return Response({"error": "invalid token"}, status=status.HTTP_400_BAD_REQUEST)

        unlock_req = qs.first()
        if not unlock_req.is_valid():
            return Response({"error": "token expired"}, status=status.HTTP_400_BAD_REQUEST)

        UnlockedContact.objects.get_or_create(user=request.user, vacancy=vacancy)

        # токен можно удалить, чтобы нельзя было использовать повторно
        unlock_req.delete()

        serializer = VacancyContactSerializer(vacancy)
        return Response(serializer.data, status=200)


class VacancyPendingListAPIView(generics.ListAPIView):
    serializer_class = VacancyModerationSerializer
    permission_classes = [IsModerator]

    def get_queryset(self):
        return Vacancy.objects.filter(
            is_approved=False,
            is_rejected=False,
            is_editing=False,
            is_deleted_by_moderator=False,
        ).filter(
            Q(rejection_reason="") | Q(rejection_reason__isnull=True)
        ).annotate(
            queue_anchor=Coalesce("editing_started_at", "published_at"),
            moderation_attempts_total=Count("moderation_attempts", distinct=True),
            moderation_approved_total=Count(
                "moderation_attempts",
                filter=Q(moderation_attempts__decision="approved"),
                distinct=True,
            ),
            moderation_rejected_total=Count(
                "moderation_attempts",
                filter=Q(moderation_attempts__decision="rejected"),
                distinct=True,
            ),
            latest_moderation_attempt_no=Max("moderation_attempts__attempt_no"),
        ).order_by("-queue_anchor", "-published_at")


class ModerationVacancyDetailAPIView(APIView):
    permission_classes = [IsModerator]

    def get(self, request, pk):
        vacancy = Vacancy.objects.select_related("created_by", "created_by__profile").prefetch_related(
            "moderation_attempts__submitted_by",
            "moderation_attempts__decided_by",
        ).filter(pk=pk).first()
        if not vacancy:
            return Response({"error": "vacancy_not_found"}, status=status.HTTP_404_NOT_FOUND)
        serializer = VacancyModerationDetailSerializer(vacancy, context={"request": request})
        payload = dict(serializer.data)
        payload["employer_summary"] = _build_employer_moderation_summary(
            vacancy.created_by,
        )
        return Response(payload, status=200)


class VacancyApproveAPIView(APIView):
    permission_classes = [IsModerator]

    def post(self, request, pk):
        vacancy = Vacancy.objects.get(pk=pk)
        if vacancy.is_deleted_by_moderator:
            return Response({"error": "vacancy_deleted"}, status=status.HTTP_410_GONE)
        if vacancy.is_editing:
            return Response({"error": "vacancy_editing"}, status=409)
        decision_time = timezone.now()
        _set_vacancy_live(vacancy, now=decision_time)
        ensure_free_contact_policy(vacancy, set_by=request.user)
        vacancy.save(
            update_fields=[
                "is_approved",
                "approved_at",
                "is_rejected",
                "is_paused_by_owner",
                "paused_by_owner_at",
                "rejection_reason",
                "last_moderator_rejection_reason",
                "moderation_baseline",
                "is_editing",
                "editing_started_at",
                "published_at",
                "expires_at",
            ]
        )
        _resolve_latest_moderation_attempt(
            vacancy,
            decision="approved",
            moderator=request.user,
            decided_at=decision_time,
        )
        try:
            summary = dispatch_vacancy_alerts(vacancy)
            print(f"[VACANCY-ALERTS] vacancy={vacancy.id} summary={summary}")
        except Exception as exc:
            print(f"[VACANCY-ALERTS-ERROR] vacancy={vacancy.id}: {exc}")
        return Response({"detail": "approved"}, status=200)


class VacancyRejectAPIView(APIView):
    permission_classes = [IsModerator]

    def post(self, request, pk):
        vacancy = Vacancy.objects.get(pk=pk)
        if vacancy.is_deleted_by_moderator:
            return Response({"error": "vacancy_deleted"}, status=status.HTTP_410_GONE)
        if vacancy.is_editing:
            return Response({"error": "vacancy_editing"}, status=409)
        reason = censor_minimal((request.data.get("reason") or "").strip())
        if contains_link(reason):
            return Response(
                {"error": "links_not_allowed_in_reason"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        decision_time = timezone.now()
        vacancy.moderation_baseline = _vacancy_editable_snapshot(vacancy)
        vacancy.last_moderator_rejection_reason = reason
        vacancy.is_approved = False
        vacancy.is_rejected = True
        vacancy.is_paused_by_owner = False
        vacancy.paused_by_owner_at = None
        vacancy.rejection_reason = reason
        vacancy.is_editing = False
        vacancy.editing_started_at = None
        vacancy.save(
            update_fields=[
                "is_approved",
                "is_rejected",
                "is_paused_by_owner",
                "paused_by_owner_at",
                "rejection_reason",
                "last_moderator_rejection_reason",
                "moderation_baseline",
                "is_editing",
                "editing_started_at",
            ]
        )
        _resolve_latest_moderation_attempt(
            vacancy,
            decision="rejected",
            moderator=request.user,
            reason=reason,
            decided_at=decision_time,
        )
        _notify_vacancy_owner_about_reject(
            vacancy=vacancy,
            moderator=request.user,
            reason=reason,
        )
        return Response({"detail": "rejected"}, status=200)


class VacancyResubmitAPIView(APIView):
    permission_classes = [IsModerator]

    def post(self, request, pk):
        vacancy = Vacancy.objects.get(pk=pk)
        if vacancy.is_deleted_by_moderator:
            return Response({"error": "vacancy_deleted"}, status=status.HTTP_410_GONE)
        vacancy.is_approved = False
        vacancy.is_rejected = False
        vacancy.is_paused_by_owner = False
        vacancy.paused_by_owner_at = None
        vacancy.rejection_reason = ""
        vacancy.is_editing = False
        vacancy.editing_started_at = None
        vacancy.save(
            update_fields=[
                "is_approved",
                "is_rejected",
                "is_paused_by_owner",
                "paused_by_owner_at",
                "rejection_reason",
                "is_editing",
                "editing_started_at",
            ]
        )
        _create_moderation_attempt(
            vacancy,
            trigger_type="moderator_resubmit",
            submitted_by=request.user,
        )
        transaction.on_commit(lambda: _notify_moderators_about_pending_vacancy_safe(vacancy))
        return Response({"detail": "resubmitted"}, status=200)


class VacancyOwnerPauseAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _to_bool(value, default):
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def post(self, request, pk):
        vacancy = Vacancy.objects.filter(pk=pk, created_by=request.user).first()
        if not vacancy:
            return Response({"error": "vacancy_not_found"}, status=status.HTTP_404_NOT_FOUND)
        if vacancy.is_deleted_by_moderator:
            return Response({"error": "vacancy_deleted"}, status=status.HTTP_410_GONE)
        if not vacancy.is_approved:
            return Response({"error": "only_approved_vacancy_allowed"}, status=status.HTTP_400_BAD_REQUEST)

        target_paused = self._to_bool(
            request.data.get("paused"),
            default=not vacancy.is_paused_by_owner,
        )
        if target_paused == vacancy.is_paused_by_owner:
            return Response(
                {
                    "detail": "updated",
                    "is_approved": bool(vacancy.is_approved),
                    "is_rejected": bool(vacancy.is_rejected),
                    "is_paused_by_owner": bool(vacancy.is_paused_by_owner),
                    "paused_by_owner_at": (
                        vacancy.paused_by_owner_at.isoformat()
                        if vacancy.paused_by_owner_at
                        else ""
                    ),
                    "is_editing": bool(vacancy.is_editing),
                    "editing_started_at": (
                        vacancy.editing_started_at.isoformat()
                        if vacancy.editing_started_at
                        else ""
                    ),
                    "revision": vacancy.revision or 1,
                    "status_label_key": (
                        "statusPaused"
                        if vacancy.is_paused_by_owner
                        else "statusApproved"
                    ),
                },
                status=200,
            )

        if target_paused:
            vacancy.paused_by_owner_at = timezone.now()
            vacancy.is_paused_by_owner = True
            vacancy.save(update_fields=["is_paused_by_owner", "paused_by_owner_at"])
            return Response(
                {
                    "detail": "updated",
                    "is_approved": bool(vacancy.is_approved),
                    "is_rejected": bool(vacancy.is_rejected),
                    "is_paused_by_owner": True,
                    "paused_by_owner_at": vacancy.paused_by_owner_at.isoformat(),
                    "is_editing": bool(vacancy.is_editing),
                    "editing_started_at": (
                        vacancy.editing_started_at.isoformat()
                        if vacancy.editing_started_at
                        else ""
                    ),
                    "revision": vacancy.revision or 1,
                    "status_label_key": "statusPaused",
                },
                status=200,
            )

        current_time = timezone.now()
        if vacancy.expires_at <= current_time:
            limit_payload = _owner_moderation_limit_payload(vacancy, now=current_time)
            if limit_payload is not None:
                return Response(limit_payload, status=status.HTTP_429_TOO_MANY_REQUESTS)

            vacancy.is_approved = False
            vacancy.is_rejected = False
            vacancy.is_paused_by_owner = False
            vacancy.paused_by_owner_at = None
            vacancy.rejection_reason = ""
            vacancy.last_moderator_rejection_reason = ""
            vacancy.moderation_baseline = {}
            vacancy.is_editing = False
            vacancy.editing_started_at = current_time
            vacancy.revision = (vacancy.revision or 1) + 1
            vacancy.save(
                update_fields=[
                    "is_approved",
                    "is_rejected",
                    "is_paused_by_owner",
                    "paused_by_owner_at",
                    "rejection_reason",
                    "last_moderator_rejection_reason",
                    "moderation_baseline",
                    "is_editing",
                    "editing_started_at",
                    "revision",
                ]
            )
            _create_moderation_attempt(
                vacancy,
                trigger_type="resume_expired",
                submitted_by=request.user,
                submitted_at=current_time,
            )
            transaction.on_commit(lambda: _notify_moderators_about_pending_vacancy_safe(vacancy))
            return Response(
                {
                    "detail": "sent_to_moderation",
                    "is_approved": False,
                    "is_rejected": False,
                    "is_paused_by_owner": False,
                    "paused_by_owner_at": "",
                    "is_editing": False,
                    "editing_started_at": current_time.isoformat(),
                    "revision": vacancy.revision or 1,
                    "status_label_key": "statusPending",
                },
                status=200,
            )

        remaining_seconds = _owner_resume_cooldown(vacancy, now=current_time)
        if remaining_seconds > 0:
            return Response(
                {
                    "error": "resume_cooldown",
                    "remaining_seconds": remaining_seconds,
                    "resume_count_today": vacancy.owner_resume_count_day or 0,
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        vacancy.is_paused_by_owner = False
        vacancy.paused_by_owner_at = None
        _register_owner_resume(vacancy, now=current_time)
        vacancy.save(
            update_fields=[
                "is_paused_by_owner",
                "paused_by_owner_at",
                "last_owner_resume_at",
                "owner_resume_day",
                "owner_resume_count_day",
            ]
        )

        return Response(
            {
                "detail": "updated",
                "is_approved": bool(vacancy.is_approved),
                "is_rejected": bool(vacancy.is_rejected),
                "is_paused_by_owner": False,
                "paused_by_owner_at": "",
                "is_editing": bool(vacancy.is_editing),
                "editing_started_at": (
                    vacancy.editing_started_at.isoformat()
                    if vacancy.editing_started_at
                    else ""
                ),
                "revision": vacancy.revision or 1,
                "status_label_key": "statusApproved",
                "resume_count_today": vacancy.owner_resume_count_day or 0,
            },
            status=200,
        )


class VacancyOwnerDeleteAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        vacancy = Vacancy.objects.filter(pk=pk, created_by=request.user).first()
        if not vacancy:
            return Response({"error": "vacancy_not_found"}, status=status.HTTP_404_NOT_FOUND)
        if vacancy.is_deleted_by_moderator:
            return Response({"error": "vacancy_deleted"}, status=status.HTTP_410_GONE)
        if vacancy.is_approved and not vacancy.is_paused_by_owner:
            return Response(
                {"error": "vacancy_delete_not_allowed"},
                status=status.HTTP_409_CONFLICT,
            )

        vacancy_id = vacancy.id
        vacancy.delete()
        return Response({"detail": "deleted", "vacancy_id": vacancy_id}, status=status.HTTP_200_OK)


class VacancyMineAPIView(generics.ListAPIView):
    serializer_class = VacancyMineSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        _auto_pause_due_owner_vacancies(self.request.user)
        return (
            Vacancy.objects.filter(
                created_by=self.request.user,
                is_deleted_by_moderator=False,
            )
            .annotate(
                bucket_order=Case(
                    When(is_approved=True, then=Value(2)),
                    When(is_rejected=True, then=Value(1)),
                    default=Value(0),  # pending + editing
                    output_field=IntegerField(),
                )
            )
            .order_by("bucket_order", "-published_at")
        )


class VacancyEditAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        vacancy = Vacancy.objects.get(pk=pk)
        if vacancy.created_by_id != request.user.id:
            return Response({"error": "invalid token"}, status=status.HTTP_403_FORBIDDEN)
        if vacancy.is_deleted_by_moderator:
            return Response({"error": "vacancy_deleted"}, status=status.HTTP_410_GONE)

        data = request.data.copy()
        submit_raw = str(data.pop("submit", "")).lower()
        submit_for_moderation = submit_raw in ("1", "true", "yes", "on")
        save_as_draft_raw = str(data.pop("save_as_draft", "")).lower()
        save_as_draft = save_as_draft_raw in ("1", "true", "yes", "on")
        draft_mode = save_as_draft or not submit_for_moderation
        serializer = VacancyCreateSerializer(
            vacancy,
            data=data,
            partial=True,
            context={"draft_mode": draft_mode},
        )
        serializer.is_valid(raise_exception=True)
        if submit_for_moderation and not save_as_draft:
            current_time = timezone.now()
            submission_flow = _submission_flow_for_vacancy(vacancy)
            if submission_flow == "edit_resubmit":
                limit_payload = _owner_moderation_limit_payload(vacancy, now=current_time)
                if limit_payload is not None:
                    return Response(limit_payload, status=status.HTTP_429_TOO_MANY_REQUESTS)
            submission_method = (request.data.get("submission_method") or "").strip().lower()
            next_revision = (
                vacancy.revision or 1
                if submission_flow == "create"
                else (vacancy.revision or 1) + 1
            )
            try:
                with transaction.atomic():
                    serializer.save(
                        is_approved=False,
                        is_rejected=False,
                        rejection_reason="",
                        is_editing=False,
                        is_paused_by_owner=False,
                        paused_by_owner_at=None,
                        revision=next_revision,
                        # Marks resubmission time; pending list uses it for ordering.
                        editing_started_at=current_time,
                    )
                    _create_moderation_attempt(
                        vacancy,
                        trigger_type="create" if submission_flow == "create" else "edit",
                        submitted_by=request.user,
                        submitted_at=current_time,
                    )
                    transaction.on_commit(
                        lambda: _notify_moderators_about_pending_vacancy_safe(vacancy)
                    )
                    apply_vacancy_submission_action(
                        request.user,
                        flow=submission_flow,
                        method=submission_method,
                        related_vacancy=vacancy,
                        now=current_time,
                    )
            except EconomyActionRequiredError as exc:
                return Response(
                    {
                        "error": exc.code,
                        "submission_state": exc.state,
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            except InsufficientCreditsError:
                return Response(
                    {
                        "error": "insufficient_credits",
                        "submission_state": build_vacancy_submission_state(
                            request.user,
                            flow=submission_flow,
                            now=current_time,
                        ),
                    },
                    status=status.HTTP_409_CONFLICT,
                )
        else:
            next_revision = (vacancy.revision or 1) + 1
            serializer.save(
                is_approved=False,
                is_rejected=False,
                rejection_reason="",
                is_editing=True,
                is_paused_by_owner=False,
                paused_by_owner_at=None,
                revision=next_revision,
                editing_started_at=timezone.now(),
            )
        return Response(serializer.data, status=200)


class ComplaintAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        vacancy_id = request.data.get("vacancy_id")
        reason = (request.data.get("reason") or "").strip()
        message = censor_minimal((request.data.get("message") or "").strip())
        if contains_link(message):
            return Response(
                {"error": "links_not_allowed_in_message"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not vacancy_id or not reason:
            return Response({"error": "vacancy_id and reason required"}, status=status.HTTP_400_BAD_REQUEST)

        vacancy = Vacancy.objects.filter(id=vacancy_id).first()
        error_response = _public_vacancy_error_response(vacancy)
        if error_response is not None:
            return error_response

        allowed_reasons = {code for code, _ in Complaint.REASON_CHOICES}
        if reason not in allowed_reasons:
            return Response(
                {
                    "error": "invalid_reason",
                    "allowed_reasons": sorted(allowed_reasons),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        reporter_email = (request.user.email or "").strip()
        if not reporter_email:
            return Response({"error": "email_auth_required"}, status=status.HTTP_403_FORBIDDEN)
        reporter = reporter_email

        complaint = Complaint.objects.create(
            vacancy=vacancy,
            reporter=request.user,
            reason=reason,
            vacancy_revision_snapshot=vacancy.revision or 1,
            message=message,
        )

        subject = f"JobHub complaint: {reason}"
        body = "\n".join(
            [
                f"Complaint ID: {complaint.id}",
                f"Vacancy: {vacancy.title}",
                f"Vacancy ID: {vacancy_id}",
                f"Reporter: {reporter}",
                f"Reason: {reason}",
                "",
                "Message:",
                message or "-",
            ]
        )

        to_email = getattr(
            settings,
            "COMPLAINT_EMAIL",
            getattr(settings, "SUPPORT_EMAIL", settings.DEFAULT_FROM_EMAIL),
        )
        try:
            send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [to_email], fail_silently=False)
        except Exception:
            return Response(
                {"detail": "saved_email_failed", "complaint_id": complaint.id},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response({"detail": "sent", "complaint_id": complaint.id}, status=200)


class ComplaintByVacancyAPIView(APIView):
    permission_classes = [IsModerator]

    def get(self, request):
        base = Complaint.objects.select_related("vacancy", "vacancy__created_by", "vacancy__created_by__profile")

        status_filter = (request.query_params.get("status") or "").strip()
        reason_filter = (request.query_params.get("reason") or "").strip()

        if status_filter:
            base = base.filter(status=status_filter)
        if reason_filter:
            base = base.filter(reason=reason_filter)

        grouped = (
            base.values(
                "vacancy_id",
                "vacancy__title",
                "vacancy__created_by_id",
            )
            .annotate(
                complaints_count=Count("id"),
                open_count=Count("id", filter=Q(status__in=["new", "in_review"])),
                latest_complaint_at=Max("created_at"),
            )
            .order_by("-complaints_count", "-latest_complaint_at")
        )

        rows = list(grouped)
        summary_map = _build_employer_moderation_summary_map(
            [row.get("vacancy__created_by_id") for row in rows]
        )

        results = [
            {
                "vacancy_id": row["vacancy_id"],
                "vacancy_title": row["vacancy__title"],
                "employer_summary": summary_map.get(
                    row.get("vacancy__created_by_id"),
                    {},
                ),
                "complaints_count": row["complaints_count"],
                "open_count": row["open_count"],
                "latest_complaint_at": row["latest_complaint_at"],
            }
            for row in rows
        ]
        return Response({"count": len(results), "results": results}, status=200)


class ComplaintListAPIView(generics.ListAPIView):
    permission_classes = [IsModerator]
    serializer_class = ComplaintListSerializer

    def get_queryset(self):
        qs = Complaint.objects.select_related("vacancy", "reporter", "handled_by").order_by("-created_at")

        status_filter = (self.request.query_params.get("status") or "").strip()
        reason_filter = (self.request.query_params.get("reason") or "").strip()
        vacancy_id = (self.request.query_params.get("vacancy_id") or "").strip()
        date_from = (self.request.query_params.get("date_from") or "").strip()
        date_to = (self.request.query_params.get("date_to") or "").strip()

        if status_filter:
            qs = qs.filter(status=status_filter)
        if reason_filter:
            qs = qs.filter(reason=reason_filter)
        if vacancy_id:
            try:
                qs = qs.filter(vacancy_id=int(vacancy_id))
            except ValueError:
                raise ValidationError({"vacancy_id": "must be integer"})
        if date_from:
            parsed = parse_date(date_from)
            if not parsed:
                raise ValidationError({"date_from": "must be YYYY-MM-DD"})
            qs = qs.filter(created_at__date__gte=parsed)
        if date_to:
            parsed = parse_date(date_to)
            if not parsed:
                raise ValidationError({"date_to": "must be YYYY-MM-DD"})
            qs = qs.filter(created_at__date__lte=parsed)

        return qs


class ComplaintModerationActionAPIView(APIView):
    permission_classes = [IsModerator]

    @staticmethod
    def _snapshot(vacancy):
        return {
            "is_approved": bool(vacancy.is_approved),
            "is_rejected": bool(vacancy.is_rejected),
            "is_paused_by_owner": bool(vacancy.is_paused_by_owner),
            "paused_by_owner_at": (
                vacancy.paused_by_owner_at.isoformat()
                if vacancy.paused_by_owner_at
                else ""
            ),
            "is_editing": bool(vacancy.is_editing),
            "rejection_reason": vacancy.rejection_reason or "",
            "is_deleted_by_moderator": bool(vacancy.is_deleted_by_moderator),
            "deleted_by_moderator_at": (
                vacancy.deleted_by_moderator_at.isoformat()
                if vacancy.deleted_by_moderator_at
                else ""
            ),
        }

    @staticmethod
    def _to_bool(value, default=True):
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def post(self, request, pk):
        complaint = Complaint.objects.select_related("vacancy").filter(pk=pk).first()
        if not complaint:
            return Response({"error": "complaint_not_found"}, status=status.HTTP_404_NOT_FOUND)

        action = (request.data.get("action") or "").strip()
        note = censor_minimal((request.data.get("note") or "").strip())
        reject_reason = censor_minimal((request.data.get("rejection_reason") or "").strip())
        if contains_link(note) or contains_link(reject_reason):
            return Response(
                {"error": "links_not_allowed"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        resolve_all = self._to_bool(request.data.get("resolve_all"), default=True)

        allowed_actions = {code for code, _ in ComplaintActionLog.ACTION_CHOICES}
        if action not in allowed_actions:
            return Response(
                {"error": "invalid_action", "allowed_actions": sorted(allowed_actions)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        vacancy = complaint.vacancy
        before_state = self._snapshot(vacancy)
        attempt_trigger = ""
        attempt_decision = ""
        attempt_reason = ""

        if action == "delete_forever":
            action_reason = note or complaint.reason
            if not vacancy.is_deleted_by_moderator:
                vacancy.moderator_deleted_state = _vacancy_moderation_state_snapshot(vacancy)
            vacancy.is_approved = False
            vacancy.is_rejected = True
            vacancy.is_paused_by_owner = False
            vacancy.paused_by_owner_at = None
            vacancy.is_editing = False
            vacancy.rejection_reason = action_reason
            vacancy.moderation_baseline = {}
            vacancy.last_moderator_rejection_reason = action_reason
            vacancy.editing_started_at = None
            vacancy.is_deleted_by_moderator = True
            vacancy.deleted_by_moderator_at = timezone.now()
            attempt_decision = "rejected"
            attempt_reason = action_reason
        elif action == "reject":
            action_reason = reject_reason or note or complaint.reason
            vacancy.moderation_baseline = _vacancy_editable_snapshot(vacancy)
            vacancy.last_moderator_rejection_reason = action_reason
            vacancy.is_approved = False
            vacancy.is_rejected = True
            vacancy.is_paused_by_owner = False
            vacancy.paused_by_owner_at = None
            vacancy.is_editing = False
            vacancy.rejection_reason = action_reason
            vacancy.editing_started_at = None
            vacancy.is_deleted_by_moderator = False
            vacancy.moderator_deleted_state = {}
            vacancy.deleted_by_moderator_at = None
            attempt_decision = "rejected"
            attempt_reason = action_reason
        elif action == "restore":
            submitted_at = timezone.now()
            vacancy.is_approved = False
            vacancy.is_rejected = False
            vacancy.is_paused_by_owner = False
            vacancy.paused_by_owner_at = None
            vacancy.is_editing = False
            vacancy.rejection_reason = ""
            vacancy.last_moderator_rejection_reason = ""
            vacancy.moderation_baseline = {}
            vacancy.editing_started_at = submitted_at
            vacancy.revision = (vacancy.revision or 1) + 1
            vacancy.is_deleted_by_moderator = False
            vacancy.moderator_deleted_state = {}
            vacancy.deleted_by_moderator_at = None
            attempt_trigger = "restore"

        vacancy.save(
            update_fields=[
                "is_approved",
                "is_rejected",
                "is_paused_by_owner",
                "paused_by_owner_at",
                "is_editing",
                "rejection_reason",
                "last_moderator_rejection_reason",
                "moderation_baseline",
                "editing_started_at",
                "revision",
                "is_deleted_by_moderator",
                "moderator_deleted_state",
                "deleted_by_moderator_at",
            ]
        )
        if attempt_trigger:
            _create_moderation_attempt(
                vacancy,
                trigger_type=attempt_trigger,
                submitted_by=request.user,
                submitted_at=vacancy.editing_started_at or timezone.now(),
                extra_context={"complaint_id": complaint.id},
            )
            transaction.on_commit(lambda: _notify_moderators_about_pending_vacancy_safe(vacancy))
        elif attempt_decision:
            _resolve_latest_moderation_attempt(
                vacancy,
                decision=attempt_decision,
                moderator=request.user,
                reason=attempt_reason,
            )
        after_state = self._snapshot(vacancy)

        now = timezone.now()
        complaints_qs = Complaint.objects.filter(vacancy=vacancy, status__in=["new", "in_review"])
        if not resolve_all:
            complaints_qs = complaints_qs.filter(pk=complaint.pk)
        resolution_text = note or f"vacancy_action:{action}"
        resolved_count = complaints_qs.update(
            status="resolved",
            handled_by=request.user,
            handled_at=now,
            resolution_note=resolution_text,
        )

        ComplaintActionLog.objects.create(
            complaint=complaint,
            vacancy=vacancy,
            actor=request.user,
            action=action,
            note=note,
            before_state=before_state,
            after_state=after_state,
        )

        owner_notified, notify_error = _notify_vacancy_owner_about_complaint_action(
            vacancy=vacancy,
            complaint=complaint,
            action=action,
            moderator=request.user,
            note=note,
            reject_reason=reject_reason,
        )

        payload = {
            "detail": "action_applied",
            "action": action,
            "complaint_id": complaint.id,
            "vacancy_id": vacancy.id,
            "resolved_complaints": resolved_count,
            "owner_notified": owner_notified,
        }
        if settings.DEBUG and notify_error:
            payload["owner_notify_error"] = notify_error
        return Response(payload, status=200)


