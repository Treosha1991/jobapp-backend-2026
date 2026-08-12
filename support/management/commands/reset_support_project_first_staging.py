from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from support.models import SupportOrganization
from support.services.audit import record_audit_event
from support.services.project_first_reset import (
    build_project_first_reset_plan,
    preserved_counts,
    reset_target_querysets,
)


class Command(BaseCommand):
    help = (
        "Preview or perform the guarded JobHub Support project-first staging "
        "reset for one organization. Workers, housing, vehicles, and factual "
        "work-time entries are preserved."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--organization",
            required=True,
            help="Support organization public UUID.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Perform the reset. Without this flag the command is read-only.",
        )
        parser.add_argument(
            "--confirm",
            default="",
            help="Required exact phrase: RESET-<organization-public-uuid>.",
        )
        parser.add_argument(
            "--actor-email",
            default="",
            help="Optional active staff user recorded as the operator.",
        )

    def handle(self, *args, **options):
        organization = SupportOrganization.objects.filter(
            public_id=options["organization"]
        ).first()
        if organization is None:
            raise CommandError("support_organization_not_found")

        actor = self._resolve_actor(options["actor_email"])
        plan = build_project_first_reset_plan(organization)
        targets = reset_target_querysets(organization)
        preserved = plan["preserve_counts"]
        target_counts = plan["delete_counts"]

        self.stdout.write(
            f"Organization: {organization.display_name} ({organization.public_id})"
        )
        self.stdout.write("Objects scheduled for deletion:")
        for label, count in target_counts.items():
            self.stdout.write(f"  {label}: {count}")
        self.stdout.write("Objects that must be preserved:")
        for label, count in preserved.items():
            self.stdout.write(f"  {label}: {count}")

        if not options["apply"]:
            self.stdout.write(
                self.style.WARNING(
                    "DRY RUN: no data changed. Use --apply only after reviewing "
                    "this organization-specific plan."
                )
            )
            return

        if not getattr(settings, "SUPPORT_PROJECT_FIRST_RESET_ALLOWED", False):
            raise CommandError("support_project_first_reset_not_allowed")

        expected_confirmation = f"RESET-{organization.public_id}"
        if options["confirm"] != expected_confirmation:
            raise CommandError(
                "confirmation_mismatch: expected " + expected_confirmation
            )

        with transaction.atomic():
            # Ordered from the most dependent operational records toward the
            # project/worksite roots.  Vehicle and housing registries are never
            # included in this list.
            for queryset in targets.values():
                queryset.delete()

            after = preserved_counts(organization)
            if after != preserved:
                raise CommandError("preserved_data_count_changed; transaction_rolled_back")

            remaining = {
                label: queryset.count()
                for label, queryset in reset_target_querysets(organization).items()
                if queryset.exists()
            }
            if remaining:
                raise CommandError(
                    "reset_targets_remain; transaction_rolled_back: "
                    + ", ".join(f"{key}={value}" for key, value in remaining.items())
                )

            record_audit_event(
                organization=organization,
                actor=actor,
                action="project_first.staging_reset",
                target=organization,
                details={
                    "deleted_counts": dict(target_counts),
                    "preserved_counts": dict(preserved),
                },
            )

        self.stdout.write(
            self.style.SUCCESS(
                "RESET COMPLETE: legacy and preview project operations were "
                "removed; workers, housing, vehicles, and factual time entries "
                "were preserved."
            )
        )

    @staticmethod
    def _resolve_actor(actor_email):
        actor_email = actor_email.strip()
        if not actor_email:
            return None
        actor = get_user_model().objects.filter(
            email__iexact=actor_email,
            is_active=True,
            is_staff=True,
        ).first()
        if actor is None:
            raise CommandError("active_jobhub_operator_not_found")
        return actor
