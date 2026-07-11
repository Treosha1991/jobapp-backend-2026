import json
import secrets

from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.db import IntegrityError
from django.db import transaction
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
from .auth_api import (
    _create_phone_code,
    _normalize_phone,
    _phone_code_too_frequent,
    _phone_country_allowed,
    _record_phone_verification_attempt,
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
from .models import PhoneVerification, UserProfile, Vacancy
from .city_catalog import CITY_CATALOG
from .web_forms import EmployerVacancyForm
from .web_i18n import apply_language_cookie, get_lang, normalize_lang, tr


class EmployerLoginView(LoginView):
    template_name = "employer/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        return reverse_lazy("employer:vacancy_list")


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


@require_POST
def phone_login_request_code(request):
    """Send a one-time SMS code for the already verified JobHub account."""
    phone = _normalize_phone(request.POST.get("phone"))
    if not phone:
        messages.error(request, tr(request, "phone_login_invalid"))
        return _login_redirect(request)
    if not _phone_country_allowed(phone):
        messages.error(request, tr(request, "phone_login_country"))
        return _login_redirect(request)

    profile = UserProfile.objects.filter(phone_e164=phone, phone_verified=True).select_related("user").first()
    if not profile:
        messages.error(request, tr(request, "phone_login_not_found"))
        return _login_redirect(request)
    if _phone_code_too_frequent(phone, "login"):
        messages.error(request, tr(request, "phone_login_too_many"))
        return _login_redirect(request)

    _create_phone_code(phone, "login", user=profile.user)
    ok, message, http_status = _twilio_verify_start(phone, channel="sms")
    if not ok:
        _record_phone_verification_attempt(
            request,
            phone_e164=phone,
            purpose="login",
            channel="sms",
            status_code="delivery_failed",
            message=message or "verification_not_sent",
            http_status=http_status,
            user=profile.user,
        )
        messages.error(request, tr(request, "phone_login_send_failed"))
        return _login_redirect(request)

    _record_phone_verification_attempt(
        request,
        phone_e164=phone,
        purpose="login",
        channel="sms",
        status_code="sent",
        http_status=200,
        user=profile.user,
    )
    request.session["employer_login_phone"] = phone
    messages.success(request, tr(request, "phone_login_code_sent"))
    return _login_redirect(request)


@require_POST
def phone_login_verify_code(request):
    phone = _normalize_phone(request.POST.get("phone") or request.session.get("employer_login_phone"))
    code = (request.POST.get("code") or "").strip()
    profile = UserProfile.objects.filter(phone_e164=phone, phone_verified=True).select_related("user").first()
    if not phone or not code or not profile:
        messages.error(request, tr(request, "phone_login_code_invalid"))
        return _login_redirect(request)

    approved, message, http_status = _twilio_verify_check(phone, code)
    if not approved:
        _record_phone_verification_attempt(
            request,
            phone_e164=phone,
            purpose="login",
            channel="sms",
            status_code="check_failed",
            message=message or "invalid_or_expired_code",
            http_status=http_status,
            user=profile.user,
        )
        messages.error(request, tr(request, "phone_login_code_invalid"))
        return _login_redirect(request)

    PhoneVerification.objects.filter(phone_e164=phone, purpose="login", is_used=False).update(is_used=True)
    _record_phone_verification_attempt(
        request,
        phone_e164=phone,
        purpose="login",
        channel="sms",
        status_code="approved",
        http_status=200,
        user=profile.user,
    )
    request.session.pop("employer_login_phone", None)
    auth_login(request, profile.user, backend="django.contrib.auth.backends.ModelBackend")
    messages.success(request, tr(request, "phone_login_success"))
    return _login_redirect(request)


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
