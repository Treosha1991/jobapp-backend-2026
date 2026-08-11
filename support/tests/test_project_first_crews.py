from datetime import date, datetime, timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from support.models import (
    ProjectCrew,
    ProjectCrewPassenger,
    ProjectCrewResourceAssignment,
    ProjectCrewShift,
    ProjectCrewShiftMember,
    SupportApplication,
    SupportConnection,
    SupportVacancy,
    Vehicle,
    WorkProject,
    Worksite,
)
from support.services.organizations import activate_organization, create_organization


@override_settings(SUPPORT_FEATURE_ENABLED=True)
class ProjectFirstCrewModelTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(
            username="project-crew-operator",
            email="project-crew-operator@example.com",
            password="password",
            is_staff=True,
        )
        self.owner = User.objects.create_user(
            username="project-crew-owner",
            email="project-crew-owner@example.com",
            password="password",
        )
        self.organization, _ = create_organization(
            jobhub_operator=self.operator,
            legal_name="Project Crew Agency sp. z o.o.",
            display_name="Project Crew Agency",
            owner_email=self.owner.email,
        )
        activate_organization(jobhub_operator=self.operator, organization=self.organization)
        self.worksite = Worksite.objects.create(
            organization=self.organization,
            internal_name="Project-first worksite",
            country_code="NL",
            city="Lelystad",
            street="Crewstraat",
            building="1",
            created_by=self.owner,
        )
        self.project = WorkProject.objects.create(
            organization=self.organization,
            worksite=self.worksite,
            internal_name="Project-first project",
            worker_visible_name="Project-first project",
            worker_capacity=20,
            created_by=self.owner,
        )
        self.driver = self._connection("driver", "Driver", has_driving_license=True)
        self.second_driver = self._connection("second-driver", "Second", has_driving_license=True)
        self.passenger = self._connection("passenger", "Passenger")
        self.vehicle = self._vehicle("CREW-PF-01")
        self.second_vehicle = self._vehicle("CREW-PF-02")
        self.crew = ProjectCrew.objects.create(
            organization=self.organization,
            project=self.project,
            internal_name="Crew A",
            created_by=self.owner,
        )

    def _connection(self, suffix, first_name, *, has_driving_license=False):
        user = User.objects.create_user(
            username=f"project-crew-{suffix}",
            email=f"project-crew-{suffix}@example.com",
            password="password",
            first_name=first_name,
            last_name="Worker",
        )
        vacancy = SupportVacancy.objects.create(
            organization=self.organization,
            internal_title=f"Project crew vacancy {suffix}",
            created_by=self.owner,
        )
        application = SupportApplication.objects.create(
            vacancy=vacancy,
            candidate=user,
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
            candidate=user,
            stage=SupportConnection.STAGE_COORDINATOR,
            has_driving_license=has_driving_license,
        )

    def _vehicle(self, registration_identifier):
        return Vehicle.objects.create(
            organization=self.organization,
            internal_name=registration_identifier,
            registration_identifier=registration_identifier,
            seat_capacity=5,
            created_by=self.owner,
        )

    def _shift(self, work_date=date(2026, 8, 12)):
        starts_at = timezone.make_aware(datetime.combine(work_date, datetime.min.time())).replace(hour=6)
        return ProjectCrewShift.objects.create(
            crew=self.crew,
            work_date=work_date,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=8, minutes=45),
            break_minutes=45,
            created_by=self.owner,
        )

    def test_resource_assignment_is_effective_dated_and_exclusive(self):
        current = ProjectCrewResourceAssignment.objects.create(
            crew=self.crew,
            driver_connection=self.driver,
            vehicle=self.vehicle,
            starts_on=date(2026, 8, 11),
            created_by=self.owner,
        )
        current.full_clean()

        other_crew = ProjectCrew.objects.create(
            organization=self.organization,
            project=self.project,
            internal_name="Crew B",
            created_by=self.owner,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProjectCrewResourceAssignment.objects.create(
                crew=other_crew,
                driver_connection=self.driver,
                vehicle=self.second_vehicle,
                starts_on=date(2026, 8, 11),
                created_by=self.owner,
            )

        current.ends_on = date(2026, 8, 12)
        current.save(update_fields=("ends_on", "updated_at"))
        replacement = ProjectCrewResourceAssignment.objects.create(
            crew=self.crew,
            driver_connection=self.second_driver,
            vehicle=self.vehicle,
            starts_on=date(2026, 8, 13),
            created_by=self.owner,
        )
        replacement.full_clean()

    def test_worker_without_driving_licence_cannot_be_driver(self):
        assignment = ProjectCrewResourceAssignment(
            crew=self.crew,
            driver_connection=self.passenger,
            vehicle=self.vehicle,
            starts_on=date(2026, 8, 11),
            created_by=self.owner,
        )
        with self.assertRaises(ValidationError) as error:
            assignment.full_clean()
        self.assertIn("driver_connection", error.exception.message_dict)

    def test_default_passenger_and_daily_snapshot_are_separate(self):
        roster_entry = ProjectCrewPassenger.objects.create(
            crew=self.crew,
            connection=self.passenger,
            starts_on=date(2026, 8, 11),
            created_by=self.owner,
        )
        roster_entry.full_clean()
        shift = self._shift()
        driver_entry = ProjectCrewShiftMember.objects.create(
            shift=shift,
            connection=self.driver,
            role=ProjectCrewShiftMember.ROLE_DRIVER,
            vehicle=self.vehicle,
            created_by=self.owner,
        )
        passenger_entry = ProjectCrewShiftMember.objects.create(
            shift=shift,
            connection=self.passenger,
            role=ProjectCrewShiftMember.ROLE_PASSENGER,
            created_by=self.owner,
        )
        driver_entry.full_clean()
        passenger_entry.full_clean()

        self.assertEqual(self.crew.passenger_assignments.count(), 1)
        self.assertEqual(shift.members.count(), 2)
        self.assertEqual(shift.members.get(role="passenger").connection, self.passenger)

    def test_one_driver_per_day_and_shift_validation(self):
        shift = self._shift()
        ProjectCrewShiftMember.objects.create(
            shift=shift,
            connection=self.driver,
            role=ProjectCrewShiftMember.ROLE_DRIVER,
            vehicle=self.vehicle,
            created_by=self.owner,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProjectCrewShiftMember.objects.create(
                shift=shift,
                connection=self.second_driver,
                role=ProjectCrewShiftMember.ROLE_DRIVER,
                vehicle=self.second_vehicle,
                created_by=self.owner,
            )

        invalid = ProjectCrewShift(
            crew=self.crew,
            work_date=date(2026, 8, 13),
            starts_at=shift.starts_at + timedelta(days=1),
            ends_at=shift.starts_at + timedelta(days=1, minutes=30),
            break_minutes=30,
            created_by=self.owner,
        )
        with self.assertRaises(ValidationError) as error:
            invalid.full_clean()
        self.assertIn("break_minutes", error.exception.message_dict)
