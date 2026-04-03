from rest_framework import serializers
import re
from .avatar_utils import avatar_public_url
from .country_choices import (
    MAX_AUDIENCE_COUNTRY_SELECTIONS,
    MIN_AUDIENCE_COUNTRY_SELECTIONS,
    decode_audience_country_codes,
    encode_audience_country_codes,
)
from .driver_licenses import (
    decode_driver_license_categories,
    encode_driver_license_categories,
    MAX_DRIVER_LICENSE_SELECTIONS,
)
from .models import (
    Complaint,
    EconomyConfig,
    EmployerSubscription,
    PushDevice,
    StoreProduct,
    UserMonetizationProfile,
    UserWallet,
    Vacancy,
    VacancyAlertSubscription,
    WalletTransaction,
    VacancyModerationAttempt,
)
from .service_sources import service_board_meta_for_user
from .economy import is_employer_profile_visible_for_vacancy
from .text_filters import censor_minimal, contains_link


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
    "city_code",
    "category",
    "audience_country_codes",
    "employment_type",
    "experience_required",
    "driver_license_categories",
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


class DriverLicenseCategoriesField(serializers.Field):
    default_error_messages = {
        "invalid": "invalid_driver_license_categories",
        "too_many": "too_many_driver_license_categories",
    }

    def __init__(self, *args, max_selections=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_selections = max_selections

    def to_representation(self, value):
        return decode_driver_license_categories(value)

    def to_internal_value(self, data):
        if data in (None, "", []):
            return ""
        if not isinstance(data, list):
            self.fail("invalid")
        try:
            return encode_driver_license_categories(
                data,
                max_selections=self.max_selections,
            )
        except ValueError as exc:
            if str(exc) == "too_many_driver_license_categories":
                self.fail("too_many")
            self.fail("invalid")


class AudienceCountriesField(serializers.Field):
    default_error_messages = {
        "invalid": "invalid_audience_countries",
        "too_many": "too_many_audience_countries",
        "too_few": "audience_countries_required",
    }

    def __init__(
        self,
        *args,
        min_selections=MIN_AUDIENCE_COUNTRY_SELECTIONS,
        max_selections=MAX_AUDIENCE_COUNTRY_SELECTIONS,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.min_selections = min_selections
        self.max_selections = max_selections

    def to_representation(self, value):
        return decode_audience_country_codes(value)

    def to_internal_value(self, data):
        if data in (None, "", []):
            if self.required:
                self.fail("too_few")
            return ""
        if not isinstance(data, list):
            self.fail("invalid")
        try:
            return encode_audience_country_codes(
                data,
                min_selections=self.min_selections,
                max_selections=self.max_selections,
            )
        except ValueError as exc:
            message = str(exc)
            if message == "too_many_audience_countries":
                self.fail("too_many")
            if message == "too_few_audience_countries":
                self.fail("too_few")
            self.fail("invalid")


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


def _service_board_meta(obj):
    creator = getattr(obj, "created_by", None)
    return service_board_meta_for_user(creator)


def _user_display_name(user):
    if not user:
        return ""
    full_name = (getattr(user, "get_full_name", lambda: "")() or "").strip()
    if full_name:
        return full_name
    username = (getattr(user, "username", "") or "").strip()
    if username:
        return username
    return f"User #{getattr(user, 'id', '?')}"


def _moderation_attempt_counts(obj):
    attempts = getattr(obj, "moderation_attempts", None)
    if attempts is not None and hasattr(attempts, "all"):
        items = list(attempts.all())
        return {
            "total": len(items),
            "approved": sum(1 for item in items if item.decision == "approved"),
            "rejected": sum(1 for item in items if item.decision == "rejected"),
        }

    return {
        "total": int(getattr(obj, "moderation_attempts_total", 0) or 0),
        "approved": int(getattr(obj, "moderation_approved_total", 0) or 0),
        "rejected": int(getattr(obj, "moderation_rejected_total", 0) or 0),
    }


def _contact_payload(obj, *, public_only=False):
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
    payload = {
        "owner_user_id": getattr(getattr(obj, "created_by", None), "id", None),
        "owner_nickname": _creator_display_name(obj),
        "owner_avatar_url": _creator_avatar_url(obj),
        # compatibility with existing mobile fields
        "nickname": _creator_nickname(obj),
        "phone": public_phone if public_only else primary_phone,
        "additional_phone": additional_phone if not public_only else "",
        "hide_primary_phone": hide_primary_phone,
        "public_phone": public_phone,
        "telegram": public_telegram if public_only else raw_telegram,
        "whatsapp": public_whatsapp if public_only else raw_whatsapp,
        "email": "" if public_only else (obj.email or ""),
        "viber": public_viber if public_only else raw_viber,
        "public_telegram": public_telegram,
        "public_whatsapp": public_whatsapp,
        "public_viber": public_viber,
    }
    payload.update(_service_board_meta(obj))
    return payload


class VacancyListSerializer(serializers.ModelSerializer):
    contacts = serializers.SerializerMethodField()
    salary_monthly_from = serializers.SerializerMethodField()
    salary_monthly_to = serializers.SerializerMethodField()
    audience_countries = AudienceCountriesField(
        source="audience_country_codes",
        read_only=True,
        required=False,
        min_selections=0,
    )
    driver_license_categories = DriverLicenseCategoriesField(read_only=True)
    is_resubmitted = serializers.SerializerMethodField()
    is_owner_subscribed = serializers.SerializerMethodField()
    show_employer_profile = serializers.SerializerMethodField()
    is_service_board = serializers.SerializerMethodField()
    service_board_kind = serializers.SerializerMethodField()

    class Meta:
        model = Vacancy
        fields = [
            "id",
            "title",
            "country",
            "city",
            "city_code",
            "category",
            "audience_countries",
            "employment_type",
            "experience_required",
            "driver_license_categories",
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
            "source",
            "contacts",
            "published_at",
            "expires_at",
            "is_resubmitted",
            "is_owner_subscribed",
            "show_employer_profile",
            "is_service_board",
            "service_board_kind",
        ]

    def get_contacts(self, obj):
        return _contact_payload(obj, public_only=True)

    def get_salary_monthly_from(self, obj):
        return _salary_monthly_from(obj)

    def get_salary_monthly_to(self, obj):
        return _salary_monthly_to(obj)

    def get_is_resubmitted(self, obj):
        return (getattr(obj, "revision", 1) or 1) > 1

    def get_is_owner_subscribed(self, obj):
        annotated = getattr(obj, "is_owner_subscribed", None)
        if annotated is not None:
            return bool(annotated)

        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            return False

        owner_id = getattr(obj, "created_by_id", None)
        if not owner_id:
            return False

        return EmployerSubscription.objects.filter(
            subscriber=user,
            employer_id=owner_id,
        ).exists()

    def get_show_employer_profile(self, obj):
        return is_employer_profile_visible_for_vacancy(obj)

    def get_is_service_board(self, obj):
        return _service_board_meta(obj)["is_service_board"]

    def get_service_board_kind(self, obj):
        return _service_board_meta(obj)["service_board_kind"]


class VacancyModerationSerializer(VacancyListSerializer):
    previous_rejection_reason = serializers.CharField(source="last_moderator_rejection_reason", read_only=True)
    resubmitted_changed_fields = serializers.SerializerMethodField()
    moderation_attempts_total = serializers.SerializerMethodField()
    moderation_approved_total = serializers.SerializerMethodField()
    moderation_rejected_total = serializers.SerializerMethodField()
    current_attempt_no = serializers.SerializerMethodField()

    class Meta(VacancyListSerializer.Meta):
        fields = VacancyListSerializer.Meta.fields + [
            "previous_rejection_reason",
            "resubmitted_changed_fields",
            "moderation_attempts_total",
            "moderation_approved_total",
            "moderation_rejected_total",
            "current_attempt_no",
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

    def get_moderation_attempts_total(self, obj):
        return _moderation_attempt_counts(obj)["total"]

    def get_moderation_approved_total(self, obj):
        return _moderation_attempt_counts(obj)["approved"]

    def get_moderation_rejected_total(self, obj):
        return _moderation_attempt_counts(obj)["rejected"]

    def get_current_attempt_no(self, obj):
        latest = getattr(obj, "latest_moderation_attempt_no", None)
        if latest is not None:
            return int(latest or 0)
        attempt = obj.moderation_attempts.order_by("-attempt_no").first()
        return int(getattr(attempt, "attempt_no", 0) or 0)


class VacancyModerationAttemptSerializer(serializers.ModelSerializer):
    submitted_by_name = serializers.SerializerMethodField()
    decided_by_name = serializers.SerializerMethodField()

    class Meta:
        model = VacancyModerationAttempt
        fields = [
            "attempt_no",
            "trigger_type",
            "submitted_at",
            "submitted_by_name",
            "decision",
            "decided_at",
            "decided_by_name",
            "rejection_reason",
            "extra_context",
        ]

    def get_submitted_by_name(self, obj):
        return _user_display_name(getattr(obj, "submitted_by", None))

    def get_decided_by_name(self, obj):
        return _user_display_name(getattr(obj, "decided_by", None))


class VacancyModerationDetailSerializer(VacancyModerationSerializer):
    moderation_history = VacancyModerationAttemptSerializer(
        source="moderation_attempts",
        many=True,
        read_only=True,
    )

    class Meta(VacancyModerationSerializer.Meta):
        fields = VacancyModerationSerializer.Meta.fields + [
            "moderation_history",
        ]

class VacancyDetailSerializer(serializers.ModelSerializer):
    contacts = serializers.SerializerMethodField()
    salary_monthly_from = serializers.SerializerMethodField()
    salary_monthly_to = serializers.SerializerMethodField()
    audience_countries = AudienceCountriesField(
        source="audience_country_codes",
        read_only=True,
        required=False,
        min_selections=0,
    )
    driver_license_categories = DriverLicenseCategoriesField(read_only=True)
    moderation_status = serializers.SerializerMethodField()
    moderation_attempts_total = serializers.SerializerMethodField()
    moderation_approved_total = serializers.SerializerMethodField()
    moderation_rejected_total = serializers.SerializerMethodField()
    moderation_history = serializers.SerializerMethodField()
    is_service_board = serializers.SerializerMethodField()
    service_board_kind = serializers.SerializerMethodField()

    class Meta:
        model = Vacancy
        fields = [
            "id",
            "title",
            "country",
            "city",
            "city_code",
            "category",
            "audience_countries",
            "employment_type",
            "experience_required",
            "driver_license_categories",
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
            "source",
            "contacts",
            "published_at",
            "expires_at",
            "moderation_status",
            "moderation_attempts_total",
            "moderation_approved_total",
            "moderation_rejected_total",
            "moderation_history",
            "is_service_board",
            "service_board_kind",
        ]

    def get_contacts(self, obj):
        return _contact_payload(obj, public_only=True)

    def get_salary_monthly_from(self, obj):
        return _salary_monthly_from(obj)

    def get_salary_monthly_to(self, obj):
        return _salary_monthly_to(obj)

    def _is_staff_view(self):
        request = self.context.get("request")
        return bool(getattr(getattr(request, "user", None), "is_staff", False))

    def get_moderation_status(self, obj):
        if not self._is_staff_view():
            return ""
        return obj.moderation_status

    def get_moderation_attempts_total(self, obj):
        if not self._is_staff_view():
            return 0
        return _moderation_attempt_counts(obj)["total"]

    def get_moderation_approved_total(self, obj):
        if not self._is_staff_view():
            return 0
        return _moderation_attempt_counts(obj)["approved"]

    def get_moderation_rejected_total(self, obj):
        if not self._is_staff_view():
            return 0
        return _moderation_attempt_counts(obj)["rejected"]

    def get_moderation_history(self, obj):
        if not self._is_staff_view():
            return []
        return VacancyModerationAttemptSerializer(
            obj.moderation_attempts.all(),
            many=True,
        ).data

    def get_is_service_board(self, obj):
        return _service_board_meta(obj)["is_service_board"]

    def get_service_board_kind(self, obj):
        return _service_board_meta(obj)["service_board_kind"]

class VacancyCreateSerializer(serializers.ModelSerializer):
    audience_countries = AudienceCountriesField(
        source="audience_country_codes",
    )
    driver_license_categories = DriverLicenseCategoriesField(
        required=False,
        max_selections=MAX_DRIVER_LICENSE_SELECTIONS,
    )

    class Meta:
        model = Vacancy
        fields = [
            "title",
            "country",
            "city",
            "city_code",
            "category",
            "audience_countries",
            "employment_type",
            "experience_required",
            "driver_license_categories",
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
            "experience_required": {"required": False, "allow_blank": True},
            "hide_primary_phone": {"required": False},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.draft_mode = bool(self.context.get("draft_mode"))
        if not self.draft_mode:
            return

        for name, field in self.fields.items():
            if name == "creator_token":
                continue
            field.required = False
            if hasattr(field, "allow_blank"):
                field.allow_blank = True
            if hasattr(field, "allow_null"):
                field.allow_null = True

        audience_field = self.fields.get("audience_countries")
        if isinstance(audience_field, AudienceCountriesField):
            audience_field.min_selections = 0

        driver_field = self.fields.get("driver_license_categories")
        if isinstance(driver_field, DriverLicenseCategoriesField):
            driver_field.required = False

    def validate(self, attrs):
        errors = {}
        raw_values = {
            field: attrs.get(field)
            for field in (
                "title",
                "city",
                "city_code",
                "description",
                "salary",
                "phone",
                "additional_phone",
                "telegram",
                "whatsapp",
                "viber",
                "email",
                "housing_cost",
            )
        }

        # Apply minimal profanity censorship to textual content.
        for field in ("title", "city", "description", "salary", "housing_cost"):
            val = attrs.get(field)
            if isinstance(val, str):
                attrs[field] = censor_minimal(val).strip()

        def _check_len(field, max_len):
            val = raw_values.get(field, attrs.get(field))
            if val is None:
                return
            if isinstance(val, str) and len(val) > max_len:
                errors[field] = f"max {max_len} chars"

        _check_len("title", 50)
        _check_len("city", 20)
        _check_len("city_code", 64)
        _check_len("salary", 80)
        _check_len("phone", 15)
        _check_len("additional_phone", 15)
        _check_len("telegram", 15)
        _check_len("whatsapp", 15)
        _check_len("viber", 15)
        _check_len("email", 30)

        for field in ("title", "city", "description", "salary", "housing_cost"):
            val = attrs.get(field)
            if isinstance(val, str) and contains_link(val):
                errors[field] = "links are not allowed"

        city_code = (attrs.get("city_code") or "").strip().lower()
        if city_code:
            if not re.match(r"^[a-z0-9_]+$", city_code):
                errors["city_code"] = "invalid city code"
            attrs["city_code"] = city_code

        desc = raw_values.get("description", attrs.get("description"))
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

        if hide_primary_phone and not additional_phone and not self.draft_mode:
            errors["additional_phone"] = "required when primary phone is hidden"

        email = (attrs.get("email") or "").strip()
        if email:
            if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
                errors["email"] = "invalid email"

        if not self.draft_mode and not public_phone and not email:
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
            if salary_from is not None and (salary_from < 1 or salary_from > 99):
                errors["salary_from"] = "must be in range 1..99"
            if salary_to is not None and (salary_to < 1 or salary_to > 99):
                errors["salary_to"] = "must be in range 1..99"
            if salary_from is not None and salary_to is not None and salary_from > salary_to:
                errors["salary_to"] = "must be greater or equal salary_from"

            if salary_hours_month is not None and (salary_hours_month < 1 or salary_hours_month > 300):
                errors["salary_hours_month"] = "must be in range 1..300"

            if not self.draft_mode:
                if salary_from is None and salary_to is None:
                    errors["salary_from"] = "required salary from/to"
                if not salary_currency:
                    errors["salary_currency"] = "required"
                if not salary_tax_type:
                    errors["salary_tax_type"] = "required"
                if salary_hours_month is None:
                    errors["salary_hours_month"] = "required"

            if (
                not errors
                and (salary_from is not None or salary_to is not None)
                and salary_currency
                and salary_tax_type
                and salary_hours_month is not None
            ):
                if salary_from is not None and salary_to is not None:
                    range_text = f"from {salary_from} to {salary_to}"
                elif salary_from is not None:
                    range_text = f"from {salary_from}"
                else:
                    range_text = f"to {salary_to}"
                attrs["salary"] = f"{range_text} {salary_currency} {salary_tax_type}"
        else:
            if not self.draft_mode and not salary_text:
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
    audience_countries = AudienceCountriesField(
        source="audience_country_codes",
        read_only=True,
        required=False,
        min_selections=0,
    )
    driver_license_categories = DriverLicenseCategoriesField(read_only=True)

    class Meta:
        model = Vacancy
        fields = [
            "id",
            "title",
            "country",
            "city",
            "city_code",
            "category",
            "audience_countries",
            "employment_type",
            "experience_required",
            "driver_license_categories",
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
            "source",
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
        if obj.is_editing:
            return "rejected"
        if obj.is_approved:
            return "approved"
        if obj.is_rejected:
            return "rejected"
        return "pending"

    def get_status_label_key(self, obj):
        if obj.is_editing:
            return "statusDraft"
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


class PushDeviceRegisterSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=512)
    platform = serializers.ChoiceField(
        choices=[code for code, _ in PushDevice.PLATFORM_CHOICES],
        required=False,
        default="android",
    )
    app_language = serializers.CharField(required=False, allow_blank=True, max_length=10, default="")

    def validate_token(self, value):
        token = (value or "").strip()
        if len(token) < 32:
            raise serializers.ValidationError("invalid_device_token")
        return token

    def validate_app_language(self, value):
        lang = (value or "").strip().lower()
        if not lang:
            return ""
        if len(lang) > 10:
            raise serializers.ValidationError("invalid_app_language")
        return lang


class VacancyAlertSubscriptionSerializer(serializers.ModelSerializer):
    audience_countries = AudienceCountriesField(
        source="audience_country_codes",
        required=False,
        min_selections=None,
        max_selections=None,
    )
    driver_license_categories = DriverLicenseCategoriesField(required=False)

    class Meta:
        model = VacancyAlertSubscription
        fields = [
            "enabled",
            "country",
            "city",
            "city_code",
            "category",
            "audience_countries",
            "employment_type",
            "housing_type",
            "driver_license_categories",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]

    def validate_country(self, value):
        code = (value or "").strip().upper()
        if not code:
            return ""
        allowed = {c for c, _ in Vacancy.COUNTRY_CHOICES}
        if code not in allowed:
            raise serializers.ValidationError("invalid_country")
        return code

    def validate_city(self, value):
        raw_city = value or ""
        if len(raw_city) > 80:
            raise serializers.ValidationError("city_too_long")
        return raw_city.strip()

    def validate_city_code(self, value):
        raw_code = (value or "").strip().lower()
        if not raw_code:
            return ""
        if len(raw_code) > 64:
            raise serializers.ValidationError("city_code_too_long")
        if not re.match(r"^[a-z0-9_]+$", raw_code):
            raise serializers.ValidationError("invalid_city_code")
        return raw_code

    def validate_category(self, value):
        code = (value or "").strip().lower()
        if not code:
            return ""
        allowed = {c for c, _ in Vacancy.CATEGORY_CHOICES}
        if code not in allowed:
            raise serializers.ValidationError("invalid_category")
        return code

    def validate_employment_type(self, value):
        code = (value or "").strip().lower()
        if not code:
            return ""
        allowed = {c for c, _ in Vacancy.EMPLOYMENT_TYPE_CHOICES}
        if code not in allowed:
            raise serializers.ValidationError("invalid_employment_type")
        return code

    def validate_housing_type(self, value):
        code = (value or "").strip().lower()
        if not code:
            return ""
        allowed = {c for c, _ in Vacancy.HOUSING_TYPE_CHOICES}
        if code not in allowed:
            raise serializers.ValidationError("invalid_housing_type")
        return code


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


class UserWalletSerializer(serializers.ModelSerializer):
    total_credits = serializers.IntegerField(read_only=True)

    class Meta:
        model = UserWallet
        fields = [
            "paid_credits",
            "bonus_credits",
            "total_credits",
            "lifetime_paid_credits",
            "lifetime_bonus_credits",
            "updated_at",
        ]


class UserMonetizationProfileSerializer(serializers.ModelSerializer):
    has_employer_subscription = serializers.SerializerMethodField()
    has_seeker_subscription = serializers.SerializerMethodField()

    class Meta:
        model = UserMonetizationProfile
        fields = [
            "has_employer_subscription",
            "employer_subscription_until",
            "has_seeker_subscription",
            "seeker_subscription_until",
            "free_create_ad_submissions_used",
            "free_edit_ad_resubmissions_used",
            "employer_daily_submission_date",
            "employer_daily_submissions_used",
            "updated_at",
        ]

    def get_has_employer_subscription(self, obj):
        return obj.has_employer_subscription()

    def get_has_seeker_subscription(self, obj):
        return obj.has_seeker_subscription()


class EconomyConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = EconomyConfig
        fields = [
            "vacancy_submit_price_credits",
            "vacancy_edit_resubmit_price_credits",
            "free_create_ad_submissions_limit",
            "free_edit_ad_resubmissions_limit",
            "employer_daily_free_submissions_limit",
            "seeker_contact_discount_percent",
            "contact_access_duration_minutes",
            "updated_at",
        ]


class StoreProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreProduct
        fields = [
            "id",
            "code",
            "title",
            "product_type",
            "platform",
            "store_product_id",
            "credit_amount",
            "duration_days",
            "price_label",
            "sort_order",
        ]


class WalletTransactionSerializer(serializers.ModelSerializer):
    total_delta = serializers.SerializerMethodField()
    related_vacancy_title = serializers.CharField(source="related_vacancy.title", read_only=True)

    class Meta:
        model = WalletTransaction
        fields = [
            "id",
            "kind",
            "delta_paid_credits",
            "delta_bonus_credits",
            "total_delta",
            "balance_paid_after",
            "balance_bonus_after",
            "note",
            "related_vacancy",
            "related_vacancy_title",
            "metadata",
            "created_at",
        ]

    def get_total_delta(self, obj):
        return int(obj.delta_paid_credits or 0) + int(obj.delta_bonus_credits or 0)


class GooglePlayPurchaseCompleteSerializer(serializers.Serializer):
    product_code = serializers.CharField(max_length=80)
    purchase_token = serializers.CharField(max_length=512)
    purchase_id = serializers.CharField(max_length=255, required=False, allow_blank=True)
    verification_data = serializers.CharField(required=False, allow_blank=True)
    local_verification_data = serializers.CharField(required=False, allow_blank=True)
    purchase_payload = serializers.JSONField(required=False)

    def validate_product_code(self, value):
        code = (value or "").strip()
        if not code:
            raise serializers.ValidationError("product_code_required")
        try:
            product = StoreProduct.objects.get(code=code, is_active=True)
        except StoreProduct.DoesNotExist as exc:
            raise serializers.ValidationError("store_product_not_found") from exc
        if product.platform not in {"android", "shared"}:
            raise serializers.ValidationError("store_product_platform_mismatch")
        if not (product.store_product_id or "").strip():
            raise serializers.ValidationError("store_product_id_missing")
        self.context["store_product"] = product
        return code

    def validate_purchase_token(self, value):
        token = (value or "").strip()
        if not token:
            raise serializers.ValidationError("purchase_token_required")
        return token

