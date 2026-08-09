from datetime import date, datetime, time, timedelta
from importlib import import_module

from django.apps import apps
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from support.models import (
    DriverVehicleAssignment,
    ProjectScheduleTemplate,
    RouteStop,
    ScheduledWorkShift,
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
from support.services.operations import (
    apply_transport_crew_schedule_override,
    create_transport_crew_for_schedule,
    resolve_transport_crew_schedule_conflict,
)
from support.services.organizations import activate_organization, create_organization


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
        activate_organization(
            jobhub_operator=self.operator,
            organization=self.organization,
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

    def test_empty_crew_can_be_created_and_published_for_driver_schedule(self):
        self.driver.has_driving_license = True
        self.driver.save(update_fields=["has_driving_license", "updated_at"])
        assignment = DriverVehicleAssignment.objects.create(
            organization=self.organization,
            driver_connection=self.driver,
            vehicle=self.vehicle,
            starts_on=date.today(),
            state=DriverVehicleAssignment.STATE_PUBLISHED,
            published_by=self.owner,
            published_at=timezone.now(),
            created_by=self.owner,
        )
        work_date = date.today() + timedelta(days=1)
        starts_at = timezone.make_aware(
            datetime.combine(work_date, self.template.starts_at_time)
        )
        ends_at = timezone.make_aware(
            datetime.combine(work_date, self.template.ends_at_time)
        )
        ScheduledWorkShift.objects.create(
            organization=self.organization,
            connection=self.driver,
            schedule_template=self.template,
            work_date=work_date,
            starts_at=starts_at,
            ends_at=ends_at,
            break_minutes=self.template.break_minutes,
            state=ScheduledWorkShift.STATE_PUBLISHED,
            published_by=self.owner,
            published_at=timezone.now(),
            created_by=self.owner,
        )

        route = create_transport_crew_for_schedule(
            actor=self.owner,
            organization=self.organization,
            driver_vehicle_assignment=assignment,
            schedule_template=self.template,
        )

        self.assertEqual(route.state, TransportRoute.STATE_PUBLISHED)
        self.assertEqual(route.passenger_assignments.count(), 0)
        self.assertIsNotNone(route.crew_id)
        self.assertEqual(
            route.crew.driver_assignments.get().driver_connection,
            self.driver,
        )
        self.assertEqual(
            route.crew.vehicle_assignments.get().vehicle,
            self.vehicle,
        )
        with self.assertRaises(ValidationError):
            create_transport_crew_for_schedule(
                actor=self.owner,
                organization=self.organization,
                driver_vehicle_assignment=assignment,
                schedule_template=self.template,
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

    def _operational_crew(self, *, suffix="A", driver=None, passenger=None):
        crew = self._crew(suffix)
        TransportCrewDriver.objects.create(
            crew=crew,
            driver_connection=driver or self.driver,
            starts_on=date.today(),
            created_by=self.owner,
        )
        TransportCrewVehicle.objects.create(
            crew=crew,
            vehicle=self.vehicle if suffix == "A" else self.other_vehicle,
            starts_on=date.today(),
            created_by=self.owner,
        )
        if passenger is not None:
            TransportCrewMember.objects.create(
                crew=crew,
                connection=passenger,
                starts_on=date.today(),
                created_by=self.owner,
            )
        return crew

    def test_crew_override_publishes_same_shift_for_driver_and_passenger(self):
        crew = self._operational_crew(passenger=self.passenger)
        work_dates = [date.today() + timedelta(days=1), date.today() + timedelta(days=2)]

        conflicts = apply_transport_crew_schedule_override(
            actor=self.owner,
            crew=crew,
            work_dates=work_dates,
            kind=TransportCrewScheduleOverride.KIND_SHIFT,
            starts_at_time=time(7, 0),
            ends_at_time=time(15, 30),
            break_minutes=30,
            note="Temporary project time",
        )

        self.assertEqual(conflicts, [])
        shifts = ScheduledWorkShift.objects.filter(
            crew=crew,
            state=ScheduledWorkShift.STATE_PUBLISHED,
        )
        self.assertEqual(shifts.count(), 4)
        self.assertSetEqual(
            set(shifts.values_list("connection_id", flat=True)),
            {self.driver.id, self.passenger.id},
        )
        self.assertEqual(crew.schedule_overrides.count(), 2)

    def test_conflicting_crew_shift_is_kept_until_manager_resolves_it(self):
        first = self._operational_crew(suffix="A", passenger=self.passenger)
        second = self._operational_crew(
            suffix="B",
            driver=self.other_driver,
            passenger=self.passenger,
        )
        work_date = date.today() + timedelta(days=1)
        for crew in (first, second):
            apply_transport_crew_schedule_override(
                actor=self.owner,
                crew=crew,
                work_dates=[work_date],
                kind=TransportCrewScheduleOverride.KIND_SHIFT,
                starts_at_time=time(7, 0),
                ends_at_time=time(15, 0),
                break_minutes=30,
            )

        passenger_shifts = ScheduledWorkShift.objects.filter(
            connection=self.passenger,
            work_date=work_date,
            state=ScheduledWorkShift.STATE_PUBLISHED,
        )
        self.assertEqual(passenger_shifts.count(), 2)
        keep_shift = passenger_shifts.get(crew=first)

        resolve_transport_crew_schedule_conflict(
            actor=self.owner,
            keep_shift=keep_shift,
        )

        self.assertEqual(
            ScheduledWorkShift.objects.filter(
                connection=self.passenger,
                work_date=work_date,
                state=ScheduledWorkShift.STATE_PUBLISHED,
            ).count(),
            1,
        )
        self.assertFalse(
            TransportCrewMember.objects.filter(
                crew=second,
                connection=self.passenger,
                ends_on__isnull=True,
            ).exists()
        )

    def test_day_off_cancels_only_selected_crew_shift(self):
        crew = self._operational_crew(passenger=self.passenger)
        work_date = date.today() + timedelta(days=1)
        apply_transport_crew_schedule_override(
            actor=self.owner,
            crew=crew,
            work_dates=[work_date],
            kind=TransportCrewScheduleOverride.KIND_SHIFT,
            starts_at_time=time(7, 0),
            ends_at_time=time(15, 0),
            break_minutes=30,
        )

        apply_transport_crew_schedule_override(
            actor=self.owner,
            crew=crew,
            work_dates=[work_date],
            kind=TransportCrewScheduleOverride.KIND_DAY_OFF,
        )

        self.assertFalse(
            ScheduledWorkShift.objects.filter(
                crew=crew,
                work_date=work_date,
                state__in=(
                    ScheduledWorkShift.STATE_DRAFT,
                    ScheduledWorkShift.STATE_PUBLISHED,
                ),
            ).exists()
        )
        self.assertEqual(
            crew.schedule_overrides.get(work_date=work_date).kind,
            TransportCrewScheduleOverride.KIND_DAY_OFF,
        )
