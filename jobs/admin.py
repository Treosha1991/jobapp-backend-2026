from django.contrib import admin
from django.contrib.admin.sites import NotRegistered
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import Vacancy, UserProfile, PhoneVerification, EmailVerification


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
