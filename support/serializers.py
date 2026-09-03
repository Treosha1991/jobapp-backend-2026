import re
import uuid

from django.utils import timezone
from rest_framework import serializers

from .permission_codes import ALL_PERMISSION_CODES
from .questionnaire import (
    CONDITION_ANSWERS,
    DRIVING_CATEGORIES,
    DRIVING_EXPERIENCE,
    DURATION_CHOICES,
    EXPERIENCE_DURATIONS,
    EXPERIENCE_SECTORS,
    LANGUAGE_LEVELS,
    LEGAL_STATUSES,
    QUALIFICATIONS,
    QUESTIONNAIRE_VERSION_V3,
    SHIFT_PREFERENCES,
    SUPPORTED_QUESTIONNAIRE_VERSIONS,
    THREE_WAY_ANSWERS,
    WORK_CONDITIONS,
    normalize_identity_name,
)


class SupportOrganizationCreateSerializer(serializers.Serializer):
    legal_name = serializers.CharField(max_length=180)
    display_name = serializers.CharField(max_length=120)
    owner_email = serializers.EmailField()

    def validate_legal_name(self, value):
        normalized = value.strip()
        if not normalized:
            raise serializers.ValidationError("legal_name_required")
        return normalized

    def validate_display_name(self, value):
        normalized = value.strip()
        if not normalized:
            raise serializers.ValidationError("display_name_required")
        return normalized


class MembershipInvitationCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    display_role = serializers.CharField(max_length=80, required=False, allow_blank=True)
    permission_codes = serializers.ListField(
        child=serializers.CharField(max_length=64),
        allow_empty=True,
    )

    def validate_permission_codes(self, value):
        normalized = sorted({item.strip() for item in value if item.strip()})
        unsupported = [item for item in normalized if item not in ALL_PERMISSION_CODES]
        if unsupported:
            raise serializers.ValidationError("unsupported_permission_code")
        return normalized


class PermissionCodeSerializer(serializers.Serializer):
    permission_code = serializers.CharField(max_length=64)

    def validate_permission_code(self, value):
        normalized = value.strip()
        if normalized not in ALL_PERMISSION_CODES:
            raise serializers.ValidationError("unsupported_permission_code")
        return normalized


class TemporarySupportAccessGrantSerializer(serializers.Serializer):
    user_email = serializers.EmailField()
    duration_days = serializers.ChoiceField(choices=(7, 14, 30))
    reason = serializers.ChoiceField(
        choices=("continue_connection", "transition_period", "technical_help")
    )
    organization_public_id = serializers.UUIDField(required=False, allow_null=True)


class StrictInputSerializer(serializers.Serializer):
    """Reject unknown JSON keys instead of silently ignoring unsafe fields."""

    def to_internal_value(self, data):
        # Nested serializers do not always receive ``initial_data`` as an
        # attribute, but ``to_internal_value`` always receives the raw value.
        # Checking here keeps strict validation working both at the request
        # root and inside the structured questionnaire.
        if isinstance(data, dict):
            unknown_fields = sorted(set(data) - set(self.fields))
            if unknown_fields:
                raise serializers.ValidationError(
                    {"non_field_errors": "unsupported_support_field"}
                )
        return super().to_internal_value(data)


class WorkerAccessScopeCreateSerializer(StrictInputSerializer):
    connection_id = serializers.UUIDField()


class WorkerShiftPeerChatOpenSerializer(StrictInputSerializer):
    target_connection_id = serializers.UUIDField()


class ScheduledWorkShiftCreateSerializer(StrictInputSerializer):
    connection_id = serializers.UUIDField()
    work_assignment_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    work_date = serializers.DateField()
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField()
    break_minutes = serializers.IntegerField(min_value=0, max_value=720, default=0)
    worker_label = serializers.CharField(max_length=160, required=False, allow_blank=True, default="")


class ShiftTemplateCreateSerializer(StrictInputSerializer):
    name = serializers.CharField(max_length=120)
    starts_at_time = serializers.TimeField()
    ends_at_time = serializers.TimeField()
    break_minutes = serializers.IntegerField(min_value=0, max_value=720, default=0)
    worker_label = serializers.CharField(max_length=160, required=False, allow_blank=True, default="")

    def validate_name(self, value):
        normalized = value.strip()
        if not normalized:
            raise serializers.ValidationError("shift_template_name_required")
        return normalized


class CalendarMarkTemplateCreateSerializer(StrictInputSerializer):
    name = serializers.CharField(max_length=120)
    request_type = serializers.ChoiceField(
        choices=("day_off", "vacation", "unpaid_absence", "unable_today")
    )

    def validate_name(self, value):
        normalized = value.strip()
        if not normalized:
            raise serializers.ValidationError("calendar_mark_template_name_required")
        return normalized


class CalendarMarkBatchCreateSerializer(StrictInputSerializer):
    template_id = serializers.UUIDField()
    worker_request_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        max_length=100,
    )

    def validate_worker_request_ids(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError("duplicate_calendar_mark_request")
        return value


class ScheduledShiftBatchCreateSerializer(StrictInputSerializer):
    template_id = serializers.UUIDField()
    connection_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        max_length=50,
    )
    starts_on = serializers.DateField()
    ends_on = serializers.DateField()
    weekdays = serializers.ListField(
        child=serializers.IntegerField(min_value=0, max_value=6),
        min_length=1,
        max_length=7,
    )

    def validate_connection_ids(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError("scheduled_shift_batch_connection_ids_must_be_unique")
        return value

    def validate_weekdays(self, value):
        normalized = sorted(set(value))
        if not normalized:
            raise serializers.ValidationError("scheduled_shift_batch_weekdays_required")
        return normalized

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs["ends_on"] < attrs["starts_on"]:
            raise serializers.ValidationError(
                {"ends_on": "period_end_must_not_be_before_start"}
            )
        return attrs


class WorkTimeEntrySubmitSerializer(StrictInputSerializer):
    work_date = serializers.DateField()
    started_at = serializers.DateTimeField()
    ended_at = serializers.DateTimeField()
    break_minutes = serializers.IntegerField(min_value=0, max_value=720, default=0)


class WorkTimeEntryCorrectionSerializer(StrictInputSerializer):
    reason = serializers.CharField(max_length=500)

    def validate_reason(self, value):
        normalized = value.strip()
        if not normalized:
            raise serializers.ValidationError("correction_reason_required")
        return normalized


class WorkTimeEntryStaffEditSerializer(StrictInputSerializer):
    started_at = serializers.DateTimeField()
    ended_at = serializers.DateTimeField()
    break_minutes = serializers.IntegerField(min_value=0, max_value=720)
    reason = serializers.CharField(max_length=500)

    def validate_reason(self, value):
        normalized = value.strip()
        if not normalized:
            raise serializers.ValidationError("staff_edit_reason_required")
        return normalized


class WorkerRequestCreateSerializer(StrictInputSerializer):
    request_type = serializers.ChoiceField(
        choices=(
            "day_off",
            "vacation",
            "unpaid_absence",
            "unable_today",
            "extra_shift",
            "exit_request",
        )
    )
    starts_on = serializers.DateField(required=False)
    ends_on = serializers.DateField(required=False)
    requested_dates = serializers.ListField(
        child=serializers.DateField(),
        required=False,
        default=list,
        min_length=1,
        max_length=31,
    )
    worker_note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")

    def validate_worker_note(self, value):
        return value.strip()

    def validate_requested_dates(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError("extra_shift_dates_must_be_unique")
        return sorted(value)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request_type = attrs["request_type"]
        requested_dates = attrs.get("requested_dates") or []
        if request_type == "extra_shift":
            if not requested_dates:
                raise serializers.ValidationError(
                    {"requested_dates": "extra_shift_dates_required"}
                )
            attrs["starts_on"] = requested_dates[0]
            attrs["ends_on"] = requested_dates[-1]
        else:
            if requested_dates:
                raise serializers.ValidationError(
                    {"requested_dates": "extra_shift_dates_not_allowed"}
                )
            if "starts_on" not in attrs:
                raise serializers.ValidationError({"starts_on": "required"})
            if "ends_on" not in attrs:
                raise serializers.ValidationError({"ends_on": "required"})
        return attrs


class WorkerRequestDecisionSerializer(StrictInputSerializer):
    manager_note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")

    def validate_manager_note(self, value):
        return value.strip()


class DocumentRequestPackageCreateSerializer(StrictInputSerializer):
    connection_id = serializers.UUIDField()
    requested_items = serializers.ListField(child=serializers.DictField(), min_length=1, max_length=12)
    additional_instructions = serializers.CharField(
        max_length=500,
        required=False,
        allow_blank=True,
        default="",
    )

    def validate_requested_items(self, value):
        allowed_types = {
            "passport",
            "visa",
            "residence_permit",
            "pesel",
            "bsn",
            "bank_account",
            "driving_license",
            "custom",
        }
        normalized = []
        seen = set()
        for raw in value:
            if not isinstance(raw, dict) or set(raw) - {"type", "custom_label"}:
                raise serializers.ValidationError("unsupported_document_request_item")
            item_type = str(raw.get("type") or "").strip()
            custom_label = str(raw.get("custom_label") or "").strip()
            if item_type not in allowed_types:
                raise serializers.ValidationError("unsupported_document_request_item")
            if item_type == "custom":
                if not custom_label or len(custom_label) > 80:
                    raise serializers.ValidationError("custom_document_request_label_required")
                key = (item_type, custom_label.casefold())
            else:
                if custom_label:
                    raise serializers.ValidationError("custom_document_request_label_not_allowed")
                key = (item_type, "")
            if key in seen:
                raise serializers.ValidationError("duplicate_document_request_item")
            seen.add(key)
            normalized.append({"type": item_type, "custom_label": custom_label})
        return normalized

    def validate_additional_instructions(self, value):
        return value.strip()


class DocumentRequestPackageDecisionSerializer(StrictInputSerializer):
    manager_note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")

    def validate_manager_note(self, value):
        return value.strip()


class GroupPushPreferenceSerializer(StrictInputSerializer):
    enabled = serializers.BooleanField()


def _validate_translations(value, *, body_field, body_max_length):
    languages = {"ru", "en", "pl", "uk"}
    if not isinstance(value, dict) or set(value) != languages:
        raise serializers.ValidationError("complete_ru_en_pl_uk_content_required")
    if _contains_encoding_placeholder(value):
        raise serializers.ValidationError("invalid_text_encoding_placeholder")
    normalized = {}
    for language, item in value.items():
        if not isinstance(item, dict) or set(item) != {"title", body_field}:
            raise serializers.ValidationError({language: "invalid_translated_content_structure"})
        title = item["title"]
        body = item[body_field]
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 180:
            raise serializers.ValidationError({language: "invalid_translated_title"})
        if (
            not isinstance(body, str)
            or not body.strip()
            or len(body.strip()) > body_max_length
        ):
            raise serializers.ValidationError({language: "invalid_translated_content"})
        normalized[language] = {"title": title.strip(), body_field: body.strip()}
    return normalized


class WorkerTaskCreateSerializer(StrictInputSerializer):
    source_language = serializers.ChoiceField(choices=("ru", "en", "pl", "uk"))
    translations = serializers.JSONField()
    priority = serializers.ChoiceField(choices=("normal", "important"), default="normal")
    context_kind = serializers.ChoiceField(
        choices=("general", "arrival", "housing", "transport", "work", "finance"),
        default="general",
    )
    due_at = serializers.DateTimeField(required=False, allow_null=True, default=None)
    connection_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        max_length=200,
    )
    responsible_membership_id = serializers.UUIDField(required=False, allow_null=True, default=None)

    def validate_translations(self, value):
        return _validate_translations(
            value,
            body_field="instructions",
            body_max_length=5000,
        )

    def validate_connection_ids(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError("duplicate_task_connection")
        return value


class WorkerTaskWorkerActionSerializer(StrictInputSerializer):
    worker_note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")

    def validate_worker_note(self, value):
        return value.strip()


class WorkerTaskStaffDecisionSerializer(StrictInputSerializer):
    manager_note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")

    def validate_manager_note(self, value):
        return value.strip()


class AnnouncementCreateSerializer(StrictInputSerializer):
    source_language = serializers.ChoiceField(choices=("ru", "en", "pl", "uk"))
    translations = serializers.JSONField()
    importance = serializers.ChoiceField(choices=("normal", "important"), default="normal")
    requires_acknowledgement = serializers.BooleanField(default=False)
    expires_at = serializers.DateTimeField(required=False, allow_null=True, default=None)
    connection_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        max_length=500,
    )

    def validate_translations(self, value):
        return _validate_translations(value, body_field="body", body_max_length=8000)

    def validate_connection_ids(self, value):
        if len(set(value)) != len(value):
            raise serializers.ValidationError("duplicate_announcement_connection")
        return value


class ContentTemplateCreateSerializer(StrictInputSerializer):
    """Reusable text only.  Recipients and publication stay outside a template."""

    name = serializers.CharField(max_length=120)
    kind = serializers.ChoiceField(choices=("task", "announcement"))
    source_language = serializers.ChoiceField(choices=("ru", "en", "pl", "uk"))
    translations = serializers.JSONField()

    def validate_name(self, value):
        normalized = value.strip()
        if not normalized:
            raise serializers.ValidationError("content_template_name_required")
        if _contains_encoding_placeholder(normalized):
            raise serializers.ValidationError("invalid_text_encoding_placeholder")
        return normalized

    def validate_translations(self, value):
        # A generic ``body`` keeps one unambiguous template schema.  It is
        # converted to ``instructions`` only while creating a worker task.
        return _validate_translations(value, body_field="body", body_max_length=5000)


class EmptyStrictInputSerializer(StrictInputSerializer):
    pass


class SupportVacancyCreateSerializer(StrictInputSerializer):
    internal_title = serializers.CharField(max_length=160)
    internal_position_limit = serializers.IntegerField(
        min_value=1,
        max_value=10000,
        required=False,
        allow_null=True,
    )
    public_vacancy_id = serializers.IntegerField(required=False, allow_null=True)

    def validate_internal_title(self, value):
        normalized = value.strip()
        if not normalized:
            raise serializers.ValidationError("internal_title_required")
        return normalized


def _contains_encoding_placeholder(value):
    if isinstance(value, str):
        return "???" in value or "\ufffd" in value
    if isinstance(value, list):
        return any(_contains_encoding_placeholder(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_encoding_placeholder(item) for item in value.values())
    return False


class BotContentRevisionCreateSerializer(StrictInputSerializer):
    LANGUAGES = ("ru", "en", "pl", "uk")
    source_language = serializers.ChoiceField(choices=LANGUAGES)
    content = serializers.JSONField()

    def validate_content(self, value):
        if not isinstance(value, dict) or set(value) != set(self.LANGUAGES):
            raise serializers.ValidationError("complete_ru_en_pl_uk_content_required")
        if _contains_encoding_placeholder(value):
            raise serializers.ValidationError("invalid_text_encoding_placeholder")

        for language, section in value.items():
            if not isinstance(section, dict) or set(section) != {"title", "intro", "steps", "faq"}:
                raise serializers.ValidationError({language: "invalid_bot_content_structure"})
            title = section["title"]
            intro = section["intro"]
            steps = section["steps"]
            faq = section["faq"]
            if not isinstance(title, str) or not title.strip() or len(title) > 160:
                raise serializers.ValidationError({language: "invalid_bot_title"})
            if not isinstance(intro, str) or not intro.strip() or len(intro) > 4000:
                raise serializers.ValidationError({language: "invalid_bot_intro"})
            if not isinstance(steps, list) or not 1 <= len(steps) <= 12:
                raise serializers.ValidationError({language: "invalid_bot_steps"})
            if any(not isinstance(item, str) or not item.strip() or len(item) > 500 for item in steps):
                raise serializers.ValidationError({language: "invalid_bot_step"})
            if not isinstance(faq, list) or len(faq) > 12:
                raise serializers.ValidationError({language: "invalid_bot_faq"})
            for item in faq:
                if not isinstance(item, dict) or set(item) != {"question", "answer"}:
                    raise serializers.ValidationError({language: "invalid_bot_faq_item"})
                if any(
                    not isinstance(item[key], str)
                    or not item[key].strip()
                    or len(item[key]) > 1500
                    for key in ("question", "answer")
                ):
                    raise serializers.ValidationError({language: "invalid_bot_faq_text"})
        return value


class SupportQuestionnaireSerializer(StrictInputSerializer):
    first_name = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    last_name = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    adult_confirmed = serializers.BooleanField()
    legal_status = serializers.ChoiceField(choices=LEGAL_STATUSES)
    document_valid_until = serializers.DateField(required=False, allow_null=True, default=None)
    current_city = serializers.CharField(max_length=120)
    available_from = serializers.DateField()
    planned_duration = serializers.ChoiceField(choices=DURATION_CHOICES)
    experience_sectors = serializers.ListField(
        child=serializers.ChoiceField(choices=EXPERIENCE_SECTORS), allow_empty=False, max_length=12
    )
    experience_duration = serializers.ChoiceField(choices=EXPERIENCE_DURATIONS)
    work_countries = serializers.ListField(
        child=serializers.CharField(max_length=2), required=False, allow_empty=True, max_length=10, default=list
    )
    last_position = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    english_level = serializers.ChoiceField(choices=LANGUAGE_LEVELS)
    polish_level = serializers.ChoiceField(choices=LANGUAGE_LEVELS)
    dutch_level = serializers.ChoiceField(choices=LANGUAGE_LEVELS)
    has_driving_license = serializers.BooleanField()
    driving_license_categories = serializers.ListField(
        child=serializers.ChoiceField(choices=DRIVING_CATEGORIES), required=False, allow_empty=True, default=list
    )
    driving_license_valid_in_eu = serializers.BooleanField(required=False, allow_null=True, default=None)
    driving_experience = serializers.ChoiceField(choices=DRIVING_EXPERIENCE)
    willing_crew_driver = serializers.BooleanField()
    has_own_car = serializers.BooleanField()
    qualifications = serializers.ListField(
        child=serializers.ChoiceField(choices=QUALIFICATIONS), required=False, allow_empty=True, default=list
    )
    work_conditions = serializers.DictField(child=serializers.ChoiceField(choices=CONDITION_ANSWERS))
    shift_preferences = serializers.ListField(
        child=serializers.ChoiceField(choices=SHIFT_PREFERENCES), allow_empty=False
    )
    overtime_willing = serializers.ChoiceField(choices=THREE_WAY_ANSWERS)
    unavailable_dates_note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    needs_housing = serializers.BooleanField()
    needs_transport = serializers.BooleanField()
    travelling_with_partner = serializers.BooleanField()
    shared_room_preference = serializers.ChoiceField(choices=THREE_WAY_ANSWERS)
    planned_move_in = serializers.DateField(required=False, allow_null=True, default=None)
    safety_policy_accepted = serializers.BooleanField()
    additional_note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")

    def _validate_identity_name(self, value):
        try:
            return normalize_identity_name(value)
        except ValueError as error:
            raise serializers.ValidationError(str(error)) from error

    validate_first_name = _validate_identity_name
    validate_last_name = _validate_identity_name

    def validate_current_city(self, value):
        normalized = value.strip()
        if not normalized:
            raise serializers.ValidationError("current_city_required")
        return normalized

    def validate_work_countries(self, values):
        normalized = []
        for value in values:
            code = value.strip().upper()
            if not re.fullmatch(r"[A-Z]{2}", code):
                raise serializers.ValidationError("country_code_must_be_iso_alpha_2")
            if code not in normalized:
                normalized.append(code)
        return normalized

    def validate_work_conditions(self, value):
        if set(value) != set(WORK_CONDITIONS):
            raise serializers.ValidationError("all_work_conditions_required")
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        for key in (
            "experience_sectors", "driving_license_categories", "qualifications", "shift_preferences"
        ):
            attrs[key] = list(dict.fromkeys(attrs.get(key, [])))
        if not attrs["adult_confirmed"]:
            raise serializers.ValidationError({"adult_confirmed": "adult_candidate_required"})
        if not attrs["safety_policy_accepted"]:
            raise serializers.ValidationError({"safety_policy_accepted": "safety_policy_required"})
        if not attrs["has_driving_license"]:
            attrs["driving_license_categories"] = []
            attrs["driving_license_valid_in_eu"] = None
            attrs["driving_experience"] = "none"
            attrs["willing_crew_driver"] = False
        return attrs


class SupportApplicationCreateSerializer(StrictInputSerializer):
    LANGUAGE_CHOICES = ("ru", "en", "pl", "uk")

    preferred_language = serializers.ChoiceField(choices=LANGUAGE_CHOICES)
    citizenship_country_code = serializers.CharField(
        max_length=2,
        required=False,
        allow_blank=True,
        default="",
    )
    current_country_code = serializers.CharField(
        max_length=2,
        required=False,
        allow_blank=True,
        default="",
    )
    availability_note = serializers.CharField(
        max_length=500,
        required=False,
        allow_blank=True,
        default="",
    )
    partner_reference_code = serializers.CharField(
        max_length=24,
        required=False,
        allow_blank=True,
        default="",
    )
    questionnaire_version = serializers.CharField(
        max_length=32,
        required=False,
        allow_blank=True,
        default="",
    )
    questionnaire = SupportQuestionnaireSerializer(required=False, default=dict)
    consent_version = serializers.CharField(max_length=32)
    consent_accepted = serializers.BooleanField()

    def _validate_country_code(self, value):
        normalized = value.strip().upper()
        if normalized and not re.fullmatch(r"[A-Z]{2}", normalized):
            raise serializers.ValidationError("country_code_must_be_iso_alpha_2")
        return normalized

    validate_citizenship_country_code = _validate_country_code
    validate_current_country_code = _validate_country_code

    def validate_partner_reference_code(self, value):
        normalized = value.strip().upper()
        if normalized and not re.fullmatch(r"JH-[0-9A-F]{6}-[0-9A-F]{4}", normalized):
            raise serializers.ValidationError("invalid_partner_reference_code")
        return normalized

    def validate_consent_version(self, value):
        normalized = value.strip()
        if not normalized:
            raise serializers.ValidationError("consent_version_required")
        return normalized

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if not attrs.get("consent_accepted"):
            raise serializers.ValidationError({"consent_accepted": "consent_required"})
        questionnaire_version = attrs.get("questionnaire_version", "")
        if questionnaire_version and questionnaire_version not in SUPPORTED_QUESTIONNAIRE_VERSIONS:
            raise serializers.ValidationError({"questionnaire_version": "unsupported_questionnaire_version"})
        if questionnaire_version in SUPPORTED_QUESTIONNAIRE_VERSIONS and not attrs.get("questionnaire"):
            raise serializers.ValidationError({"questionnaire": "questionnaire_required"})
        questionnaire = attrs.get("questionnaire") or {}
        if questionnaire_version == QUESTIONNAIRE_VERSION_V3:
            identity_errors = {}
            if not questionnaire.get("first_name"):
                identity_errors["first_name"] = "first_name_required"
            if not questionnaire.get("last_name"):
                identity_errors["last_name"] = "last_name_required"
            if identity_errors:
                raise serializers.ValidationError({"questionnaire": identity_errors})
        for key in ("document_valid_until", "available_from", "planned_move_in"):
            if questionnaire.get(key) is not None:
                questionnaire[key] = questionnaire[key].isoformat()
        return attrs


class ApplicationReviewSerializer(StrictInputSerializer):
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


class ApplicationClarificationResponseSerializer(StrictInputSerializer):
    answer = serializers.CharField(max_length=500, allow_blank=False, trim_whitespace=True)


class ConnectionTransitionSerializer(StrictInputSerializer):
    next_stage = serializers.ChoiceField(
        choices=("documents_stage", "coordinator_stage", "active_worker", "closed")
    )
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class SupportMessageCreateSerializer(StrictInputSerializer):
    original_language = serializers.ChoiceField(choices=("ru", "en", "pl", "uk"))
    body = serializers.CharField(max_length=1500)
    reply_to_message_id = serializers.UUIDField(required=False, allow_null=True)
    client_message_id = serializers.UUIDField(required=False, default=uuid.uuid4)

    def validate_body(self, value):
        normalized = value.strip()
        if not normalized:
            raise serializers.ValidationError("message_body_required")
        return normalized


class SupportMessageForwardSerializer(StrictInputSerializer):
    target_conversation_id = serializers.UUIDField()
    client_message_id = serializers.UUIDField(required=False, default=uuid.uuid4)


class SupportContactMessageCreateSerializer(StrictInputSerializer):
    target_type = serializers.ChoiceField(choices=("worker", "staff"))
    target_id = serializers.UUIDField()
    original_language = serializers.ChoiceField(choices=("ru", "en", "pl", "uk"))
    client_message_id = serializers.UUIDField(required=False, default=uuid.uuid4)


class HousingSiteCreateSerializer(StrictInputSerializer):
    internal_name = serializers.CharField(max_length=160)
    country_code = serializers.CharField(max_length=2)
    city = serializers.CharField(max_length=120)
    postal_code = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    street = serializers.CharField(max_length=160)
    building = serializers.CharField(max_length=40)
    rules_text = serializers.CharField(max_length=5000, required=False, allow_blank=True, default="")
    contact_name = serializers.CharField(max_length=160, required=False, allow_blank=True, default="")
    contact_phone = serializers.CharField(max_length=48, required=False, allow_blank=True, default="")

    def validate_country_code(self, value):
        normalized = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", normalized):
            raise serializers.ValidationError("country_code_must_be_iso_alpha_2")
        return normalized


class HousingRoomCreateSerializer(StrictInputSerializer):
    site_id = serializers.UUIDField()
    label = serializers.CharField(max_length=80)
    capacity = serializers.IntegerField(min_value=1, max_value=50, default=1)


class HousingPlaceCreateSerializer(StrictInputSerializer):
    room_id = serializers.UUIDField()
    label = serializers.CharField(max_length=80)


class WorksiteCreateSerializer(StrictInputSerializer):
    internal_name = serializers.CharField(max_length=160)
    country_code = serializers.CharField(max_length=2)
    city = serializers.CharField(max_length=120)
    postal_code = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    street = serializers.CharField(max_length=160)
    building = serializers.CharField(max_length=40)
    instructions = serializers.CharField(max_length=5000, required=False, allow_blank=True, default="")

    def validate_country_code(self, value):
        normalized = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", normalized):
            raise serializers.ValidationError("country_code_must_be_iso_alpha_2")
        return normalized


class WorkProjectCreateSerializer(StrictInputSerializer):
    worksite_id = serializers.UUIDField()
    internal_name = serializers.CharField(max_length=160)
    worker_visible_name = serializers.CharField(max_length=160)
    instructions = serializers.CharField(max_length=5000, required=False, allow_blank=True, default="")


class ProjectCreateSerializer(StrictInputSerializer):
    name = serializers.CharField(max_length=160)
    country_code = serializers.CharField(max_length=2)
    city = serializers.CharField(max_length=120)
    postal_code = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    street = serializers.CharField(max_length=160)
    building = serializers.CharField(max_length=40)
    worker_capacity = serializers.IntegerField(min_value=1, max_value=5000)
    starts_on = serializers.DateField()
    ends_on = serializers.DateField(required=False, allow_null=True, default=None)
    contact_name = serializers.CharField(max_length=160, required=False, allow_blank=True, default="")
    contact_phone = serializers.CharField(max_length=48, required=False, allow_blank=True, default="")
    contact_email = serializers.EmailField(required=False, allow_blank=True, default="")
    instructions = serializers.CharField(max_length=5000, required=False, allow_blank=True, default="")

    def validate_country_code(self, value):
        normalized = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", normalized):
            raise serializers.ValidationError("country_code_must_be_iso_alpha_2")
        return normalized

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs["ends_on"] is not None and attrs["ends_on"] < attrs["starts_on"]:
            raise serializers.ValidationError({"ends_on": "period_end_must_not_be_before_start"})
        return attrs


class ProjectUpdateSerializer(StrictInputSerializer):
    """Validate a partial project patch before merging it with stored data."""

    name = serializers.CharField(max_length=160, required=False)
    country_code = serializers.CharField(max_length=2, required=False)
    city = serializers.CharField(max_length=120, required=False)
    postal_code = serializers.CharField(max_length=20, required=False, allow_blank=True)
    street = serializers.CharField(max_length=160, required=False)
    building = serializers.CharField(max_length=40, required=False)
    worker_capacity = serializers.IntegerField(min_value=1, max_value=5000, required=False)
    starts_on = serializers.DateField(required=False)
    ends_on = serializers.DateField(required=False, allow_null=True)
    contact_name = serializers.CharField(max_length=160, required=False, allow_blank=True)
    contact_phone = serializers.CharField(max_length=48, required=False, allow_blank=True)
    contact_email = serializers.EmailField(required=False, allow_blank=True)
    instructions = serializers.CharField(max_length=5000, required=False, allow_blank=True)

    def validate_country_code(self, value):
        normalized = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", normalized):
            raise serializers.ValidationError("country_code_must_be_iso_alpha_2")
        return normalized

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if not attrs:
            raise serializers.ValidationError("project_patch_empty")
        return attrs


class ProjectCrewCreateSerializer(StrictInputSerializer):
    internal_name = serializers.CharField(max_length=160)
    driver_connection_id = serializers.UUIDField()
    vehicle_id = serializers.UUIDField()
    starts_on = serializers.DateField(required=False, default=timezone.localdate)

    def validate_internal_name(self, value):
        normalized = value.strip()
        if not normalized:
            raise serializers.ValidationError("crew_name_required")
        return normalized


class ProjectCrewUpdateSerializer(StrictInputSerializer):
    internal_name = serializers.CharField(max_length=160, required=False)

    def validate_internal_name(self, value):
        normalized = value.strip()
        if not normalized:
            raise serializers.ValidationError("crew_name_required")
        return normalized

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if not attrs:
            raise serializers.ValidationError("crew_patch_empty")
        return attrs


class ProjectCrewShiftReplaceSerializer(StrictInputSerializer):
    work_dates = serializers.ListField(
        child=serializers.DateField(),
        min_length=1,
        max_length=62,
        error_messages={
            "required": "work_dates_required",
            "empty": "work_dates_required",
            "min_length": "work_dates_required",
        },
    )
    starts_at_time = serializers.TimeField()
    ends_at_time = serializers.TimeField()
    break_minutes = serializers.IntegerField(min_value=0, max_value=720, default=0)

    def validate_work_dates(self, value):
        return sorted(set(value))


class ProjectCrewShiftReleaseSerializer(StrictInputSerializer):
    work_dates = serializers.ListField(
        child=serializers.DateField(),
        min_length=1,
        max_length=62,
        error_messages={
            "required": "work_dates_required",
            "empty": "work_dates_required",
            "min_length": "work_dates_required",
        },
    )

    def validate_work_dates(self, value):
        return sorted(set(value))


class ProjectCrewPassengerWriteSerializer(StrictInputSerializer):
    """Validate the public passenger-roster command contract.

    Public names intentionally describe the employer's action.  The service
    layer keeps its shorter internal constants (``future`` / ``selected``).
    """

    SCOPE_ALL_FUTURE = "all_future"
    SCOPE_SELECTED_DATES = "selected_dates"

    connection_id = serializers.UUIDField()
    scope = serializers.ChoiceField(
        choices=(SCOPE_ALL_FUTURE, SCOPE_SELECTED_DATES),
        error_messages={"invalid_choice": "passenger_scope_invalid"},
    )
    effective_on = serializers.DateField(required=False)
    work_dates = serializers.ListField(
        child=serializers.DateField(),
        min_length=1,
        max_length=62,
        required=False,
        error_messages={
            "empty": "passenger_work_dates_required",
            "min_length": "passenger_work_dates_required",
        },
    )

    def validate_work_dates(self, value):
        return sorted(set(value))

    def validate(self, attrs):
        attrs = super().validate(attrs)
        scope = attrs.get("scope")
        has_effective_on = "effective_on" in attrs
        has_work_dates = "work_dates" in attrs
        if scope == self.SCOPE_ALL_FUTURE:
            if not has_effective_on:
                raise serializers.ValidationError(
                    {"effective_on": "passenger_effective_on_required"}
                )
            if has_work_dates:
                raise serializers.ValidationError(
                    {"work_dates": "passenger_work_dates_not_allowed"}
                )
        elif scope == self.SCOPE_SELECTED_DATES:
            if not has_work_dates:
                raise serializers.ValidationError(
                    {"work_dates": "passenger_work_dates_required"}
                )
            if has_effective_on:
                raise serializers.ValidationError(
                    {"effective_on": "passenger_effective_on_not_allowed"}
                )
        return attrs


class ProjectCrewDriverReplaceSerializer(StrictInputSerializer):
    """Validate one permanent driver/resource replacement command.

    The vehicle is intentionally not accepted from the client.  A permanent
    replacement transfers the crew's effective vehicle to the selected
    passenger, which prevents a stale mobile screen from silently moving a
    different fleet vehicle.
    """

    new_driver_connection_id = serializers.UUIDField()
    effective_on = serializers.DateField(required=False, default=timezone.localdate)


class ProjectCrewVehicleSwapSerializer(StrictInputSerializer):
    target_vehicle_id = serializers.UUIDField()
    effective_on = serializers.DateField(required=False, default=timezone.localdate)


class ProjectCrewDriverAbsenceSerializer(StrictInputSerializer):
    """Validate selected dates for marking or cancelling driver absence."""

    work_dates = serializers.ListField(
        child=serializers.DateField(),
        min_length=1,
        max_length=62,
        error_messages={
            "required": "work_dates_required",
            "empty": "work_dates_required",
            "min_length": "work_dates_required",
        },
    )

    def validate_work_dates(self, value):
        return sorted(set(value))


class ProjectCrewDriverSubstituteSerializer(ProjectCrewDriverAbsenceSerializer):
    """Validate a temporary substitute-driver command."""

    substitute_driver_connection_id = serializers.UUIDField()


class ProjectScheduleTemplateCreateSerializer(StrictInputSerializer):
    name = serializers.CharField(max_length=30)
    starts_at_time = serializers.TimeField()
    ends_at_time = serializers.TimeField()
    break_minutes = serializers.IntegerField(min_value=0, max_value=720, default=0)

    def validate_name(self, value):
        normalized = value.strip()
        if not normalized:
            raise serializers.ValidationError("project_schedule_template_name_required")
        return normalized

class VehicleCreateSerializer(StrictInputSerializer):
    internal_name = serializers.CharField(max_length=120)
    registration_identifier = serializers.CharField(max_length=64)
    seat_capacity = serializers.IntegerField(min_value=2, max_value=100)


class HousingAssignmentCreateSerializer(StrictInputSerializer):
    connection_id = serializers.UUIDField()
    place_id = serializers.UUIDField()
    check_in_at = serializers.DateTimeField()
    check_out_at = serializers.DateTimeField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs["check_out_at"] is not None and attrs["check_out_at"] <= attrs["check_in_at"]:
            raise serializers.ValidationError({"check_out_at": "period_end_must_be_after_start"})
        return attrs


class HousingAvailableWorkersQuerySerializer(StrictInputSerializer):
    check_in_at = serializers.DateTimeField()


class HousingAssignmentCheckOutSerializer(StrictInputSerializer):
    check_out_at = serializers.DateTimeField()


class WorkerProjectAssignmentCreateSerializer(StrictInputSerializer):
    connection_id = serializers.UUIDField()
    project_id = serializers.UUIDField()
    worker_role = serializers.CharField(max_length=160, required=False, allow_blank=True, default="")
    starts_at = serializers.DateTimeField()
    ends_at = serializers.DateTimeField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs["ends_at"] is not None and attrs["ends_at"] <= attrs["starts_at"]:
            raise serializers.ValidationError({"ends_at": "period_end_must_be_after_start"})
        return attrs


class DriverVehicleAssignmentCreateSerializer(StrictInputSerializer):
    driver_connection_id = serializers.UUIDField()
    vehicle_id = serializers.UUIDField()
    starts_on = serializers.DateField()
    ends_on = serializers.DateField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs["ends_on"] is not None and attrs["ends_on"] < attrs["starts_on"]:
            raise serializers.ValidationError({"ends_on": "period_end_must_not_be_before_start"})
        return attrs


class TransportRouteCreateSerializer(StrictInputSerializer):
    internal_name = serializers.CharField(max_length=160)
    driver_vehicle_assignment_id = serializers.UUIDField()
    starts_on = serializers.DateField()
    ends_on = serializers.DateField(required=False, allow_null=True, default=None)
    worksite_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    departure_time = serializers.TimeField(required=False, allow_null=True, default=None)
    reservation_expires_at = serializers.DateTimeField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs["ends_on"] is not None and attrs["ends_on"] < attrs["starts_on"]:
            raise serializers.ValidationError({"ends_on": "period_end_must_not_be_before_start"})
        return attrs


class RouteStopCreateSerializer(StrictInputSerializer):
    sequence = serializers.IntegerField(min_value=1, max_value=500)
    kind = serializers.ChoiceField(choices=("pickup", "dropoff"))
    label = serializers.CharField(max_length=160)
    housing_site_id = serializers.UUIDField(required=False, allow_null=True, default=None)


class RoutePassengerCreateSerializer(StrictInputSerializer):
    connection_id = serializers.UUIDField()
    pickup_stop_id = serializers.UUIDField()
    dropoff_stop_id = serializers.UUIDField()
    boarding_order = serializers.IntegerField(min_value=1, max_value=500, default=1)
