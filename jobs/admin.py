from django.contrib import admin
from django.contrib.admin.sites import NotRegistered
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.db.models import Count
from django.utils import timezone
from django.utils.html import format_html

from .models import (
    AccountDeletionRequest,
    Complaint,
    ComplaintActionLog,
    EmailVerification,
    EmployerSubscription,
    PhoneVerification,
    PushDevice,
    UnlockedContact,
    UnlockRequest,
    UserBlock,
    UserProfile,
    Vacancy,
    VacancyAlertDelivery,
    VacancyAlertSubscription,
    VacancyModerationAttempt,
)


admin.site.site_header = "JobHub Operator Console"
admin.site.site_title = "JobHub Admin"
admin.site.index_title = "Moderation and support panel"
admin.site.empty_value_display = "—"


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


def _bool_badge(value, true_label="Yes", false_label="No"):
    return _badge(
        true_label if value else false_label,
        bg="#198754" if value else "#6C757D",
    )


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
        "avatar_key",
        "avatar_updated_at",
    )
    readonly_fields = ("phone_verified_at", "avatar_updated_at")


try:
    admin.site.unregister(User)
except NotRegistered:
    pass


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)
    list_display = (
        "username",
        "display_name",
        "email_with_phone",
        "role_badges",
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
        return super().get_queryset(request).select_related("profile")

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
        return format_html(
            "<strong>{}</strong><br><span style='color:#6B7280'>{}</span>",
            email,
            phone,
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


@admin.register(Vacancy)
class VacancyAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "status_badge",
        "owner_display",
        "location_display",
        "category",
        "salary_preview",
        "complaints_total",
        "published_at",
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
    date_hierarchy = "published_at"
    list_per_page = 40
    readonly_fields = (
        "status_badge",
        "owner_display",
        "complaints_total",
        "published_at",
        "revision",
        "paused_by_owner_at",
        "editing_started_at",
        "deleted_by_moderator_at",
        "last_owner_resume_at",
    )
    fieldsets = (
        (
            "Main",
            {
                "fields": (
                    ("title", "status_badge"),
                    ("owner_display", "source", "revision"),
                    ("country", "city", "city_code"),
                    ("category", "employment_type", "experience_required"),
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
                    ("phone", "additional_phone", "hide_primary_phone"),
                    ("whatsapp", "viber", "telegram"),
                    "email",
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
                    ("published_at", "expires_at"),
                    ("paused_by_owner_at", "editing_started_at"),
                    ("last_owner_resume_at", "owner_resume_day", "owner_resume_count_day"),
                    "moderation_baseline",
                    "moderator_deleted_state",
                ),
            },
        ),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("created_by", "created_by__profile")
            .annotate(_complaints_total=Count("complaints"))
        )

    @admin.display(description="Status")
    def status_badge(self, obj):
        label, color = _vacancy_status_meta(obj)
        return _badge(label, bg=color)

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
        "has_avatar_badge",
        "avatar_updated_at",
    )
    search_fields = ("user__username", "user__email", "nickname", "phone_e164")
    list_filter = ("phone_verified",)
    list_select_related = ("user",)
    ordering = ("user__username",)

    @admin.display(description="Nickname", ordering="nickname")
    def nickname_display(self, obj):
        return obj.nickname or _muted("No nickname")

    @admin.display(description="Phone")
    def phone_verified_badge(self, obj):
        return _bool_badge(obj.phone_verified, "Verified", "Not verified")

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
    list_display = ("id", "user", "vacancy", "opened_at")
    search_fields = ("user__username", "user__email", "vacancy__title")
    ordering = ("-opened_at",)
    raw_id_fields = ("user", "vacancy")


@admin.register(UnlockRequest)
class UnlockRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "vacancy", "created_at", "expires_at", "state_badge")
    search_fields = ("user__username", "user__email", "vacancy__title", "token")
    ordering = ("-created_at",)
    raw_id_fields = ("user", "vacancy")

    @admin.display(description="State")
    def state_badge(self, obj):
        return _badge("Active", bg="#198754") if obj.is_valid() else _badge("Expired", bg="#667085")


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
