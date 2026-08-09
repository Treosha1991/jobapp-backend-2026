from datetime import date, time, timedelta
from importlib import import_module

from django.apps import apps
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from support.models import (
    DriverVehicleAssignment,
    ProjectScheduleTemplate,
    RouteStop,
    SupportApplication,
    SupportConnection,
    SupportVacancy,
    TransportCrew,
    TransportCrewDriver,
    TransportCrewMember,
    TransportCrewScheduleOverride,
    TransportCrewVehicle,
    TransportPassengerAssignment,
    TransportRoute,
    Vehicle,
    WorkProject,
    Worksite,
)
from support.services.organizations import create_organization


@override_settings(SUPPORT_FEATURE_ENABLED=True)
class StableTransportCrewModelTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(
            username="crew-operator",
            email="crew-operator@example.com",
            password="password",
            is_staff=True,
        )
        self.owner = User.objects.create_user(
            username="crew-owner",
            email="crew-owner@example.com",
            password="password",
        )
        self.organization, _ = create_organization(
            jobhub_operator=self.operator,
            legal_name="Stable Crew Agency sp. z o.o.",
            display_name="Stable Crew Agency",
            owner_email=self.owner.email,
        )
        self.worksite = Worksite.objects.create(
            organization=self.organization,
            internal_name="Crew worksite",
            country_code="NL",
            city="Lelystad",
            street="Crewstraat",
            building="1",
            created_by=self.owner,
        )
        self.project = WorkProject.objects.create(
            organization=self.organization,
            worksite=self.worksite,
            internal_name="Crew project",
            worker_visible_name="Crew project",
            worker_capacity=20,
            created_by=self.owner,
        )
        self.template = ProjectScheduleTemplate.objects.create(
            project=self.project,
            name="Morning",
            starts_at_time=time(6, 0),
            ends_at_time=time(14, 45),
            break_minutes=45,
            created_by=self.owner,
        )
        self.driver = self._connection("driver", "Driver")
        self.other_driver = self._connection("other-driver", "Other Driver")
        self.passenger = self._connection("passenger", "Passenger")
        self.vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Crew car 1",
            registration_identifier="CREW-01",
            seat_capacity=5,
            created_by=self.owner,
        )
        self.other_vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Crew car 2",
            registration_identifier="CREW-02",
            seat_capacity=9,
            created_by=self.owner,
        )

    def _connection(self, suffix, first_name):
        user = User.objects.create_user(
            username=f"crew-{suffix}",
            email=f"crew-{suffix}@example.com",
            password="password",
            first_name=first_name,
            last_name="Worker",
        )
        vacancy = SupportVacancy.objects.create(
            organization=self.organization,
            internal_title=f"Crew vacancy {suffix}",
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
        )

    def _crew(self, suffix="A"):
        return TransportCrew.objects.create(
            organization=self.organization,
            project=self.project,
            schedule_template=self.template,
            internal_name=f"Crew {suffix}",
            starts_on=date.today(),
            created_by=self.owner,
        )

    def test_one_template_supports_multiple_independent_crews(self):
        first = self._crew("A")
        second = self._crew("B")
        TransportCrewDriver.objects.create(
            crew=first,
            driver_connection=self.driver,
            starts_on=date.today(),
            created_by=self.owner,
        )
        TransportCrewDriver.objects.create(
            crew=second,
            driver_connection=self.other_driver,
            starts_on=date.today(),
            created_by=self.owner,
        )
        TransportCrewVehicle.objects.create(
            crew=first,
            vehicle=self.vehicle,
            starts_on=date.today(),
            created_by=self.owner,
        )
        TransportCrewVehicle.objects.create(
            crew=second,
            vehicle=self.other_vehicle,
            starts_on=date.today(),
            created_by=self.owner,
        )

        self.assertEqual(
            TransportCrew.objects.filter(schedule_template=self.template).count(),
            2,
        )
        self.assertNotEqual(
            first.vehicle_assignments.get().vehicle_id,
            second.vehicle_assignments.get().vehicle_id,
        )

    def test_replacing_driver_keeps_crew_and_passengers(self):
        crew = self._crew()
        previous = TransportCrewDriver.objects.create(
            crew=crew,
            driver_connection=self.driver,
            starts_on=date.today(),
            created_by=self.owner,
        )
        member = TransportCrewMember.objects.create(
            crew=crew,
            connection=self.passenger,
            starts_on=date.today(),
            created_by=self.owner,
        )
        previous.ends_on = date.today()
        previous.save(update_fields=["ends_on", "updated_at"])
        TransportCrewDriver.objects.create(
            crew=crew,
            driver_connection=self.other_driver,
            starts_on=date.today() + timedelta(days=1),
            created_by=self.owner,
        )

        self.assertTrue(TransportCrew.objects.filter(pk=crew.pk).exists())
        self.assertTrue(TransportCrewMember.objects.filter(pk=member.pk).exists())
        self.assertEqual(crew.driver_assignments.count(), 2)

    def test_only_one_open_resource_or_membership_per_crew(self):
        crew = self._crew()
        TransportCrewDriver.objects.create(
            crew=crew,
            driver_connection=self.driver,
            starts_on=date.today(),
            created_by=self.owner,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            TransportCrewDriver.objects.create(
                crew=crew,
                driver_connection=self.other_driver,
                starts_on=date.today(),
                created_by=self.owner,
            )

        TransportCrewVehicle.objects.create(
            crew=crew,
            vehicle=self.vehicle,
            starts_on=date.today(),
            created_by=self.owner,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            TransportCrewVehicle.objects.create(
                crew=crew,
                vehicle=self.other_vehicle,
                starts_on=date.today(),
                created_by=self.owner,
            )

        TransportCrewMember.objects.create(
            crew=crew,
            connection=self.passenger,
            starts_on=date.today(),
            created_by=self.owner,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            TransportCrewMember.objects.create(
                crew=crew,
                connection=self.passenger,
                starts_on=date.today(),
                created_by=self.owner,
            )

    def test_schedule_override_supports_shift_day_off_and_cancelled(self):
        crew = self._crew()
        changed = TransportCrewScheduleOverride.objects.create(
            crew=crew,
            work_date=date.today(),
            kind=TransportCrewScheduleOverride.KIND_SHIFT,
            starts_at_time=time(7, 0),
            ends_at_time=time(15, 0),
            break_minutes=30,
            created_by=self.owner,
        )
        day_off = TransportCrewScheduleOverride.objects.create(
            crew=crew,
            work_date=date.today() + timedelta(days=1),
            kind=TransportCrewScheduleOverride.KIND_DAY_OFF,
            created_by=self.owner,
        )
        cancelled = TransportCrewScheduleOverride.objects.create(
            crew=crew,
            work_date=date.today() + timedelta(days=2),
            kind=TransportCrewScheduleOverride.KIND_CANCELLED,
            created_by=self.owner,
        )

        self.assertEqual(changed.starts_at_time, time(7, 0))
        self.assertIsNone(day_off.starts_at_time)
        self.assertIsNone(cancelled.ends_at_time)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TransportCrewScheduleOverride.objects.create(
                crew=crew,
                work_date=date.today() + timedelta(days=3),
                kind=TransportCrewScheduleOverride.KIND_SHIFT,
                created_by=self.owner,
            )

    def test_data_migration_backfills_a_template_route_and_its_passenger(self):
        driver_vehicle = DriverVehicleAssignment.objects.create(
            organization=self.organization,
            driver_connection=self.driver,
            vehicle=self.vehicle,
            starts_on=date.today(),
            state=DriverVehicleAssignment.STATE_PUBLISHED,
            created_by=self.owner,
        )
        route = TransportRoute.objects.create(
            organization=self.organization,
            internal_name="Legacy template route",
            worksite=self.worksite,
            schedule_template=self.template,
            driver_vehicle_assignment=driver_vehicle,
            starts_on=date.today(),
            state=TransportRoute.STATE_PUBLISHED,
            created_by=self.owner,
        )
        pickup = RouteStop.objects.create(
            route=route,
            sequence=1,
            kind=RouteStop.KIND_PICKUP,
            label="Pickup",
        )
        dropoff = RouteStop.objects.create(
            route=route,
            sequence=2,
            kind=RouteStop.KIND_DROPOFF,
            label="Dropoff",
        )
        TransportPassengerAssignment.objects.create(
            route=route,
            connection=self.passenger,
            pickup_stop=pickup,
            dropoff_stop=dropoff,
            boarding_order=1,
        )

        migration = import_module(
            "support.migrations.0021_add_stable_transport_crews"
        )
        migration.backfill_stable_transport_crews(apps, None)

        route.refresh_from_db()
        self.assertIsNotNone(route.crew_id)
        self.assertEqual(route.crew.project_id, self.project.id)
        self.assertEqual(
            route.crew.driver_assignments.get().driver_connection_id,
            self.driver.id,
        )
        self.assertEqual(
            route.crew.vehicle_assignments.get().vehicle_id,
            self.vehicle.id,
        )
        self.assertEqual(
            route.crew.members.get().connection_id,
            self.passenger.id,
        )
