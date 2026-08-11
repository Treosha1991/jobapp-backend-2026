from datetime import date, datetime, time, timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from support.models import (
    AuditEvent,
    DriverVehicleAssignment,
    HousingAssignment,
    HousingPlace,
    HousingRoom,
    HousingSite,
    ProjectCrew,
    ProjectScheduleTemplate,
    ScheduledShiftBatch,
    ScheduledWorkShift,
    ShiftTemplate,
    SupportApplication,
    SupportConnection,
    SupportVacancy,
    TransportCrew,
    TransportRoute,
    Vehicle,
    WorkerProjectAssignment,
    WorkProject,
    WorkTimeEntry,
    Worksite,
)
from support.services.organizations import activate_organization, create_organization
from support.services.project_crews import create_project_crew


@override_settings(SUPPORT_FEATURE_ENABLED=True)
class ProjectFirstResetCommandTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(
            username="reset-operator",
            email="reset-operator@example.com",
            password="password",
            is_staff=True,
        )
        self.owner = User.objects.create_user(
            username="reset-owner",
            email="reset-owner@example.com",
            password="password",
        )
        self.organization, _ = create_organization(
            jobhub_operator=self.operator,
            legal_name="Reset Pilot sp. z o.o.",
            display_name="Reset Pilot",
            owner_email=self.owner.email,
        )
        activate_organization(
            jobhub_operator=self.operator,
            organization=self.organization,
        )
        self.connection = self._connection()
        self._create_housing()
        self._create_project_operations()

    def _connection(self):
        worker = User.objects.create_user(
            username="reset-worker",
            email="reset-worker@example.com",
            password="password",
            first_name="Reset",
            last_name="Worker",
        )
        vacancy = SupportVacancy.objects.create(
            organization=self.organization,
            internal_title="Reset vacancy",
            created_by=self.owner,
        )
        application = SupportApplication.objects.create(
            vacancy=vacancy,
            candidate=worker,
            revision=1,
            preferred_language="ru",
            citizenship_country_code="BY",
            current_country_code="PL",
            consent_version="support-application-v1",
            consented_at=timezone.now(),
        )
        return SupportConnection.objects.create(
            organization=self.organization,
            vacancy=vacancy,
            application=application,
            candidate=worker,
            stage=SupportConnection.STAGE_ACTIVE_WORKER,
            has_driving_license=True,
        )

    def _create_housing(self):
        site = HousingSite.objects.create(
            organization=self.organization,
            internal_name="Reset home",
            country_code="NL",
            city="Lelystad",
            street="Resetstraat",
            building="1",
            created_by=self.owner,
        )
        room = HousingRoom.objects.create(site=site, label="1A", capacity=1)
        place = HousingPlace.objects.create(room=room, label="1")
        HousingAssignment.objects.create(
            organization=self.organization,
            connection=self.connection,
            place=place,
            check_in_at=timezone.now(),
            state=HousingAssignment.STATE_PUBLISHED,
            created_by=self.owner,
            published_by=self.owner,
            published_at=timezone.now(),
        )

    def _create_project_operations(self):
        worksite = Worksite.objects.create(
            organization=self.organization,
            internal_name="Reset worksite",
            country_code="NL",
            city="Lelystad",
            street="Projectstraat",
            building="2",
            created_by=self.owner,
        )
        project = WorkProject.objects.create(
            organization=self.organization,
            worksite=worksite,
            internal_name="Reset project",
            worker_visible_name="Reset project",
            worker_capacity=5,
            created_by=self.owner,
        )
        project_template = ProjectScheduleTemplate.objects.create(
            project=project,
            name="Morning",
            starts_at_time=time(6, 0),
            ends_at_time=time(14, 0),
            created_by=self.owner,
        )
        self.vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Reset car",
            registration_identifier="RESET-01",
            seat_capacity=4,
            created_by=self.owner,
        )
        second_vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Preview car",
            registration_identifier="RESET-02",
            seat_capacity=4,
            created_by=self.owner,
        )
        driver_assignment = DriverVehicleAssignment.objects.create(
            organization=self.organization,
            driver_connection=self.connection,
            vehicle=self.vehicle,
            starts_on=date(2026, 8, 1),
            state=DriverVehicleAssignment.STATE_PUBLISHED,
            created_by=self.owner,
            published_by=self.owner,
            published_at=timezone.now(),
        )
        worker_assignment = WorkerProjectAssignment.objects.create(
            organization=self.organization,
            connection=self.connection,
            project=project,
            starts_at=timezone.make_aware(datetime(2026, 8, 1, 6, 0)),
            state=WorkerProjectAssignment.STATE_PUBLISHED,
            created_by=self.owner,
            published_by=self.owner,
            published_at=timezone.now(),
        )
        legacy_crew = TransportCrew.objects.create(
            organization=self.organization,
            project=project,
            schedule_template=project_template,
            internal_name="Legacy crew",
            starts_on=date(2026, 8, 1),
            created_by=self.owner,
        )
        TransportRoute.objects.create(
            organization=self.organization,
            internal_name="Legacy route",
            worksite=worksite,
            schedule_template=project_template,
            crew=legacy_crew,
            driver_vehicle_assignment=driver_assignment,
            starts_on=date(2026, 8, 1),
            state=TransportRoute.STATE_PUBLISHED,
            created_by=self.owner,
            published_by=self.owner,
            published_at=timezone.now(),
        )
        shift_template = ShiftTemplate.objects.create(
            organization=self.organization,
            name="Legacy shift",
            starts_at_time=time(6, 0),
            ends_at_time=time(14, 0),
            created_by=self.owner,
        )
        batch = ScheduledShiftBatch.objects.create(
            organization=self.organization,
            template=shift_template,
            starts_on=date(2026, 8, 1),
            ends_on=date(2026, 8, 1),
            weekdays=[5],
            created_by=self.owner,
        )
        starts_at = timezone.make_aware(datetime(2026, 8, 1, 6, 0))
        ends_at = starts_at + timedelta(hours=8)
        shift = ScheduledWorkShift.objects.create(
            organization=self.organization,
            connection=self.connection,
            batch=batch,
            work_assignment=worker_assignment,
            schedule_template=project_template,
            crew=legacy_crew,
            work_date=date(2026, 8, 1),
            starts_at=starts_at,
            ends_at=ends_at,
            state=ScheduledWorkShift.STATE_PUBLISHED,
            created_by=self.owner,
            published_by=self.owner,
            published_at=timezone.now(),
        )
        self.time_entry = WorkTimeEntry.objects.create(
            organization=self.organization,
            connection=self.connection,
            scheduled_shift=shift,
            work_date=date(2026, 8, 1),
            started_at=starts_at,
            ended_at=ends_at,
            break_minutes=0,
            worked_minutes=480,
            submitted_at=timezone.now(),
        )
        create_project_crew(
            actor=self.owner,
            organization=self.organization,
            project=project,
            driver_connection=self.connection,
            vehicle=second_vehicle,
            internal_name="Preview crew",
            starts_on=date(2026, 8, 1),
        )

    def _command(self, *args, **kwargs):
        output = StringIO()
        call_command(
            "reset_support_project_first_staging",
            organization=str(self.organization.public_id),
            stdout=output,
            *args,
            **kwargs,
        )
        return output.getvalue()

    def test_dry_run_is_read_only(self):
        output = self._command()

        self.assertIn("DRY RUN", output)
        self.assertEqual(WorkProject.objects.count(), 1)
        self.assertEqual(ProjectCrew.objects.count(), 1)
        self.assertEqual(Vehicle.objects.count(), 2)
        self.assertEqual(HousingAssignment.objects.count(), 1)

    def test_apply_requires_server_side_guard_and_exact_confirmation(self):
        confirmation = f"RESET-{self.organization.public_id}"
        with self.assertRaisesMessage(
            CommandError, "support_project_first_reset_not_allowed"
        ):
            self._command(apply=True, confirm=confirmation)

        with override_settings(SUPPORT_PROJECT_FIRST_RESET_ALLOWED=True):
            with self.assertRaisesMessage(CommandError, "confirmation_mismatch"):
                self._command(apply=True, confirm="RESET-WRONG")

        self.assertEqual(WorkProject.objects.count(), 1)

    @override_settings(SUPPORT_PROJECT_FIRST_RESET_ALLOWED=True)
    def test_apply_clears_operations_and_preserves_worker_housing_fleet_and_time(self):
        output = self._command(
            apply=True,
            confirm=f"RESET-{self.organization.public_id}",
            actor_email=self.operator.email,
        )

        self.assertIn("RESET COMPLETE", output)
        for model in (
            ProjectCrew,
            TransportCrew,
            ScheduledWorkShift,
            TransportRoute,
            DriverVehicleAssignment,
            WorkerProjectAssignment,
            ProjectScheduleTemplate,
            WorkProject,
            Worksite,
            ScheduledShiftBatch,
            ShiftTemplate,
        ):
            self.assertEqual(model.objects.count(), 0, model.__name__)

        self.assertEqual(SupportConnection.objects.count(), 1)
        self.assertEqual(HousingAssignment.objects.count(), 1)
        self.assertEqual(HousingPlace.objects.count(), 1)
        self.assertEqual(Vehicle.objects.count(), 2)
        self.assertEqual(WorkTimeEntry.objects.count(), 1)
        self.time_entry.refresh_from_db()
        self.assertIsNone(self.time_entry.scheduled_shift_id)
        self.assertTrue(
            AuditEvent.objects.filter(
                organization=self.organization,
                actor=self.operator,
                action="project_first.staging_reset",
            ).exists()
        )
