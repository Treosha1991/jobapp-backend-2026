import json
import secrets

from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.db import IntegrityError
from django.db import transaction
from django.db.models import F
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.safestring import mark_safe
from django.utils import timezone
from django.views.decorators.http import require_POST

from .api import (
    VACANCY_LIVE_WINDOW,
    _create_moderation_attempt,
    _is_moderator,
    _notify_moderators_about_pending_vacancy_safe,
    _submission_flow_for_vacancy,
)
from .chat_api import (
    CHAT_MESSAGE_RATE_LIMIT,
    CHAT_MESSAGE_RATE_WINDOW,
    _chat_users_are_blocked,
    _send_chat_push_safe,
    _unread_counts,
    _user_avatar_url,
    _user_display_name,
)
from .board_publishing import (
    AUTHORIZATION_TEXT,
    accept_authorization,
    revoke_authorization,
)
from .auth_api import (
    _create_phone_code,
    _normalize_phone,
    _phone_code_too_frequent,
    _phone_country_allowed,
    _phone_reset_request_blocked,
    _phone_reset_verify_blocked,
    _is_password_policy_valid,
    _record_phone_verification_attempt,
    _login_candidates,
    _twilio_verify_check,
    _twilio_verify_start,
)
from .economy import (
    EconomyActionRequiredError,
    InsufficientCreditsError,
    apply_vacancy_submission_action,
    build_vacancy_submission_state,
    ensure_free_contact_policy,
)
from .models import (
    ChatConversation,
    ChatMessage,
    ChatReport,
    EmployerBoardPublishingAuthorization,
    PhoneVerification,
    UserBlock,
    UserProfile,
    Vacancy,
)
from .serializers import chat_message_has_external_links
from .city_catalog import CITY_CATALOG
from .web_forms import EmployerVacancyForm
from .web_i18n import apply_language_cookie, get_lang, normalize_lang, tr


class EmployerLoginView(LoginView):
    template_name = "employer/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        return reverse_lazy("employer:vacancy_list")

    def post(self, request, *args, **kwargs):
        """Use the same email/nickname lookup as the mobile login endpoint."""
        identifier = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        authenticated_users = []
        seen_ids = set()

        for candidate in _login_candidates(identifier):
            user = authenticate(request, username=candidate.username, password=password)
            if user and user.id not in seen_ids:
                authenticated_users.append(user)
                seen_ids.add(user.id)

        if len(authenticated_users) == 1:
            auth_login(request, authenticated_users[0])
            return _login_redirect(request)

        messages.error(request, tr(request, "login_failed"))
        return render(request, self.template_name, {"login_identifier": identifier}, status=400)


class EmployerLogoutView(LogoutView):
    next_page = "employer:login"

    def dispatch(self, request, *args, **kwargs):
        self.lang = get_lang(request)
        return super().dispatch(request, *args, **kwargs)

    def get_next_page(self):
        return f"{reverse_lazy(self.next_page)}?lang={self.lang}"


def _login_redirect(request):
    next_url = request.POST.get("next") or request.GET.get("next") or str(reverse_lazy("employer:vacancy_list"))
    # Employer login only ever redirects within this site.
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = str(reverse_lazy("employer:vacancy_list"))
    return redirect(next_url)


def password_reset(request):
    """Recover a web password through a phone already verified in JobHub."""
    phone = request.session.get("employer_password_reset_phone", "")

    if request.method == "GET":
        return render(request, "employer/password_reset.html", {"reset_phone": phone})

    action = request.POST.get("action")
    if action == "request_code":
        phone = _normalize_phone(request.POST.get("phone"))
        if not phone:
            messages.error(request, tr(request, "password_reset_invalid_phone"))
            return redirect("employer:password_reset")
        if not _phone_country_allowed(phone):
            messages.error(request, tr(request, "password_reset_country"))
            return redirect("employer:password_reset")

        profile = UserProfile.objects.filter(phone_e164=phone, phone_verified=True).select_related("user").first()
        if not profile:
            messages.error(request, tr(request, "password_reset_not_found"))
            return redirect("employer:password_reset")
        if _phone_reset_request_blocked(phone):
            messages.error(request, tr(request, "password_reset_too_many"))
            return redirect("employer:password_reset")

        _create_phone_code(phone, "reset", user=profile.user)
        ok, message, http_status = _twilio_verify_start(phone, channel="sms")
        if not ok:
            _record_phone_verification_attempt(
                request, phone_e164=phone, purpose="reset", channel="sms",
                status_code="delivery_failed", message=message or "verification_not_sent",
                http_status=http_status, user=profile.user,
            )
            messages.error(request, tr(request, "password_reset_send_failed"))
            return redirect("employer:password_reset")

        _record_phone_verification_attempt(
            request, phone_e164=phone, purpose="reset", channel="sms",
            status_code="sent", http_status=200, user=profile.user,
        )
        request.session["employer_password_reset_phone"] = phone
        messages.success(request, tr(request, "password_reset_code_sent"))
        return redirect("employer:password_reset")

    if action == "confirm":
        code = (request.POST.get("code") or "").strip()
        password = request.POST.get("password") or ""
        confirm_password = request.POST.get("confirm_password") or ""
        profile = UserProfile.objects.filter(phone_e164=phone, phone_verified=True).select_related("user").first()
        if not phone or not code or not profile:
            messages.error(request, tr(request, "password_reset_code_invalid"))
            return redirect("employer:password_reset")
        if password != confirm_password:
            messages.error(request, tr(request, "password_reset_password_mismatch"))
            return redirect("employer:password_reset")
        if not _is_password_policy_valid(password):
            messages.error(request, tr(request, "password_reset_password_invalid"))
            return redirect("employer:password_reset")
        if _phone_reset_verify_blocked(phone):
            messages.error(request, tr(request, "password_reset_too_many"))
            return redirect("employer:password_reset")

        approved, message, http_status = _twilio_verify_check(phone, code)
        if not approved:
            PhoneVerification.objects.filter(phone_e164=phone, purpose="reset", is_used=False).update(
                attempts=F("attempts") + 1
            )
            _record_phone_verification_attempt(
                request, phone_e164=phone, purpose="reset", channel="sms",
                status_code="check_failed", message=message or "invalid_or_expired_code",
                http_status=http_status, user=profile.user,
            )
            messages.error(request, tr(request, "password_reset_code_invalid"))
            return redirect("employer:password_reset")

        PhoneVerification.objects.filter(phone_e164=phone, purpose="reset", is_used=False).update(is_used=True)
        profile.user.set_password(password)
        profile.user.save(update_fields=["password"])
        _record_phone_verification_attempt(
            request, phone_e164=phone, purpose="reset", channel="sms",
            status_code="approved", http_status=200, user=profile.user,
        )
        request.session.pop("employer_password_reset_phone", None)
        messages.success(request, tr(request, "password_reset_success"))
        return redirect("employer:login")

    return redirect("employer:password_reset")


def _employer_chat_queryset(user):
    return (
        ChatConversation.objects.filter(employer=user, last_message_at__isnull=False)
        .select_related(
            "candidate",
            "candidate__profile",
            "employer",
            "employer__profile",
            "initial_vacancy",
        )
        .order_by("-last_message_at", "-id")
    )


def _chat_block_state(conversation, viewer):
    other_user = conversation.other_user_for(viewer)
    return (
        UserBlock.objects.filter(blocker=viewer, blocked_user=other_user).exists(),
        UserBlock.objects.filter(blocker=other_user, blocked_user=viewer).exists(),
    )


@login_required(login_url="employer:login")
def chat_list(request):
    conversations = list(_employer_chat_queryset(request.user)[:50])
    unread_by_id = _unread_counts(conversations, request.user)
    latest_messages = {}
    for message in ChatMessage.objects.filter(conversation__in=conversations).order_by("conversation_id", "-id"):
        latest_messages.setdefault(message.conversation_id, message)

    rows = []
    for conversation in conversations:
        candidate = conversation.candidate
        latest = latest_messages.get(conversation.id)
        rows.append(
            {
                "conversation": conversation,
                "candidate_name": _user_display_name(candidate),
                "candidate_avatar_url": _user_avatar_url(candidate),
                "unread_count": unread_by_id.get(conversation.id, 0),
                "last_message": latest,
            }
        )
    return render(request, "employer/chat_list.html", {"rows": rows, "unread_count": sum(unread_by_id.values())})


@login_required(login_url="employer:login")
def chat_detail(request, conversation_id):
    conversation = get_object_or_404(
        ChatConversation.objects.select_related(
            "candidate", "candidate__profile", "employer", "employer__profile", "initial_vacancy"
        ),
        id=conversation_id,
        employer=request.user,
    )
    candidate = conversation.candidate
    blocked_by_me, blocked_by_other = _chat_block_state(conversation, request.user)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "block":
            UserBlock.objects.get_or_create(blocker=request.user, blocked_user=candidate)
            messages.success(request, tr(request, "chat_blocked_success"))
        elif action == "unblock":
            UserBlock.objects.filter(blocker=request.user, blocked_user=candidate).delete()
            messages.success(request, tr(request, "chat_unblocked_success"))
        elif action == "send":
            body = (request.POST.get("body") or "").strip()
            reply_to_id = request.POST.get("reply_to_id")
            if blocked_by_me or blocked_by_other or _chat_users_are_blocked(request.user, candidate):
                messages.error(request, tr(request, "chat_unavailable"))
            elif not body or len(body) > 1500 or "\x00" in body:
                messages.error(request, tr(request, "chat_message_invalid"))
            elif ChatMessage.objects.filter(
                sender=request.user,
                created_at__gte=timezone.now() - CHAT_MESSAGE_RATE_WINDOW,
            ).count() >= CHAT_MESSAGE_RATE_LIMIT:
                messages.error(request, tr(request, "chat_rate_limited"))
            else:
                reply_to = None
                if reply_to_id:
                    reply_to = ChatMessage.objects.filter(id=reply_to_id, conversation=conversation).first()
                    if reply_to is None:
                        messages.error(request, tr(request, "chat_reply_missing"))
                        return redirect("employer:chat_detail", conversation_id=conversation.id)
                with transaction.atomic():
                    message = ChatMessage.objects.create(
                        conversation=conversation,
                        sender=request.user,
                        body=body,
                        reply_to=reply_to,
                        has_external_links=chat_message_has_external_links(body),
                    )
                    conversation.last_message_at = message.created_at
                    conversation.employer_last_read_at = message.created_at
                    conversation.save(update_fields=["last_message_at", "employer_last_read_at", "updated_at"])
                    sender_name = _user_display_name(request.user)
                    transaction.on_commit(lambda: _send_chat_push_safe(message, candidate, sender_name))
        elif action in {"edit", "delete"}:
            try:
                message_id = int(request.POST.get("message_id"))
            except (TypeError, ValueError):
                message_id = None
            message = ChatMessage.objects.filter(id=message_id, conversation=conversation).first()
            recipient_read_at = conversation.candidate_last_read_at
            can_modify = bool(
                message
                and message.sender_id == request.user.id
                and not message.deleted_at
                and (not recipient_read_at or recipient_read_at < message.created_at)
            )
            if not can_modify:
                messages.error(request, tr(request, "chat_message_already_read"))
            elif action == "delete":
                message.body = ""
                message.has_external_links = False
                message.deleted_at = timezone.now()
                message.save(update_fields=["body", "has_external_links", "deleted_at"])
            else:
                body = (request.POST.get("body") or "").strip()
                if not body or len(body) > 1500 or "\x00" in body:
                    messages.error(request, tr(request, "chat_message_invalid"))
                else:
                    message.body = body
                    message.has_external_links = chat_message_has_external_links(body)
                    message.edited_at = timezone.now()
                    message.save(update_fields=["body", "has_external_links", "edited_at"])
        elif action == "report":
            reason = (request.POST.get("reason") or "").strip()
            allowed_reasons = {item[0] for item in ChatReport.REASON_CHOICES}
            if reason not in allowed_reasons:
                messages.error(request, tr(request, "chat_report_reason_required"))
            else:
                ChatReport.objects.create(
                    conversation=conversation,
                    reporter=request.user,
                    reported_user=candidate,
                    reason=reason,
                    message=(request.POST.get("message") or "").strip()[:1000],
                )
                messages.success(request, tr(request, "chat_report_sent"))
        return redirect("employer:chat_detail", conversation_id=conversation.id)

    chat_messages = list(
        ChatMessage.objects.filter(conversation=conversation)
        .select_related("sender", "reply_to", "reply_to__sender")
        .order_by("id")
    )
    if chat_messages:
        conversation.employer_last_read_at = chat_messages[-1].created_at
        conversation.save(update_fields=["employer_last_read_at", "updated_at"])
    return render(
        request,
        "employer/chat_detail.html",
        {
            "conversation": conversation,
            "candidate": candidate,
            "candidate_name": _user_display_name(candidate),
            "candidate_avatar_url": _user_avatar_url(candidate),
            "chat_messages": chat_messages,
            "blocked_by_me": blocked_by_me,
            "blocked_by_other": blocked_by_other,
            "report_reasons": ChatReport.REASON_CHOICES,
        },
    )


def set_language(request, lang):
    lang = normalize_lang(lang)
    next_url = request.GET.get("next") or request.POST.get("next") or reverse_lazy("employer:vacancy_list")
    response = redirect(next_url)
    return apply_language_cookie(response, lang)


def _user_has_verified_phone(user):
    return UserProfile.objects.filter(user=user, phone_verified=True).exists()


def _verified_phone_profile(user):
    return UserProfile.objects.filter(user=user, phone_verified=True).first()


def _phone_verification_redirect(request):
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or str(reverse_lazy("employer:vacancy_create"))
    next_url = next_url.split("#", 1)[0]
    return redirect(f"{next_url}#phone-verification")


def _save_form_to_vacancy(form, *, user, vacancy=None):
    vacancy = form.save(commit=False)
    vacancy.created_by = user
    vacancy.audience_country_codes = form.cleaned_data.get("audience_countries") or ""
    vacancy.driver_license_categories = form.cleaned_data.get("driver_license_categories") or ""
    if not vacancy.creator_token:
        vacancy.creator_token = secrets.token_hex(32)
    if not vacancy.expires_at:
        vacancy.expires_at = timezone.now() + VACANCY_LIVE_WINDOW
    if not vacancy.source:
        vacancy.source = "direct"
    return vacancy


def _vacancy_status(vacancy):
    if vacancy.is_deleted_by_moderator:
        return "status_deleted", "danger"
    if vacancy.is_editing:
        return "status_draft", "muted"
    if vacancy.is_approved and vacancy.is_paused_by_owner:
        return "status_paused", "warning"
    if vacancy.is_approved:
        return "status_published", "success"
    if vacancy.is_rejected:
        return "status_rejected", "danger"
    return "status_pending", "info"


def _handle_submission_error(request, exc, *, flow):
    if isinstance(exc, EconomyActionRequiredError):
        state = exc.state or build_vacancy_submission_state(request.user, flow=flow)
        if state.get("current_action") == "paid":
            price = state.get("effective_price_credits", 0)
            message = tr(request, "submission_paid_hint").format(price=price)
        elif state.get("current_action") == "ad":
            message = tr(request, "submission_ad_hint")
        else:
            message = state.get("message") or tr(request, "msg_store_action")
        messages.error(request, message)
        return
    if isinstance(exc, InsufficientCreditsError):
        messages.error(request, tr(request, "msg_insufficient_credits"))
        return
    raise exc


def _apply_web_submission_action(user, *, flow, related_vacancy, now):
    """Use credits/subscription on web; rewarded ads remain app-only."""
    state = build_vacancy_submission_state(user, flow=flow, now=now)
    method = state.get("expected_method")
    if method == "ad":
        raise EconomyActionRequiredError("submission_action_required", state)
    return apply_vacancy_submission_action(
        user,
        flow=flow,
        method=method,
        related_vacancy=related_vacancy,
        now=now,
    )


def _vacancy_form_context(request, form, *, mode, vacancy=None):
    city_catalog = {
        country: sorted(cities, key=str.casefold)
        for country, cities in CITY_CATALOG.items()
    }
    context = {
        "form": form,
        "mode": mode,
        "city_catalog_json": mark_safe(json.dumps(city_catalog, ensure_ascii=False)),
        # Keep the stored city separate from the rendered select value. The
        # browser rebuilds this select from the catalog, and legacy/imported
        # cities may not be in that catalog yet.
        "initial_city_json": mark_safe(
            json.dumps(
                (getattr(vacancy, "city", "") or form.initial.get("city", "")).strip(),
                ensure_ascii=False,
            )
        ),
        "phone_verified_profile": _verified_phone_profile(request.user),
        "phone_verify_pending": request.session.get("employer_phone_verify_phone", ""),
    }
    if vacancy is not None:
        context["vacancy"] = vacancy
    return context



@login_required(login_url="employer:login")
@require_POST
def phone_request_code(request):
    phone = _normalize_phone(request.POST.get("phone"))
    channel = "sms"

    if not phone:
        _record_phone_verification_attempt(
            request,
            phone_e164=phone,
            purpose="verify_phone",
            channel=channel,
            status_code="invalid_phone",
            message="invalid_phone",
            http_status=400,
        )
        messages.error(request, tr(request, "phone_verify_invalid"))
        return _phone_verification_redirect(request)

    if not _phone_country_allowed(phone):
        _record_phone_verification_attempt(
            request,
            phone_e164=phone,
            purpose="verify_phone",
            channel=channel,
            status_code="unsupported_country",
            message="unsupported_phone_country",
            http_status=400,
        )
        messages.error(request, tr(request, "phone_verify_country"))
        return _phone_verification_redirect(request)

    owner = UserProfile.objects.filter(phone_e164=phone, phone_verified=True).exclude(user=request.user).first()
    if owner:
        _record_phone_verification_attempt(
            request,
            phone_e164=phone,
            purpose="verify_phone",
            channel=channel,
            status_code="phone_already_used",
            message="phone_already_used",
            http_status=400,
        )
        messages.error(request, tr(request, "phone_verify_used"))
        return _phone_verification_redirect(request)

    if _phone_code_too_frequent(phone, "verify_phone"):
        _record_phone_verification_attempt(
            request,
            phone_e164=phone,
            purpose="verify_phone",
            channel=channel,
            status_code="too_many_requests",
            message="too_many_requests",
            http_status=429,
        )
        messages.error(request, tr(request, "phone_verify_too_many"))
        request.session["employer_phone_verify_phone"] = phone
        return _phone_verification_redirect(request)

    _create_phone_code(phone, "verify_phone", user=request.user)
    ok, _msg, _http_code = _twilio_verify_start(phone, channel=channel)
    if not ok:
        _record_phone_verification_attempt(
            request,
            phone_e164=phone,
            purpose="verify_phone",
            channel=channel,
            status_code="delivery_failed",
            message=_msg or "verification_not_sent",
            http_status=_http_code,
        )
        messages.error(request, tr(request, "phone_verify_send_failed"))
        return _phone_verification_redirect(request)

    _record_phone_verification_attempt(
        request,
        phone_e164=phone,
        purpose="verify_phone",
        channel=channel,
        status_code="sent",
        http_status=200,
    )
    request.session["employer_phone_verify_phone"] = phone
    messages.success(request, tr(request, "phone_verify_code_sent"))
    return _phone_verification_redirect(request)


@login_required(login_url="employer:login")
@require_POST
def phone_verify_code(request):
    phone = _normalize_phone(request.POST.get("phone") or request.session.get("employer_phone_verify_phone"))
    code = (request.POST.get("code") or "").strip()
    if not phone or not code:
        _record_phone_verification_attempt(
            request,
            phone_e164=phone,
            purpose="verify_phone",
            channel="sms",
            status_code="check_failed",
            message="phone_and_code_required",
            http_status=400,
        )
        messages.error(request, tr(request, "phone_verify_code_invalid"))
        return _phone_verification_redirect(request)

    owner = UserProfile.objects.filter(phone_e164=phone, phone_verified=True).exclude(user=request.user).first()
    if owner:
        _record_phone_verification_attempt(
            request,
            phone_e164=phone,
            purpose="verify_phone",
            channel="sms",
            status_code="phone_already_used",
            message="phone_already_used",
            http_status=400,
        )
        messages.error(request, tr(request, "phone_verify_used"))
        return _phone_verification_redirect(request)

    approved, _msg, _http_code = _twilio_verify_check(phone, code)
    if not approved:
        _record_phone_verification_attempt(
            request,
            phone_e164=phone,
            purpose="verify_phone",
            channel="sms",
            status_code="check_failed",
            message=_msg or "verification_check_failed",
            http_status=_http_code,
        )
        messages.error(request, tr(request, "phone_verify_failed"))
        return _phone_verification_redirect(request)

    PhoneVerification.objects.filter(
        phone_e164=phone,
        purpose="verify_phone",
        is_used=False,
    ).update(is_used=True)
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
            purpose="verify_phone",
            channel="sms",
            status_code="phone_already_used",
            message="phone_already_used",
            http_status=400,
        )
        messages.error(request, tr(request, "phone_verify_used"))
        return _phone_verification_redirect(request)

    request.session.pop("employer_phone_verify_phone", None)
    _record_phone_verification_attempt(
        request,
        phone_e164=phone,
        purpose="verify_phone",
        channel="sms",
        status_code="approved",
        http_status=200,
    )
    messages.success(request, tr(request, "phone_verify_success"))
    return _phone_verification_redirect(request)


@login_required(login_url="employer:login")
def vacancy_list(request):
    vacancies = (
        Vacancy.objects.filter(created_by=request.user, is_deleted_by_moderator=False)
        .order_by("-published_at")
    )
    rows = []
    for vacancy in vacancies:
        status_key, status_kind = _vacancy_status(vacancy)
        rows.append({"vacancy": vacancy, "status_label": tr(request, status_key), "status_kind": status_kind})
    return render(request, "employer/vacancy_list.html", {"rows": rows})


@login_required(login_url="employer:login")
def board_publishing(request):
    """Employer-facing confirmation and revocation for JobHub Board."""
    authorization = EmployerBoardPublishingAuthorization.objects.filter(
        employer=request.user
    ).first()

    if request.method == "POST" and authorization:
        action = (request.POST.get("action") or "").strip()
        if action == "accept":
            if request.POST.get("confirm_terms") != "1":
                messages.error(request, tr(request, "board_consent_required"))
            elif accept_authorization(authorization, request=request):
                messages.success(request, tr(request, "board_authorization_active"))
            else:
                messages.error(request, tr(request, "board_request_unavailable"))
        elif action == "revoke":
            if revoke_authorization(authorization, request=request):
                messages.success(request, tr(request, "board_authorization_revoked"))
            else:
                messages.error(request, tr(request, "board_request_unavailable"))
        return redirect("employer:board_publishing")

    return render(
        request,
        "employer/board_publishing.html",
        {
            "authorization": authorization,
            "authorization_text": AUTHORIZATION_TEXT,
        },
    )


@login_required(login_url="employer:login")
def vacancy_create(request):
    draft_mode = request.method == "POST" and "save_draft" in request.POST
    form = EmployerVacancyForm(request.POST or None, draft_mode=draft_mode, lang=get_lang(request), user=request.user)
    if request.method == "POST" and form.is_valid():
        submit = "submit" in request.POST
        if submit and not _user_has_verified_phone(request.user):
            messages.error(request, tr(request, "msg_verify_phone"))
            return render(request, "employer/vacancy_form.html", _vacancy_form_context(request, form, mode="create"))
        now = timezone.now()
        notify_moderators = False
        try:
            with transaction.atomic():
                vacancy = _save_form_to_vacancy(form, user=request.user)
                vacancy.is_approved = _is_moderator(request) and submit
                vacancy.approved_at = now if vacancy.is_approved else None
                vacancy.is_rejected = False
                vacancy.is_paused_by_owner = False
                vacancy.rejection_reason = ""
                vacancy.last_moderator_rejection_reason = ""
                vacancy.moderation_baseline = {}
                vacancy.is_editing = not submit
                vacancy.editing_started_at = now
                vacancy.revision = 1
                vacancy.save()
                ensure_free_contact_policy(vacancy, set_by=request.user)
                if submit and not vacancy.is_approved:
                    _create_moderation_attempt(
                        vacancy,
                        trigger_type="create",
                        submitted_by=request.user,
                        submitted_at=now,
                    )
                    _apply_web_submission_action(
                        request.user,
                        flow="create",
                        related_vacancy=vacancy,
                        now=now,
                    )
                    notify_moderators = True
        except (EconomyActionRequiredError, InsufficientCreditsError) as exc:
            _handle_submission_error(request, exc, flow="create")
            return render(request, "employer/vacancy_form.html", _vacancy_form_context(request, form, mode="create"))
        if notify_moderators:
            transaction.on_commit(lambda: _notify_moderators_about_pending_vacancy_safe(vacancy))
        messages.success(request, tr(request, "msg_saved_draft") if not submit else tr(request, "msg_submitted"))
        return redirect("employer:vacancy_list")
    return render(request, "employer/vacancy_form.html", _vacancy_form_context(request, form, mode="create"))


@login_required(login_url="employer:login")
def vacancy_edit(request, pk):
    vacancy = get_object_or_404(
        Vacancy,
        pk=pk,
        created_by=request.user,
        is_deleted_by_moderator=False,
    )
    draft_mode = request.method == "POST" and "save_draft" in request.POST
    form = EmployerVacancyForm(request.POST or None, instance=vacancy, draft_mode=draft_mode, lang=get_lang(request), user=request.user)
    if request.method == "POST" and form.is_valid():
        submit = "submit" in request.POST
        if submit and not _user_has_verified_phone(request.user):
            messages.error(request, tr(request, "msg_verify_phone"))
            return render(request, "employer/vacancy_form.html", _vacancy_form_context(request, form, mode="edit", vacancy=vacancy))
        now = timezone.now()
        flow = _submission_flow_for_vacancy(vacancy)
        trigger_type = "create" if flow == "create" else "edit"
        notify_moderators = False
        try:
            with transaction.atomic():
                vacancy = _save_form_to_vacancy(form, user=request.user, vacancy=vacancy)
                vacancy.is_approved = False if submit else vacancy.is_approved
                vacancy.approved_at = None if submit else vacancy.approved_at
                vacancy.is_rejected = False
                vacancy.rejection_reason = ""
                vacancy.is_paused_by_owner = False if submit else vacancy.is_paused_by_owner
                vacancy.paused_by_owner_at = None if submit else vacancy.paused_by_owner_at
                vacancy.is_editing = not submit
                vacancy.editing_started_at = now
                vacancy.revision = (vacancy.revision or 1) + 1 if submit else (vacancy.revision or 1)
                vacancy.expires_at = vacancy.expires_at or (now + VACANCY_LIVE_WINDOW)
                vacancy.save()
                ensure_free_contact_policy(vacancy, set_by=request.user)
                if submit:
                    _create_moderation_attempt(
                        vacancy,
                        trigger_type=trigger_type,
                        submitted_by=request.user,
                        submitted_at=now,
                    )
                    _apply_web_submission_action(
                        request.user,
                        flow=flow,
                        related_vacancy=vacancy,
                        now=now,
                    )
                    notify_moderators = True
        except (EconomyActionRequiredError, InsufficientCreditsError) as exc:
            _handle_submission_error(request, exc, flow=flow)
            return render(request, "employer/vacancy_form.html", _vacancy_form_context(request, form, mode="edit", vacancy=vacancy))
        if notify_moderators:
            transaction.on_commit(lambda: _notify_moderators_about_pending_vacancy_safe(vacancy))
        messages.success(request, tr(request, "msg_changes_saved") if not submit else tr(request, "msg_submitted"))
        return redirect("employer:vacancy_list")
    return render(request, "employer/vacancy_form.html", _vacancy_form_context(request, form, mode="edit", vacancy=vacancy))


@login_required(login_url="employer:login")
@require_POST
def vacancy_pause(request, pk):
    vacancy = get_object_or_404(Vacancy, pk=pk, created_by=request.user, is_deleted_by_moderator=False)
    if not vacancy.is_approved:
        messages.error(request, tr(request, "msg_only_published_pause"))
        return redirect("employer:vacancy_list")
    if vacancy.is_paused_by_owner:
        vacancy.is_paused_by_owner = False
        vacancy.paused_by_owner_at = None
        vacancy.save(update_fields=["is_paused_by_owner", "paused_by_owner_at"])
        messages.success(request, tr(request, "msg_resumed"))
    else:
        vacancy.is_paused_by_owner = True
        vacancy.paused_by_owner_at = timezone.now()
        vacancy.save(update_fields=["is_paused_by_owner", "paused_by_owner_at"])
        messages.success(request, tr(request, "msg_paused"))
    return redirect("employer:vacancy_list")


@login_required(login_url="employer:login")
@require_POST
def vacancy_delete(request, pk):
    vacancy = get_object_or_404(Vacancy, pk=pk, created_by=request.user, is_deleted_by_moderator=False)
    if vacancy.is_approved and not vacancy.is_paused_by_owner:
        messages.error(request, tr(request, "msg_pause_before_delete"))
        return redirect("employer:vacancy_list")
    vacancy.delete()
    messages.success(request, tr(request, "msg_deleted"))
    return redirect("employer:vacancy_list")
