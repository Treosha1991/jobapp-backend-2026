from django.contrib import admin
from .models import Vacancy


@admin.register(Vacancy)
class VacancyAdmin(admin.ModelAdmin):
    list_display = ("title", "country", "city", "category", "employment_type", "housing_type", "is_approved", "expires_at")
    list_filter = ("country", "category", "employment_type", "housing_type", "is_approved")
    search_fields = ("title", "city")
