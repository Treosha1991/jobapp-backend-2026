from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from support.models import SupportOrganization
from support.services.project_first_reset import (
    build_project_first_reset_plan,
    execute_project_first_reset,
)


class Command(BaseCommand):
    help = (
        "Preview or perform the guarded JobHub Support project-first staging "
        "reset for one organization. Workers, housing, vehicles, and factual "
        "work-time entries are preserved."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--include-work-time",
            action="store_true",
            help="Also delete factual work-time entries for this staging organization.",
        )
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
        include_work_time = options["include_work_time"]
        plan = build_project_first_reset_plan(
            organization,
            include_work_time=include_work_time,
        )
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

        expected_confirmation = plan["confirmation"]
        if options["confirm"] != expected_confirmation:
            raise CommandError(
                "confirmation_mismatch: expected " + expected_confirmation
            )

        execute_project_first_reset(
            organization=organization,
            actor=actor,
            include_work_time=include_work_time,
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
