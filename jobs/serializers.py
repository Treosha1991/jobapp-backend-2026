from rest_framework import serializers
from .models import Vacancy


class VacancyListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vacancy
        fields = [
            "id",
            "title",
            "country",
            "city",
            "category",
            "employment_type",
            "salary",
            "description",
            "published_at",
            "expires_at",
        ]


class VacancyDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vacancy
        fields = [
            "id",
            "title",
            "country",
            "city",
            "category",
            "employment_type",
            "salary",
            "description",
            "published_at",
            "expires_at",
        ]


class VacancyCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vacancy
        fields = [
            "title",
            "country",
            "city",
            "category",
            "employment_type",
            "salary",
            "description",
            "phone",
            "whatsapp",
            "viber",
            "telegram",
            "email",
            "source",
        ]

class VacancyContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vacancy
        fields = [
            "phone",
            "whatsapp",
            "viber",
            "telegram",
            "email",
        ]
