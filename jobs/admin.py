from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import admin
from django.contrib.admin.sites import NotRegistered
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.db.models import Count
from django.utils import timezone
from django.utils.html import format_html

from .country_choices import (
    MAX_AUDIENCE_COUNTRY_SELECTIONS,
    MIN_AUDIENCE_COUNTRY_SELECTIONS,
    VACANCY_COUNTRY_CHOICES,
    decode_audience_country_codes,
    encode_audience_country_codes,
)
from .driver_licenses import (
    DRIVER_LICENSE_CHOICES,
    MAX_DRIVER_LICENSE_SELECTIONS,
    decode_driver_license_categories,
    encode_driver_license_categories,
)
from .economy import get_vacancy_contact_unlock_stats, set_wallet_balances
from .models import (
    AccountDeletionRequest,
    ChatConversation,
    ChatMessage,
    ChatReport,
    Complaint,
    ComplaintActionLog,
    EconomyConfig,
    EmailVerification,
    EmployerSubscription,
    PhoneVerification,
    PhoneVerificationAttempt,
    PurchaseRecord,
    PushDevice,
    ModeratorNotificationDelivery,
    StoreProduct,
    UnlockedContact,
    UnlockRequest,
    UserBlock,
    UserMonetizationProfile,
    UserWallet,
    VacancyContactAccessPolicy,
    UserProfile,
    Vacancy,
    VacancyReview,
    VacancyAlertDelivery,
    VacancyAlertSubscription,
    VacancyModerationAttempt,
    WalletTransaction,
)
from .review_presets import REVIEW_PRESET_LABELS


admin.site.site_header = "JobHub Operator Console"
admin.site.site_title = "JobHub Admin"
admin.site.index_title = "Moderation and support panel"
admin.site.empty_value_display = "-"


class VacancyAdminForm(forms.ModelForm):
    audience_country_codes = forms.MultipleChoiceField(
        required=True,
        choices=VACANCY_COUNTRY_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        help_text="Select from 1 to 20 countries.",
    )
    driver_license_categories = forms.MultipleChoiceField(
        required=False,
        choices=DRIVER_LICENSE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        help_text="Select up to 3 categories.",
    )

    class Meta:
        model = Vacancy
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        audience_value = getattr(self.instance, "audience_country_codes", "")
        audience_initial = decode_audience_country_codes(audience_value)
        self.initial["audience_country_codes"] = audience_initial
        self.fields["audience_country_codes"].initial = audience_initial
        initial_value = getattr(self.instance, "driver_license_categories", "")
        driver_initial = decode_driver_license_categories(initial_value)
        self.initial["driver_license_categories"] = driver_initial
        self.fields["driver_license_categories"].initial = driver_initial

    def clean_audience_country_codes(self):
        selected = self.cleaned_data.get("audience_country_codes") or []
        try:
            return encode_audience_country_codes(
                selected,
                min_selections=MIN_AUDIENCE_COUNTRY_SELECTIONS,
                max_selections=MAX_AUDIENCE_COUNTRY_SELECTIONS,
            )
        except ValueError as exc:
            message = str(exc)
            if message == "too_few_audience_countries":
                message = "Select at least 1 country."
            elif message == "too_many_audience_countries":
                message = "You can select up to 20 countries."
            else:
                message = "Invalid audience countries."
            raise forms.ValidationError(message) from exc

    def clean_driver_license_categories(self):
        selected = self.cleaned_data.get("driver_license_categories") or []
        try:
            return encode_driver_license_categories(
                selected,
                max_selections=MAX_DRIVER_LICENSE_SELECTIONS,
            )
        except ValueError as exc:
            message = str(exc)
            if message == "too_many_driver_license_categories":
                message = "You can select up to 3 categories."
            else:
                message = "Invalid driver license categories."
            raise forms.ValidationError(message) from exc

    def clean(self):
        cleaned_data = super().clean()
        pinned_from = cleaned_data.get("pinned_from")
        pinned_until = cleaned_data.get("pinned_until")
        if bool(pinned_from) != bool(pinned_until):
            raise forms.ValidationError(
                "Set both pin start and pin end, or leave both empty."
            )
        if pinned_from and pinned_until and pinned_until <= pinned_from:
            raise forms.ValidationError("Pin end must be later than pin start.")
        return cleaned_data


def _badge(label, *, bg, fg="#FFFFFF"):
    return format_html(
        (
            '<span style="display:inline-block;padding:2px 8px;border-radius:999px;'
            "font-weight:700;font-size:11px;line-height:1.6;background:{};color:{};"
            'white-space:nowrap;">{}</span>'
        ),
        bg,
        fg,
        label,
    )


def _muted(text):
    return format_html(
        '<span style="color:#6B7280;font-weight:600;">{}</span>',
        text,
    )


def _format_credit_value(value):
    try:
        amount = Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        return "0"
    normalized = amount.quantize(Decimal("0.01"))
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _bool_badge(value, true_label="Yes", false_label="No"):
    return _badge(
        true_label if value else false_label,
        bg="#198754" if value else "#6C757D",
    )


def _contact_unlock_stats_summary(policy):
    if not getattr(policy, "pk", None):
        return _muted("No unlocks yet")

    stats = get_vacancy_contact_unlock_stats(policy.vacancy, policy=policy)
    paid_count = int(stats["paid_unlocks"] or 0)
    ad_count = int(stats["ad_unlocks"] or 0)
    subscription_count = int(stats["subscription_unlocks"] or 0)
    unique_users = int(stats["unique_users"] or 0)
    earned_credits = _format_credit_value(stats["earned_credits"])
    click_limit = getattr(policy, "contact_unlock_paid_click_limit", None)
    limit_label = (
        f"Paid {paid_count}/{int(click_limit)}"
        if click_limit
        else f"Paid {paid_count}"
    )
    limit_bg = "#B42318" if click_limit and paid_count >= int(click_limit) else "#175CD3"
    badges = [
        _badge(limit_label, bg=limit_bg),
        _badge(f"Ad {ad_count}", bg="#027A48"),
        _badge(f"Sub {subscription_count}", bg="#7A5AF8"),
        _badge(f"Users {unique_users}", bg="#475467"),
        _badge(f"Credits {earned_credits}", bg="#B54708"),
    ]
    return format_html(" ".join(str(item) for item in badges))


def _review_presets_html(codes):
    if not codes:
        return _muted("No presets")
    badges = []
    for code in codes:
        label = REVIEW_PRESET_LABELS.get(code, code)
        badges.append(_badge(label, bg="#155EEF"))
    return format_html(" ".join(str(item) for item in badges))


def _vacancy_status_meta(obj):
    now = timezone.now()
    if obj.is_deleted_by_moderator:
        return ("Deleted", "#7A2433")
    if obj.is_rejected:
        return ("Rejected", "#B42318")
    if obj.is_editing:
        return ("Editing", "#175CD3")
    if obj.is_approved and obj.is_paused_by_owner:
        return ("Paused", "#B54708")
    if obj.is_approved and obj.expires_at <= now:
        return ("Expired", "#667085")
    if obj.is_approved:
        return ("Live", "#198754")
    return ("Pending", "#7A5AF8")


def _vacancy_promotion_meta(obj):
    return {
        "vip": ("VIP", "#9A7B33"),
        "premium": ("Premium", "#9A7B33"),
        "urgent": ("Urgent", "#B05A3C"),
    }.get((obj.promotion_kind or "").strip().lower(), ("Standard", "#667085"))


def _complaint_status_meta(value):
    return {
        "new": ("New", "#175CD3"),
        "in_review": ("In review", "#B54708"),
        "resolved": ("Resolved", "#198754"),
        "rejected": ("Rejected", "#B42318"),
    }.get(value or "", ("Unknown", "#667085"))


def _complaint_reason_meta(value):
    return {
        "spam": ("Spam", "#B42318"),
        "fake": ("Fake", "#7A2433"),
        "abuse": ("Abuse", "#C4320A"),
        "wrong_info": ("Wrong info", "#B54708"),
        "contacts": ("Contacts", "#175CD3"),
        "not_actual": ("Not actual", "#667085"),
        "other": ("Other", "#7A5AF8"),
    }.get(value or "", ("Other", "#667085"))


def _action_meta(value):
    return {
        "delete_forever": ("Delete forever", "#B42318"),
        "reject": ("Reject vacancy", "#C4320A"),
        "restore": ("Restore", "#198754"),
    }.get(value or "", ("Action", "#667085"))


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    extra = 0
    fk_name = "user"
    fields = (
        "nickname",
        "description",
        "phone_e164",
        "phone_verified",
        "phone_verified_at",
        "employer_verified",
        "avatar_key",
        "avatar_updated_at",
    )
    readonly_fields = ("phone_verified_at", "avatar_updated_at")


class UserWalletInline(admin.StackedInline):
    model = UserWallet
    can_delete = False
    extra = 0
    fk_name = "user"
    fields = (
        ("paid_credits", "bonus_credits"),
        ("lifetime_paid_credits", "lifetime_bonus_credits"),
        ("created_at", "updated_at"),
    )
    readonly_fields = (
        "paid_credits",
        "bonus_credits",
        "lifetime_paid_credits",
        "lifetime_bonus_credits",
        "created_at",
        "updated_at",
    )


class UserMonetizationProfileInline(admin.StackedInline):
    model = UserMonetizationProfile
    can_delete = False
    extra = 0
    fk_name = "user"
    fields = (
        ("employer_subscription_until", "seeker_subscription_until"),
        ("free_create_ad_submissions_used", "free_edit_ad_resubmissions_used"),
        ("employer_daily_submission_date", "employer_daily_submissions_used"),
        ("created_at", "updated_at"),
    )
    readonly_fields = ("created_at", "updated_at")


class VacancyContactAccessPolicyInline(admin.StackedInline):
    model = VacancyContactAccessPolicy
    can_delete = False
    extra = 0
    max_num = 1
    fk_name = "vacancy"
    fields = (
        ("contact_unlock_mode", "contact_unlock_timer_hours"),
        ("contact_unlock_price_credits", "contact_unlock_paid_click_limit"),
        "unlock_stats_summary",
        "paid_window_started_at",
        ("set_by", "set_at"),
    )
    readonly_fields = ("paid_window_started_at", "set_at", "unlock_stats_summary")

    def get_extra(self, request, obj=None, **kwargs):
        if obj is None:
            return 1
        return 0 if hasattr(obj, "contact_access_policy") else 1

    @admin.display(description="Unlock stats")
    def unlock_stats_summary(self, obj):
        return _contact_unlock_stats_summary(obj)


try:
    admin.site.unregister(User)
except NotRegistered:
    pass


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline, UserWalletInline, UserMonetizationProfileInline)
    list_display = (
        "username",
        "display_name",
        "email_with_phone",
        "role_badges",
        "wallet_balance",
        "subscription_badges",
        "vacancies_total",
        "complaints_filed",
        "last_login",
    )
    list_filter = ("is_active", "is_staff", "is_superuser", "date_joined")
    search_fields = (
        "username",
        "email",
        "first_name",
        "last_name",
        "profile__nickname",
        "profile__phone_e164",
    )
    ordering = ("-date_joined",)
    list_per_page = 40

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "profile",
            "wallet",
            "monetization_profile",
        )

    @admin.display(description="Display name")
    def display_name(self, obj):
        nickname = ((getattr(getattr(obj, "profile", None), "nickname", "") or "").strip())
        if nickname:
            return nickname
        full_name = f"{obj.first_name} {obj.last_name}".strip()
        return full_name or _muted("No public name")

    @admin.display(description="Email / Phone")
    def email_with_phone(self, obj):
        profile = getattr(obj, "profile", None)
        phone = profile.phone_e164 if profile and profile.phone_e164 else "—"
        email = obj.email or "—"
        latest_failed = (
            PhoneVerificationAttempt.objects.filter(user=obj)
            .exclude(status__in=("sent", "approved"))
            .order_by("-created_at")
            .first()
        )
        failed_html = ""
        if latest_failed and latest_failed.phone_e164:
            failed_html = format_html(
                "<br><span style='color:#B42318'>Last failed: {} ({})</span>",
                latest_failed.phone_e164,
                latest_failed.get_status_display(),
            )
        return format_html(
            "<strong>{}</strong><br><span style='color:#6B7280'>{}</span>{}",
            email,
            phone,
            failed_html,
        )

    @admin.display(description="Roles")
    def role_badges(self, obj):
        badges = [
            _badge("Active", bg="#198754") if obj.is_active else _badge("Disabled", bg="#667085"),
            _badge("Staff", bg="#175CD3") if obj.is_staff else _badge("User", bg="#6C757D"),
        ]
        if obj.is_superuser:
            badges.append(_badge("Superuser", bg="#7A5AF8"))
        return format_html(" ".join(str(item) for item in badges))

    @admin.display(description="Vacancies")
    def vacancies_total(self, obj):
        total = obj.vacancy_set.count()
        live = obj.vacancy_set.filter(
            is_approved=True,
            is_paused_by_owner=False,
            is_deleted_by_moderator=False,
            expires_at__gt=timezone.now(),
        ).count()
        return format_html(
            "<strong>{}</strong> <span style='color:#6B7280'>(live: {})</span>",
            total,
            live,
        )

    @admin.display(description="Complaints filed")
    def complaints_filed(self, obj):
        return obj.complaints.count()

    @admin.display(description="Wallet")
    def wallet_balance(self, obj):
        try:
            wallet = obj.wallet
        except UserWallet.DoesNotExist:
            return _muted("No wallet")
        return format_html(
            "<strong>{}</strong><br><span style='color:#6B7280'>paid: {} • bonus: {}</span>",
            wallet.total_credits,
            wallet.paid_credits,
            wallet.bonus_credits,
        )

    @admin.display(description="Subscriptions")
    def subscription_badges(self, obj):
        try:
            profile = obj.monetization_profile
        except UserMonetizationProfile.DoesNotExist:
            return _muted("No profile")
        badges = []
        if profile.has_employer_subscription():
            badges.append(_badge("Employer sub", bg="#175CD3"))
        if profile.has_seeker_subscription():
            badges.append(_badge("Seeker sub", bg="#7A5AF8"))
        if not badges:
            return _muted("Inactive")
        return format_html(" ".join(str(item) for item in badges))


@admin.register(Vacancy)
class VacancyAdmin(admin.ModelAdmin):
    form = VacancyAdminForm
    inlines = (VacancyContactAccessPolicyInline,)
    list_display = (
        "id",
        "title",
        "status_badge",
        "promotion_badge",
        "pin_badge",
        "owner_display",
        "location_display",
        "category",
        "salary_preview",
        "complaints_total",
        "published_at",
        "approved_at",
        "expires_at",
    )
    list_filter = (
        "country",
        "category",
        "source",
        "employment_type",
        "housing_type",
        "is_approved",
        "is_rejected",
        "is_paused_by_owner",
        "is_editing",
        "is_deleted_by_moderator",
        "promotion_kind",
        "pinned_from",
        "pinned_until",
    )
    search_fields = (
        "title",
        "city",
        "city_code",
        "salary",
        "created_by__username",
        "created_by__email",
        "created_by__profile__nickname",
        "phone",
        "email",
    )
    ordering = ("-published_at", "-id")
    list_select_related = ("created_by", "created_by__profile")
    autocomplete_fields = ("created_by",)
    date_hierarchy = "published_at"
    list_per_page = 40
    readonly_fields = (
        "status_badge",
        "owner_display",
        "complaints_total",
        "published_at",
        "approved_at",
        "revision",
        "paused_by_owner_at",
        "editing_started_at",
        "deleted_by_moderator_at",
        "last_owner_resume_at",
        "promotion_badge",
        "pin_badge",
    )
    actions = (
        "pin_for_1_day",
        "pin_for_3_days",
        "pin_for_7_days",
        "pin_for_14_days",
        "clear_pin",
    )
    fieldsets = (
        (
            "Main",
            {
                "fields": (
                    ("title", "status_badge"),
                    ("owner_display", "source", "revision"),
                    ("country", "city", "city_code"),
                    "audience_country_codes",
                    ("category", "employment_type", "experience_required"),
                    "driver_license_categories",
                    "description",
                )
            },
        ),
        (
            "Salary",
            {
                "fields": (
                    "salary",
                    ("salary_from", "salary_to"),
                    ("salary_currency", "salary_tax_type", "salary_hours_month"),
                )
            },
        ),
        (
            "Housing",
            {
                "classes": ("collapse",),
                "fields": (("housing_type", "housing_cost"),),
            },
        ),
        (
            "Contacts",
            {
                "classes": ("collapse",),
                "fields": (
                    (
                        "phone",
                        "additional_phone",
                        "additional_phone_2",
                        "additional_phone_3",
                        "hide_primary_phone",
                    ),
                    ("whatsapp", "viber", "telegram_username", "telegram_usernames"),
                    ("telegram",),
                    "email",
                ),
            },
        ),
        (
            "Feed promotion",
            {
                "classes": ("collapse",),
                "fields": (
                    "promotion_kind",
                    ("pinned_from", "pinned_until"),
                    ("promotion_badge", "pin_badge"),
                ),
            },
        ),
        (
            "Moderation",
            {
                "classes": ("collapse",),
                "fields": (
                    ("is_approved", "is_paused_by_owner", "is_editing", "is_rejected"),
                    "rejection_reason",
                    "last_moderator_rejection_reason",
                    ("is_deleted_by_moderator", "deleted_by_moderator_at"),
                    ("published_at", "approved_at", "expires_at"),
                    ("paused_by_owner_at", "editing_started_at"),
                    ("last_owner_resume_at", "owner_resume_day", "owner_resume_count_day"),
                    "moderation_baseline",
                    "moderator_deleted_state",
                ),
            },
        ),
    )

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is None and "owner_display" in fields:
            fields.remove("owner_display")
        return fields

    def get_fieldsets(self, request, obj=None):
        fieldsets = list(super().get_fieldsets(request, obj))
        if not fieldsets:
            return fieldsets

        main_title, main_options = fieldsets[0]
        fields = list(main_options.get("fields", ()))
        if len(fields) > 1:
            fields[1] = ("owner_display", "source", "revision") if obj else (
                "created_by",
                "source",
                "revision",
            )
        fieldsets[0] = (
            main_title,
            {
                **main_options,
                "fields": tuple(fields),
            },
        )
        return fieldsets

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("created_by", "created_by__profile")
            .annotate(_complaints_total=Count("complaints"))
        )

    def save_model(self, request, obj, form, change):
        now = timezone.now()
        was_approved = False
        previous_approved_at = None

        if change and obj.pk:
            previous = (
                Vacancy.objects.filter(pk=obj.pk)
                .values("is_approved", "approved_at", "published_at")
                .first()
                or {}
            )
            was_approved = bool(previous.get("is_approved"))
            previous_approved_at = previous.get("approved_at") or previous.get("published_at")

        if obj.is_approved:
            if not was_approved:
                obj.approved_at = now
                obj.published_at = now
                obj.expires_at = now + timedelta(days=30)
            elif obj.approved_at is None:
                obj.approved_at = previous_approved_at or now
            obj.is_rejected = False
            obj.is_paused_by_owner = False
            obj.paused_by_owner_at = None
            obj.rejection_reason = ""
            obj.last_moderator_rejection_reason = ""
            obj.is_editing = False

        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for instance in instances:
            if isinstance(instance, VacancyContactAccessPolicy):
                instance.set_by = request.user
            instance.save()
        formset.save_m2m()

    @admin.display(description="Status")
    def status_badge(self, obj):
        label, color = _vacancy_status_meta(obj)
        return _badge(label, bg=color)

    @admin.display(description="Promotion")
    def promotion_badge(self, obj):
        label, color = _vacancy_promotion_meta(obj)
        return _badge(label, bg=color)

    @admin.display(description="Pinned")
    def pin_badge(self, obj):
        if obj.is_pinned_now():
            return _badge("Pinned now", bg="#356E9A")
        if obj.pinned_from and obj.pinned_until:
            return _muted(
                f"Scheduled: {obj.pinned_from:%d.%m %H:%M} - {obj.pinned_until:%d.%m %H:%M}"
            )
        return _muted("Not pinned")

    def _pin_for_duration(self, request, queryset, duration_days):
        now = timezone.now()
        updated = queryset.update(
            pinned_from=now,
            pinned_until=now + timedelta(days=duration_days),
        )
        self.message_user(request, f"Pinned {updated} vacancy(s) for {duration_days} day(s).")

    @admin.action(description="Pin selected vacancies for 1 day")
    def pin_for_1_day(self, request, queryset):
        self._pin_for_duration(request, queryset, 1)

    @admin.action(description="Pin selected vacancies for 3 days")
    def pin_for_3_days(self, request, queryset):
        self._pin_for_duration(request, queryset, 3)

    @admin.action(description="Pin selected vacancies for 7 days")
    def pin_for_7_days(self, request, queryset):
        self._pin_for_duration(request, queryset, 7)

    @admin.action(description="Pin selected vacancies for 14 days")
    def pin_for_14_days(self, request, queryset):
        self._pin_for_duration(request, queryset, 14)

    @admin.action(description="Remove pin from selected vacancies")
    def clear_pin(self, request, queryset):
        updated = queryset.update(pinned_from=None, pinned_until=None)
        self.message_user(request, f"Removed pin from {updated} vacancy(s).")

    @admin.display(description="Owner", ordering="created_by__username")
    def owner_display(self, obj):
        profile = getattr(obj.created_by, "profile", None)
        nickname = ((getattr(profile, "nickname", "") or "").strip())
        primary = nickname or obj.created_by.username
        secondary = obj.created_by.email or obj.created_by.username
        return format_html(
            "<strong>{}</strong><br><span style='color:#6B7280'>{}</span>",
            primary,
            secondary,
        )

    @admin.display(description="Location")
    def location_display(self, obj):
        return f"{obj.country} / {obj.city}"

    @admin.display(description="Salary")
    def salary_preview(self, obj):
        if obj.salary_from or obj.salary_to:
            start = obj.salary_from if obj.salary_from is not None else "—"
            end = obj.salary_to if obj.salary_to is not None else "—"
            currency = obj.salary_currency or ""
            return f"{start}–{end} {currency}".strip()
        return obj.salary

    @admin.display(description="Complaints", ordering="_complaints_total")
    def complaints_total(self, obj):
        total = getattr(obj, "_complaints_total", 0)
        return _badge(str(total), bg="#C4320A" if total else "#667085")


@admin.register(VacancyModerationAttempt)
class VacancyModerationAttemptAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "vacancy",
        "attempt_no",
        "trigger_type",
        "decision_badge",
        "submitted_by",
        "submitted_at",
        "decided_by",
        "decided_at",
    )
    list_filter = ("trigger_type", "decision", "submitted_at", "decided_at")
    search_fields = (
        "vacancy__title",
        "vacancy__created_by__username",
        "vacancy__created_by__email",
        "vacancy__created_by__profile__nickname",
        "rejection_reason",
    )
    ordering = ("-submitted_at", "-id")
    list_select_related = ("vacancy", "submitted_by", "decided_by")
    readonly_fields = ("submitted_at", "decided_at")
    raw_id_fields = ("vacancy", "submitted_by", "decided_by")

    @admin.display(description="Decision")
    def decision_badge(self, obj):
        meta = {
            "pending": ("Pending", "#7A5AF8"),
            "approved": ("Approved", "#198754"),
            "rejected": ("Rejected", "#B42318"),
        }.get(obj.decision, ("Unknown", "#667085"))
        return _badge(meta[0], bg=meta[1])


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "nickname_display",
        "phone_e164",
        "phone_verified_badge",
        "employer_verified_badge",
        "has_avatar_badge",
        "avatar_updated_at",
    )
    search_fields = ("user__username", "user__email", "nickname", "phone_e164")
    list_filter = ("phone_verified", "employer_verified")
    list_select_related = ("user",)
    ordering = ("user__username",)
    actions = ("mark_employers_verified", "remove_employer_verification")

    @admin.display(description="Nickname", ordering="nickname")
    def nickname_display(self, obj):
        return obj.nickname or _muted("No nickname")

    @admin.display(description="Phone")
    def phone_verified_badge(self, obj):
        return _bool_badge(obj.phone_verified, "Verified", "Not verified")

    @admin.display(description="Employer")
    def employer_verified_badge(self, obj):
        return _bool_badge(obj.employer_verified, "Verified", "Not verified")

    @admin.action(description="Mark selected employers as verified")
    def mark_employers_verified(self, request, queryset):
        updated = queryset.update(employer_verified=True)
        self.message_user(request, f"Employer verification enabled for {updated} profile(s).")

    @admin.action(description="Remove employer verification from selected profiles")
    def remove_employer_verification(self, request, queryset):
        updated = queryset.update(employer_verified=False)
        self.message_user(request, f"Employer verification removed from {updated} profile(s).")

    @admin.display(description="Avatar")
    def has_avatar_badge(self, obj):
        return _bool_badge(bool((obj.avatar_key or "").strip()), "Uploaded", "Missing")


@admin.register(PhoneVerification)
class PhoneVerificationAdmin(admin.ModelAdmin):
    list_display = (
        "phone_e164",
        "purpose",
        "user",
        "state_badge",
        "attempts",
        "created_at",
        "expires_at",
    )
    search_fields = ("phone_e164", "user__username", "user__email")
    list_filter = ("purpose", "is_used")
    ordering = ("-created_at",)

    @admin.display(description="State")
    def state_badge(self, obj):
        active = obj.is_valid()
        if obj.is_used:
            return _badge("Used", bg="#667085")
        if active:
            return _badge("Active", bg="#198754")
        return _badge("Expired", bg="#B42318")


@admin.register(PhoneVerificationAttempt)
class PhoneVerificationAttemptAdmin(admin.ModelAdmin):
    list_display = (
        "phone_e164",
        "user",
        "status_badge",
        "purpose",
        "channel",
        "http_status",
        "ip_address",
        "created_at",
    )
    search_fields = ("phone_e164", "user__username", "user__email", "message", "ip_address")
    list_filter = ("status", "purpose", "channel", "created_at")
    list_select_related = ("user",)
    readonly_fields = (
        "phone_e164",
        "user",
        "purpose",
        "channel",
        "status",
        "message",
        "http_status",
        "ip_address",
        "user_agent",
        "created_at",
    )
    ordering = ("-created_at",)

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        if obj.status in {"sent", "approved"}:
            return _badge(obj.get_status_display(), bg="#198754")
        if obj.status == "unsupported_country":
            return _badge(obj.get_status_display(), bg="#B42318")
        if obj.status == "too_many_requests":
            return _badge(obj.get_status_display(), bg="#B54708")
        return _badge(obj.get_status_display(), bg="#667085")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(EmailVerification)
class EmailVerificationAdmin(admin.ModelAdmin):
    list_display = ("user", "purpose", "state_badge", "created_at", "expires_at")
    search_fields = ("user__username", "user__email", "target_email")
    list_filter = ("purpose", "is_used")
    ordering = ("-created_at",)

    @admin.display(description="State")
    def state_badge(self, obj):
        if obj.is_used:
            return _badge("Used", bg="#667085")
        if obj.is_valid():
            return _badge("Active", bg="#198754")
        return _badge("Expired", bg="#B42318")


@admin.register(Complaint)
class ComplaintAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "status_badge",
        "reason_badge",
        "vacancy_title",
        "vacancy_owner",
        "reporter_display",
        "created_at",
        "handled_by",
    )
    list_filter = ("status", "reason", "created_at")
    search_fields = (
        "vacancy__id",
        "vacancy__title",
        "reporter__username",
        "reporter__email",
        "message",
    )
    ordering = ("-created_at",)
    list_select_related = ("vacancy", "vacancy__created_by", "vacancy__created_by__profile", "reporter", "handled_by")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("vacancy", "reporter", "handled_by")
    actions = ("mark_in_review", "mark_resolved", "mark_rejected")

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        label, color = _complaint_status_meta(obj.status)
        return _badge(label, bg=color)

    @admin.display(description="Reason", ordering="reason")
    def reason_badge(self, obj):
        label, color = _complaint_reason_meta(obj.reason)
        return _badge(label, bg=color)

    @admin.display(description="Vacancy")
    def vacancy_title(self, obj):
        return format_html(
            "<strong>#{}</strong> {}",
            obj.vacancy_id,
            obj.vacancy.title,
        )

    @admin.display(description="Vacancy owner")
    def vacancy_owner(self, obj):
        owner = obj.vacancy.created_by
        nickname = ((getattr(getattr(owner, "profile", None), "nickname", "") or "").strip())
        return nickname or owner.username

    @admin.display(description="Reporter")
    def reporter_display(self, obj):
        return obj.reporter.email or obj.reporter.username

    @admin.action(description="Mark selected complaints as In review")
    def mark_in_review(self, request, queryset):
        queryset.update(
            status="in_review",
            handled_by=request.user,
            handled_at=timezone.now(),
        )

    @admin.action(description="Mark selected complaints as Resolved")
    def mark_resolved(self, request, queryset):
        queryset.update(
            status="resolved",
            handled_by=request.user,
            handled_at=timezone.now(),
        )

    @admin.action(description="Mark selected complaints as Rejected")
    def mark_rejected(self, request, queryset):
        queryset.update(
            status="rejected",
            handled_by=request.user,
            handled_at=timezone.now(),
        )


@admin.register(ComplaintActionLog)
class ComplaintActionLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "action_badge",
        "vacancy_id_display",
        "complaint_id_display",
        "actor",
        "created_at",
    )
    list_filter = ("action", "created_at")
    search_fields = (
        "vacancy__id",
        "vacancy__title",
        "complaint__id",
        "actor__email",
        "actor__username",
        "note",
    )
    ordering = ("-created_at",)
    list_select_related = ("vacancy", "complaint", "actor")
    readonly_fields = ("created_at", "before_state", "after_state")
    raw_id_fields = ("vacancy", "complaint", "actor")

    @admin.display(description="Action", ordering="action")
    def action_badge(self, obj):
        label, color = _action_meta(obj.action)
        return _badge(label, bg=color)

    @admin.display(description="Vacancy ID")
    def vacancy_id_display(self, obj):
        return obj.vacancy_id

    @admin.display(description="Complaint ID")
    def complaint_id_display(self, obj):
        return obj.complaint_id


@admin.register(AccountDeletionRequest)
class AccountDeletionRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user_id_snapshot",
        "email_snapshot",
        "status_badge",
        "confirmed_via",
        "requested_at",
        "execute_after",
        "processed_at",
    )
    list_filter = ("status", "confirmed_via", "requested_at", "execute_after")
    search_fields = ("user_id_snapshot", "email_snapshot")
    ordering = ("-requested_at",)
    readonly_fields = ("requested_at", "processed_at", "user_id_snapshot", "email_snapshot")

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        meta = {
            "pending": ("Pending", "#B54708"),
            "completed": ("Completed", "#198754"),
            "cancelled": ("Cancelled", "#667085"),
        }.get(obj.status, ("Unknown", "#667085"))
        return _badge(meta[0], bg=meta[1])


@admin.register(UserBlock)
class UserBlockAdmin(admin.ModelAdmin):
    list_display = ("id", "blocker", "blocked_user", "created_at")
    search_fields = ("blocker__username", "blocker__email", "blocked_user__username", "blocked_user__email")
    list_filter = ("created_at",)
    ordering = ("-created_at",)


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    can_delete = False
    fields = ("sender", "body", "has_external_links", "created_at")
    readonly_fields = fields
    ordering = ("-created_at",)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ChatConversation)
class ChatConversationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "candidate",
        "employer",
        "initial_vacancy",
        "last_message_at",
        "created_at",
    )
    search_fields = (
        "candidate__username",
        "candidate__email",
        "candidate__profile__nickname",
        "employer__username",
        "employer__email",
        "employer__profile__nickname",
        "initial_vacancy_title",
    )
    list_filter = ("created_at", "last_message_at")
    ordering = ("-last_message_at", "-id")
    list_select_related = ("candidate", "employer", "initial_vacancy")
    raw_id_fields = ("candidate", "employer", "initial_vacancy")
    readonly_fields = (
        "initial_vacancy_title",
        "candidate_last_read_at",
        "employer_last_read_at",
        "last_message_at",
        "created_at",
        "updated_at",
    )
    inlines = (ChatMessageInline,)

    def has_add_permission(self, request):
        return False


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "conversation",
        "sender",
        "body_preview",
        "has_external_links",
        "created_at",
    )
    search_fields = (
        "conversation__candidate__username",
        "conversation__candidate__email",
        "conversation__employer__username",
        "conversation__employer__email",
        "body",
    )
    list_filter = ("has_external_links", "created_at")
    ordering = ("-created_at", "-id")
    list_select_related = ("conversation", "sender")
    raw_id_fields = ("conversation", "sender")
    readonly_fields = ("conversation", "sender", "body", "has_external_links", "client_message_id", "created_at")

    @admin.display(description="Message")
    def body_preview(self, obj):
        text = " ".join((obj.body or "").split())
        return text if len(text) <= 90 else f"{text[:89]}…"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ChatReport)
class ChatReportAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "status_badge",
        "reason",
        "conversation",
        "reporter",
        "reported_user",
        "created_at",
    )
    search_fields = (
        "conversation__candidate__username",
        "conversation__candidate__email",
        "conversation__employer__username",
        "conversation__employer__email",
        "reporter__username",
        "reporter__email",
        "reported_user__username",
        "reported_user__email",
        "message",
    )
    list_filter = ("status", "reason", "created_at")
    ordering = ("-created_at", "-id")
    list_select_related = ("conversation", "reporter", "reported_user", "reported_message", "handled_by")
    raw_id_fields = ("conversation", "reporter", "reported_user", "reported_message", "handled_by")
    readonly_fields = ("created_at", "updated_at")
    actions = ("mark_in_review", "mark_resolved", "mark_rejected")

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        meta = {
            "new": ("New", "#B54708"),
            "in_review": ("In review", "#175CD3"),
            "resolved": ("Resolved", "#198754"),
            "rejected": ("Rejected", "#667085"),
        }.get(obj.status, ("Unknown", "#667085"))
        return _badge(meta[0], bg=meta[1])

    @admin.action(description="Mark selected reports as In review")
    def mark_in_review(self, request, queryset):
        queryset.update(status="in_review", handled_by=request.user, handled_at=timezone.now())

    @admin.action(description="Mark selected reports as Resolved")
    def mark_resolved(self, request, queryset):
        queryset.update(status="resolved", handled_by=request.user, handled_at=timezone.now())

    @admin.action(description="Mark selected reports as Rejected")
    def mark_rejected(self, request, queryset):
        queryset.update(status="rejected", handled_by=request.user, handled_at=timezone.now())


@admin.register(EmployerSubscription)
class EmployerSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("id", "subscriber", "employer", "created_at")
    search_fields = (
        "subscriber__username",
        "subscriber__email",
        "employer__username",
        "employer__email",
        "employer__profile__nickname",
    )
    list_filter = ("created_at",)
    ordering = ("-created_at",)
    list_select_related = ("subscriber", "employer", "employer__profile")


@admin.register(UnlockedContact)
class UnlockedContactAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "vacancy",
        "unlock_source",
        "charged_credits",
        "opened_at",
        "expires_at",
    )
    search_fields = ("user__username", "user__email", "vacancy__title")
    ordering = ("-opened_at",)
    raw_id_fields = ("user", "vacancy")


@admin.register(VacancyReview)
class VacancyReviewAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "reviewer",
        "employer",
        "vacancy",
        "rating",
        "preset_badges",
        "updated_at",
    )
    search_fields = (
        "reviewer__username",
        "reviewer__email",
        "employer__username",
        "employer__email",
        "vacancy__title",
    )
    list_filter = ("rating", "updated_at")
    ordering = ("-updated_at", "-id")
    raw_id_fields = ("reviewer", "employer", "vacancy")
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Presets")
    def preset_badges(self, obj):
        return _review_presets_html(list(getattr(obj, "preset_codes", []) or []))


@admin.register(UnlockRequest)
class UnlockRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "vacancy", "created_at", "expires_at", "state_badge")
    search_fields = ("user__username", "user__email", "vacancy__title", "token")
    ordering = ("-created_at",)
    raw_id_fields = ("user", "vacancy")

    @admin.display(description="State")
    def state_badge(self, obj):
        return _badge("Active", bg="#198754") if obj.is_valid() else _badge("Expired", bg="#667085")


@admin.register(EconomyConfig)
class EconomyConfigAdmin(admin.ModelAdmin):
    list_display = (
        "singleton_key",
        "vacancy_submit_price_credits",
        "vacancy_edit_resubmit_price_credits",
        "free_create_ad_submissions_limit",
        "free_edit_ad_resubmissions_limit",
        "employer_daily_free_submissions_limit",
        "seeker_contact_discount_percent",
        "contact_access_duration_minutes",
        "updated_at",
    )

    def has_add_permission(self, request):
        if EconomyConfig.objects.exists():
            return False
        return super().has_add_permission(request)


@admin.register(UserWallet)
class UserWalletAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "paid_credits",
        "bonus_credits",
        "total_credits_display",
        "updated_at",
    )
    search_fields = ("user__username", "user__email")
    ordering = ("-updated_at",)
    list_select_related = ("user",)
    fields = (
        "user",
        ("paid_credits", "bonus_credits"),
        ("lifetime_paid_credits", "lifetime_bonus_credits"),
        ("created_at", "updated_at"),
    )
    readonly_fields = ("lifetime_paid_credits", "lifetime_bonus_credits", "created_at", "updated_at")

    @admin.display(description="Total")
    def total_credits_display(self, obj):
        return obj.total_credits

    def save_model(self, request, obj, form, change):
        if not change:
            super().save_model(request, obj, form, change)
            return

        wallet, _ = set_wallet_balances(
            obj.user,
            paid_credits=obj.paid_credits,
            bonus_credits=obj.bonus_credits,
            note=f"Admin manual adjustment by {request.user.username}",
            metadata={
                "actor_user_id": request.user.id,
                "actor_username": request.user.username,
            },
        )
        obj.paid_credits = wallet.paid_credits
        obj.bonus_credits = wallet.bonus_credits
        obj.lifetime_paid_credits = wallet.lifetime_paid_credits
        obj.lifetime_bonus_credits = wallet.lifetime_bonus_credits
        obj.updated_at = wallet.updated_at


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "kind",
        "delta_paid_credits",
        "delta_bonus_credits",
        "balance_after_display",
        "related_vacancy",
        "created_at",
    )
    search_fields = (
        "user__username",
        "user__email",
        "note",
        "related_vacancy__title",
    )
    list_filter = ("kind", "created_at")
    ordering = ("-created_at", "-id")
    list_select_related = ("user", "wallet", "related_vacancy")
    raw_id_fields = ("user", "wallet", "related_vacancy")
    readonly_fields = ("created_at",)

    @admin.display(description="Balance after")
    def balance_after_display(self, obj):
        return format_html(
            "paid: {}<br><span style='color:#6B7280'>bonus: {}</span>",
            obj.balance_paid_after,
            obj.balance_bonus_after,
        )


@admin.register(StoreProduct)
class StoreProductAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "title",
        "product_type",
        "platform",
        "credit_amount",
        "duration_days",
        "price_label",
        "active_badge",
        "sort_order",
    )
    list_filter = ("product_type", "platform", "is_active")
    search_fields = ("code", "title", "store_product_id", "price_label")
    ordering = ("sort_order", "id")

    @admin.display(description="Active", ordering="is_active")
    def active_badge(self, obj):
        return _bool_badge(obj.is_active, "Active", "Inactive")


@admin.register(PurchaseRecord)
class PurchaseRecordAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "product",
        "platform",
        "status_badge",
        "credits_granted",
        "created_at",
        "validated_at",
    )
    list_filter = ("status", "platform", "product_type", "created_at")
    search_fields = (
        "user__username",
        "user__email",
        "external_transaction_id",
        "store_product_id",
        "purchase_token",
    )
    ordering = ("-created_at", "-id")
    list_select_related = ("user", "product")
    readonly_fields = ("created_at", "updated_at", "validated_at", "payload")
    raw_id_fields = ("user", "product")

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        meta = {
            "pending": ("Pending", "#B54708"),
            "validated": ("Validated", "#198754"),
            "rejected": ("Rejected", "#B42318"),
            "refunded": ("Refunded", "#7A2433"),
            "cancelled": ("Cancelled", "#667085"),
        }.get(obj.status, ("Unknown", "#667085"))
        return _badge(meta[0], bg=meta[1])


@admin.register(UserMonetizationProfile)
class UserMonetizationProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "employer_subscription_until",
        "seeker_subscription_until",
        "free_create_ad_submissions_used",
        "free_edit_ad_resubmissions_used",
        "employer_daily_submission_date",
        "employer_daily_submissions_used",
    )
    search_fields = ("user__username", "user__email")
    ordering = ("user__username",)
    list_select_related = ("user",)
    actions = (
        "activate_employer_subscription_30_days",
        "activate_seeker_subscription_30_days",
        "clear_all_subscriptions",
    )

    @admin.action(description="Activate employer subscription for 30 days")
    def activate_employer_subscription_30_days(self, request, queryset):
        now = timezone.now()
        updated = 0
        for profile in queryset:
            current_until = profile.employer_subscription_until
            baseline = current_until if current_until and current_until > now else now
            profile.employer_subscription_until = baseline + timedelta(days=30)
            profile.save(update_fields=["employer_subscription_until", "updated_at"])
            updated += 1
        self.message_user(request, f"Employer subscription extended for {updated} user(s).")

    @admin.action(description="Activate seeker subscription for 30 days")
    def activate_seeker_subscription_30_days(self, request, queryset):
        now = timezone.now()
        updated = 0
        for profile in queryset:
            current_until = profile.seeker_subscription_until
            baseline = current_until if current_until and current_until > now else now
            profile.seeker_subscription_until = baseline + timedelta(days=30)
            profile.save(update_fields=["seeker_subscription_until", "updated_at"])
            updated += 1
        self.message_user(request, f"Seeker subscription extended for {updated} user(s).")

    @admin.action(description="Clear employer and seeker subscriptions")
    def clear_all_subscriptions(self, request, queryset):
        updated = queryset.update(
            employer_subscription_until=None,
            seeker_subscription_until=None,
        )
        self.message_user(request, f"Cleared subscriptions for {updated} user(s).")


@admin.register(VacancyContactAccessPolicy)
class VacancyContactAccessPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "vacancy",
        "contact_unlock_mode",
        "contact_unlock_timer_hours",
        "contact_unlock_price_credits",
        "contact_unlock_paid_click_limit",
        "paid_click_progress",
        "earned_credits_display",
        "unique_users_display",
        "paid_window_started_at",
        "set_by",
        "set_at",
    )
    list_filter = (
        "contact_unlock_mode",
        "contact_unlock_timer_hours",
        "contact_unlock_price_credits",
        "contact_unlock_paid_click_limit",
    )
    search_fields = ("vacancy__title", "vacancy__id", "set_by__username", "set_by__email")
    ordering = ("-set_at",)
    list_select_related = ("vacancy", "set_by")
    fields = (
        "vacancy",
        ("contact_unlock_mode", "contact_unlock_timer_hours"),
        ("contact_unlock_price_credits", "contact_unlock_paid_click_limit"),
        "unlock_stats_summary",
        ("paid_window_started_at", "set_by", "set_at"),
    )
    readonly_fields = ("unlock_stats_summary", "paid_window_started_at", "set_at")

    @admin.display(description="Paid opens")
    def paid_click_progress(self, obj):
        stats = get_vacancy_contact_unlock_stats(obj.vacancy, policy=obj)
        paid_count = int(stats["paid_unlocks"] or 0)
        click_limit = getattr(obj, "contact_unlock_paid_click_limit", None)
        if click_limit:
            return _badge(
                f"{paid_count}/{int(click_limit)}",
                bg="#B42318" if paid_count >= int(click_limit) else "#175CD3",
            )
        return _badge(str(paid_count), bg="#175CD3")

    @admin.display(description="Credits earned")
    def earned_credits_display(self, obj):
        stats = get_vacancy_contact_unlock_stats(obj.vacancy, policy=obj)
        return stats["earned_credits"]

    @admin.display(description="Unique users")
    def unique_users_display(self, obj):
        stats = get_vacancy_contact_unlock_stats(obj.vacancy, policy=obj)
        return stats["unique_users"]

    @admin.display(description="Unlock stats")
    def unlock_stats_summary(self, obj):
        return _contact_unlock_stats_summary(obj)


@admin.register(PushDevice)
class PushDeviceAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "platform", "active_badge", "last_seen_at")
    search_fields = ("user__username", "user__email", "token")
    list_filter = ("platform", "is_active", "last_seen_at")
    ordering = ("-last_seen_at",)

    @admin.display(description="Active", ordering="is_active")
    def active_badge(self, obj):
        return _bool_badge(obj.is_active, "Active", "Inactive")


@admin.register(VacancyAlertSubscription)
class VacancyAlertSubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "enabled_badge",
        "country",
        "city",
        "category",
        "employment_type",
        "housing_type",
        "updated_at",
    )
    search_fields = ("user__username", "user__email", "city")
    list_filter = ("enabled", "country", "category", "employment_type", "housing_type")
    ordering = ("-updated_at",)

    @admin.display(description="Enabled", ordering="enabled")
    def enabled_badge(self, obj):
        return _bool_badge(obj.enabled, "Enabled", "Disabled")


@admin.register(VacancyAlertDelivery)
class VacancyAlertDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "vacancy",
        "status_badge",
        "device_platform",
        "created_at",
    )
    search_fields = ("user__username", "user__email", "vacancy__title", "provider_message_id")
    list_filter = ("status", "device_platform", "created_at")
    ordering = ("-created_at",)

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        meta = {
            "sent": ("Sent", "#198754"),
            "failed": ("Failed", "#B42318"),
            "skipped_no_device": ("No device", "#667085"),
            "skipped_not_configured": ("Not configured", "#7A5AF8"),
        }.get(obj.status, ("Unknown", "#667085"))
        return _badge(meta[0], bg=meta[1])


@admin.register(ModeratorNotificationDelivery)
class ModeratorNotificationDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "vacancy",
        "kind",
        "status_badge",
        "device_platform",
        "created_at",
    )
    search_fields = ("user__username", "user__email", "vacancy__title", "provider_message_id")
    list_filter = ("kind", "status", "device_platform", "created_at")
    ordering = ("-created_at",)

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        meta = {
            "sent": ("Sent", "#198754"),
            "failed": ("Failed", "#B42318"),
            "skipped_no_device": ("No device", "#667085"),
            "skipped_not_configured": ("Not configured", "#7A5AF8"),
        }.get(obj.status, ("Unknown", "#667085"))
        return _badge(meta[0], bg=meta[1])
