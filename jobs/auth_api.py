import json
import os
import random
import re
import threading
import base64
import hashlib
from importlib import util as importlib_util
from datetime import timedelta
from urllib import parse, request as urllib_request
from urllib.error import HTTPError

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password
from django.core.mail import send_mail
from django.db import IntegrityError
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.views import APIView

from .avatar_storage import (
    delete_avatar_object,
    is_avatar_storage_configured,
    upload_avatar_bytes,
)
from .avatar_utils import (
    avatar_public_url,
    build_avatar_object_key,
    process_avatar_image,
)
from .economy import get_or_create_monetization_profile, get_or_create_wallet
from .models import AccountDeletionRequest, EmailVerification, PhoneVerification, PhoneVerificationAttempt, UserProfile, Vacancy
from .reviews import get_employer_review_summary
from .text_filters import (
    censor_minimal,
    contains_digit_or_number_emoji,
    contains_link,
    line_constraints_error,
    normalize_newlines,
)

_PHONE_REQUEST_WINDOW = timedelta(minutes=10)
_PHONE_REQUEST_MAX_ATTEMPTS = 3
_DEFAULT_ALLOWED_PHONE_COUNTRY_CODES = (
    "+43",  # Austria
    "+32",  # Belgium
    "+359",  # Bulgaria
    "+385",  # Croatia
    "+357",  # Cyprus
    "+420",  # Czech Republic
    "+45",  # Denmark
    "+372",  # Estonia
    "+358",  # Finland
    "+33",  # France
    "+49",  # Germany
    "+30",  # Greece
    "+36",  # Hungary
    "+353",  # Ireland
    "+39",  # Italy
    "+371",  # Latvia
    "+370",  # Lithuania
    "+352",  # Luxembourg
    "+356",  # Malta
    "+31",  # Netherlands
    "+48",  # Poland
    "+351",  # Portugal
    "+40",  # Romania
    "+421",  # Slovakia
    "+386",  # Slovenia
    "+34",  # Spain
    "+46",  # Sweden
    "+375",  # Belarus
    "+380",  # Ukraine
)
_ACCOUNT_DELETION_DELAY = timedelta(days=30)
_phone_request_attempts = {}
_phone_request_lock = threading.Lock()
_PHONE_CODE_COOLDOWN = timedelta(seconds=45)
_PHONE_RESET_REQUEST_HOURLY_LIMIT = 5
_PHONE_RESET_REQUEST_DAILY_LIMIT = 10
_PHONE_RESET_VERIFY_ATTEMPT_LIMIT = 5
_PHONE_RESET_VERIFY_BLOCK_WINDOW = timedelta(minutes=15)
_PASSWORD_MIN_LENGTH = 8
_PROFILE_DESCRIPTION_MAX_LENGTH = 160
_PROFILE_DESCRIPTION_MAX_LINES = 3
_PROFILE_DESCRIPTION_MAX_CHARS_PER_LINE = 27
_EMAIL_RE = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.IGNORECASE)
APPLE_ID_KEYS_URL = "https://appleid.apple.com/auth/keys"
_APPLE_JWKS_CACHE = {"fetched_at": None, "keys": []}
_APPLE_JWKS_LOCK = threading.Lock()
_APPLE_JWKS_TTL = timedelta(hours=1)
_RESERVED_NICKNAME_PARTS = (
    "jobhub",
    "support",
    "moderator",
    "admin",
    "creator",
    "administrator",
)


def _allowed_phone_country_codes():
    raw_codes = os.environ.get("PHONE_AUTH_ALLOWED_COUNTRY_CODES", "").strip()
    if raw_codes:
        codes = tuple(code.strip() for code in raw_codes.split(",") if code.strip())
    else:
        codes = _DEFAULT_ALLOWED_PHONE_COUNTRY_CODES
    return tuple(sorted(codes, key=len, reverse=True))


def _phone_country_allowed(phone_e164):
    return any(phone_e164.startswith(code) for code in _allowed_phone_country_codes())


def _unsupported_phone_country_payload(simple_mode=False):
    text = (
        "Подтверждение по телефону доступно только для номеров стран ЕС, Украины "
        "и Беларуси. Укажите поддерживаемый номер или свяжитесь с поддержкой."
    )
    payload = {
        "error": "unsupported_phone_country",
        "message": text,
        "detail": text,
    }
    if simple_mode:
        payload["status"] = "error"
    return payload


def _generate_code():
    return f"{random.randint(0, 999999):06d}"


def _normalize_phone(raw_phone):
    value = (raw_phone or "").strip()
    if value.startswith("00"):
        value = f"+{value[2:]}"
    digits = re.sub(r"[^\d+]", "", value)
    if not digits.startswith("+"):
        digits = f"+{digits}"
    if not re.match(r"^\+\d{8,15}$", digits):
        return None
    return digits


def _auth_payload(user, token):
    # Every account gets a safe public name even if the person skipped nickname.
    profile, _ = UserProfile.objects.get_or_create(user=user)
    wallet = get_or_create_wallet(user)
    monetization_profile = get_or_create_monetization_profile(user)
    avatar_key = (profile.avatar_key if profile else "") or ""
    return {
        "token": token.key,
        "is_staff": user.is_staff,
        "email": user.email or "",
        "nickname": (profile.nickname if profile else "") or "",
        "profile_description": (profile.description if profile else "") or "",
        "avatar_url": avatar_public_url(avatar_key),
        "subscribers_count": user.employer_followers.count(),
        "phone": (profile.phone_e164 if profile else "") or "",
        "phone_verified": bool(profile and profile.phone_verified),
        "has_password": user.has_usable_password(),
        "wallet_total_credits": wallet.total_credits,
        "wallet_paid_credits": wallet.paid_credits,
        "wallet_bonus_credits": wallet.bonus_credits,
        "employer_subscription_until": monetization_profile.employer_subscription_until,
        "seeker_subscription_until": monetization_profile.seeker_subscription_until,
        "employer_review_summary": get_employer_review_summary(user),
    }


def _rotate_auth_token(user):
    Token.objects.filter(user=user).delete()
    return Token.objects.create(user=user)


def _login_candidates(identifier):
    value = (identifier or "").strip()
    if not value:
        return []

    users = []
    seen_ids = set()

    for user in User.objects.filter(username__iexact=value):
        if user.id in seen_ids:
            continue
        users.append(user)
        seen_ids.add(user.id)

    nickname_matches = UserProfile.objects.filter(
        nickname__iexact=value
    ).select_related("user")
    for profile in nickname_matches:
        user = profile.user
        if not user or user.id in seen_ids:
            continue
        users.append(user)
        seen_ids.add(user.id)

    return users


def _nickname_reserved_matches(nickname):
    lowered = (nickname or "").strip().casefold()
    if not lowered:
        return []
    return [word for word in _RESERVED_NICKNAME_PARTS if word in lowered]


def _is_password_policy_valid(password):
    value = (password or "")
    if len(value) < _PASSWORD_MIN_LENGTH:
        return False
    if re.search(r"\s", value):
        return False
    if not re.search(r"[A-Z]", value):
        return False
    if not re.search(r"[a-z]", value):
        return False
    return True


def _is_valid_email(email):
    value = (email or "").strip().lower()
    if not value:
        return False
    if contains_link(value):
        return False
    return bool(_EMAIL_RE.match(value))


def _google_sign_in_client_ids():
    raw = (getattr(settings, "GOOGLE_SIGN_IN_CLIENT_IDS", "") or "").strip()
    return {
        item.strip()
        for item in raw.split(",")
        if item and item.strip()
    }


def _verify_google_id_token(raw_token):
    token = (raw_token or "").strip()
    if not token:
        raise ValueError("google_token_required")

    allowed_client_ids = _google_sign_in_client_ids()
    if not allowed_client_ids:
        raise RuntimeError("google_sign_in_not_configured")

    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import id_token
    except ImportError as exc:
        raise RuntimeError("google_sign_in_dependencies_missing") from exc

    try:
        payload = id_token.verify_oauth2_token(token, Request(), audience=None)
    except Exception as exc:
        raise ValueError("google_token_invalid") from exc

    issuer = (payload.get("iss") or "").strip()
    if issuer not in {"accounts.google.com", "https://accounts.google.com"}:
        raise ValueError("google_token_invalid")

    audience = (payload.get("aud") or "").strip()
    if audience not in allowed_client_ids:
        raise ValueError("google_token_invalid")

    email = (payload.get("email") or "").strip().lower()
    if not _is_valid_email(email):
        raise ValueError("google_email_missing")

    if payload.get("email_verified") is not True:
        raise ValueError("google_email_not_verified")

    return payload


def _google_login_user(payload):
    email = (payload.get("email") or "").strip().lower()
    user = User.objects.filter(username__iexact=email).first()
    if not user:
        user = User.objects.filter(email__iexact=email).first()

    if user is None:
        user = User(username=email, email=email, is_active=True)
        user.set_unusable_password()
        user.save()
    else:
        updated_fields = []
        if user.username != email:
            user.username = email
            updated_fields.append("username")
        if user.email != email:
            user.email = email
            updated_fields.append("email")
        if not user.is_active:
            user.is_active = True
            updated_fields.append("is_active")
        if updated_fields:
            user.save(update_fields=updated_fields)

    UserProfile.objects.get_or_create(user=user)
    return user


def _apple_sign_in_client_ids():
    raw = (getattr(settings, "APPLE_SIGN_IN_CLIENT_IDS", "") or "").strip()
    return {
        item.strip()
        for item in raw.split(",")
        if item and item.strip()
    }


def _apple_public_keys(force_refresh=False):
    now = timezone.now()
    with _APPLE_JWKS_LOCK:
        fetched_at = _APPLE_JWKS_CACHE.get("fetched_at")
        cached_keys = _APPLE_JWKS_CACHE.get("keys") or []
        if (
            not force_refresh
            and cached_keys
            and fetched_at
            and now - fetched_at < _APPLE_JWKS_TTL
        ):
            return cached_keys

        try:
            with urllib_request.urlopen(APPLE_ID_KEYS_URL, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError("apple_sign_in_keys_unavailable") from exc

        keys = data.get("keys") or []
        if not isinstance(keys, list) or not keys:
            raise RuntimeError("apple_sign_in_keys_invalid")

        _APPLE_JWKS_CACHE["fetched_at"] = now
        _APPLE_JWKS_CACHE["keys"] = keys
        return keys


def _verify_apple_id_token(raw_token):
    token = (raw_token or "").strip()
    if not token:
        raise ValueError("apple_token_required")

    allowed_client_ids = _apple_sign_in_client_ids()
    if not allowed_client_ids:
        raise RuntimeError("apple_sign_in_not_configured")

    try:
        import jwt
    except ImportError as exc:
        raise RuntimeError("apple_sign_in_dependencies_missing") from exc

    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise ValueError("apple_token_invalid") from exc

    key_id = (header.get("kid") or "").strip()
    if not key_id:
        raise ValueError("apple_token_invalid")

    last_error = None
    for force_refresh in (False, True):
        keys = _apple_public_keys(force_refresh=force_refresh)
        key = next((item for item in keys if item.get("kid") == key_id), None)
        if not key:
            last_error = ValueError("apple_token_invalid")
            continue

        try:
            public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key))
            payload = jwt.decode(
                token,
                public_key,
                algorithms=["RS256"],
                audience=list(allowed_client_ids),
                issuer="https://appleid.apple.com",
            )
        except jwt.PyJWTError as exc:
            last_error = exc
            continue

        apple_user_id = (payload.get("sub") or "").strip()
        if not apple_user_id:
            raise ValueError("apple_token_invalid")

        email = (payload.get("email") or "").strip().lower()
        if email:
            if not _is_valid_email(email):
                raise ValueError("apple_email_missing")
            email_verified = payload.get("email_verified")
            if email_verified not in {True, "true", "1", 1}:
                raise ValueError("apple_email_not_verified")

        return payload

    raise ValueError("apple_token_invalid") from last_error


def _synthetic_apple_username(apple_user_id):
    digest = hashlib.sha256(apple_user_id.encode("utf-8")).hexdigest()[:32]
    return f"apple_{digest}"


def _apple_login_user(payload):
    apple_user_id = (payload.get("sub") or "").strip()
    email = (payload.get("email") or "").strip().lower()
    email = email if _is_valid_email(email) else ""

    profile = (
        UserProfile.objects.select_related("user")
        .filter(apple_user_id=apple_user_id)
        .first()
    )
    if profile:
        user = profile.user
    else:
        user = None
        if email:
            user = User.objects.filter(username__iexact=email).first()
            if user is None:
                user = User.objects.filter(email__iexact=email).first()

        if user is None:
            username = email or _synthetic_apple_username(apple_user_id)
            user = User(username=username, email=email, is_active=True)
            user.set_unusable_password()
            user.save()

        profile, _ = UserProfile.objects.get_or_create(user=user)
        if profile.apple_user_id and profile.apple_user_id != apple_user_id:
            raise ValueError("apple_account_conflict")
        if not profile.apple_user_id:
            profile.apple_user_id = apple_user_id
            profile.save(update_fields=["apple_user_id"])

    updated_fields = []
    if email and not user.email:
        user.email = email
        updated_fields.append("email")
    if email and user.username.startswith("apple_"):
        username_taken = User.objects.filter(username__iexact=email).exclude(id=user.id).exists()
        if not username_taken:
            user.username = email
            updated_fields.append("username")
    if not user.is_active:
        user.is_active = True
        updated_fields.append("is_active")
    if updated_fields:
        user.save(update_fields=updated_fields)

    UserProfile.objects.get_or_create(user=user)
    return user


def _send_email_code(email, code, purpose="register"):
    if purpose == "reset":
        subject = "JobHub password reset code"
        message = f"Your password reset code: {code}\nIt is valid for 10 minutes."
    elif purpose == "change_password":
        subject = "JobHub password change code"
        message = f"Your password change code: {code}\nIt is valid for 10 minutes."
    elif purpose == "link_email":
        subject = "JobHub email linking code"
        message = f"Your email linking code: {code}\nIt is valid for 10 minutes."
    elif purpose == "delete_account":
        subject = "JobHub account deletion confirmation code"
        message = f"Your account deletion confirmation code: {code}\nIt is valid for 10 minutes."
    else:
        subject = "JobHub verification code"
        message = f"Your verification code: {code}\nIt is valid for 10 minutes."
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)


def _send_whatsapp_code(phone_e164, code, purpose):
    if not _phone_country_allowed(phone_e164):
        return False

    if purpose == "reset":
        text = f"JobHub: password reset code {code}. Valid 10 minutes."
    elif purpose == "login":
        text = f"JobHub: login code {code}. Valid 10 minutes."
    else:
        text = f"JobHub: phone verification code {code}. Valid 10 minutes."

    sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
    token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
    from_phone = os.environ.get("TWILIO_WHATSAPP_FROM", "").strip() or os.environ.get("TWILIO_FROM_NUMBER", "").strip()

    if not sid or not token or not from_phone:
        print(f"[WHATSAPP-DEV] {phone_e164}: {text}")
        return False

    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    to_value = phone_e164 if phone_e164.startswith("whatsapp:") else f"whatsapp:{phone_e164}"
    from_value = from_phone if from_phone.startswith("whatsapp:") else f"whatsapp:{from_phone}"
    payload = parse.urlencode({"To": to_value, "From": from_value, "Body": text}).encode()
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    req = urllib_request.Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib_request.urlopen(req, timeout=15):
            return True
    except Exception as exc:
        print(f"[WHATSAPP-ERROR] {phone_e164}: {exc}")
        return False


def _twilio_verify_service_sid():
    return os.environ.get("TWILIO_VERIFY_SERVICE_SID", "").strip()


def _mask_service_sid(value):
    if not value:
        return ""
    if len(value) <= 8:
        return f"{value[:2]}****"
    return f"{value[:2]}{'*' * (len(value) - 6)}{value[-4:]}"


def _log_twilio_verify_health():
    sid = _twilio_verify_service_sid()
    if sid:
        print(f"[TWILIO-VERIFY-HEALTH] service_sid={_mask_service_sid(sid)}")
    else:
        print("[TWILIO-VERIFY-HEALTH] service_sid is missing")


def _debug_error_details(raw):
    if not settings.DEBUG:
        return None
    txt = (raw or "").strip()
    return txt[:800] if txt else None


def _twilio_error_message(raw, fallback="twilio_error"):
    try:
        payload = json.loads(raw or "{}")
        message = (payload.get("message") or "").strip()
        code = payload.get("code")
        if message and code:
            return f"{message} (code {code})"
        if message:
            return message
    except Exception:
        pass
    txt = (raw or "").strip()
    return txt[:200] if txt else fallback


def _consume_phone_request_slot(phone_e164):
    now = timezone.now()
    with _phone_request_lock:
        current = _phone_request_attempts.get(phone_e164, [])
        fresh = [ts for ts in current if ts > now - _PHONE_REQUEST_WINDOW]
        if len(fresh) >= _PHONE_REQUEST_MAX_ATTEMPTS:
            _phone_request_attempts[phone_e164] = fresh
            return False
        fresh.append(now)
        _phone_request_attempts[phone_e164] = fresh
        return True


def _twilio_credentials():
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
    token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
    return sid, token


def _twilio_verify_start(phone_e164, channel="whatsapp"):
    if not _phone_country_allowed(phone_e164):
        return False, "unsupported_phone_country", status.HTTP_400_BAD_REQUEST

    service_sid = _twilio_verify_service_sid()
    sid, token = _twilio_credentials()
    if not service_sid or not sid or not token:
        return False, "Twilio Verify is not configured", status.HTTP_503_SERVICE_UNAVAILABLE

    url = f"https://verify.twilio.com/v2/Services/{service_sid}/Verifications"
    payload = parse.urlencode({"To": phone_e164, "Channel": channel}).encode()
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    req = urllib_request.Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib_request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            if body.get("status") in {"pending", "approved"}:
                return True, None, status.HTTP_200_OK
            details = json.dumps(body)
            return False, _twilio_error_message(details, "verification_not_sent"), status.HTTP_502_BAD_GATEWAY
    except HTTPError as exc:
        try:
            details = exc.read().decode("utf-8")
        except Exception:
            details = str(exc)
        print(f"[TWILIO-VERIFY-START-ERROR] {phone_e164}: {details}")
        code = status.HTTP_400_BAD_REQUEST if exc.code and 400 <= int(exc.code) < 500 else status.HTTP_503_SERVICE_UNAVAILABLE
        return False, _twilio_error_message(details, "verification_not_sent"), code
    except Exception as exc:
        print(f"[TWILIO-VERIFY-START-ERROR] {phone_e164}: {exc}")
        return False, _twilio_error_message(str(exc), "verification_not_sent"), status.HTTP_503_SERVICE_UNAVAILABLE


def _twilio_verify_check(phone_e164, code):
    service_sid = _twilio_verify_service_sid()
    sid, token = _twilio_credentials()
    if not service_sid or not sid or not token:
        return False, "Twilio Verify is not configured", status.HTTP_503_SERVICE_UNAVAILABLE

    url = f"https://verify.twilio.com/v2/Services/{service_sid}/VerificationCheck"
    payload = parse.urlencode({"To": phone_e164, "Code": code}).encode()
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    req = urllib_request.Request(url, data=payload, method="POST")
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib_request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            if body.get("status") == "approved":
                return True, None, status.HTTP_200_OK
            return False, "denied", status.HTTP_400_BAD_REQUEST
    except HTTPError as exc:
        try:
            details = exc.read().decode("utf-8")
        except Exception:
            details = str(exc)
        print(f"[TWILIO-VERIFY-CHECK-ERROR] {phone_e164}: {details}")
        code = status.HTTP_400_BAD_REQUEST if exc.code and 400 <= int(exc.code) < 500 else status.HTTP_503_SERVICE_UNAVAILABLE
        return False, _twilio_error_message(details, "verification_check_failed"), code
    except Exception as exc:
        print(f"[TWILIO-VERIFY-CHECK-ERROR] {phone_e164}: {exc}")
        return False, _twilio_error_message(str(exc), "verification_check_failed"), status.HTTP_503_SERVICE_UNAVAILABLE


def _request_ip(request):
    forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",", 1)[0].strip()
    return forwarded or (request.META.get("REMOTE_ADDR") or None)


def _record_phone_verification_attempt(
    request,
    *,
    phone_e164="",
    purpose="",
    channel="",
    status_code="",
    message="",
    http_status=None,
    user=None,
):
    try:
        request_user = getattr(request, "user", None)
        if user is None and getattr(request_user, "is_authenticated", False):
            user = request_user
        PhoneVerificationAttempt.objects.create(
            phone_e164=(phone_e164 or "")[:20],
            user=user if user and getattr(user, "is_authenticated", True) else None,
            purpose=(purpose or "")[:20],
            channel=(channel or "")[:20],
            status=status_code,
            message=(message or "")[:255],
            http_status=http_status,
            ip_address=_request_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:255],
        )
    except Exception as exc:
        # Verification must not fail just because audit logging failed.
        print(f"[PHONE-VERIFY-ATTEMPT-LOG-ERROR] {exc}")


def _username_for_phone(phone_e164):
    # Keep usernames deterministic and ASCII-safe for phone-only accounts.
    return f"phone_{phone_e164.replace('+', '')}"


def _phone_code_too_frequent(phone_e164, purpose):
    return PhoneVerification.objects.filter(
        phone_e164=phone_e164,
        purpose=purpose,
        created_at__gt=timezone.now() - _PHONE_CODE_COOLDOWN,
    ).exists()


def _create_phone_code(phone_e164, purpose, user=None):
    PhoneVerification.objects.filter(
        phone_e164=phone_e164,
        purpose=purpose,
        is_used=False,
    ).update(is_used=True)
    code = _generate_code()
    record = PhoneVerification.objects.create(
        phone_e164=phone_e164,
        user=user,
        code=code,
        purpose=purpose,
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    return record


def _latest_phone_code_record(phone_e164, purpose):
    return (
        PhoneVerification.objects.filter(
            phone_e164=phone_e164,
            purpose=purpose,
            is_used=False,
        )
        .order_by("-created_at")
        .first()
    )


def _phone_reset_failed_attempts(phone_e164):
    window_start = timezone.now() - _PHONE_RESET_VERIFY_BLOCK_WINDOW
    return sum(
        PhoneVerification.objects.filter(
            phone_e164=phone_e164,
            purpose="reset",
            created_at__gt=window_start,
        ).values_list("attempts", flat=True)
    )


def _phone_reset_verify_blocked(phone_e164):
    return _phone_reset_failed_attempts(phone_e164) >= _PHONE_RESET_VERIFY_ATTEMPT_LIMIT


def _phone_reset_request_blocked(phone_e164):
    if _phone_code_too_frequent(phone_e164, "reset"):
        return True
    if _phone_reset_verify_blocked(phone_e164):
        return True
    now = timezone.now()
    base_qs = PhoneVerification.objects.filter(phone_e164=phone_e164, purpose="reset")
    if base_qs.filter(created_at__gt=now - timedelta(hours=1)).count() >= _PHONE_RESET_REQUEST_HOURLY_LIMIT:
        return True
    if base_qs.filter(created_at__gt=now - timedelta(days=1)).count() >= _PHONE_RESET_REQUEST_DAILY_LIMIT:
        return True
    return False


def _increment_phone_attempt(record):
    if not record:
        return 0
    record.attempts = max(0, int(record.attempts or 0)) + 1
    record.save(update_fields=["attempts"])
    return record.attempts


def _email_code_too_frequent(user, purpose):
    return EmailVerification.objects.filter(
        user=user,
        purpose=purpose,
        created_at__gt=timezone.now() - timedelta(seconds=45),
    ).exists()


def _create_email_code(user, purpose, target_email=""):
    EmailVerification.objects.filter(
        user=user,
        purpose=purpose,
        is_used=False,
    ).update(is_used=True)
    code = _generate_code()
    rec = EmailVerification.objects.create(
        user=user,
        code=code,
        purpose=purpose,
        target_email=target_email,
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    return rec


def _hide_user_vacancies(user):
    now = timezone.now()
    Vacancy.objects.filter(created_by=user).update(
        is_approved=False,
        is_rejected=True,
        is_editing=False,
        rejection_reason="Account scheduled for deletion",
        last_moderator_rejection_reason="Account scheduled for deletion",
        editing_started_at=None,
        expires_at=now,
    )


def _schedule_account_deletion(user, confirmed_via, note=""):
    now = timezone.now()
    req = AccountDeletionRequest.objects.filter(user=user, status="pending").order_by("-requested_at").first()
    if req is None:
        req = AccountDeletionRequest.objects.create(
            user=user,
            user_id_snapshot=user.id,
            email_snapshot=(user.email or "").strip(),
            status="pending",
            confirmed_via=confirmed_via,
            execute_after=now + _ACCOUNT_DELETION_DELAY,
            note=(note or "").strip(),
        )
    else:
        req.confirmed_via = confirmed_via
        if note:
            req.note = note.strip()
        req.execute_after = now + _ACCOUNT_DELETION_DELAY
        req.save(update_fields=["confirmed_via", "note", "execute_after"])

    _hide_user_vacancies(user)

    profile = UserProfile.objects.filter(user=user).first()
    if profile:
        profile.phone_verified = False
        profile.save(update_fields=["phone_verified"])

    if user.is_active:
        user.is_active = False
        user.save(update_fields=["is_active"])

    Token.objects.filter(user=user).delete()
    return req


_log_twilio_verify_health()


class RegisterAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password") or ""

        if not email or not password:
            return Response({"error": "email and password required"}, status=status.HTTP_400_BAD_REQUEST)
        if not _is_valid_email(email):
            return Response({"error": "invalid email"}, status=status.HTTP_400_BAD_REQUEST)
        if not _is_password_policy_valid(password):
            return Response({"error": "password_policy_violation"}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(username=email).first()
        if user and user.is_active:
            return Response({"error": "user already exists"}, status=status.HTTP_400_BAD_REQUEST)

        if user:
            user.set_password(password)
            user.save()
        else:
            user = User.objects.create_user(username=email, email=email, password=password, is_active=False)

        EmailVerification.objects.filter(user=user, purpose="register", is_used=False).update(is_used=True)
        code = _generate_code()
        EmailVerification.objects.create(
            user=user,
            code=code,
            purpose="register",
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        _send_email_code(email, code, "register")

        return Response({"detail": "verification_sent"})


class VerifyEmailAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        code = (request.data.get("code") or "").strip()

        if not email or not code:
            return Response({"error": "email and code required"}, status=status.HTTP_400_BAD_REQUEST)
        if not _is_valid_email(email):
            return Response({"error": "invalid email"}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(username=email).first()
        if not user:
            return Response({"error": "user not found"}, status=status.HTTP_400_BAD_REQUEST)

        rec = EmailVerification.objects.filter(
            user=user,
            purpose="register",
            code=code,
            is_used=False,
        ).order_by("-created_at").first()

        if not rec or not rec.is_valid():
            return Response({"error": "invalid or expired code"}, status=status.HTTP_400_BAD_REQUEST)

        rec.is_used = True
        rec.save(update_fields=["is_used"])

        user.is_active = True
        user.save(update_fields=["is_active"])

        token, _ = Token.objects.get_or_create(user=user)
        return Response(_auth_payload(user, token))


class ResendCodeAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        if not email:
            return Response({"error": "email required"}, status=status.HTTP_400_BAD_REQUEST)
        if not _is_valid_email(email):
            return Response({"error": "invalid email"}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(username=email).first()
        if not user:
            return Response({"error": "user not found"}, status=status.HTTP_400_BAD_REQUEST)
        if user.is_active:
            return Response({"error": "already verified"}, status=status.HTTP_400_BAD_REQUEST)

        EmailVerification.objects.filter(user=user, purpose="register", is_used=False).update(is_used=True)
        code = _generate_code()
        EmailVerification.objects.create(
            user=user,
            code=code,
            purpose="register",
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        _send_email_code(email, code, "register")
        return Response({"detail": "verification_sent"})


class PhoneRequestCodeAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        phone = _normalize_phone(request.data.get("phone"))
        purpose = (request.data.get("purpose") or "").strip()
        channel = (request.data.get("channel") or "").strip().lower() or "whatsapp"
        if channel not in {"whatsapp", "sms"}:
            _record_phone_verification_attempt(
                request,
                phone_e164=phone,
                purpose=purpose,
                channel=channel,
                status_code="invalid_channel",
                message="invalid_channel",
                http_status=status.HTTP_400_BAD_REQUEST,
            )
            if purpose:
                return Response({"error": "invalid channel"}, status=status.HTTP_400_BAD_REQUEST)
            return Response({"status": "error", "message": "invalid_channel"}, status=status.HTTP_400_BAD_REQUEST)

        if not phone:
            _record_phone_verification_attempt(
                request,
                phone_e164=phone,
                purpose=purpose,
                channel=channel,
                status_code="invalid_phone",
                message="invalid_phone",
                http_status=status.HTTP_400_BAD_REQUEST,
            )
            if purpose:
                return Response({"error": "invalid phone"}, status=status.HTTP_400_BAD_REQUEST)
            return Response({"status": "error", "message": "invalid_phone"}, status=status.HTTP_400_BAD_REQUEST)

        if not _phone_country_allowed(phone):
            _record_phone_verification_attempt(
                request,
                phone_e164=phone,
                purpose=purpose,
                channel=channel,
                status_code="unsupported_country",
                message="unsupported_phone_country",
                http_status=status.HTTP_400_BAD_REQUEST,
            )
            return Response(
                _unsupported_phone_country_payload(simple_mode=not bool(purpose)),
                status=status.HTTP_400_BAD_REQUEST,
            )

        # New simple mode (no purpose): pure Twilio Verify request.
        if not purpose:
            if not _consume_phone_request_slot(phone):
                _record_phone_verification_attempt(
                    request,
                    phone_e164=phone,
                    channel=channel,
                    status_code="too_many_requests",
                    message="too_many_requests",
                    http_status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
                return Response({"status": "error", "message": "too_many_requests"}, status=status.HTTP_429_TOO_MANY_REQUESTS)
            ok, msg, http_code = _twilio_verify_start(phone, channel=channel)
            if ok:
                _record_phone_verification_attempt(
                    request,
                    phone_e164=phone,
                    channel=channel,
                    status_code="sent",
                    http_status=status.HTTP_200_OK,
                )
                return Response({"status": "sent"})
            _record_phone_verification_attempt(
                request,
                phone_e164=phone,
                channel=channel,
                status_code="delivery_failed",
                message=msg or "verification_not_sent",
                http_status=http_code,
            )
            payload = {"status": "error", "message": "verification_not_sent"}
            if settings.DEBUG and msg:
                payload["detail"] = _debug_error_details(msg)
            return Response(payload, status=http_code)

        if purpose not in {"verify_phone", "login", "reset"}:
            _record_phone_verification_attempt(
                request,
                phone_e164=phone,
                purpose=purpose,
                channel=channel,
                status_code="invalid_purpose",
                message="invalid_purpose",
                http_status=status.HTTP_400_BAD_REQUEST,
            )
            return Response({"error": "invalid purpose"}, status=status.HTTP_400_BAD_REQUEST)
        if purpose == "reset":
            if _phone_reset_request_blocked(phone):
                _record_phone_verification_attempt(
                    request,
                    phone_e164=phone,
                    purpose=purpose,
                    channel=channel,
                    status_code="too_many_requests",
                    message="too_many_requests",
                    http_status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
                return Response({"error": "too_many_requests"}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        elif _phone_code_too_frequent(phone, purpose):
            _record_phone_verification_attempt(
                request,
                phone_e164=phone,
                purpose=purpose,
                channel=channel,
                status_code="too_many_requests",
                message="too_many_requests",
                http_status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
            return Response({"error": "too_many_requests"}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        user = None
        if purpose == "verify_phone":
            if not request.user.is_authenticated:
                _record_phone_verification_attempt(
                    request,
                    phone_e164=phone,
                    purpose=purpose,
                    channel=channel,
                    status_code="auth_required",
                    message="auth_required",
                    http_status=status.HTTP_401_UNAUTHORIZED,
                )
                return Response({"error": "auth_required"}, status=status.HTTP_401_UNAUTHORIZED)
            owner = UserProfile.objects.filter(phone_e164=phone, phone_verified=True).exclude(user=request.user).first()
            if owner:
                _record_phone_verification_attempt(
                    request,
                    phone_e164=phone,
                    purpose=purpose,
                    channel=channel,
                    status_code="phone_already_used",
                    message="phone_already_used",
                    http_status=status.HTTP_400_BAD_REQUEST,
                )
                return Response({"error": "phone_already_used"}, status=status.HTTP_400_BAD_REQUEST)
            user = request.user
        elif purpose == "login":
            prof = UserProfile.objects.filter(phone_e164=phone, phone_verified=True).select_related("user").first()
            if prof:
                user = prof.user
        else:
            prof = UserProfile.objects.filter(phone_e164=phone, phone_verified=True).select_related("user").first()
            if not prof:
                _record_phone_verification_attempt(
                    request,
                    phone_e164=phone,
                    purpose=purpose,
                    channel=channel,
                    status_code="user_not_found",
                    message="user not found",
                    http_status=status.HTTP_400_BAD_REQUEST,
                )
                return Response({"error": "user not found"}, status=status.HTTP_400_BAD_REQUEST)
            user = prof.user

        # Keep local record for throttling/audit even when Twilio Verify is used.
        _create_phone_code(phone, purpose, user=user)

        ok, msg, http_code = _twilio_verify_start(phone, channel=channel)
        if not ok:
            _record_phone_verification_attempt(
                request,
                phone_e164=phone,
                purpose=purpose,
                channel=channel,
                status_code="delivery_failed",
                message=msg or f"{channel}_delivery_failed",
                http_status=http_code,
                user=user,
            )
            payload = {"error": f"{channel}_delivery_failed"}
            if settings.DEBUG and msg:
                payload["detail"] = _debug_error_details(msg)
            return Response(payload, status=http_code)
        _record_phone_verification_attempt(
            request,
            phone_e164=phone,
            purpose=purpose,
            channel=channel,
            status_code="sent",
            http_status=status.HTTP_200_OK,
            user=user,
        )
        return Response({"detail": "code_sent", "channel": channel})


class PhoneVerifyCodeAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        phone = _normalize_phone(request.data.get("phone"))
        code = (request.data.get("code") or "").strip()
        purpose = (request.data.get("purpose") or "").strip()

        if not phone or not code:
            _record_phone_verification_attempt(
                request,
                phone_e164=phone,
                purpose=purpose,
                status_code="check_failed",
                message="phone_and_code_required",
                http_status=status.HTTP_400_BAD_REQUEST,
            )
            if purpose:
                return Response({"error": "phone and code required"}, status=status.HTTP_400_BAD_REQUEST)
            return Response({"status": "error", "message": "phone_and_code_required"}, status=status.HTTP_400_BAD_REQUEST)

        # New simple mode (no purpose): pure Twilio Verify check.
        if not purpose:
            ok, msg, http_code = _twilio_verify_check(phone, code)
            if ok:
                _record_phone_verification_attempt(
                    request,
                    phone_e164=phone,
                    status_code="approved",
                    http_status=status.HTTP_200_OK,
                )
                return Response({"status": "approved"})
            if msg == "denied":
                _record_phone_verification_attempt(
                    request,
                    phone_e164=phone,
                    status_code="check_failed",
                    message="denied",
                    http_status=status.HTTP_400_BAD_REQUEST,
                )
                return Response({"status": "denied"}, status=status.HTTP_400_BAD_REQUEST)
            _record_phone_verification_attempt(
                request,
                phone_e164=phone,
                status_code="check_failed",
                message=msg or "verification_check_failed",
                http_status=http_code,
            )
            payload = {"status": "error", "message": "verification_check_failed"}
            if settings.DEBUG and msg:
                payload["detail"] = _debug_error_details(msg)
            return Response(payload, status=http_code)

        if purpose not in {"verify_phone", "login", "reset"}:
            _record_phone_verification_attempt(
                request,
                phone_e164=phone,
                purpose=purpose,
                status_code="invalid_purpose",
                message="invalid_purpose",
                http_status=status.HTTP_400_BAD_REQUEST,
            )
            return Response({"error": "invalid purpose"}, status=status.HTTP_400_BAD_REQUEST)

        if purpose == "reset" and _phone_reset_verify_blocked(phone):
            _record_phone_verification_attempt(
                request,
                phone_e164=phone,
                purpose=purpose,
                status_code="too_many_requests",
                message="too_many_attempts",
                http_status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
            return Response({"error": "too_many_attempts"}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        rec = PhoneVerification.objects.filter(
            phone_e164=phone,
            purpose=purpose,
            is_used=False,
        ).order_by("-created_at").first()

        if _twilio_verify_service_sid():
            approved, _, _ = _twilio_verify_check(phone, code)
            if not approved:
                if purpose == "reset":
                    _increment_phone_attempt(_latest_phone_code_record(phone, purpose))
                    if _phone_reset_verify_blocked(phone):
                        _record_phone_verification_attempt(
                            request,
                            phone_e164=phone,
                            purpose=purpose,
                            status_code="too_many_requests",
                            message="too_many_attempts",
                            http_status=status.HTTP_429_TOO_MANY_REQUESTS,
                        )
                        return Response({"error": "too_many_attempts"}, status=status.HTTP_429_TOO_MANY_REQUESTS)
                _record_phone_verification_attempt(
                    request,
                    phone_e164=phone,
                    purpose=purpose,
                    status_code="check_failed",
                    message="invalid_or_expired_code",
                    http_status=status.HTTP_400_BAD_REQUEST,
                )
                return Response({"error": "invalid or expired code"}, status=status.HTTP_400_BAD_REQUEST)
            if rec:
                rec.is_used = True
                rec.save(update_fields=["is_used"])
        else:
            if not rec or not rec.is_valid():
                if purpose == "reset":
                    _increment_phone_attempt(_latest_phone_code_record(phone, purpose))
                    if _phone_reset_verify_blocked(phone):
                        _record_phone_verification_attempt(
                            request,
                            phone_e164=phone,
                            purpose=purpose,
                            status_code="too_many_requests",
                            message="too_many_attempts",
                            http_status=status.HTTP_429_TOO_MANY_REQUESTS,
                        )
                        return Response({"error": "too_many_attempts"}, status=status.HTTP_429_TOO_MANY_REQUESTS)
                _record_phone_verification_attempt(
                    request,
                    phone_e164=phone,
                    purpose=purpose,
                    status_code="check_failed",
                    message="invalid_or_expired_code",
                    http_status=status.HTTP_400_BAD_REQUEST,
                )
                return Response({"error": "invalid or expired code"}, status=status.HTTP_400_BAD_REQUEST)
            if rec.code != code:
                rec.attempts += 1
                rec.save(update_fields=["attempts"])
                if purpose == "reset" and _phone_reset_verify_blocked(phone):
                    _record_phone_verification_attempt(
                        request,
                        phone_e164=phone,
                        purpose=purpose,
                        status_code="too_many_requests",
                        message="too_many_attempts",
                        http_status=status.HTTP_429_TOO_MANY_REQUESTS,
                    )
                    return Response({"error": "too_many_attempts"}, status=status.HTTP_429_TOO_MANY_REQUESTS)
                _record_phone_verification_attempt(
                    request,
                    phone_e164=phone,
                    purpose=purpose,
                    status_code="check_failed",
                    message="invalid_or_expired_code",
                    http_status=status.HTTP_400_BAD_REQUEST,
                )
                return Response({"error": "invalid or expired code"}, status=status.HTTP_400_BAD_REQUEST)
            rec.is_used = True
            rec.save(update_fields=["is_used"])

        if purpose == "verify_phone":
            if not request.user.is_authenticated:
                _record_phone_verification_attempt(
                    request,
                    phone_e164=phone,
                    purpose=purpose,
                    status_code="auth_required",
                    message="auth_required",
                    http_status=status.HTTP_401_UNAUTHORIZED,
                )
                return Response({"error": "auth_required"}, status=status.HTTP_401_UNAUTHORIZED)
            profile, _ = UserProfile.objects.get_or_create(user=request.user)
            try:
                profile.phone_e164 = phone
                profile.phone_verified = True
                profile.phone_verified_at = timezone.now()
                profile.save(update_fields=["phone_e164", "phone_verified", "phone_verified_at"])
            except IntegrityError:
                _record_phone_verification_attempt(
                    request,
                    phone_e164=phone,
                    purpose=purpose,
                    status_code="phone_already_used",
                    message="phone_already_used",
                    http_status=status.HTTP_400_BAD_REQUEST,
                )
                return Response({"error": "phone_already_used"}, status=status.HTTP_400_BAD_REQUEST)
            _record_phone_verification_attempt(
                request,
                phone_e164=phone,
                purpose=purpose,
                status_code="approved",
                http_status=status.HTTP_200_OK,
            )
            return Response({"detail": "phone_verified", "phone": phone, "phone_verified": True})

        if purpose == "reset":
            _record_phone_verification_attempt(
                request,
                phone_e164=phone,
                purpose=purpose,
                status_code="approved",
                http_status=status.HTTP_200_OK,
            )
            return Response({"detail": "code_verified"})

        profile = UserProfile.objects.filter(phone_e164=phone).select_related("user").first()
        if not profile:
            username = _username_for_phone(phone)
            user = User.objects.filter(username=username).first()
            if not user:
                user = User.objects.create_user(username=username, email="", password=None, is_active=True)
            profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.phone_e164 = phone
        profile.phone_verified = True
        profile.phone_verified_at = timezone.now()
        profile.save(update_fields=["phone_e164", "phone_verified", "phone_verified_at"])
        user = profile.user
        token, _ = Token.objects.get_or_create(user=user)
        _record_phone_verification_attempt(
            request,
            phone_e164=phone,
            purpose=purpose,
            status_code="approved",
            http_status=status.HTTP_200_OK,
            user=user,
        )
        return Response(_auth_payload(user, token))


class ResetPasswordRequestAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        phone = _normalize_phone(request.data.get("phone"))

        if not email and not phone:
            return Response({"error": "email or phone required"}, status=status.HTTP_400_BAD_REQUEST)

        if phone:
            if not _phone_country_allowed(phone):
                return Response(
                    _unsupported_phone_country_payload(),
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if _phone_reset_request_blocked(phone):
                return Response({"error": "too_many_requests"}, status=status.HTTP_429_TOO_MANY_REQUESTS)
            prof = UserProfile.objects.filter(phone_e164=phone, phone_verified=True).select_related("user").first()
            if not prof:
                return Response({"error": "user not found"}, status=status.HTTP_400_BAD_REQUEST)
            rec = _create_phone_code(phone, "reset", user=prof.user)
            sent = _send_whatsapp_code(phone, rec.code, "reset")
            if not sent:
                if settings.DEBUG:
                    return Response({"detail": "reset_code_sent", "channel": "debug", "debug_code": rec.code})
                return Response({"error": "whatsapp_delivery_failed"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            return Response({"detail": "reset_code_sent", "channel": "whatsapp"})

        if not _is_valid_email(email):
            return Response({"error": "invalid email"}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(username=email).first()
        if not user:
            return Response({"error": "user not found"}, status=status.HTTP_400_BAD_REQUEST)
        if not user.is_active:
            return Response({"error": "email_not_verified"}, status=status.HTTP_400_BAD_REQUEST)

        EmailVerification.objects.filter(user=user, purpose="reset", is_used=False).update(is_used=True)
        code = _generate_code()
        EmailVerification.objects.create(
            user=user,
            code=code,
            purpose="reset",
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        _send_email_code(email, code, "reset")
        return Response({"detail": "reset_code_sent", "channel": "email"})


class ResetPasswordConfirmAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        phone = _normalize_phone(request.data.get("phone"))
        code = (request.data.get("code") or "").strip()
        new_password = request.data.get("new_password") or ""

        if not code or not new_password:
            return Response({"error": "code and new_password required"}, status=status.HTTP_400_BAD_REQUEST)
        if not _is_password_policy_valid(new_password):
            return Response({"error": "password_policy_violation"}, status=status.HTTP_400_BAD_REQUEST)

        if phone:
            if _phone_reset_verify_blocked(phone):
                return Response({"error": "too_many_attempts"}, status=status.HTTP_429_TOO_MANY_REQUESTS)
            prof = UserProfile.objects.filter(phone_e164=phone, phone_verified=True).select_related("user").first()
            if not prof:
                return Response({"error": "user not found"}, status=status.HTTP_400_BAD_REQUEST)
            rec = PhoneVerification.objects.filter(
                phone_e164=phone,
                purpose="reset",
                code=code,
                is_used=False,
            ).order_by("-created_at").first()
            if not rec or not rec.is_valid():
                _increment_phone_attempt(_latest_phone_code_record(phone, "reset"))
                if _phone_reset_verify_blocked(phone):
                    return Response({"error": "too_many_attempts"}, status=status.HTTP_429_TOO_MANY_REQUESTS)
                return Response({"error": "invalid or expired code"}, status=status.HTTP_400_BAD_REQUEST)
            rec.is_used = True
            rec.save(update_fields=["is_used"])
            user = prof.user
            user.set_password(new_password)
            user.save(update_fields=["password"])
            return Response({"detail": "password_reset"})

        if not email:
            return Response({"error": "email or phone required"}, status=status.HTTP_400_BAD_REQUEST)
        if not _is_valid_email(email):
            return Response({"error": "invalid email"}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(username=email).first()
        if not user:
            return Response({"error": "user not found"}, status=status.HTTP_400_BAD_REQUEST)

        rec = EmailVerification.objects.filter(
            user=user,
            purpose="reset",
            code=code,
            is_used=False,
        ).order_by("-created_at").first()

        if not rec or not rec.is_valid():
            return Response({"error": "invalid or expired code"}, status=status.HTTP_400_BAD_REQUEST)

        rec.is_used = True
        rec.save(update_fields=["is_used"])

        user.set_password(new_password)
        user.save(update_fields=["password"])
        return Response({"detail": "password_reset"})


class ChangePasswordRequestAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        if user.has_usable_password():
            return Response(
                {"error": "current_password_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        channel = (request.data.get("channel") or "").strip().lower()
        if channel not in {"email", "sms"}:
            return Response({"error": "invalid_channel"}, status=status.HTTP_400_BAD_REQUEST)

        if channel == "email":
            if not (user.email or "").strip():
                return Response({"error": "email_not_linked"}, status=status.HTTP_400_BAD_REQUEST)
            if _email_code_too_frequent(user, "change_password"):
                return Response({"error": "too_many_requests"}, status=status.HTTP_429_TOO_MANY_REQUESTS)
            rec = _create_email_code(user, "change_password", target_email=user.email)
            _send_email_code(user.email, rec.code, "change_password")
            return Response({"detail": "code_sent", "channel": "email"})

        profile = UserProfile.objects.filter(user=user).first()
        if not profile or not profile.phone_verified or not profile.phone_e164:
            return Response({"error": "phone_not_verified"}, status=status.HTTP_400_BAD_REQUEST)
        if not _phone_country_allowed(profile.phone_e164):
            return Response(
                _unsupported_phone_country_payload(),
                status=status.HTTP_400_BAD_REQUEST,
            )
        if _phone_code_too_frequent(profile.phone_e164, "change_password"):
            return Response({"error": "too_many_requests"}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        _create_phone_code(profile.phone_e164, "change_password", user=user)
        ok, msg, http_code = _twilio_verify_start(profile.phone_e164, channel="sms")
        if not ok:
            payload = {"error": "sms_delivery_failed"}
            if settings.DEBUG and msg:
                payload["detail"] = _debug_error_details(msg)
            return Response(payload, status=http_code)
        return Response({"detail": "code_sent", "channel": "sms"})


class ChangePasswordAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        current_password = request.data.get("current_password") or ""
        new_password = request.data.get("new_password") or ""
        code = (request.data.get("code") or "").strip()
        channel = (request.data.get("channel") or "").strip().lower()

        if not new_password:
            return Response(
                {"error": "new_password_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not _is_password_policy_valid(new_password):
            return Response(
                {"error": "password_policy_violation"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if user.has_usable_password():
            if not current_password:
                return Response(
                    {"error": "current_password_required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not user.check_password(current_password):
                return Response(
                    {"error": "invalid_current_password"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            if channel not in {"email", "sms"}:
                return Response({"error": "invalid_channel"}, status=status.HTTP_400_BAD_REQUEST)
            if not code:
                return Response({"error": "code_required"}, status=status.HTTP_400_BAD_REQUEST)

            if channel == "email":
                if not (user.email or "").strip():
                    return Response(
                        {"error": "email_not_linked"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                rec = EmailVerification.objects.filter(
                    user=user,
                    purpose="change_password",
                    code=code,
                    is_used=False,
                ).order_by("-created_at").first()
                if not rec or not rec.is_valid():
                    return Response(
                        {"error": "invalid_or_expired_code"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                rec.is_used = True
                rec.save(update_fields=["is_used"])
            else:
                profile = UserProfile.objects.filter(user=user).first()
                if not profile or not profile.phone_verified or not profile.phone_e164:
                    return Response(
                        {"error": "phone_not_verified"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if _twilio_verify_service_sid():
                    approved, msg, http_code = _twilio_verify_check(profile.phone_e164, code)
                    if not approved:
                        if msg == "denied":
                            return Response(
                                {"error": "invalid_or_expired_code"},
                                status=status.HTTP_400_BAD_REQUEST,
                            )
                        payload = {"error": "verification_check_failed"}
                        if settings.DEBUG and msg:
                            payload["detail"] = _debug_error_details(msg)
                        return Response(payload, status=http_code)

                    local_rec = PhoneVerification.objects.filter(
                        phone_e164=profile.phone_e164,
                        purpose="change_password",
                        is_used=False,
                    ).order_by("-created_at").first()
                    if local_rec:
                        local_rec.is_used = True
                        local_rec.save(update_fields=["is_used"])
                else:
                    local_rec = PhoneVerification.objects.filter(
                        phone_e164=profile.phone_e164,
                        purpose="change_password",
                        code=code,
                        is_used=False,
                    ).order_by("-created_at").first()
                    if not local_rec or not local_rec.is_valid():
                        return Response(
                            {"error": "invalid_or_expired_code"},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    local_rec.is_used = True
                    local_rec.save(update_fields=["is_used"])

        user.set_password(new_password)
        user.save(update_fields=["password"])
        token = _rotate_auth_token(user)
        payload = _auth_payload(user, token)
        payload["detail"] = "password_changed"
        return Response(payload)


class LoginAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        identifier = (request.data.get("identifier") or request.data.get("email") or "").strip()
        password = request.data.get("password") or ""

        if not identifier or not password:
            return Response({"error": "invalid credentials"}, status=status.HTTP_400_BAD_REQUEST)

        candidates = _login_candidates(identifier)
        if not candidates:
            return Response({"error": "invalid credentials"}, status=status.HTTP_400_BAD_REQUEST)

        if "@" in identifier:
            email_candidate = next(
                (u for u in candidates if (u.username or "").lower() == identifier.lower()),
                None,
            )
            if email_candidate and not email_candidate.is_active:
                return Response({"error": "email_not_verified"}, status=status.HTTP_400_BAD_REQUEST)

        authenticated_users = []
        seen_ids = set()
        for candidate in candidates:
            user = authenticate(username=candidate.username, password=password)
            if not user or user.id in seen_ids:
                continue
            authenticated_users.append(user)
            seen_ids.add(user.id)

        if not authenticated_users:
            return Response({"error": "invalid_password"}, status=status.HTTP_400_BAD_REQUEST)

        if len(authenticated_users) > 1:
            return Response({"error": "nickname_not_unique"}, status=status.HTTP_400_BAD_REQUEST)

        user = authenticated_users[0]

        token, _ = Token.objects.get_or_create(user=user)
        return Response(_auth_payload(user, token))


class GoogleLoginAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        try:
            payload = _verify_google_id_token(request.data.get("id_token"))
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except RuntimeError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        user = _google_login_user(payload)
        token, _ = Token.objects.get_or_create(user=user)
        auth_payload = _auth_payload(user, token)
        auth_payload["detail"] = "google_login"
        return Response(auth_payload)


class AppleLoginAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        try:
            payload = _verify_apple_id_token(request.data.get("id_token"))
            user = _apple_login_user(payload)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except RuntimeError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except IntegrityError:
            return Response(
                {"error": "apple_account_conflict"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        token, _ = Token.objects.get_or_create(user=user)
        auth_payload = _auth_payload(user, token)
        auth_payload["detail"] = "apple_login"
        return Response(auth_payload)


class MeAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        token, _ = Token.objects.get_or_create(user=request.user)
        return Response(_auth_payload(request.user, token))

    def patch(self, request):
        has_nickname = "nickname" in request.data
        nickname = (
            (request.data.get("nickname") or "")
            if has_nickname
            else None
        )
        has_profile_description = "profile_description" in request.data
        profile_description = (
            normalize_newlines((request.data.get("profile_description") or ""))
            if has_profile_description
            else None
        )
        if nickname is not None:
            nickname = censor_minimal(nickname)
        if profile_description is not None:
            profile_description = censor_minimal(profile_description)

        if nickname is not None and len(nickname) > 32:
            return Response({"error": "nickname_too_long"}, status=status.HTTP_400_BAD_REQUEST)
        if nickname is not None and contains_link(nickname):
            return Response(
                {"error": "nickname_links_not_allowed"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if nickname is not None and contains_digit_or_number_emoji(nickname):
            return Response(
                {"error": "nickname_digits_not_allowed"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if (
            profile_description is not None
            and len(profile_description) > _PROFILE_DESCRIPTION_MAX_LENGTH
        ):
            return Response({"error": "profile_description_too_long"}, status=status.HTTP_400_BAD_REQUEST)
        if profile_description is not None and contains_link(profile_description):
            return Response(
                {"error": "profile_description_links_not_allowed"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if profile_description is not None and contains_digit_or_number_emoji(profile_description):
            return Response(
                {"error": "profile_description_digits_not_allowed"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        profile_desc_line_error = (
            line_constraints_error(
                profile_description,
                max_lines=_PROFILE_DESCRIPTION_MAX_LINES,
                max_chars_per_line=_PROFILE_DESCRIPTION_MAX_CHARS_PER_LINE,
            )
            if profile_description is not None
            else None
        )
        if profile_desc_line_error == "too_many_lines":
            return Response(
                {"error": "profile_description_too_many_lines"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if profile_desc_line_error == "line_too_long":
            return Response(
                {"error": "profile_description_line_too_long"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        nickname_to_store = nickname.strip() if nickname is not None else None
        profile_description_to_store = (
            profile_description.strip() if profile_description is not None else None
        )

        reserved_matches = _nickname_reserved_matches(nickname_to_store or "")
        if nickname is not None and reserved_matches:
            return Response(
                {
                    "error": "nickname_reserved",
                    "blocked": reserved_matches,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        updated_fields = []
        if nickname_to_store is not None and profile.nickname != nickname_to_store:
            profile.nickname = nickname_to_store
            updated_fields.append("nickname")
        if (
            profile_description_to_store is not None
            and profile.description != profile_description_to_store
        ):
            profile.description = profile_description_to_store
            updated_fields.append("description")
        if updated_fields:
            profile.save(update_fields=updated_fields)

        token, _ = Token.objects.get_or_create(user=request.user)
        payload = _auth_payload(request.user, token)
        payload["detail"] = "nickname_updated"
        return Response(payload, status=status.HTTP_200_OK)


class MeAvatarAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if not is_avatar_storage_configured():
            return Response(
                {"error": "avatar_storage_not_configured"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if importlib_util.find_spec("PIL") is None:
            return Response(
                {"error": "avatar_processing_unavailable"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        avatar_file = request.FILES.get("avatar")
        try:
            payload, content_type, ext = process_avatar_image(avatar_file)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            print(f"[AVATAR-PROCESS-ERROR] user={request.user.id}: {exc}")
            return Response(
                {"error": "avatar_processing_failed"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        old_key = (profile.avatar_key or "").strip()
        new_key = build_avatar_object_key(
            user_id=request.user.id,
            filename=f"avatar{ext}",
        )

        try:
            upload_avatar_bytes(
                object_key=new_key,
                payload=payload,
                content_type=content_type,
            )
        except Exception as exc:
            print(f"[AVATAR-UPLOAD-ERROR] user={request.user.id}: {exc}")
            return Response(
                {"error": "avatar_upload_failed"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        profile.avatar_key = new_key
        profile.avatar_updated_at = timezone.now()
        profile.save(update_fields=["avatar_key", "avatar_updated_at"])

        if old_key and old_key != new_key:
            try:
                delete_avatar_object(old_key)
            except Exception as exc:
                # Non-blocking by design: keep successful update even if cleanup failed.
                print(f"[AVATAR-DELETE-OLD-ERROR] user={request.user.id}: {exc}")

        token, _ = Token.objects.get_or_create(user=request.user)
        auth_payload = _auth_payload(request.user, token)
        auth_payload["detail"] = "avatar_updated"
        return Response(auth_payload, status=status.HTTP_200_OK)

    def delete(self, request):
        profile = UserProfile.objects.filter(user=request.user).first()
        old_key = (profile.avatar_key or "").strip() if profile else ""

        if profile:
            profile.avatar_key = ""
            profile.avatar_updated_at = timezone.now()
            profile.save(update_fields=["avatar_key", "avatar_updated_at"])

        if old_key:
            try:
                delete_avatar_object(old_key)
            except Exception as exc:
                # Non-blocking by design: local state already cleaned.
                print(f"[AVATAR-DELETE-ERROR] user={request.user.id}: {exc}")

        token, _ = Token.objects.get_or_create(user=request.user)
        auth_payload = _auth_payload(request.user, token)
        auth_payload["detail"] = "avatar_deleted"
        return Response(auth_payload, status=status.HTTP_200_OK)


class AccountDeletionRequestAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        channel = (request.data.get("channel") or "email").strip().lower()
        user = request.user

        if channel not in {"email", "sms"}:
            return Response({"error": "invalid_channel"}, status=status.HTTP_400_BAD_REQUEST)

        if channel == "email":
            if not (user.email or "").strip():
                return Response({"error": "email_not_linked"}, status=status.HTTP_400_BAD_REQUEST)
            if _email_code_too_frequent(user, "delete_account"):
                return Response({"error": "too_many_requests"}, status=status.HTTP_429_TOO_MANY_REQUESTS)

            rec = _create_email_code(user, "delete_account", target_email=user.email)
            _send_email_code(user.email, rec.code, "delete_account")
            return Response({"detail": "code_sent", "channel": "email"})

        profile = UserProfile.objects.filter(user=user).first()
        if not profile or not profile.phone_verified or not profile.phone_e164:
            return Response({"error": "phone_not_verified"}, status=status.HTTP_400_BAD_REQUEST)
        if not _phone_country_allowed(profile.phone_e164):
            return Response(
                _unsupported_phone_country_payload(),
                status=status.HTTP_400_BAD_REQUEST,
            )

        if _phone_code_too_frequent(profile.phone_e164, "delete_account"):
            return Response({"error": "too_many_requests"}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        _create_phone_code(profile.phone_e164, "delete_account", user=user)
        ok, msg, http_code = _twilio_verify_start(profile.phone_e164, channel="sms")
        if not ok:
            payload = {"error": "sms_delivery_failed"}
            if settings.DEBUG and msg:
                payload["detail"] = _debug_error_details(msg)
            return Response(payload, status=http_code)

        return Response({"detail": "code_sent", "channel": "sms"})


class AccountDeletionConfirmAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        password = request.data.get("password") or ""
        code = (request.data.get("code") or "").strip()
        channel = (request.data.get("channel") or "").strip().lower()
        note = (request.data.get("note") or "").strip()

        confirmed_via = ""

        if password:
            if not user.has_usable_password():
                return Response({"error": "password_not_set"}, status=status.HTTP_400_BAD_REQUEST)
            if not user.check_password(password):
                return Response({"error": "invalid_password"}, status=status.HTTP_400_BAD_REQUEST)
            confirmed_via = "password"

        elif code and channel == "email":
            rec = EmailVerification.objects.filter(
                user=user,
                purpose="delete_account",
                code=code,
                is_used=False,
            ).order_by("-created_at").first()

            if not rec or not rec.is_valid():
                return Response({"error": "invalid_or_expired_code"}, status=status.HTTP_400_BAD_REQUEST)

            rec.is_used = True
            rec.save(update_fields=["is_used"])
            confirmed_via = "email_code"

        elif code and channel == "sms":
            profile = UserProfile.objects.filter(user=user).first()
            if not profile or not profile.phone_verified or not profile.phone_e164:
                return Response({"error": "phone_not_verified"}, status=status.HTTP_400_BAD_REQUEST)

            if _twilio_verify_service_sid():
                approved, msg, http_code = _twilio_verify_check(profile.phone_e164, code)
                if not approved:
                    if msg == "denied":
                        return Response({"error": "invalid_or_expired_code"}, status=status.HTTP_400_BAD_REQUEST)
                    payload = {"error": "verification_check_failed"}
                    if settings.DEBUG and msg:
                        payload["detail"] = _debug_error_details(msg)
                    return Response(payload, status=http_code)

                local_rec = PhoneVerification.objects.filter(
                    phone_e164=profile.phone_e164,
                    purpose="delete_account",
                    is_used=False,
                ).order_by("-created_at").first()
                if local_rec:
                    local_rec.is_used = True
                    local_rec.save(update_fields=["is_used"])
            else:
                local_rec = PhoneVerification.objects.filter(
                    phone_e164=profile.phone_e164,
                    purpose="delete_account",
                    code=code,
                    is_used=False,
                ).order_by("-created_at").first()
                if not local_rec or not local_rec.is_valid():
                    return Response({"error": "invalid_or_expired_code"}, status=status.HTTP_400_BAD_REQUEST)
                local_rec.is_used = True
                local_rec.save(update_fields=["is_used"])

            confirmed_via = "sms_code"

        else:
            return Response(
                {"error": "password_or_code_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        req = _schedule_account_deletion(user, confirmed_via=confirmed_via, note=note)
        return Response(
            {
                "detail": "account_deletion_scheduled",
                "status": req.status,
                "execute_after": req.execute_after.isoformat(),
            }
        )


class LinkEmailRequestAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password") or ""
        user = request.user

        if not email or not password:
            return Response({"error": "email and password required"}, status=status.HTTP_400_BAD_REQUEST)
        if not _is_valid_email(email):
            return Response({"error": "invalid email"}, status=status.HTTP_400_BAD_REQUEST)
        if not _is_password_policy_valid(password):
            return Response({"error": "password_policy_violation"}, status=status.HTTP_400_BAD_REQUEST)
        if user.email:
            return Response({"error": "email_already_linked"}, status=status.HTTP_400_BAD_REQUEST)

        owner = User.objects.filter(username=email).exclude(id=user.id).first()
        if owner:
            return Response({"error": "email_already_used"}, status=status.HTTP_400_BAD_REQUEST)

        EmailVerification.objects.filter(user=user, purpose="link_email", is_used=False).update(is_used=True)
        code = _generate_code()
        EmailVerification.objects.create(
            user=user,
            code=code,
            purpose="link_email",
            target_email=email,
            pending_password=make_password(password),
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        _send_email_code(email, code, "link_email")
        return Response({"detail": "verification_sent"})


class LinkEmailConfirmAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        code = (request.data.get("code") or "").strip()
        user = request.user
        if not code:
            return Response({"error": "code required"}, status=status.HTTP_400_BAD_REQUEST)
        if user.email:
            return Response({"error": "email_already_linked"}, status=status.HTTP_400_BAD_REQUEST)

        rec = EmailVerification.objects.filter(
            user=user,
            purpose="link_email",
            code=code,
            is_used=False,
        ).order_by("-created_at").first()
        if not rec or not rec.is_valid():
            return Response({"error": "invalid or expired code"}, status=status.HTTP_400_BAD_REQUEST)
        if not rec.target_email or not rec.pending_password:
            return Response({"error": "invalid or expired code"}, status=status.HTTP_400_BAD_REQUEST)

        owner = User.objects.filter(username=rec.target_email).exclude(id=user.id).first()
        if owner:
            return Response({"error": "email_already_used"}, status=status.HTTP_400_BAD_REQUEST)

        user.username = rec.target_email
        user.email = rec.target_email
        user.password = rec.pending_password
        user.is_active = True
        user.save(update_fields=["username", "email", "password", "is_active"])

        rec.is_used = True
        rec.save(update_fields=["is_used"])

        token, _ = Token.objects.get_or_create(user=user)
        return Response(_auth_payload(user, token))

