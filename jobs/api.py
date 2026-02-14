from django.utils import timezone
from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Case, Count, IntegerField, Max, Q, Value, When
import secrets
from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError

from .models import Complaint, ComplaintActionLog, Vacancy, UserProfile
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
            is_editing=False,
            # Reuse this field as "submitted_at" for moderation visibility delay.
            editing_started_at=timezone.now(),
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
        visible_after = timezone.now() - timedelta(seconds=60)
        return Vacancy.objects.filter(
            is_approved=False,
            is_rejected=False,
            is_editing=False,
        ).filter(
            Q(editing_started_at__isnull=True) | Q(editing_started_at__lte=visible_after)
        ).filter(
            Q(rejection_reason="") | Q(rejection_reason__isnull=True)
        ).order_by("-published_at")


class VacancyApproveAPIView(APIView):
    permission_classes = [IsModerator]

    def post(self, request, pk):
        vacancy = Vacancy.objects.get(pk=pk)
        if vacancy.is_editing:
            return Response({"error": "vacancy_editing"}, status=409)
        vacancy.is_approved = True
        vacancy.is_rejected = False
        vacancy.rejection_reason = ""
        vacancy.is_editing = False
        vacancy.editing_started_at = None
        vacancy.save(
            update_fields=[
                "is_approved",
                "is_rejected",
                "rejection_reason",
                "is_editing",
                "editing_started_at",
            ]
        )
        return Response({"detail": "approved"}, status=200)


class VacancyRejectAPIView(APIView):
    permission_classes = [IsModerator]

    def post(self, request, pk):
        vacancy = Vacancy.objects.get(pk=pk)
        if vacancy.is_editing:
            return Response({"error": "vacancy_editing"}, status=409)
        reason = request.data.get("reason", "").strip()
        vacancy.is_approved = False
        vacancy.is_rejected = True
        vacancy.rejection_reason = reason
        vacancy.is_editing = False
        vacancy.editing_started_at = None
        vacancy.save(
            update_fields=[
                "is_approved",
                "is_rejected",
                "rejection_reason",
                "is_editing",
                "editing_started_at",
            ]
        )
        return Response({"detail": "rejected"}, status=200)


class VacancyResubmitAPIView(APIView):
    permission_classes = [IsModerator]

    def post(self, request, pk):
        vacancy = Vacancy.objects.get(pk=pk)
        vacancy.is_approved = False
        vacancy.is_rejected = False
        vacancy.rejection_reason = ""
        vacancy.is_editing = False
        vacancy.editing_started_at = None
        vacancy.save(
            update_fields=[
                "is_approved",
                "is_rejected",
                "rejection_reason",
                "is_editing",
                "editing_started_at",
            ]
        )
        return Response({"detail": "resubmitted"}, status=200)


class VacancyMineAPIView(generics.ListAPIView):
    serializer_class = VacancyMineSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return (
            Vacancy.objects.filter(created_by=self.request.user)
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

        data = request.data.copy()
        submit_raw = str(data.pop("submit", "")).lower()
        submit_for_moderation = submit_raw in ("1", "true", "yes", "on")
        serializer = VacancyCreateSerializer(vacancy, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        if submit_for_moderation:
            serializer.save(
                is_approved=False,
                is_rejected=False,
                rejection_reason="",
                is_editing=False,
                # Marks resubmission time; pending list applies 60s delay.
                editing_started_at=timezone.now(),
            )
        else:
            serializer.save(
                is_approved=False,
                is_rejected=False,
                rejection_reason="",
                is_editing=True,
                editing_started_at=timezone.now(),
            )
        return Response(serializer.data, status=200)


class ComplaintAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        vacancy_id = request.data.get("vacancy_id")
        reason = (request.data.get("reason") or "").strip()
        message = (request.data.get("message") or "").strip()

        if not vacancy_id or not reason:
            return Response({"error": "vacancy_id and reason required"}, status=status.HTTP_400_BAD_REQUEST)

        vacancy = Vacancy.objects.filter(id=vacancy_id).first()
        if not vacancy:
            return Response({"error": "vacancy_not_found"}, status=status.HTTP_404_NOT_FOUND)

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
            message=message,
        )

        subject = f"JobApp complaint: {reason}"
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
        base = Complaint.objects.select_related("vacancy")

        status_filter = (request.query_params.get("status") or "").strip()
        reason_filter = (request.query_params.get("reason") or "").strip()

        if status_filter:
            base = base.filter(status=status_filter)
        if reason_filter:
            base = base.filter(reason=reason_filter)

        grouped = (
            base.values("vacancy_id", "vacancy__title")
            .annotate(
                complaints_count=Count("id"),
                open_count=Count("id", filter=Q(status__in=["new", "in_review"])),
                latest_complaint_at=Max("created_at"),
            )
            .order_by("-complaints_count", "-latest_complaint_at")
        )

        results = [
            {
                "vacancy_id": row["vacancy_id"],
                "vacancy_title": row["vacancy__title"],
                "complaints_count": row["complaints_count"],
                "open_count": row["open_count"],
                "latest_complaint_at": row["latest_complaint_at"],
            }
            for row in grouped
        ]
        return Response({"count": len(results), "results": results}, status=200)


class ComplaintModerationActionAPIView(APIView):
    permission_classes = [IsModerator]

    @staticmethod
    def _snapshot(vacancy):
        return {
            "is_approved": bool(vacancy.is_approved),
            "is_rejected": bool(vacancy.is_rejected),
            "is_editing": bool(vacancy.is_editing),
            "rejection_reason": vacancy.rejection_reason or "",
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
        note = (request.data.get("note") or "").strip()
        reject_reason = (request.data.get("rejection_reason") or "").strip()
        resolve_all = self._to_bool(request.data.get("resolve_all"), default=True)

        allowed_actions = {code for code, _ in ComplaintActionLog.ACTION_CHOICES}
        if action not in allowed_actions:
            return Response(
                {"error": "invalid_action", "allowed_actions": sorted(allowed_actions)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        vacancy = complaint.vacancy
        before_state = self._snapshot(vacancy)

        if action == "hide":
            vacancy.is_approved = False
            vacancy.is_rejected = False
            vacancy.is_editing = False
            vacancy.rejection_reason = ""
            vacancy.editing_started_at = None
        elif action == "reject":
            vacancy.is_approved = False
            vacancy.is_rejected = True
            vacancy.is_editing = False
            vacancy.rejection_reason = reject_reason or note or complaint.reason
            vacancy.editing_started_at = None
        elif action == "restore":
            vacancy.is_approved = True
            vacancy.is_rejected = False
            vacancy.is_editing = False
            vacancy.rejection_reason = ""
            vacancy.editing_started_at = None

        vacancy.save(
            update_fields=[
                "is_approved",
                "is_rejected",
                "is_editing",
                "rejection_reason",
                "editing_started_at",
            ]
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

        return Response(
            {
                "detail": "action_applied",
                "action": action,
                "complaint_id": complaint.id,
                "vacancy_id": vacancy.id,
                "resolved_complaints": resolved_count,
            },
            status=200,
        )


