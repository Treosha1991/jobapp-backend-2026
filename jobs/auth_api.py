import random
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import EmailVerification


def _generate_code():
    return f"{random.randint(0, 999999):06d}"


def _send_code(email, code, purpose="register"):
    if purpose == "reset":
        subject = "JobApp password reset code"
        message = f"Your password reset code: {code}\nIt is valid for 10 minutes."
    else:
        subject = "JobApp verification code"
        message = f"Your verification code: {code}\nIt is valid for 10 minutes."
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)


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
        _send_code(email, code, "register")

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
        return Response({"token": token.key, "is_staff": user.is_staff, "email": user.email})


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
        _send_code(email, code, "register")
        return Response({"detail": "verification_sent"})


class ResetPasswordRequestAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        if not email:
            return Response({"error": "email required"}, status=status.HTTP_400_BAD_REQUEST)

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
        _send_code(email, code, "reset")
        return Response({"detail": "reset_code_sent"})


class ResetPasswordConfirmAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        code = (request.data.get("code") or "").strip()
        new_password = request.data.get("new_password") or ""

        if not email or not code or not new_password:
            return Response({"error": "email, code, new_password required"}, status=status.HTTP_400_BAD_REQUEST)

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
        return Response({"token": token.key, "is_staff": user.is_staff, "email": user.email})
