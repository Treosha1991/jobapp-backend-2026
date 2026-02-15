from django.contrib import admin
from django.contrib.admin.sites import NotRegistered
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.utils import timezone
from .models import AccountDeletionRequest, Complaint, ComplaintActionLog, Vacancy, UserProfile, PhoneVerification, EmailVerification


try:
    admin.site.unregister(User)
except NotRegistered:
    pass


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "email_with_phone", "first_name", "last_name", "is_staff")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("profile")

    @admin.display(description="Email / Phone")
    def email_with_phone(self, obj):
        profile = getattr(obj, "profile", None)
        phone = profile.phone_e164 if profile and profile.phone_e164 else "-"
        email = obj.email or "-"
        return f"{email} | {phone}"


@admin.register(Vacancy)
class VacancyAdmin(admin.ModelAdmin):
    list_display = ("title", "country", "city", "category", "employment_type", "housing_type", "is_approved", "expires_at")
    list_filter = ("country", "category", "employment_type", "housing_type", "is_approved")
    search_fields = ("title", "city")


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "phone_e164", "phone_verified", "phone_verified_at")
    search_fields = ("user__username", "user__email", "phone_e164")
    list_filter = ("phone_verified",)


@admin.register(PhoneVerification)
class PhoneVerificationAdmin(admin.ModelAdmin):
    list_display = ("phone_e164", "purpose", "user", "is_used", "attempts", "expires_at")
    search_fields = ("phone_e164", "user__username", "user__email")
    list_filter = ("purpose", "is_used")


@admin.register(EmailVerification)
class EmailVerificationAdmin(admin.ModelAdmin):
    list_display = ("user", "purpose", "is_used", "expires_at", "created_at")
    search_fields = ("user__username", "user__email")
    list_filter = ("purpose", "is_used")


@admin.register(Complaint)
class ComplaintAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "vacancy_id_display",
        "vacancy_title",
        "reporter_email",
        "reason",
        "status",
        "created_at",
        "handled_by",
        "handled_at",
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
    list_select_related = ("vacancy", "reporter", "handled_by")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("vacancy", "reporter", "handled_by")
    actions = ("mark_in_review", "mark_resolved", "mark_rejected")

    @admin.display(description="Vacancy ID")
    def vacancy_id_display(self, obj):
        return obj.vacancy_id

    @admin.display(description="Vacancy")
    def vacancy_title(self, obj):
        return obj.vacancy.title

    @admin.display(description="Reporter")
    def reporter_email(self, obj):
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
        "action",
        "vacancy_id_display",
        "complaint_id_display",
        "actor",
        "created_at",
    )
    list_filter = ("action", "created_at")
    search_fields = ("vacancy__id", "vacancy__title", "complaint__id", "actor__email", "actor__username", "note")
    ordering = ("-created_at",)
    list_select_related = ("vacancy", "complaint", "actor")
    readonly_fields = ("created_at", "before_state", "after_state")
    raw_id_fields = ("vacancy", "complaint", "actor")

    @admin.display(description="Vacancy ID")
    def vacancy_id_display(self, obj):
        return obj.vacancy_id

    @admin.display(description="Complaint ID")
    def complaint_id_display(self, obj):
        return obj.complaint_id


@admin.register(AccountDeletionRequest)
class AccountDeletionRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "user_id_snapshot", "email_snapshot", "status", "confirmed_via", "requested_at", "execute_after", "processed_at")
    list_filter = ("status", "confirmed_via", "requested_at", "execute_after")
    search_fields = ("user_id_snapshot", "email_snapshot")
    ordering = ("-requested_at",)
    readonly_fields = ("requested_at", "processed_at", "user_id_snapshot", "email_snapshot")
