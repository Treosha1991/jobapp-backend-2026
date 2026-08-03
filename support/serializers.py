import re
import uuid

from rest_framework import serializers

from .permission_codes import ALL_PERMISSION_CODES


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

    def validate(self, attrs):
        if isinstance(self.initial_data, dict):
            unknown_fields = sorted(set(self.initial_data) - set(self.fields))
            if unknown_fields:
                raise serializers.ValidationError(
                    {"non_field_errors": "unsupported_support_field"}
                )
        return attrs


class WorkerAccessScopeCreateSerializer(StrictInputSerializer):
    connection_id = serializers.UUIDField()


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
        choices=("day_off", "vacation", "unpaid_absence", "unable_today", "exit_request")
    )
    starts_on = serializers.DateField()
    ends_on = serializers.DateField()
    worker_note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")

    def validate_worker_note(self, value):
        return value.strip()


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
        return attrs


class ApplicationReviewSerializer(StrictInputSerializer):
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


class ConnectionTransitionSerializer(StrictInputSerializer):
    next_stage = serializers.ChoiceField(
        choices=("documents_stage", "coordinator_stage", "active_worker", "closed")
    )
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class SupportMessageCreateSerializer(StrictInputSerializer):
    original_language = serializers.ChoiceField(choices=("ru", "en", "pl", "uk"))
    body = serializers.CharField(max_length=1500)
    client_message_id = serializers.UUIDField(required=False, default=uuid.uuid4)

    def validate_body(self, value):
        normalized = value.strip()
        if not normalized:
            raise serializers.ValidationError("message_body_required")
        return normalized


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
        if attrs["ends_on"] is not None and attrs["ends_on"] <= attrs["starts_on"]:
            raise serializers.ValidationError({"ends_on": "period_end_must_be_after_start"})
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
        if attrs["ends_on"] is not None and attrs["ends_on"] <= attrs["starts_on"]:
            raise serializers.ValidationError({"ends_on": "period_end_must_be_after_start"})
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
