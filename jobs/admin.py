from django.contrib import admin
from .models import Vacancy, UserProfile, PhoneVerification, EmailVerification


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
