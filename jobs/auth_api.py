import json
import os
import random
import re
import threading
import base64
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
from .models import AccountDeletionRequest, EmailVerification, PhoneVerification, UserProfile, Vacancy
from .text_filters import (
    censor_minimal,
    contains_digit_or_number_emoji,
    contains_link,
    line_constraints_error,
    normalize_newlines,
)

_PHONE_REQUEST_WINDOW = timedelta(minutes=10)
_PHONE_REQUEST_MAX_ATTEMPTS = 3
_ACCOUNT_DELETION_DELAY = timedelta(days=30)
_phone_request_attempts = {}
_phone_request_lock = threading.Lock()
_PASSWORD_MIN_LENGTH = 8
_PROFILE_DESCRIPTION_MAX_LENGTH = 160
_PROFILE_DESCRIPTION_MAX_LINES = 3
_PROFILE_DESCRIPTION_MAX_CHARS_PER_LINE = 27
_RESERVED_NICKNAME_PARTS = (
    "jobhub",
    "support",
    "moderator",
    "admin",
    "creator",
    "administrator",
)


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
    profile = UserProfile.objects.filter(user=user).first()
    avatar_key = (profile.avatar_key if profile else "") or ""
    return {
        "token": token.key,
        "is_staff": user.is_staff,
        "email": user.email or "",
        "nickname": (profile.nickname if profile else "") or "",
        "profile_description": (profile.description if profile else "") or "",
        "avatar_url": avatar_public_url(avatar_key),
        "phone": (profile.phone_e164 if profile else "") or "",
        "phone_verified": bool(profile and profile.phone_verified),
    }


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
    if not re.search(r"[A-Z]", value):
        return False
    if not re.search(r"[a-z]", value):
        return False
    return True


def _send_email_code(email, code, purpose="register"):
    if purpose == "reset":
        subject = "JobHub password reset code"
        message = f"Your password reset code: {code}\nIt is valid for 10 minutes."
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


def _username_for_phone(phone_e164):
    # Keep usernames deterministic and ASCII-safe for phone-only accounts.
    return f"phone_{phone_e164.replace('+', '')}"


def _phone_code_too_frequent(phone_e164, purpose):
    return PhoneVerification.objects.filter(
        phone_e164=phone_e164,
        purpose=purpose,
        created_at__gt=timezone.now() - timedelta(seconds=45),
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
        if "@" not in email:
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
            if purpose:
                return Response({"error": "invalid channel"}, status=status.HTTP_400_BAD_REQUEST)
            return Response({"status": "error", "message": "invalid_channel"}, status=status.HTTP_400_BAD_REQUEST)

        if not phone:
            if purpose:
                return Response({"error": "invalid phone"}, status=status.HTTP_400_BAD_REQUEST)
            return Response({"status": "error", "message": "invalid_phone"}, status=status.HTTP_400_BAD_REQUEST)

        # New simple mode (no purpose): pure Twilio Verify request.
        if not purpose:
            if not _consume_phone_request_slot(phone):
                return Response({"status": "error", "message": "too_many_requests"}, status=status.HTTP_429_TOO_MANY_REQUESTS)
            ok, msg, http_code = _twilio_verify_start(phone, channel=channel)
            if ok:
                return Response({"status": "sent"})
            payload = {"status": "error", "message": "verification_not_sent"}
            if settings.DEBUG and msg:
                payload["detail"] = _debug_error_details(msg)
            return Response(payload, status=http_code)

        if purpose not in {"verify_phone", "login", "reset"}:
            return Response({"error": "invalid purpose"}, status=status.HTTP_400_BAD_REQUEST)
        if _phone_code_too_frequent(phone, purpose):
            return Response({"error": "too_many_requests"}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        user = None
        if purpose == "verify_phone":
            if not request.user.is_authenticated:
                return Response({"error": "auth_required"}, status=status.HTTP_401_UNAUTHORIZED)
            owner = UserProfile.objects.filter(phone_e164=phone, phone_verified=True).exclude(user=request.user).first()
            if owner:
                return Response({"error": "phone_already_used"}, status=status.HTTP_400_BAD_REQUEST)
            user = request.user
        elif purpose == "login":
            prof = UserProfile.objects.filter(phone_e164=phone, phone_verified=True).select_related("user").first()
            if prof:
                user = prof.user
        else:
            prof = UserProfile.objects.filter(phone_e164=phone, phone_verified=True).select_related("user").first()
            if not prof:
                return Response({"error": "user not found"}, status=status.HTTP_400_BAD_REQUEST)
            user = prof.user

        # Keep local record for throttling/audit even when Twilio Verify is used.
        _create_phone_code(phone, purpose, user=user)

        ok, msg, http_code = _twilio_verify_start(phone, channel=channel)
        if not ok:
            payload = {"error": f"{channel}_delivery_failed"}
            if settings.DEBUG and msg:
                payload["detail"] = _debug_error_details(msg)
            return Response(payload, status=http_code)
        return Response({"detail": "code_sent", "channel": channel})


class PhoneVerifyCodeAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        phone = _normalize_phone(request.data.get("phone"))
        code = (request.data.get("code") or "").strip()
        purpose = (request.data.get("purpose") or "").strip()

        if not phone or not code:
            if purpose:
                return Response({"error": "phone and code required"}, status=status.HTTP_400_BAD_REQUEST)
            return Response({"status": "error", "message": "phone_and_code_required"}, status=status.HTTP_400_BAD_REQUEST)

        # New simple mode (no purpose): pure Twilio Verify check.
        if not purpose:
            ok, msg, http_code = _twilio_verify_check(phone, code)
            if ok:
                return Response({"status": "approved"})
            if msg == "denied":
                return Response({"status": "denied"}, status=status.HTTP_400_BAD_REQUEST)
            payload = {"status": "error", "message": "verification_check_failed"}
            if settings.DEBUG and msg:
                payload["detail"] = _debug_error_details(msg)
            return Response(payload, status=http_code)

        if purpose not in {"verify_phone", "login", "reset"}:
            return Response({"error": "invalid purpose"}, status=status.HTTP_400_BAD_REQUEST)

        rec = PhoneVerification.objects.filter(
            phone_e164=phone,
            purpose=purpose,
            is_used=False,
        ).order_by("-created_at").first()

        if _twilio_verify_service_sid():
            approved, _, _ = _twilio_verify_check(phone, code)
            if not approved:
                return Response({"error": "invalid or expired code"}, status=status.HTTP_400_BAD_REQUEST)
            if rec:
                rec.is_used = True
                rec.save(update_fields=["is_used"])
        else:
            if not rec or not rec.is_valid():
                return Response({"error": "invalid or expired code"}, status=status.HTTP_400_BAD_REQUEST)
            if rec.code != code:
                rec.attempts += 1
                rec.save(update_fields=["attempts"])
                return Response({"error": "invalid or expired code"}, status=status.HTTP_400_BAD_REQUEST)
            rec.is_used = True
            rec.save(update_fields=["is_used"])

        if purpose == "verify_phone":
            if not request.user.is_authenticated:
                return Response({"error": "auth_required"}, status=status.HTTP_401_UNAUTHORIZED)
            profile, _ = UserProfile.objects.get_or_create(user=request.user)
            try:
                profile.phone_e164 = phone
                profile.phone_verified = True
                profile.phone_verified_at = timezone.now()
                profile.save(update_fields=["phone_e164", "phone_verified", "phone_verified_at"])
            except IntegrityError:
                return Response({"error": "phone_already_used"}, status=status.HTTP_400_BAD_REQUEST)
            return Response({"detail": "phone_verified", "phone": phone, "phone_verified": True})

        if purpose == "reset":
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
        return Response(_auth_payload(user, token))


class ResetPasswordRequestAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        phone = _normalize_phone(request.data.get("phone"))

        if not email and not phone:
            return Response({"error": "email or phone required"}, status=status.HTTP_400_BAD_REQUEST)

        if phone:
            if _phone_code_too_frequent(phone, "reset"):
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
                return Response({"error": "invalid or expired code"}, status=status.HTTP_400_BAD_REQUEST)
            rec.is_used = True
            rec.save(update_fields=["is_used"])
            user = prof.user
            user.set_password(new_password)
            user.save(update_fields=["password"])
            return Response({"detail": "password_reset"})

        if not email:
            return Response({"error": "email or phone required"}, status=status.HTTP_400_BAD_REQUEST)

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


class MeAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        token, _ = Token.objects.get_or_create(user=request.user)
        return Response(_auth_payload(request.user, token))

    def patch(self, request):
        has_nickname = "nickname" in request.data
        nickname = (
            (request.data.get("nickname") or "").strip()
            if has_nickname
            else None
        )
        has_profile_description = "profile_description" in request.data
        profile_description = (
            normalize_newlines((request.data.get("profile_description") or "").strip())
            if has_profile_description
            else None
        )
        if nickname is not None:
            nickname = censor_minimal(nickname)
        if profile_description is not None:
            profile_description = censor_minimal(profile_description).strip()

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
        reserved_matches = _nickname_reserved_matches(nickname or "")
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
        if nickname is not None and profile.nickname != nickname:
            profile.nickname = nickname
            updated_fields.append("nickname")
        if (
            profile_description is not None
            and profile.description != profile_description
        ):
            profile.description = profile_description
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
        if "@" not in email:
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
