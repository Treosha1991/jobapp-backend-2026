from rest_framework import serializers
import re
from .models import Vacancy


class VacancyListSerializer(serializers.ModelSerializer):
    contacts = serializers.SerializerMethodField()

    class Meta:
        model = Vacancy
        fields = [
            "id",
            "title",
            "country",
            "city",
            "category",
            "employment_type",
            "experience_required",
            "salary",
            "description",
            "housing_type",
            "housing_cost",
            "contacts",
            "published_at",
            "expires_at",
        ]

    def get_contacts(self, obj):
        return {
            "phone": obj.phone or "",
            "telegram": obj.telegram or "",
            "whatsapp": obj.whatsapp or "",
            "email": obj.email or "",
            "viber": obj.viber or "",
        }

class VacancyDetailSerializer(serializers.ModelSerializer):
    contacts = serializers.SerializerMethodField()

    class Meta:
        model = Vacancy
        fields = [
            "id",
            "title",
            "country",
            "city",
            "category",
            "employment_type",
            "experience_required",
            "salary",
            "description",
            "housing_type",
            "housing_cost",
            "contacts",
            "published_at",
            "expires_at",
        ]

    def get_contacts(self, obj):
        return {
            "phone": obj.phone or "",
            "telegram": obj.telegram or "",
            "whatsapp": obj.whatsapp or "",
            "email": obj.email or "",
            "viber": obj.viber or "",
        }

class VacancyCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vacancy
        fields = [
            "title",
            "country",
            "city",
            "category",
            "employment_type",
            "experience_required",
            "salary",
            "description",
            "housing_type",
            "housing_cost",
            "phone",
            "whatsapp",
            "viber",
            "telegram",
            "email",
            "source",
            "creator_token",
        ]
        read_only_fields = ["creator_token"]

    def validate(self, attrs):
        errors = {}

        def _check_len(field, max_len):
            val = attrs.get(field)
            if val is None:
                return
            if isinstance(val, str) and len(val) > max_len:
                errors[field] = f"max {max_len} chars"

        _check_len("title", 30)
        _check_len("city", 20)
        _check_len("salary", 20)
        _check_len("phone", 15)
        _check_len("telegram", 15)
        _check_len("whatsapp", 15)
        _check_len("viber", 15)
        _check_len("email", 30)

        desc = attrs.get("description")
        if desc is not None:
            if len(desc) > 300:
                errors["description"] = "max 300 chars"
            else:
                lines = re.split(r"\r?\n", desc)
                if len(lines) > 50:
                    errors["description"] = "max 50 lines"

        contact_pattern = re.compile(r"^[0-9+()\-\ ]+$")
        for field in ("phone", "telegram", "whatsapp", "viber"):
            val = attrs.get(field)
            if val:
                if not contact_pattern.match(val):
                    errors[field] = "only digits and symbols"

        email = attrs.get("email")
        if email:
            if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
                errors["email"] = "invalid email"

        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class VacancyMineSerializer(serializers.ModelSerializer):
    contacts = serializers.SerializerMethodField()
    moderation_status = serializers.SerializerMethodField()
    bucket = serializers.SerializerMethodField()
    status_label_key = serializers.SerializerMethodField()
    rejection_reason_code = serializers.SerializerMethodField()
    rejection_reason_comment = serializers.SerializerMethodField()

    class Meta:
        model = Vacancy
        fields = [
            "id",
            "title",
            "country",
            "city",
            "category",
            "employment_type",
            "experience_required",
            "salary",
            "description",
            "housing_type",
            "housing_cost",
            "contacts",
            "published_at",
            "expires_at",
            "is_approved",
            "is_rejected",
            "rejection_reason",
            "is_editing",
            "editing_started_at",
            "moderation_status",
            "bucket",
            "status_label_key",
            "rejection_reason_code",
            "rejection_reason_comment",
        ]

    def get_contacts(self, obj):
        return {
            "phone": obj.phone or "",
            "telegram": obj.telegram or "",
            "whatsapp": obj.whatsapp or "",
            "email": obj.email or "",
            "viber": obj.viber or "",
        }

    def get_moderation_status(self, obj):
        return obj.moderation_status

    def get_bucket(self, obj):
        if obj.is_approved:
            return "approved"
        if obj.is_rejected:
            return "rejected"
        return "pending"

    def get_status_label_key(self, obj):
        if obj.is_editing:
            return "statusEditing"
        if obj.is_approved:
            return "statusApproved"
        if obj.is_rejected:
            return "statusRejected"
        return "statusPending"

    def get_rejection_reason_code(self, obj):
        raw = (obj.rejection_reason or "").strip()
        if not raw:
            return ""
        parts = raw.split(":", 1)
        return parts[0].strip()

    def get_rejection_reason_comment(self, obj):
        raw = (obj.rejection_reason or "").strip()
        if not raw or ":" not in raw:
            return ""
        return raw.split(":", 1)[1].strip()

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

