"""Create an idempotent, non-production-only Support visual-test workspace.

The command has no effect until SUPPORT_DEMO_SEED=1 is configured explicitly.
It never belongs in the production service build command.
"""

from datetime import timedelta
from os import environ

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from support.models import (
    EmploymentExclusivityLock,
    OrganizationMembership,
    SupportAccessGrant,
    SupportApplication,
    SupportConnection,
    SupportOrganization,
    SupportVacancy,
)
from support.services.organizations import activate_organization, create_organization


DEMO_OWNER_EMAIL = "support-owner@jobhub.test"
DEMO_WORKER_EMAIL = "support-worker@jobhub.test"
DEMO_COORDINATOR_EMAIL = "support-coordinator@jobhub.test"


class Command(BaseCommand):
    help = "Seed an isolated JobHub Support demo workspace when explicitly enabled."

    def handle(self, *args, **options):
        if environ.get("SUPPORT_DEMO_SEED", "").strip() != "1":
            self.stdout.write("Support demo seed is disabled.")
            return

        password = environ.get("SUPPORT_DEMO_PASSWORD", "")
        if len(password) < 12:
            raise CommandError("support_demo_password_is_missing_or_too_short")

        user_model = get_user_model()
        owner, owner_created = user_model.objects.get_or_create(
            username=DEMO_OWNER_EMAIL,
            defaults={
                "email": DEMO_OWNER_EMAIL,
                "first_name": "Demo",
                "last_name": "Manager",
                "is_active": True,
                "is_staff": True,
            },
        )
        changed_owner_fields = []
        for field, value in {
            "email": DEMO_OWNER_EMAIL,
            "first_name": "Demo",
            "last_name": "Manager",
            "is_active": True,
            "is_staff": True,
        }.items():
            if getattr(owner, field) != value:
                setattr(owner, field, value)
                changed_owner_fields.append(field)
        if owner_created or changed_owner_fields or not owner.check_password(password):
            owner.set_password(password)
            owner.save(update_fields=[*changed_owner_fields, "password"])

        worker, worker_created = user_model.objects.get_or_create(
            username=DEMO_WORKER_EMAIL,
            defaults={
                "email": DEMO_WORKER_EMAIL,
                "first_name": "Demo",
                "last_name": "Worker",
                "is_active": True,
            },
        )
        changed_worker_fields = []
        for field, value in {
            "email": DEMO_WORKER_EMAIL,
            "first_name": "Demo",
            "last_name": "Worker",
            "is_active": True,
        }.items():
            if getattr(worker, field) != value:
                setattr(worker, field, value)
                changed_worker_fields.append(field)
        if worker_created or changed_worker_fields or not worker.check_password(password):
            worker.set_password(password)
            worker.save(update_fields=[*changed_worker_fields, "password"])

        coordinator, coordinator_created = user_model.objects.get_or_create(
            username=DEMO_COORDINATOR_EMAIL,
            defaults={
                "email": DEMO_COORDINATOR_EMAIL,
                "first_name": "Demo",
                "last_name": "Coordinator",
                "is_active": True,
            },
        )
        changed_coordinator_fields = []
        for field, value in {
            "email": DEMO_COORDINATOR_EMAIL,
            "first_name": "Demo",
            "last_name": "Coordinator",
            "is_active": True,
        }.items():
            if getattr(coordinator, field) != value:
                setattr(coordinator, field, value)
                changed_coordinator_fields.append(field)
        if (
            coordinator_created
            or changed_coordinator_fields
            or not coordinator.check_password(password)
        ):
            coordinator.set_password(password)
            coordinator.save(update_fields=[*changed_coordinator_fields, "password"])

        organization, created = SupportOrganization.objects.get_or_create(
            legal_name="JobHub Support Demo B.V.",
            defaults={
                "display_name": "JobHub Support — demo",
                "created_by": owner,
            },
        )
        if created:
            membership = OrganizationMembership.objects.create(
                organization=organization,
                user=owner,
                display_role="Demo owner",
                is_owner=True,
                accepted_at=timezone.now(),
                created_by=owner,
            )
        else:
            membership, _ = OrganizationMembership.objects.get_or_create(
                organization=organization,
                user=owner,
                defaults={
                    "display_role": "Demo owner",
                    "is_owner": True,
                    "accepted_at": timezone.now(),
                    "created_by": owner,
                },
            )
            if not membership.is_owner or not membership.is_active:
                membership.is_owner = True
                membership.state = OrganizationMembership.STATE_ACTIVE
                membership.save(update_fields=["is_owner", "state", "updated_at"])

        if organization.status != SupportOrganization.STATUS_ACTIVE:
            activate_organization(jobhub_operator=owner, organization=organization)
            organization.refresh_from_db()

        vacancy, _ = SupportVacancy.objects.get_or_create(
            organization=organization,
            internal_title="Демонстрация: склад в Нидерландах",
            defaults={
                "status": SupportVacancy.STATUS_PUBLISHED,
                "internal_position_limit": 10,
                "created_by": owner,
                "published_at": timezone.now(),
            },
        )
        application, _ = SupportApplication.objects.get_or_create(
            vacancy=vacancy,
            candidate=worker,
            revision=1,
            defaults={
                "preferred_language": "ru",
                "citizenship_country_code": "UA",
                "current_country_code": "PL",
                "availability_note": "Демонстрационный аккаунт для визуального теста.",
                "consent_version": "demo-v1",
                "consented_at": timezone.now(),
                "status": SupportApplication.STATUS_APPROVED,
            },
        )
        connection, _ = SupportConnection.objects.get_or_create(
            application=application,
            defaults={
                "organization": organization,
                "vacancy": vacancy,
                "candidate": worker,
                "assigned_manager": membership,
                "stage": SupportConnection.STAGE_ACTIVE_WORKER,
                "visible_stage": "Работа и поддержка",
            },
        )
        if connection.stage != SupportConnection.STAGE_ACTIVE_WORKER:
            connection.stage = SupportConnection.STAGE_ACTIVE_WORKER
            connection.visible_stage = "Работа и поддержка"
            connection.assigned_manager = membership
            connection.save(
                update_fields=["stage", "visible_stage", "assigned_manager", "updated_at"]
            )
        EmploymentExclusivityLock.objects.get_or_create(
            candidate=worker,
            connection=connection,
            defaults={"state": EmploymentExclusivityLock.STATE_ACTIVE},
        )

        access_grant = (
            SupportAccessGrant.objects.filter(
                user=worker,
                organization=organization,
                reason=SupportAccessGrant.REASON_TECHNICAL,
            )
            .order_by("-ends_at", "-id")
            .first()
        )
        access_end = timezone.now() + timedelta(days=365)
        if access_grant is None:
            SupportAccessGrant.objects.create(
                user=worker,
                organization=organization,
                granted_by=owner,
                starts_at=timezone.now(),
                ends_at=access_end,
                reason=SupportAccessGrant.REASON_TECHNICAL,
                status=SupportAccessGrant.STATUS_ACTIVE,
            )
        else:
            access_grant.granted_by = owner
            access_grant.starts_at = timezone.now()
            access_grant.ends_at = access_end
            access_grant.status = SupportAccessGrant.STATUS_ACTIVE
            access_grant.revoked_at = None
            access_grant.revoked_by = None
            access_grant.save(
                update_fields=[
                    "granted_by",
                    "starts_at",
                    "ends_at",
                    "status",
                    "revoked_at",
                    "revoked_by",
                    "updated_at",
                ]
            )

        self.stdout.write(
            self.style.SUCCESS(
                "JobHub Support demo workspace is ready: "
                f"owner={DEMO_OWNER_EMAIL}, worker={DEMO_WORKER_EMAIL}, "
                f"coordinator={DEMO_COORDINATOR_EMAIL}."
            )
        )
