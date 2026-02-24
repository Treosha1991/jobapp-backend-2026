from rest_framework import serializers
import re
from .avatar_utils import avatar_public_url
from .models import Complaint, Vacancy


def _to_int_or_none(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _salary_monthly_from(obj):
    salary_from = _to_int_or_none(getattr(obj, "salary_from", None))
    hours = _to_int_or_none(getattr(obj, "salary_hours_month", None))
    if salary_from is None or hours is None:
        return None
    return salary_from * hours


def _salary_monthly_to(obj):
    salary_to = _to_int_or_none(getattr(obj, "salary_to", None))
    hours = _to_int_or_none(getattr(obj, "salary_hours_month", None))
    if salary_to is None or hours is None:
        return None
    return salary_to * hours


_MODERATION_COMPARISON_FIELDS = [
    "title",
    "country",
    "city",
    "category",
    "employment_type",
    "experience_required",
    "salary_from",
    "salary_to",
    "salary_currency",
    "salary_tax_type",
    "salary_hours_month",
    "description",
    "housing_type",
    "housing_cost",
    "phone",
    "additional_phone",
    "hide_primary_phone",
    "whatsapp",
    "viber",
    "telegram",
    "email",
    "source",
]


def _normalize_compare_value(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return value
    return str(value).strip()


def _creator_nickname(obj):
    creator = getattr(obj, "created_by", None)
    if not creator:
        return ""
    try:
        profile = creator.profile
    except Exception:
        profile = None
    return (getattr(profile, "nickname", "") or "").strip()

def _creator_display_name(obj):
    creator = getattr(obj, "created_by", None)
    if not creator:
        return ""
    nickname = _creator_nickname(obj)
    if nickname:
        return nickname
    return f"Employer #{creator.id}"


def _creator_avatar_url(obj):
    creator = getattr(obj, "created_by", None)
    if not creator:
        return ""
    try:
        profile = creator.profile
    except Exception:
        profile = None
    avatar_key = (getattr(profile, "avatar_key", "") or "").strip()
    return avatar_public_url(avatar_key)


def _contact_payload(obj):
    primary_phone = (obj.phone or "").strip()
    additional_phone = (getattr(obj, "additional_phone", "") or "").strip()
    hide_primary_phone = bool(getattr(obj, "hide_primary_phone", False))
    public_phone = additional_phone if hide_primary_phone else primary_phone
    raw_whatsapp = (obj.whatsapp or "").strip()
    raw_viber = (obj.viber or "").strip()
    raw_telegram = (obj.telegram or "").strip()
    public_whatsapp = additional_phone if hide_primary_phone and raw_whatsapp else raw_whatsapp
    public_viber = additional_phone if hide_primary_phone and raw_viber else raw_viber
    public_telegram = additional_phone if hide_primary_phone and raw_telegram else raw_telegram
    return {
        "owner_user_id": getattr(getattr(obj, "created_by", None), "id", None),
        "owner_nickname": _creator_display_name(obj),
        "owner_avatar_url": _creator_avatar_url(obj),
        # compatibility with existing mobile fields
        "nickname": _creator_nickname(obj),
        "phone": primary_phone,
        "additional_phone": additional_phone,
        "hide_primary_phone": hide_primary_phone,
        "public_phone": public_phone,
        "telegram": raw_telegram,
        "whatsapp": raw_whatsapp,
        "email": obj.email or "",
        "viber": raw_viber,
        "public_telegram": public_telegram,
        "public_whatsapp": public_whatsapp,
        "public_viber": public_viber,
    }


class VacancyListSerializer(serializers.ModelSerializer):
    contacts = serializers.SerializerMethodField()
    salary_monthly_from = serializers.SerializerMethodField()
    salary_monthly_to = serializers.SerializerMethodField()
    is_resubmitted = serializers.SerializerMethodField()

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
            "salary_from",
            "salary_to",
            "salary_currency",
            "salary_tax_type",
            "salary_hours_month",
            "salary_monthly_from",
            "salary_monthly_to",
            "description",
            "housing_type",
            "housing_cost",
            "contacts",
            "published_at",
            "expires_at",
            "is_resubmitted",
        ]

    def get_contacts(self, obj):
        return _contact_payload(obj)

    def get_salary_monthly_from(self, obj):
        return _salary_monthly_from(obj)

    def get_salary_monthly_to(self, obj):
        return _salary_monthly_to(obj)

    def get_is_resubmitted(self, obj):
        return (getattr(obj, "revision", 1) or 1) > 1


class VacancyModerationSerializer(VacancyListSerializer):
    previous_rejection_reason = serializers.CharField(source="last_moderator_rejection_reason", read_only=True)
    resubmitted_changed_fields = serializers.SerializerMethodField()

    class Meta(VacancyListSerializer.Meta):
        fields = VacancyListSerializer.Meta.fields + [
            "previous_rejection_reason",
            "resubmitted_changed_fields",
        ]

    def get_resubmitted_changed_fields(self, obj):
        baseline = getattr(obj, "moderation_baseline", None) or {}
        if not isinstance(baseline, dict):
            return []
        if not baseline:
            return []
        if not any(field in baseline for field in _MODERATION_COMPARISON_FIELDS):
            return []

        changed = []
        for field in _MODERATION_COMPARISON_FIELDS:
            baseline_value = _normalize_compare_value(baseline.get(field))
            current_value = _normalize_compare_value(getattr(obj, field, None))
            if baseline_value != current_value:
                changed.append(field)
        return changed

class VacancyDetailSerializer(serializers.ModelSerializer):
    contacts = serializers.SerializerMethodField()
    salary_monthly_from = serializers.SerializerMethodField()
    salary_monthly_to = serializers.SerializerMethodField()

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
            "salary_from",
            "salary_to",
            "salary_currency",
            "salary_tax_type",
            "salary_hours_month",
            "salary_monthly_from",
            "salary_monthly_to",
            "description",
            "housing_type",
            "housing_cost",
            "contacts",
            "published_at",
            "expires_at",
        ]

    def get_contacts(self, obj):
        return _contact_payload(obj)

    def get_salary_monthly_from(self, obj):
        return _salary_monthly_from(obj)

    def get_salary_monthly_to(self, obj):
        return _salary_monthly_to(obj)

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
            "salary_from",
            "salary_to",
            "salary_currency",
            "salary_tax_type",
            "salary_hours_month",
            "description",
            "housing_type",
            "housing_cost",
            "phone",
            "additional_phone",
            "hide_primary_phone",
            "whatsapp",
            "viber",
            "telegram",
            "email",
            "source",
            "creator_token",
        ]
        read_only_fields = ["creator_token"]
        extra_kwargs = {
            "salary": {"required": False, "allow_blank": True},
            "salary_from": {"required": False, "allow_null": True},
            "salary_to": {"required": False, "allow_null": True},
            "salary_currency": {"required": False, "allow_blank": True},
            "salary_tax_type": {"required": False, "allow_blank": True},
            "salary_hours_month": {"required": False, "allow_null": True},
            "additional_phone": {"required": False, "allow_blank": True},
            "hide_primary_phone": {"required": False},
        }

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
        _check_len("salary", 80)
        _check_len("phone", 15)
        _check_len("additional_phone", 15)
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
        for field in ("phone", "additional_phone", "telegram", "whatsapp", "viber"):
            val = attrs.get(field)
            if val:
                if not contact_pattern.match(val):
                    errors[field] = "only digits and symbols"

        primary_phone = (attrs.get("phone") or "").strip()
        additional_phone = (attrs.get("additional_phone") or "").strip()
        hide_primary_phone = bool(attrs.get("hide_primary_phone"))
        public_phone = additional_phone if hide_primary_phone else primary_phone

        if hide_primary_phone and not additional_phone:
            errors["additional_phone"] = "required when primary phone is hidden"

        email = (attrs.get("email") or "").strip()
        if email:
            if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
                errors["email"] = "invalid email"

        if not public_phone and not email:
            errors["contacts"] = "provide at least one contact"

        salary_from = attrs.get("salary_from")
        salary_to = attrs.get("salary_to")
        salary_currency = (attrs.get("salary_currency") or "").strip()
        salary_tax_type = (attrs.get("salary_tax_type") or "").strip()
        salary_hours_month = attrs.get("salary_hours_month")
        salary_text = (attrs.get("salary") or "").strip()

        structured_used = any(
            x not in (None, "", [])
            for x in [salary_from, salary_to, salary_currency, salary_tax_type, salary_hours_month]
        )

        if structured_used:
            if salary_from is None and salary_to is None:
                errors["salary_from"] = "required salary from/to"

            if salary_from is not None and (salary_from < 1 or salary_from > 99):
                errors["salary_from"] = "must be in range 1..99"
            if salary_to is not None and (salary_to < 1 or salary_to > 99):
                errors["salary_to"] = "must be in range 1..99"
            if salary_from is not None and salary_to is not None and salary_from > salary_to:
                errors["salary_to"] = "must be greater or equal salary_from"

            if not salary_currency:
                errors["salary_currency"] = "required"
            if not salary_tax_type:
                errors["salary_tax_type"] = "required"

            if salary_hours_month is None:
                errors["salary_hours_month"] = "required"
            elif salary_hours_month < 1 or salary_hours_month > 300:
                errors["salary_hours_month"] = "must be in range 1..300"

            if not errors:
                if salary_from is not None and salary_to is not None:
                    range_text = f"from {salary_from} to {salary_to}"
                elif salary_from is not None:
                    range_text = f"from {salary_from}"
                else:
                    range_text = f"to {salary_to}"
                attrs["salary"] = f"{range_text} {salary_currency} {salary_tax_type}"
        else:
            if not salary_text:
                errors["salary"] = "required"

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
    salary_monthly_from = serializers.SerializerMethodField()
    salary_monthly_to = serializers.SerializerMethodField()

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
            "salary_from",
            "salary_to",
            "salary_currency",
            "salary_tax_type",
            "salary_hours_month",
            "salary_monthly_from",
            "salary_monthly_to",
            "description",
            "housing_type",
            "housing_cost",
            "contacts",
            "published_at",
            "expires_at",
            "is_approved",
            "is_rejected",
            "rejection_reason",
            "is_paused_by_owner",
            "paused_by_owner_at",
            "is_editing",
            "editing_started_at",
            "moderation_status",
            "bucket",
            "status_label_key",
            "rejection_reason_code",
            "rejection_reason_comment",
        ]

    def get_contacts(self, obj):
        return _contact_payload(obj)

    def get_salary_monthly_from(self, obj):
        return _salary_monthly_from(obj)

    def get_salary_monthly_to(self, obj):
        return _salary_monthly_to(obj)

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
        if obj.is_approved and obj.is_paused_by_owner:
            return "statusPaused"
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
    nickname = serializers.SerializerMethodField()
    owner_user_id = serializers.SerializerMethodField()
    owner_nickname = serializers.SerializerMethodField()
    owner_avatar_url = serializers.SerializerMethodField()
    phone = serializers.SerializerMethodField()
    whatsapp = serializers.SerializerMethodField()
    viber = serializers.SerializerMethodField()
    telegram = serializers.SerializerMethodField()
    additional_phone = serializers.SerializerMethodField()
    hide_primary_phone = serializers.SerializerMethodField()

    class Meta:
        model = Vacancy
        fields = [
            "owner_user_id",
            "owner_nickname",
            "owner_avatar_url",
            "nickname",
            "phone",
            "additional_phone",
            "hide_primary_phone",
            "whatsapp",
            "viber",
            "telegram",
            "email",
        ]

    def get_owner_user_id(self, obj):
        return getattr(obj.created_by, "id", None)

    def get_owner_nickname(self, obj):
        return _creator_display_name(obj)

    def get_owner_avatar_url(self, obj):
        return _creator_avatar_url(obj)

    def get_nickname(self, obj):
        return _creator_display_name(obj)

    def get_hide_primary_phone(self, obj):
        return bool(getattr(obj, "hide_primary_phone", False))

    def get_additional_phone(self, obj):
        return (getattr(obj, "additional_phone", "") or "").strip()

    def get_phone(self, obj):
        primary = (obj.phone or "").strip()
        additional = self.get_additional_phone(obj)
        if self.get_hide_primary_phone(obj):
            return additional
        return primary

    def _public_messenger(self, obj, raw_value):
        raw = (raw_value or "").strip()
        if not raw:
            return ""
        if self.get_hide_primary_phone(obj):
            return self.get_additional_phone(obj)
        return raw

    def get_whatsapp(self, obj):
        return self._public_messenger(obj, obj.whatsapp)

    def get_viber(self, obj):
        return self._public_messenger(obj, obj.viber)

    def get_telegram(self, obj):
        return self._public_messenger(obj, obj.telegram)


class ComplaintListSerializer(serializers.ModelSerializer):
    vacancy_title = serializers.CharField(source="vacancy.title", read_only=True)
    reporter_email = serializers.SerializerMethodField()
    handled_by_email = serializers.SerializerMethodField()

    class Meta:
        model = Complaint
        fields = [
            "id",
            "vacancy_id",
            "vacancy_title",
            "reporter_email",
            "reason",
            "message",
            "status",
            "resolution_note",
            "created_at",
            "updated_at",
            "handled_by_email",
            "handled_at",
        ]

    def get_reporter_email(self, obj):
        return obj.reporter.email or obj.reporter.username

    def get_handled_by_email(self, obj):
        if not obj.handled_by:
            return ""
        return obj.handled_by.email or obj.handled_by.username

