from django.utils import timezone
from django.conf import settings
from django.core.mail import send_mail
import secrets
from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError

from .models import Vacancy, UserProfile
from .serializers import (
    VacancyListSerializer,
    VacancyDetailSerializer,
    VacancyCreateSerializer,
    VacancyMineSerializer,
)
from datetime import timedelta

class VacancyListAPIView(generics.ListAPIView):
    serializer_class = VacancyListSerializer

    def get_queryset(self):
        qs = Vacancy.objects.filter(
            is_approved=True,
            expires_at__gt=timezone.now()
        ).order_by("-published_at")

        country = self.request.query_params.get("country")
        city = self.request.query_params.get("city")
        category = self.request.query_params.get("category")
        employment_type = self.request.query_params.get("employment_type")
        source = self.request.query_params.get("source")
        housing_type = self.request.query_params.get("housing_type")
        search = self.request.query_params.get("search")

        if country:
            qs = qs.filter(country=country)
        if city:
            qs = qs.filter(city__icontains=city)
        if category:
            qs = qs.filter(category=category)
        if employment_type:
            qs = qs.filter(employment_type=employment_type)
        if source:
            qs = qs.filter(source=source)
        if housing_type:
            qs = qs.filter(housing_type=housing_type)
        if search:
            qs = qs.filter(title__icontains=search)

        return qs


class VacancyDetailAPIView(generics.RetrieveAPIView):
    serializer_class = VacancyDetailSerializer

    def get_queryset(self):
        return Vacancy.objects.filter(is_approved=True, expires_at__gt=timezone.now())


class VacancyCreateAPIView(generics.CreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = VacancyCreateSerializer

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        # include creator_token in response for client storage
        return response

    def perform_create(self, serializer):
        profile = UserProfile.objects.filter(user=self.request.user).first()
        if not profile or not profile.phone_verified:
            raise ValidationError({"error": "phone_verification_required"})
        is_moderator = _is_moderator(self.request)
        token = secrets.token_hex(32)
        serializer.save(
            created_by=self.request.user,
            is_approved=is_moderator,
            is_rejected=False,
            rejection_reason="",
            creator_token=token,
            expires_at=timezone.now() + timedelta(days=30),
        )

from rest_framework.response import Response
from rest_framework.views import APIView
from .models import UnlockedContact
from .serializers import VacancyContactSerializer

def _is_moderator(request):
    return request.user.is_authenticated and request.user.is_staff

class IsModerator(permissions.BasePermission):
    def has_permission(self, request, view):
        return _is_moderator(request)

class VacancyContactAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        vacancy = Vacancy.objects.get(pk=pk)

        unlocked = UnlockedContact.objects.filter(
            user=request.user,
            vacancy=vacancy
        ).exists()

        if not unlocked:
            return Response(
                {"detail": "contacts_locked"},
                status=403
            )

        serializer = VacancyContactSerializer(vacancy)
        return Response(serializer.data)

    def post(self, request, pk):
        vacancy = Vacancy.objects.get(pk=pk)

        UnlockedContact.objects.get_or_create(
            user=request.user,
            vacancy=vacancy
        )

        serializer = VacancyContactSerializer(vacancy)
        return Response(serializer.data)
    
from .models import UnlockRequest
from rest_framework import status


class VacancyUnlockRequestAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        vacancy = Vacancy.objects.get(pk=pk)

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
        vacancy = Vacancy.objects.get(pk=pk)
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
    serializer_class = VacancyListSerializer
    permission_classes = [IsModerator]

    def get_queryset(self):
        return Vacancy.objects.filter(is_approved=False).order_by("-published_at")


class VacancyApproveAPIView(APIView):
    permission_classes = [IsModerator]

    def post(self, request, pk):
        vacancy = Vacancy.objects.get(pk=pk)
        vacancy.is_approved = True
        vacancy.is_rejected = False
        vacancy.rejection_reason = ""
        vacancy.save(update_fields=["is_approved", "is_rejected", "rejection_reason"])
        return Response({"detail": "approved"}, status=200)


class VacancyRejectAPIView(APIView):
    permission_classes = [IsModerator]

    def post(self, request, pk):
        vacancy = Vacancy.objects.get(pk=pk)
        reason = request.data.get("reason", "").strip()
        vacancy.is_approved = False
        vacancy.is_rejected = True
        vacancy.rejection_reason = reason
        vacancy.save(update_fields=["is_approved", "is_rejected", "rejection_reason"])
        return Response({"detail": "rejected"}, status=200)


class VacancyResubmitAPIView(APIView):
    permission_classes = [IsModerator]

    def post(self, request, pk):
        vacancy = Vacancy.objects.get(pk=pk)
        vacancy.is_approved = False
        vacancy.is_rejected = False
        vacancy.rejection_reason = ""
        vacancy.save(update_fields=["is_approved", "is_rejected", "rejection_reason"])
        return Response({"detail": "resubmitted"}, status=200)


class VacancyMineAPIView(generics.ListAPIView):
    serializer_class = VacancyMineSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Vacancy.objects.filter(created_by=self.request.user).order_by("-published_at")


class VacancyEditAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        vacancy = Vacancy.objects.get(pk=pk)
        if vacancy.created_by_id != request.user.id:
            return Response({"error": "invalid token"}, status=status.HTTP_403_FORBIDDEN)

        data = request.data.copy()
        serializer = VacancyCreateSerializer(vacancy, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(is_approved=False, is_rejected=False, rejection_reason="")
        return Response(serializer.data, status=200)


class ComplaintAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        vacancy_id = request.data.get("vacancy_id")
        reason = (request.data.get("reason") or "").strip()
        message = (request.data.get("message") or "").strip()

        if not vacancy_id or not reason:
            return Response({"error": "vacancy_id and reason required"}, status=status.HTTP_400_BAD_REQUEST)

        vacancy = Vacancy.objects.filter(id=vacancy_id).first()
        title = vacancy.title if vacancy else f"id={vacancy_id}"

        reporter = ""
        if request.user.is_authenticated:
            reporter = request.user.email or request.user.username
        else:
            reporter = (request.data.get("email") or "").strip()

        subject = f"JobApp complaint: {reason}"
        body = "\n".join(
            [
                f"Vacancy: {title}",
                f"Vacancy ID: {vacancy_id}",
                f"Reporter: {reporter or 'anonymous'}",
                f"Reason: {reason}",
                "",
                "Message:",
                message or "-",
            ]
        )

        to_email = getattr(settings, "SUPPORT_EMAIL", settings.DEFAULT_FROM_EMAIL)
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [to_email], fail_silently=False)
        return Response({"detail": "sent"}, status=200)


