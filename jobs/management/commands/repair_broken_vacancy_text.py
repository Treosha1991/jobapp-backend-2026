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

    def handle(self, *args, **options):
        apply_changes = bool(options["apply"])
        raw_ids = (options.get("ids") or "").strip()

        qs = Vacancy.objects.all().order_by("id")
        if raw_ids:
            ids = [int(part.strip()) for part in raw_ids.split(",") if part.strip()]
            qs = qs.filter(id__in=ids)
        else:
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

            for field in TEXT_FIELDS:
                current = getattr(vacancy, field, "") or ""
                if not looks_broken(current):
                    continue
                baseline_value = baseline.get(field, "")
                if is_clean_candidate(baseline_value):
                    changes[field] = baseline_value
                else:
                    unresolved_fields.append(field)

            status = vacancy.moderation_status
            if changes:
                fixed += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"Vacancy #{vacancy.id} ({status}) restore fields: {', '.join(changes.keys())}"
                    )
                )
                for field, value in changes.items():
                    preview = str(value).replace("\n", " ")[:120]
                    self.stdout.write(f"  - {field}: {preview}")
                if apply_changes:
                    with transaction.atomic():
                        for field, value in changes.items():
                            setattr(vacancy, field, value)
                        vacancy.save(update_fields=[*changes.keys()])

            if unresolved_fields:
                unresolved.append((vacancy.id, status, unresolved_fields))
                self.stdout.write(
                    self.style.ERROR(
                        f"Vacancy #{vacancy.id} ({status}) has broken fields without clean baseline: "
                        f"{', '.join(unresolved_fields)}"
                    )
                )

        mode = "APPLIED" if apply_changes else "DRY RUN"
        self.stdout.write(self.style.SUCCESS(f"{mode}: inspected={inspected}, fixable={fixed}, unresolved={len(unresolved)}"))
        if unresolved:
            self.stdout.write("Unresolved vacancy IDs: " + ", ".join(str(item[0]) for item in unresolved))
