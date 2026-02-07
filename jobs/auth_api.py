import base64
import os
import random
import re
from datetime import timedelta
from urllib import parse, request as urllib_request

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

from .models import EmailVerification, PhoneVerification, UserProfile


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
    return {
        "token": token.key,
        "is_staff": user.is_staff,
        "email": user.email or "",
        "phone": (profile.phone_e164 if profile else "") or "",
        "phone_verified": bool(profile and profile.phone_verified),
    }


def _send_email_code(email, code, purpose="register"):
    if purpose == "reset":
        subject = "JobApp password reset code"
        message = f"Your password reset code: {code}\nIt is valid for 10 minutes."
    elif purpose == "link_email":
        subject = "JobApp email linking code"
        message = f"Your email linking code: {code}\nIt is valid for 10 minutes."
    else:
        subject = "JobApp verification code"
        message = f"Your verification code: {code}\nIt is valid for 10 minutes."
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)


def _send_whatsapp_code(phone_e164, code, purpose):
    if purpose == "reset":
        text = f"JobApp: password reset code {code}. Valid 10 minutes."
    elif purpose == "login":
        text = f"JobApp: login code {code}. Valid 10 minutes."
    else:
        text = f"JobApp: phone verification code {code}. Valid 10 minutes."

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


class RegisterAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password") or ""

        if not email or not password:
            return Response({"error": "email and password required"}, status=status.HTTP_400_BAD_REQUEST)
        if "@" not in email:
            return Response({"error": "invalid email"}, status=status.HTTP_400_BAD_REQUEST)

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
        purpose = (request.data.get("purpose") or "login").strip()

        if not phone:
            return Response({"error": "invalid phone"}, status=status.HTTP_400_BAD_REQUEST)
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

        rec = _create_phone_code(phone, purpose, user=user)
        sent = _send_whatsapp_code(phone, rec.code, purpose)
        if not sent:
            if settings.DEBUG:
                return Response({"detail": "code_sent", "channel": "debug", "debug_code": rec.code})
            return Response({"error": "whatsapp_delivery_failed"}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({"detail": "code_sent", "channel": "whatsapp"})


class PhoneVerifyCodeAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        phone = _normalize_phone(request.data.get("phone"))
        code = (request.data.get("code") or "").strip()
        purpose = (request.data.get("purpose") or "login").strip()

        if not phone or not code:
            return Response({"error": "phone and code required"}, status=status.HTTP_400_BAD_REQUEST)
        if purpose not in {"verify_phone", "login", "reset"}:
            return Response({"error": "invalid purpose"}, status=status.HTTP_400_BAD_REQUEST)

        rec = PhoneVerification.objects.filter(
            phone_e164=phone,
            purpose=purpose,
            is_used=False,
        ).order_by("-created_at").first()
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
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password") or ""

        user = User.objects.filter(username=email).first()
        if user and not user.is_active:
            return Response({"error": "email_not_verified"}, status=status.HTTP_400_BAD_REQUEST)

        user = authenticate(username=email, password=password)
        if not user:
            return Response({"error": "invalid credentials"}, status=status.HTTP_400_BAD_REQUEST)

        token, _ = Token.objects.get_or_create(user=user)
        return Response(_auth_payload(user, token))


class MeAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        token, _ = Token.objects.get_or_create(user=request.user)
        return Response(_auth_payload(request.user, token))


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
        if len(password) < 6:
            return Response({"error": "password_too_short"}, status=status.HTTP_400_BAD_REQUEST)
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
