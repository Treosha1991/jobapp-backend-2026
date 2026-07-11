from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from jobs.models import Vacancy


TEXT_FIELDS = (
    "title",
    "city",
    "description",
    "housing_cost",
    "salary",
)

EDITABLE_BASELINE_FIELDS = (
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
    "additional_phone_2",
    "additional_phone_3",
    "hide_primary_phone",
    "whatsapp",
    "viber",
    "telegram_username",
    "telegram",
    "email",
    "source",
)


def looks_broken(value):
    text = (value or "").strip()
    if not text:
        return False
    if "\ufffd" in text:
        return True
    if "???" in text:
        return True
    question_marks = text.count("?")
    return question_marks >= 3 and question_marks / max(len(text), 1) > 0.2


def is_clean_candidate(value):
    text = (value or "").strip()
    return bool(text) and not looks_broken(text)


def preview(value):
    return str(value or "").replace("\n", " ")[:160]


class Command(BaseCommand):
    help = "Restore broken question-mark vacancy text from moderation_baseline."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually save fixes. Without this flag the command only reports what would change.",
        )
        parser.add_argument(
            "--ids",
            default="",
            help="Optional comma-separated vacancy IDs to inspect/fix.",
        )
        parser.add_argument(
            "--show",
            action="store_true",
            help="Print current values and moderation_baseline values for inspected vacancies.",
        )
        parser.add_argument(
            "--restore-baseline",
            action="store_true",
            help="For explicit --ids only: restore all editable fields present in moderation_baseline.",
        )

    def handle(self, *args, **options):
        apply_changes = bool(options["apply"])
        show_values = bool(options["show"])
        restore_baseline = bool(options["restore_baseline"])
        raw_ids = (options.get("ids") or "").strip()

        qs = Vacancy.objects.all().order_by("id")
        explicit_ids = bool(raw_ids)
        if explicit_ids:
            ids = [int(part.strip()) for part in raw_ids.split(",") if part.strip()]
            qs = qs.filter(id__in=ids)
        else:
            if restore_baseline:
                raise SystemExit("--restore-baseline requires explicit --ids")
            query = Q()
            for field in TEXT_FIELDS:
                query |= Q(**{f"{field}__contains": "?"})
            qs = qs.filter(query)

        inspected = 0
        fixed = 0
        unresolved = []

        for vacancy in qs:
            inspected += 1
            baseline = vacancy.moderation_baseline or {}
            changes = {}
            unresolved_fields = []
            status = vacancy.moderation_status

            self.stdout.write(self.style.NOTICE(f"Vacancy #{vacancy.id} status={status}"))

            if show_values:
                for field in TEXT_FIELDS:
                    self.stdout.write(f"  current.{field}: {preview(getattr(vacancy, field, ''))}")
                    self.stdout.write(f"  baseline.{field}: {preview(baseline.get(field, ''))}")
                for field in ("country", "category", "housing_type", "salary_from", "salary_to", "salary_currency", "salary_tax_type"):
                    self.stdout.write(f"  current.{field}: {preview(getattr(vacancy, field, ''))}")
                    self.stdout.write(f"  baseline.{field}: {preview(baseline.get(field, ''))}")

            if restore_baseline:
                for field in EDITABLE_BASELINE_FIELDS:
                    if field not in baseline:
                        continue
                    current = getattr(vacancy, field, None)
                    baseline_value = baseline.get(field)
                    if current != baseline_value:
                        changes[field] = baseline_value
            else:
                for field in TEXT_FIELDS:
                    current = getattr(vacancy, field, "") or ""
                    if not looks_broken(current):
                        continue
                    baseline_value = baseline.get(field, "")
                    if is_clean_candidate(baseline_value):
                        changes[field] = baseline_value
                    else:
                        unresolved_fields.append(field)

            if changes:
                fixed += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"  restore fields: {', '.join(changes.keys())}"
                    )
                )
                for field, value in changes.items():
                    self.stdout.write(f"    - {field}: {preview(getattr(vacancy, field, ''))} -> {preview(value)}")
                if apply_changes:
                    with transaction.atomic():
                        for field, value in changes.items():
                            setattr(vacancy, field, value)
                        vacancy.save(update_fields=[*changes.keys()])
            else:
                self.stdout.write("  no changes")

            if unresolved_fields:
                unresolved.append((vacancy.id, status, unresolved_fields))
                self.stdout.write(
                    self.style.ERROR(
                        f"  broken fields without clean baseline: {', '.join(unresolved_fields)}"
                    )
                )

        mode = "APPLIED" if apply_changes else "DRY RUN"
        self.stdout.write(self.style.SUCCESS(f"{mode}: inspected={inspected}, fixable={fixed}, unresolved={len(unresolved)}"))
        if unresolved:
            self.stdout.write("Unresolved vacancy IDs: " + ", ".join(str(item[0]) for item in unresolved))
