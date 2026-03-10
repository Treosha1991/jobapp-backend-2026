from django.utils import timezone
from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db.models import Case, Count, F, IntegerField, Max, Q, Value, When
from django.db.models.functions import Coalesce
from django.utils.dateparse import parse_date, parse_datetime
import secrets
from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from .alerts import dispatch_vacancy_alerts, preview_vacancy_alerts
from .avatar_utils import avatar_public_url
from .models import (
    Complaint,
    ComplaintActionLog,
    PushDevice,
    UserBlock,
    Vacancy,
    VacancyAlertSubscription,
    UserProfile,
    UnlockedContact,
)
from .serializers import (
    ComplaintListSerializer,
    PushDeviceRegisterSerializer,
    VacancyAlertSubscriptionSerializer,
    VacancyContactSerializer,
    VacancyListSerializer,
    VacancyModerationSerializer,
    VacancyDetailSerializer,
    VacancyCreateSerializer,
    VacancyMineSerializer,
)
from .text_filters import censor_minimal, contains_link
from datetime import timedelta


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
        qs = Vacancy.objects.filter(
            is_approved=True,
            is_paused_by_owner=False,
            is_deleted_by_moderator=False,
            expires_at__gt=timezone.now()
        ).order_by("-published_at")

        country = self.request.query_params.get("country")
        city = self.request.query_params.get("city")
        category = self.request.query_params.get("category")
        employment_type = self.request.query_params.get("employment_type")
        source = self.request.query_params.get("source")
        housing_type = self.request.query_params.get("housing_type")
        search = self.request.query_params.get("search")
        search_alt = self.request.query_params.get("search_alt")

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
            qs = qs.exclude(
                Q(
                    complaints__reporter=self.request.user,
                    complaints__vacancy_revision_snapshot=F("revision"),
                )
                & ~Q(created_by=self.request.user)
            ).distinct()

        return qs


class VacancyDetailAPIView(APIView):
    def get(self, request, pk):
        vacancy = Vacancy.objects.filter(pk=pk).first()
        if not vacancy:
            return Response({"error": "vacancy_not_found"}, status=status.HTTP_404_NOT_FOUND)
        if vacancy.is_deleted_by_moderator:
            return Response({"error": "vacancy_deleted"}, status=status.HTTP_410_GONE)
        if vacancy.is_paused_by_owner:
            return Response({"error": "vacancy_not_found"}, status=status.HTTP_404_NOT_FOUND)
        if not vacancy.is_approved or vacancy.expires_at <= timezone.now():
            return Response({"error": "vacancy_not_found"}, status=status.HTTP_404_NOT_FOUND)
        serializer = VacancyDetailSerializer(vacancy)
        return Response(serializer.data, status=200)


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

    def perform_create(self, serializer):
        profile = UserProfile.objects.filter(user=self.request.user).first()
        if not profile or not profile.phone_verified:
            raise ValidationError({"error": "phone_verification_required"})
        is_moderator = _is_moderator(self.request)
        token = secrets.token_hex(32)
        vacancy = serializer.save(
            created_by=self.request.user,
            is_approved=is_moderator,
            is_rejected=False,
            is_paused_by_owner=False,
            paused_by_owner_at=None,
            rejection_reason="",
            is_editing=False,
            revision=1,
            # Reuse this field as "submitted_at" for moderation visibility delay.
            editing_started_at=timezone.now(),
            creator_token=token,
            expires_at=timezone.now() + timedelta(days=30),
        )
        if vacancy.is_approved and not vacancy.is_deleted_by_moderator:
            try:
                summary = dispatch_vacancy_alerts(vacancy)
                print(f"[VACANCY-ALERTS] vacancy={vacancy.id} summary={summary}")
            except Exception as exc:
                print(f"[VACANCY-ALERTS-ERROR] vacancy={vacancy.id}: {exc}")

def _is_moderator(request):
    return request.user.is_authenticated and request.user.is_staff


def _auto_pause_due_owner_vacancies(owner):
    """
    Auto-pause approved live vacancies after 30 days from:
    - creation/publish time; or
    - last pause time (if vacancy was paused before).
    """
    if not owner or not owner.is_authenticated:
        return 0

    now = timezone.now()
    cutoff = now - timedelta(days=30)
    due_qs = (
        Vacancy.objects.filter(
            created_by=owner,
            is_approved=True,
            is_paused_by_owner=False,
            is_deleted_by_moderator=False,
        )
        .annotate(live_anchor=Coalesce("paused_by_owner_at", "published_at"))
        .filter(live_anchor__lte=cutoff)
    )
    updated = due_qs.update(
        is_paused_by_owner=True,
        paused_by_owner_at=now,
    )
    return updated


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


def _vacancy_editable_snapshot(vacancy):
    return {
        "title": vacancy.title or "",
        "country": vacancy.country or "",
        "city": vacancy.city or "",
        "category": vacancy.category or "",
        "employment_type": vacancy.employment_type or "",
        "experience_required": vacancy.experience_required or "",
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
        "hide_primary_phone": bool(vacancy.hide_primary_phone),
        "whatsapp": vacancy.whatsapp or "",
        "viber": vacancy.viber or "",
        "telegram": vacancy.telegram or "",
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


class VacancyContactAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        vacancy = Vacancy.objects.get(pk=pk)
        if vacancy.is_deleted_by_moderator:
            return Response({"error": "vacancy_deleted"}, status=status.HTTP_410_GONE)
        if vacancy.is_paused_by_owner:
            return Response({"error": "vacancy_not_found"}, status=status.HTTP_404_NOT_FOUND)

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

        source = (request.query_params.get("source") or "").strip().lower()
        viewer_blocked_owner = UserBlock.objects.filter(
            blocker=request.user,
            blocked_user=owner,
        ).exists()
        blocked_by_owner = UserBlock.objects.filter(
            blocker=owner,
            blocked_user=request.user,
        ).exists()
        if blocked_by_owner:
            return Response({"error": "employer_not_found"}, status=status.HTTP_404_NOT_FOUND)

        qs = Vacancy.objects.filter(
            created_by=owner,
            is_approved=True,
            is_paused_by_owner=False,
            is_deleted_by_moderator=False,
            expires_at__gt=timezone.now(),
        ).order_by("-published_at")

        if not (source == "blacklist" and viewer_blocked_owner):
            qs = qs.exclude(
                Q(
                    complaints__reporter=request.user,
                    complaints__vacancy_revision_snapshot=F("revision"),
                )
                & ~Q(created_by=request.user)
            ).distinct()

        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = VacancyListSerializer(page, many=True)
        paginated = paginator.get_paginated_response(serializer.data).data

        return Response(
            {
                "employer": {
                    "id": owner.id,
                    "nickname": _owner_nickname_or_fallback(owner),
                    "profile_description": (
                        (getattr(getattr(owner, "profile", None), "description", "") or "").strip()
                    ),
                    "email_masked": _masked_email(owner.email),
                    "avatar_url": _owner_avatar_url(owner),
                },
                "count": paginated.get("count", 0),
                "next": paginated.get("next"),
                "previous": paginated.get("previous"),
                "results": paginated.get("results", []),
            },
            status=status.HTTP_200_OK,
        )


from .models import UnlockRequest
from rest_framework import status


class VacancyUnlockRequestAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        vacancy = Vacancy.objects.get(pk=pk)
        if vacancy.is_deleted_by_moderator:
            return Response({"error": "vacancy_deleted"}, status=status.HTTP_410_GONE)
        if vacancy.is_paused_by_owner:
            return Response({"error": "vacancy_not_found"}, status=status.HTTP_404_NOT_FOUND)

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
        if vacancy.is_deleted_by_moderator:
            return Response({"error": "vacancy_deleted"}, status=status.HTTP_410_GONE)
        if vacancy.is_paused_by_owner:
            return Response({"error": "vacancy_not_found"}, status=status.HTTP_404_NOT_FOUND)
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
        visible_after = timezone.now() - timedelta(seconds=60)
        return Vacancy.objects.filter(
            is_approved=False,
            is_rejected=False,
            is_editing=False,
            is_deleted_by_moderator=False,
        ).filter(
            Q(editing_started_at__isnull=True) | Q(editing_started_at__lte=visible_after)
        ).filter(
            Q(rejection_reason="") | Q(rejection_reason__isnull=True)
        ).order_by("-published_at")


class ModerationVacancyDetailAPIView(APIView):
    permission_classes = [IsModerator]

    def get(self, request, pk):
        vacancy = Vacancy.objects.filter(pk=pk).first()
        if not vacancy:
            return Response({"error": "vacancy_not_found"}, status=status.HTTP_404_NOT_FOUND)
        serializer = VacancyModerationSerializer(vacancy)
        return Response(serializer.data, status=200)


class VacancyApproveAPIView(APIView):
    permission_classes = [IsModerator]

    def post(self, request, pk):
        vacancy = Vacancy.objects.get(pk=pk)
        if vacancy.is_deleted_by_moderator:
            return Response({"error": "vacancy_deleted"}, status=status.HTTP_410_GONE)
        if vacancy.is_editing:
            return Response({"error": "vacancy_editing"}, status=409)
        vacancy.is_approved = True
        vacancy.is_rejected = False
        vacancy.is_paused_by_owner = False
        vacancy.paused_by_owner_at = None
        vacancy.rejection_reason = ""
        vacancy.last_moderator_rejection_reason = ""
        vacancy.moderation_baseline = {}
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
        vacancy.is_paused_by_owner = target_paused
        # Keep last pause timestamp even after resume:
        # auto-pause timer uses "created_at or last pause".
        if target_paused:
            vacancy.paused_by_owner_at = timezone.now()
        vacancy.save(update_fields=["is_paused_by_owner", "paused_by_owner_at"])

        return Response(
            {
                "detail": "updated",
                "is_paused_by_owner": bool(vacancy.is_paused_by_owner),
                "paused_by_owner_at": (
                    vacancy.paused_by_owner_at.isoformat()
                    if vacancy.paused_by_owner_at
                    else ""
                ),
            },
            status=200,
        )


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
        serializer = VacancyCreateSerializer(vacancy, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        next_revision = (vacancy.revision or 1) + 1
        if submit_for_moderation:
            serializer.save(
                is_approved=False,
                is_rejected=False,
                rejection_reason="",
                is_editing=False,
                is_paused_by_owner=False,
                paused_by_owner_at=None,
                revision=next_revision,
                # Marks resubmission time; pending list applies 60s delay.
                editing_started_at=timezone.now(),
            )
        else:
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
        if not vacancy:
            return Response({"error": "vacancy_not_found"}, status=status.HTTP_404_NOT_FOUND)
        if vacancy.is_deleted_by_moderator:
            return Response({"error": "vacancy_deleted"}, status=status.HTTP_410_GONE)

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
        elif action == "restore":
            deleted_state = vacancy.moderator_deleted_state or {}
            if vacancy.is_deleted_by_moderator and isinstance(deleted_state, dict) and deleted_state:
                vacancy.is_approved = bool(deleted_state.get("is_approved", False))
                vacancy.is_rejected = bool(deleted_state.get("is_rejected", False))
                vacancy.is_paused_by_owner = bool(deleted_state.get("is_paused_by_owner", False))
                paused_raw = (deleted_state.get("paused_by_owner_at") or "").strip()
                paused_parsed = parse_datetime(paused_raw) if paused_raw else None
                vacancy.paused_by_owner_at = paused_parsed if vacancy.is_paused_by_owner else None
                vacancy.is_editing = bool(deleted_state.get("is_editing", False))
                vacancy.rejection_reason = (deleted_state.get("rejection_reason") or "").strip()
                vacancy.last_moderator_rejection_reason = (
                    deleted_state.get("last_moderator_rejection_reason") or ""
                ).strip()
                baseline = deleted_state.get("moderation_baseline")
                vacancy.moderation_baseline = baseline if isinstance(baseline, dict) else {}
            else:
                vacancy.is_approved = True
                vacancy.is_rejected = False
                vacancy.is_paused_by_owner = False
                vacancy.paused_by_owner_at = None
                vacancy.is_editing = False
                vacancy.rejection_reason = ""
                vacancy.last_moderator_rejection_reason = ""
                vacancy.moderation_baseline = {}
            vacancy.editing_started_at = None
            vacancy.is_deleted_by_moderator = False
            vacancy.moderator_deleted_state = {}
            vacancy.deleted_by_moderator_at = None

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
                "is_deleted_by_moderator",
                "moderator_deleted_state",
                "deleted_by_moderator_at",
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


