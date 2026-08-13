from datetime import date, time

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from support.models import (
    AuditEvent,
    DriverVehicleAssignment,
    ProjectCrewDriverSubstitution,
    ProjectCrewMemberAbsence,
    ProjectCrewPassenger,
    ProjectCrewResourceAssignment,
    ProjectCrewShift,
    ProjectCrewShiftMember,
    ScheduledWorkShift,
    SupportApplication,
    SupportConnection,
    SupportVacancy,
    Vehicle,
    WorkerScheduleDayOff,
    WorkProject,
    Worksite,
)
from support.services.organizations import activate_organization, create_organization
from support.services.project_crews import (
    PASSENGER_SCOPE_FUTURE,
    PASSENGER_SCOPE_SELECTED,
    assign_project_crew_substitute_driver,
    assign_project_crew_passenger,
    create_project_crew,
    mark_worker_schedule_days_off,
    publish_project_crew_shifts,
    project_crew_substitute_driver_candidates,
    release_project_crew_member_days,
    release_project_crew_shifts,
    remove_project_crew_passenger,
    replace_project_crew_driver,
    restore_project_crew_member_days,
    restore_worker_schedule_days_off,
)


@override_settings(SUPPORT_FEATURE_ENABLED=True)
class ProjectCrewServiceTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(
            username="project-crew-service-operator",
            email="project-crew-service-operator@example.com",
            password="password",
            is_staff=True,
        )
        self.owner = User.objects.create_user(
            username="project-crew-service-owner",
            email="project-crew-service-owner@example.com",
            password="password",
        )
        self.organization, _ = create_organization(
            jobhub_operator=self.operator,
            legal_name="Project Crew Service Agency sp. z o.o.",
            display_name="Project Crew Service Agency",
            owner_email=self.owner.email,
        )
        activate_organization(jobhub_operator=self.operator, organization=self.organization)
        self.worksite = Worksite.objects.create(
            organization=self.organization,
            internal_name="Service worksite",
            country_code="NL",
            city="Lelystad",
            street="Servicelaan",
            building="1",
            created_by=self.owner,
        )
        self.project = WorkProject.objects.create(
            organization=self.organization,
            worksite=self.worksite,
            internal_name="Service project",
            worker_visible_name="Service project",
            worker_capacity=20,
            created_by=self.owner,
        )
        self.driver = self._connection("driver", "Driver", licence=True)
        self.second_driver = self._connection("second-driver", "Second", licence=True)
        self.passenger = self._connection("passenger", "Passenger")
        self.other_passenger = self._connection("other-passenger", "Other")
        self.vehicle = self._vehicle("SERVICE-01", seats=4)
        self.second_vehicle = self._vehicle("SERVICE-02", seats=4)

    def _connection(self, suffix, first_name, *, licence=False):
        user = User.objects.create_user(
            username=f"project-crew-service-{suffix}",
            email=f"project-crew-service-{suffix}@example.com",
            password="password",
            first_name=first_name,
            last_name="Worker",
        )
        vacancy = SupportVacancy.objects.create(
            organization=self.organization,
            internal_title=f"Service vacancy {suffix}",
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
            stage=SupportConnection.STAGE_ACTIVE_WORKER,
            has_driving_license=licence,
        )

    def _vehicle(self, registration, *, seats):
        return Vehicle.objects.create(
            organization=self.organization,
            internal_name=registration,
            registration_identifier=registration,
            seat_capacity=seats,
            created_by=self.owner,
        )

    def _crew(self, *, driver=None, vehicle=None, name="Crew A"):
        return create_project_crew(
            actor=self.owner,
            organization=self.organization,
            project=self.project,
            driver_connection=driver or self.driver,
            vehicle=vehicle or self.vehicle,
            internal_name=name,
            starts_on=date(2026, 8, 1),
        )

    def _publish(self, crew, dates):
        return publish_project_crew_shifts(
            actor=self.owner,
            crew=crew,
            work_dates=dates,
            starts_at_time=time(6, 0),
            ends_at_time=time(14, 45),
            break_minutes=45,
        )

    @staticmethod
    def _error_code(exception):
        return str(exception.detail["code"])

    def test_create_crew_and_publish_selected_days_atomically(self):
        crew = self._crew()
        shifts = self._publish(crew, [date(2026, 8, 11), date(2026, 8, 12)])

        self.assertEqual(len(shifts), 2)
        self.assertEqual(crew.resource_assignments.count(), 1)
        self.assertEqual(crew.calendar_shifts.count(), 2)
        self.assertEqual(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                role=ProjectCrewShiftMember.ROLE_DRIVER,
                connection=self.driver,
                vehicle=self.vehicle,
            ).count(),
            2,
        )
        self.assertEqual(
            ScheduledWorkShift.objects.filter(
                connection=self.driver,
                project_crew_member__shift__crew=crew,
                state=ScheduledWorkShift.STATE_PUBLISHED,
            ).count(),
            2,
        )
        self.assertTrue(
            AuditEvent.objects.filter(
                action="project_crew.shifts_published",
                target_public_id=crew.public_id,
            ).exists()
        )

        replaced = publish_project_crew_shifts(
            actor=self.owner,
            crew=crew,
            work_dates=[date(2026, 8, 11)],
            starts_at_time=time(7, 0),
            ends_at_time=time(15, 0),
            break_minutes=30,
        )[0]
        self.assertEqual(timezone.localtime(replaced.starts_at).time(), time(7, 0))
        self.assertEqual(replaced.break_minutes, 30)
        self.assertEqual(replaced.members.count(), 1)
        synced_replacement = ScheduledWorkShift.objects.get(
            project_crew_member__shift=replaced,
            connection=self.driver,
        )
        self.assertEqual(timezone.localtime(synced_replacement.starts_at).time(), time(7, 0))
        self.assertEqual(synced_replacement.break_minutes, 30)

        released = release_project_crew_shifts(
            actor=self.owner,
            crew=crew,
            work_dates=[date(2026, 8, 11)],
        )
        self.assertEqual(len(released), 1)
        replaced.refresh_from_db()
        self.assertEqual(replaced.state, replaced.STATE_CANCELLED)
        synced_replacement.refresh_from_db()
        self.assertEqual(synced_replacement.state, ScheduledWorkShift.STATE_CANCELLED)
        # The composition remains as a historical snapshot, while cancelled
        # days no longer participate in schedule conflicts.
        self.assertEqual(replaced.members.count(), 1)

    def test_create_crew_rejects_worker_without_licence_with_exact_code(self):
        with self.assertRaises(ValidationError) as error:
            self._crew(driver=self.passenger)
        self.assertEqual(self._error_code(error.exception), "driver_licence_not_confirmed")
        self.assertEqual(self.project.project_crews.count(), 0)

    def test_passenger_can_be_planned_while_driver_is_temporarily_absent(self):
        crew = self._crew()
        work_date = date(2026, 8, 14)
        self._publish(crew, [work_date])
        release_project_crew_member_days(
            actor=self.owner,
            connection=self.driver,
            work_dates=[work_date],
        )

        assign_project_crew_passenger(
            actor=self.owner,
            crew=crew,
            connection=self.passenger,
            scope=PASSENGER_SCOPE_SELECTED,
            selected_dates=[work_date],
        )

        shift = crew.calendar_shifts.get(work_date=work_date)
        self.assertFalse(
            shift.members.filter(role=ProjectCrewShiftMember.ROLE_DRIVER).exists()
        )
        self.assertTrue(
            shift.members.filter(
                connection=self.passenger,
                role=ProjectCrewShiftMember.ROLE_PASSENGER,
            ).exists()
        )

    def test_releasing_crew_day_removes_obsolete_crew_absences(self):
        crew = self._crew()
        work_date = date(2026, 8, 14)
        self._publish(crew, [work_date])
        release_project_crew_member_days(
            actor=self.owner,
            connection=self.driver,
            work_dates=[work_date],
        )
        self.assertTrue(
            ProjectCrewMemberAbsence.objects.filter(
                crew=crew,
                connection=self.driver,
                work_date=work_date,
            ).exists()
        )

        release_project_crew_shifts(
            actor=self.owner,
            crew=crew,
            work_dates=[work_date],
        )

        self.assertFalse(
            ProjectCrewMemberAbsence.objects.filter(
                crew=crew,
                work_date=work_date,
            ).exists()
        )

    def test_released_passenger_day_can_be_restored(self):
        crew = self._crew()
        work_date = date(2026, 8, 14)
        self._publish(crew, [work_date])
        assign_project_crew_passenger(
            actor=self.owner,
            crew=crew,
            connection=self.passenger,
            scope=PASSENGER_SCOPE_FUTURE,
            effective_on=work_date,
        )
        release_project_crew_member_days(
            actor=self.owner,
            connection=self.passenger,
            work_dates=[work_date],
        )

        restore_project_crew_member_days(
            actor=self.owner,
            connection=self.passenger,
            work_dates=[work_date],
        )

        self.assertFalse(
            ProjectCrewMemberAbsence.objects.filter(
                crew=crew,
                connection=self.passenger,
                work_date=work_date,
            ).exists()
        )
        self.assertTrue(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                shift__work_date=work_date,
                connection=self.passenger,
                role=ProjectCrewShiftMember.ROLE_PASSENGER,
            ).exists()
        )

    def test_driver_day_off_can_be_cancelled_and_driver_restored(self):
        crew = self._crew()
        work_date = date(2026, 8, 14)
        self._publish(crew, [work_date])
        mark_worker_schedule_days_off(
            actor=self.owner,
            connection=self.driver,
            work_dates=[work_date],
        )

        restore_worker_schedule_days_off(
            actor=self.owner,
            connection=self.driver,
            work_dates=[work_date],
        )

        self.assertFalse(
            WorkerScheduleDayOff.objects.filter(
                connection=self.driver,
                work_date=work_date,
            ).exists()
        )
        self.assertTrue(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                shift__work_date=work_date,
                connection=self.driver,
                role=ProjectCrewShiftMember.ROLE_DRIVER,
                vehicle=self.vehicle,
            ).exists()
        )

    def test_create_crew_rejects_vehicle_still_owned_by_legacy_fleet(self):
        DriverVehicleAssignment.objects.create(
            organization=self.organization,
            driver_connection=self.second_driver,
            vehicle=self.vehicle,
            starts_on=date(2026, 8, 1),
            state=DriverVehicleAssignment.STATE_PUBLISHED,
            created_by=self.owner,
        )

        with self.assertRaises(ValidationError) as error:
            self._crew()

        self.assertEqual(
            self._error_code(error.exception),
            "legacy_driver_or_vehicle_already_assigned",
        )
        self.assertEqual(self.project.project_crews.count(), 0)

    def test_future_passenger_roster_populates_existing_and_new_days(self):
        crew = self._crew()
        self._publish(crew, [date(2026, 8, 11)])
        assign_project_crew_passenger(
            actor=self.owner,
            crew=crew,
            connection=self.passenger,
            scope=PASSENGER_SCOPE_FUTURE,
            effective_on=date(2026, 8, 11),
        )
        self._publish(crew, [date(2026, 8, 12)])

        self.assertTrue(
            ProjectCrewPassenger.objects.filter(
                crew=crew,
                connection=self.passenger,
                ends_on__isnull=True,
            ).exists()
        )
        self.assertEqual(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                connection=self.passenger,
                role=ProjectCrewShiftMember.ROLE_PASSENGER,
            ).count(),
            2,
        )
        self.assertEqual(
            ScheduledWorkShift.objects.filter(
                connection=self.passenger,
                project_crew_member__shift__crew=crew,
                state=ScheduledWorkShift.STATE_PUBLISHED,
            ).count(),
            2,
        )

        remove_project_crew_passenger(
            actor=self.owner,
            crew=crew,
            connection=self.passenger,
            scope=PASSENGER_SCOPE_FUTURE,
            effective_on=date(2026, 8, 12),
        )
        self.assertTrue(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                shift__work_date=date(2026, 8, 11),
                connection=self.passenger,
            ).exists()
        )
        self.assertFalse(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                shift__work_date=date(2026, 8, 12),
                connection=self.passenger,
            ).exists()
        )
        self.assertFalse(
            ScheduledWorkShift.objects.filter(
                connection=self.passenger,
                project_crew_member__shift__crew=crew,
                work_date=date(2026, 8, 12),
            ).exists()
        )

    def test_worker_day_off_removes_day_membership_and_survives_republish(self):
        crew = self._crew()
        work_date = date(2026, 8, 12)
        self._publish(crew, [work_date])
        assign_project_crew_passenger(
            actor=self.owner,
            crew=crew,
            connection=self.passenger,
            scope=PASSENGER_SCOPE_FUTURE,
            effective_on=work_date,
        )

        mark_worker_schedule_days_off(
            actor=self.owner,
            connection=self.passenger,
            work_dates=[work_date],
        )

        self.assertTrue(
            WorkerScheduleDayOff.objects.filter(
                connection=self.passenger,
                work_date=work_date,
            ).exists()
        )
        self.assertFalse(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                shift__work_date=work_date,
                connection=self.passenger,
            ).exists()
        )
        self._publish(crew, [work_date])
        self.assertFalse(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                shift__work_date=work_date,
                connection=self.passenger,
            ).exists()
        )
        self.assertTrue(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                shift__work_date=work_date,
                connection=self.driver,
                role=ProjectCrewShiftMember.ROLE_DRIVER,
            ).exists()
        )

    def test_crew_absence_survives_republish_and_future_roster_continues(self):
        crew = self._crew()
        absent_date = date(2026, 8, 12)
        later_date = date(2026, 8, 13)
        self._publish(crew, [absent_date])
        assign_project_crew_passenger(
            actor=self.owner,
            crew=crew,
            connection=self.passenger,
            scope=PASSENGER_SCOPE_FUTURE,
            effective_on=absent_date,
        )

        release_project_crew_member_days(
            actor=self.owner,
            connection=self.passenger,
            work_dates=[absent_date],
        )

        self.assertTrue(
            ProjectCrewMemberAbsence.objects.filter(
                crew=crew,
                connection=self.passenger,
                work_date=absent_date,
            ).exists()
        )
        self._publish(crew, [absent_date, later_date])
        self.assertFalse(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                shift__work_date=absent_date,
                connection=self.passenger,
            ).exists()
        )
        self.assertTrue(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                shift__work_date=later_date,
                connection=self.passenger,
            ).exists()
        )

        remove_project_crew_passenger(
            actor=self.owner,
            crew=crew,
            connection=self.passenger,
            scope=PASSENGER_SCOPE_FUTURE,
            effective_on=absent_date,
        )
        self.assertFalse(
            ProjectCrewMemberAbsence.objects.filter(
                crew=crew,
                connection=self.passenger,
            ).exists()
        )

    def test_driver_day_off_publishes_crew_day_without_driver(self):
        crew = self._crew()
        work_date = date(2026, 8, 14)
        mark_worker_schedule_days_off(
            actor=self.owner,
            connection=self.driver,
            work_dates=[work_date],
        )

        shift = self._publish(crew, [work_date])[0]

        self.assertEqual(shift.state, ProjectCrewShift.STATE_PUBLISHED)
        self.assertFalse(
            shift.members.filter(
                connection=self.driver,
                role=ProjectCrewShiftMember.ROLE_DRIVER,
            ).exists()
        )

    def test_driver_absence_keeps_published_crew_day_without_driver(self):
        crew = self._crew()
        work_date = date(2026, 8, 14)
        shift = self._publish(crew, [work_date])[0]

        release_project_crew_member_days(
            actor=self.owner,
            connection=self.driver,
            work_dates=[work_date],
        )

        self.assertTrue(
            ProjectCrewMemberAbsence.objects.filter(
                crew=crew,
                connection=self.driver,
                work_date=work_date,
            ).exists()
        )
        self._publish(crew, [work_date])
        shift.refresh_from_db()
        self.assertEqual(shift.state, ProjectCrewShift.STATE_PUBLISHED)
        self.assertFalse(
            shift.members.filter(role=ProjectCrewShiftMember.ROLE_DRIVER).exists()
        )

    def test_substitute_candidates_put_available_crew_passenger_first(self):
        crew = self._crew()
        dates = [date(2026, 8, 14), date(2026, 8, 15)]
        self.passenger.has_driving_license = True
        self.passenger.save(update_fields=["has_driving_license", "updated_at"])
        self._publish(crew, dates)
        assign_project_crew_passenger(
            actor=self.owner,
            crew=crew,
            connection=self.passenger,
            scope=PASSENGER_SCOPE_FUTURE,
            effective_on=dates[0],
        )
        release_project_crew_member_days(
            actor=self.owner,
            connection=self.driver,
            work_dates=dates,
        )

        candidates = project_crew_substitute_driver_candidates(
            crew=crew,
            work_dates=dates,
        )

        self.assertEqual(
            [item.id for item in candidates],
            [self.passenger.id, self.second_driver.id],
        )
        self.assertTrue(candidates[0].is_current_crew_passenger)
        self.assertFalse(candidates[1].is_current_crew_passenger)

    def test_substitute_candidates_exclude_driver_with_schedule_conflict(self):
        target_crew = self._crew()
        other_crew = self._crew(
            driver=self.second_driver,
            vehicle=self.second_vehicle,
            name="Other crew",
        )
        work_date = date(2026, 8, 14)
        self.passenger.has_driving_license = True
        self.passenger.save(update_fields=["has_driving_license", "updated_at"])
        self._publish(target_crew, [work_date])
        self._publish(other_crew, [work_date])
        assign_project_crew_passenger(
            actor=self.owner,
            crew=target_crew,
            connection=self.passenger,
            scope=PASSENGER_SCOPE_SELECTED,
            selected_dates=[work_date],
        )
        release_project_crew_member_days(
            actor=self.owner,
            connection=self.driver,
            work_dates=[work_date],
        )

        candidates = project_crew_substitute_driver_candidates(
            crew=target_crew,
            work_dates=[work_date],
        )

        self.assertEqual([item.id for item in candidates], [self.passenger.id])

    def test_substitute_candidates_require_primary_driver_absence(self):
        crew = self._crew()
        work_date = date(2026, 8, 14)
        self._publish(crew, [work_date])

        with self.assertRaises(ValidationError) as error:
            project_crew_substitute_driver_candidates(
                crew=crew,
                work_dates=[work_date],
            )

        self.assertEqual(
            self._error_code(error.exception),
            "substitution_requires_driver_absence",
        )

    def test_substitute_candidates_accept_red_day_without_legacy_absence_row(self):
        crew = self._crew()
        work_date = date(2026, 8, 14)
        shift = self._publish(crew, [work_date])[0]
        shift.members.filter(role=ProjectCrewShiftMember.ROLE_DRIVER).delete()

        candidates = project_crew_substitute_driver_candidates(
            crew=crew,
            work_dates=[work_date],
        )

        self.assertEqual([item.id for item in candidates], [self.second_driver.id])

    def test_substitute_cannot_be_assigned_to_past_day(self):
        crew = self._crew()

        with self.assertRaises(ValidationError) as error:
            project_crew_substitute_driver_candidates(
                crew=crew,
                work_dates=[date(2026, 8, 11)],
            )

        self.assertEqual(
            self._error_code(error.exception),
            "substitution_date_in_past",
        )

    def test_assign_substitute_driver_only_on_primary_absence_dates(self):
        crew = self._crew()
        dates = [date(2026, 8, 14), date(2026, 8, 15)]
        self.passenger.has_driving_license = True
        self.passenger.save(update_fields=["has_driving_license", "updated_at"])
        self._publish(crew, dates)
        assign_project_crew_passenger(
            actor=self.owner,
            crew=crew,
            connection=self.passenger,
            scope=PASSENGER_SCOPE_FUTURE,
            effective_on=dates[0],
        )
        release_project_crew_member_days(
            actor=self.owner,
            connection=self.driver,
            work_dates=dates,
        )

        substitutions = assign_project_crew_substitute_driver(
            actor=self.owner,
            crew=crew,
            substitute_driver_connection=self.passenger,
            work_dates=dates,
        )

        self.assertEqual(len(substitutions), 2)
        self.assertTrue(all(item.substitute_was_passenger for item in substitutions))
        self.assertEqual(
            ProjectCrewDriverSubstitution.objects.filter(
                crew=crew,
                state=ProjectCrewDriverSubstitution.STATE_ACTIVE,
            ).count(),
            2,
        )
        for work_date in dates:
            member = ProjectCrewShiftMember.objects.get(
                shift__crew=crew,
                shift__work_date=work_date,
                role=ProjectCrewShiftMember.ROLE_DRIVER,
            )
            self.assertEqual(member.connection, self.passenger)
            self.assertEqual(member.vehicle, self.vehicle)
            self.assertTrue(
                ScheduledWorkShift.objects.filter(
                    project_crew_member=member,
                    connection=self.passenger,
                    state=ScheduledWorkShift.STATE_PUBLISHED,
                ).exists()
            )

    def test_new_substitute_replaces_entire_previous_selection_and_restores_passenger(self):
        crew = self._crew()
        dates = [date(2026, 8, 14), date(2026, 8, 15)]
        self.passenger.has_driving_license = True
        self.passenger.save(update_fields=["has_driving_license", "updated_at"])
        self._publish(crew, dates)
        assign_project_crew_passenger(
            actor=self.owner,
            crew=crew,
            connection=self.second_driver,
            scope=PASSENGER_SCOPE_FUTURE,
            effective_on=dates[0],
        )
        assign_project_crew_passenger(
            actor=self.owner,
            crew=crew,
            connection=self.passenger,
            scope=PASSENGER_SCOPE_FUTURE,
            effective_on=dates[0],
        )
        release_project_crew_member_days(
            actor=self.owner,
            connection=self.driver,
            work_dates=dates,
        )
        assign_project_crew_substitute_driver(
            actor=self.owner,
            crew=crew,
            substitute_driver_connection=self.second_driver,
            work_dates=dates,
        )

        replacements = assign_project_crew_substitute_driver(
            actor=self.owner,
            crew=crew,
            substitute_driver_connection=self.passenger,
            work_dates=[dates[1]],
        )

        self.assertEqual(len(replacements), 1)
        self.assertEqual(
            ProjectCrewDriverSubstitution.objects.filter(
                crew=crew,
                substitute_driver_connection=self.second_driver,
                state=ProjectCrewDriverSubstitution.STATE_REPLACED,
            ).count(),
            2,
        )
        self.assertFalse(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                shift__work_date=dates[0],
                role=ProjectCrewShiftMember.ROLE_DRIVER,
            ).exists()
        )
        self.assertTrue(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                shift__work_date=dates[0],
                connection=self.second_driver,
                role=ProjectCrewShiftMember.ROLE_PASSENGER,
            ).exists()
        )
        new_driver = ProjectCrewShiftMember.objects.get(
            shift__crew=crew,
            shift__work_date=dates[1],
            role=ProjectCrewShiftMember.ROLE_DRIVER,
        )
        self.assertEqual(new_driver.connection, self.passenger)
        self.assertTrue(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                shift__work_date=dates[1],
                connection=self.second_driver,
                role=ProjectCrewShiftMember.ROLE_PASSENGER,
            ).exists()
        )

    def test_releasing_crew_shift_cancels_active_substitution(self):
        crew = self._crew()
        work_date = date(2026, 8, 14)
        self.passenger.has_driving_license = True
        self.passenger.save(update_fields=["has_driving_license", "updated_at"])
        self._publish(crew, [work_date])
        assign_project_crew_passenger(
            actor=self.owner,
            crew=crew,
            connection=self.passenger,
            scope=PASSENGER_SCOPE_SELECTED,
            selected_dates=[work_date],
        )
        release_project_crew_member_days(
            actor=self.owner,
            connection=self.driver,
            work_dates=[work_date],
        )
        substitution = assign_project_crew_substitute_driver(
            actor=self.owner,
            crew=crew,
            substitute_driver_connection=self.passenger,
            work_dates=[work_date],
        )[0]

        release_project_crew_shifts(
            actor=self.owner,
            crew=crew,
            work_dates=[work_date],
        )

        substitution.refresh_from_db()
        self.assertEqual(
            substitution.state,
            ProjectCrewDriverSubstitution.STATE_CANCELLED,
        )
        self.assertIsNotNone(substitution.ended_at)
        shift = crew.calendar_shifts.get(work_date=work_date)
        self.assertEqual(shift.state, ProjectCrewShift.STATE_CANCELLED)
        self.assertFalse(
            shift.members.filter(role=ProjectCrewShiftMember.ROLE_DRIVER).exists()
        )

    def test_substitute_day_off_cancels_substitution_and_leaves_no_driver(self):
        crew = self._crew()
        work_date = date(2026, 8, 14)
        self.passenger.has_driving_license = True
        self.passenger.save(update_fields=["has_driving_license", "updated_at"])
        self._publish(crew, [work_date])
        assign_project_crew_passenger(
            actor=self.owner,
            crew=crew,
            connection=self.passenger,
            scope=PASSENGER_SCOPE_SELECTED,
            selected_dates=[work_date],
        )
        release_project_crew_member_days(
            actor=self.owner,
            connection=self.driver,
            work_dates=[work_date],
        )
        substitution = assign_project_crew_substitute_driver(
            actor=self.owner,
            crew=crew,
            substitute_driver_connection=self.passenger,
            work_dates=[work_date],
        )[0]

        mark_worker_schedule_days_off(
            actor=self.owner,
            connection=self.passenger,
            work_dates=[work_date],
        )

        substitution.refresh_from_db()
        self.assertEqual(
            substitution.state,
            ProjectCrewDriverSubstitution.STATE_CANCELLED,
        )
        shift = crew.calendar_shifts.get(work_date=work_date)
        self.assertFalse(
            shift.members.filter(role=ProjectCrewShiftMember.ROLE_DRIVER).exists()
        )
        self.assertTrue(
            WorkerScheduleDayOff.objects.filter(
                connection=self.passenger,
                work_date=work_date,
            ).exists()
        )

    def test_removing_substitute_passenger_closes_substitution_and_membership(self):
        crew = self._crew()
        work_date = date(2026, 8, 14)
        self.passenger.has_driving_license = True
        self.passenger.save(update_fields=["has_driving_license", "updated_at"])
        self._publish(crew, [work_date])
        assign_project_crew_passenger(
            actor=self.owner,
            crew=crew,
            connection=self.passenger,
            scope=PASSENGER_SCOPE_FUTURE,
            effective_on=work_date,
        )
        release_project_crew_member_days(
            actor=self.owner,
            connection=self.driver,
            work_dates=[work_date],
        )
        substitution = assign_project_crew_substitute_driver(
            actor=self.owner,
            crew=crew,
            substitute_driver_connection=self.passenger,
            work_dates=[work_date],
        )[0]

        remove_project_crew_passenger(
            actor=self.owner,
            crew=crew,
            connection=self.passenger,
            scope=PASSENGER_SCOPE_FUTURE,
            effective_on=work_date,
        )

        substitution.refresh_from_db()
        self.assertEqual(
            substitution.state,
            ProjectCrewDriverSubstitution.STATE_CANCELLED,
        )
        self.assertFalse(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                shift__work_date=work_date,
                connection=self.passenger,
            ).exists()
        )

    def test_selected_passenger_replaces_other_passenger_day(self):
        first_crew = self._crew()
        second_crew = self._crew(
            driver=self.second_driver,
            vehicle=self.second_vehicle,
            name="Crew B",
        )
        work_date = date(2026, 8, 11)
        self._publish(first_crew, [work_date])
        self._publish(second_crew, [work_date])
        assign_project_crew_passenger(
            actor=self.owner,
            crew=second_crew,
            connection=self.passenger,
            scope=PASSENGER_SCOPE_SELECTED,
            selected_dates=[work_date],
        )
        assign_project_crew_passenger(
            actor=self.owner,
            crew=first_crew,
            connection=self.passenger,
            scope=PASSENGER_SCOPE_SELECTED,
            selected_dates=[work_date],
        )

        memberships = ProjectCrewShiftMember.objects.filter(
            connection=self.passenger,
            shift__work_date=work_date,
        )
        self.assertEqual(memberships.count(), 1)
        self.assertEqual(memberships.get().shift.crew, first_crew)

    def test_driver_of_other_crew_cannot_become_passenger_and_rolls_back(self):
        first_crew = self._crew()
        second_crew = self._crew(
            driver=self.second_driver,
            vehicle=self.second_vehicle,
            name="Crew B",
        )
        work_date = date(2026, 8, 11)
        self._publish(first_crew, [work_date])
        self._publish(second_crew, [work_date])

        with self.assertRaises(ValidationError) as error:
            assign_project_crew_passenger(
                actor=self.owner,
                crew=first_crew,
                connection=self.second_driver,
                scope=PASSENGER_SCOPE_SELECTED,
                selected_dates=[work_date],
            )
        self.assertEqual(self._error_code(error.exception), "worker_drives_other_crew")
        self.assertFalse(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=first_crew,
                connection=self.second_driver,
            ).exists()
        )

    def test_permanent_driver_replacement_swaps_roles_and_keeps_vehicle(self):
        crew = self._crew()
        dates = [date(2026, 8, 11), date(2026, 8, 12)]
        self._publish(crew, dates)
        assign_project_crew_passenger(
            actor=self.owner,
            crew=crew,
            connection=self.second_driver,
            scope=PASSENGER_SCOPE_FUTURE,
            effective_on=dates[0],
        )

        replacement = replace_project_crew_driver(
            actor=self.owner,
            crew=crew,
            new_driver_connection=self.second_driver,
            effective_on=dates[1],
        )

        self.assertEqual(replacement.driver_connection, self.second_driver)
        self.assertEqual(replacement.vehicle, self.vehicle)
        self.assertEqual(
            ProjectCrewResourceAssignment.objects.get(crew=crew, ends_on__isnull=False).ends_on,
            dates[0],
        )
        day_two = crew.calendar_shifts.get(work_date=dates[1])
        self.assertTrue(
            day_two.members.filter(
                connection=self.second_driver,
                role=ProjectCrewShiftMember.ROLE_DRIVER,
                vehicle=self.vehicle,
            ).exists()
        )
        self.assertTrue(
            day_two.members.filter(
                connection=self.driver,
                role=ProjectCrewShiftMember.ROLE_PASSENGER,
            ).exists()
        )
        day_one = crew.calendar_shifts.get(work_date=dates[0])
        self.assertTrue(
            day_one.members.filter(
                connection=self.driver,
                role=ProjectCrewShiftMember.ROLE_DRIVER,
            ).exists()
        )

    def test_permanent_driver_replacement_cancels_future_substitution(self):
        crew = self._crew()
        work_date = date(2026, 8, 14)
        self.passenger.has_driving_license = True
        self.passenger.save(update_fields=["has_driving_license", "updated_at"])
        self._publish(crew, [work_date])
        for connection in (self.passenger, self.second_driver):
            assign_project_crew_passenger(
                actor=self.owner,
                crew=crew,
                connection=connection,
                scope=PASSENGER_SCOPE_SELECTED,
                selected_dates=[work_date],
            )
        release_project_crew_member_days(
            actor=self.owner,
            connection=self.driver,
            work_dates=[work_date],
        )
        substitution = assign_project_crew_substitute_driver(
            actor=self.owner,
            crew=crew,
            substitute_driver_connection=self.passenger,
            work_dates=[work_date],
        )[0]

        replace_project_crew_driver(
            actor=self.owner,
            crew=crew,
            new_driver_connection=self.second_driver,
            effective_on=work_date,
        )

        substitution.refresh_from_db()
        self.assertEqual(
            substitution.state,
            ProjectCrewDriverSubstitution.STATE_CANCELLED,
        )
        driver_member = ProjectCrewShiftMember.objects.get(
            shift__crew=crew,
            shift__work_date=work_date,
            role=ProjectCrewShiftMember.ROLE_DRIVER,
        )
        self.assertEqual(driver_member.connection, self.second_driver)
        self.assertTrue(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                shift__work_date=work_date,
                connection=self.passenger,
                role=ProjectCrewShiftMember.ROLE_PASSENGER,
            ).exists()
        )

    def test_replacement_releases_previous_vehicle_and_leaves_other_crew_without_driver(self):
        target_crew = self._crew()
        previous_crew = self._crew(
            driver=self.second_driver,
            vehicle=self.second_vehicle,
            name="Previous crew",
        )
        replacement_date = date(2026, 8, 12)
        self._publish(target_crew, [replacement_date])
        self._publish(previous_crew, [date(2026, 8, 13)])
        assign_project_crew_passenger(
            actor=self.owner,
            crew=target_crew,
            connection=self.second_driver,
            scope=PASSENGER_SCOPE_SELECTED,
            selected_dates=[replacement_date],
        )

        replacement = replace_project_crew_driver(
            actor=self.owner,
            crew=target_crew,
            new_driver_connection=self.second_driver,
            effective_on=replacement_date,
        )

        self.assertEqual(replacement.vehicle, self.vehicle)
        self.assertFalse(
            ProjectCrewResourceAssignment.objects.filter(
                crew=previous_crew,
                ends_on__isnull=True,
            ).exists()
        )
        self.assertFalse(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=previous_crew,
                shift__work_date=date(2026, 8, 13),
                role=ProjectCrewShiftMember.ROLE_DRIVER,
            ).exists()
        )
        self.assertFalse(
            ProjectCrewResourceAssignment.objects.filter(
                vehicle=self.second_vehicle,
                ends_on__isnull=True,
            ).exists()
        )

    def test_capacity_failure_rolls_back_future_roster(self):
        small_vehicle = self._vehicle("SERVICE-SMALL", seats=2)
        crew = self._crew(vehicle=small_vehicle)
        self._publish(crew, [date(2026, 8, 11)])
        assign_project_crew_passenger(
            actor=self.owner,
            crew=crew,
            connection=self.passenger,
            scope=PASSENGER_SCOPE_FUTURE,
            effective_on=date(2026, 8, 11),
        )
        with self.assertRaises(ValidationError) as error:
            assign_project_crew_passenger(
                actor=self.owner,
                crew=crew,
                connection=self.other_passenger,
                scope=PASSENGER_SCOPE_FUTURE,
                effective_on=date(2026, 8, 11),
            )
        self.assertEqual(self._error_code(error.exception), "crew_capacity_exceeded")
        self.assertFalse(
            ProjectCrewPassenger.objects.filter(
                crew=crew,
                connection=self.other_passenger,
            ).exists()
        )
