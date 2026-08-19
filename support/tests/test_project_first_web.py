from datetime import date, datetime, time, timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from support.models import (
    DriverVehicleAssignment,
    ProjectCrew,
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
from support.project_first_web import COPY, PROJECT_CREW_ERROR_COPY


@override_settings(
    SUPPORT_FEATURE_ENABLED=True,
    SUPPORT_PROJECT_FIRST_ENABLED=True,
)
class ProjectFirstWorkspaceTests(TestCase):
    def setUp(self):
        # Driver substitutions are future-only business operations.  A
        # relative fixture keeps the browser-flow tests valid year-round.
        self.future_work_date = timezone.localdate() + timedelta(days=10)
        self.operator = User.objects.create_user(
            username="project-first-web-operator",
            email="project-first-web-operator@example.com",
            password="password",
            is_staff=True,
        )
        self.owner = User.objects.create_user(
            username="project-first-web-owner",
            email="project-first-web-owner@example.com",
            password="password",
        )
        self.organization, _ = create_organization(
            jobhub_operator=self.operator,
            legal_name="Project First Web Agency sp. z o.o.",
            display_name="Project First Web Agency",
            owner_email=self.owner.email,
        )
        activate_organization(
            jobhub_operator=self.operator,
            organization=self.organization,
        )
        self.worksite = Worksite.objects.create(
            organization=self.organization,
            internal_name="Preview worksite",
            country_code="NL",
            city="Lelystad",
            street="Previewlaan",
            building="7",
            created_by=self.owner,
        )
        self.project = WorkProject.objects.create(
            organization=self.organization,
            worksite=self.worksite,
            internal_name="Preview project",
            worker_visible_name="Preview project",
            worker_capacity=12,
            created_by=self.owner,
        )
        self.driver = self._connection("driver", "Driver", licence=True)
        self.second_driver = self._connection("second-driver", "Second", licence=True)
        self.passenger = self._connection("passenger", "Passenger")
        self.vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Preview van",
            registration_identifier="PREVIEW-01",
            seat_capacity=4,
            created_by=self.owner,
        )
        self.client.force_login(self.owner)

    def _connection(self, suffix, first_name, *, licence=False):
        candidate = User.objects.create_user(
            username=f"project-first-web-{suffix}",
            email=f"project-first-web-{suffix}@example.com",
            password="password",
            first_name=first_name,
            last_name="Worker",
        )
        vacancy = SupportVacancy.objects.create(
            organization=self.organization,
            internal_title=f"Preview vacancy {suffix}",
            created_by=self.owner,
        )
        application = SupportApplication.objects.create(
            vacancy=vacancy,
            candidate=candidate,
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
            candidate=candidate,
            stage=SupportConnection.STAGE_ACTIVE_WORKER,
            has_driving_license=licence,
        )

    def _list_url(self):
        return (
            f"{reverse('support:project-first')}"
            f"?organization={self.organization.public_id}"
        )

    def _detail_url(self):
        return (
            f"{reverse('support:project-first-detail', kwargs={'project_public_id': self.project.public_id})}"
            f"?organization={self.organization.public_id}"
        )

    def _worker_url(self, connection):
        return (
            reverse(
                "support:worker-card",
                kwargs={"connection_public_id": connection.public_id},
            )
            + f"?organization={self.organization.public_id}"
            + "&tab=work_transport&month=2026-08"
        )

    def _reset_plan_url(self):
        return (
            f"{reverse('support:project-first-reset-plan')}"
            f"?organization={self.organization.public_id}"
        )

    def _create_crew(self):
        response = self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "crew_create",
                "internal_name": "Crew One",
                "driver_id": str(self.driver.public_id),
                "vehicle_id": str(self.vehicle.public_id),
                "starts_on": "2026-08-11",
            },
        )
        self.assertEqual(response.status_code, 302)
        return ProjectCrew.objects.get(project=self.project)

    def test_project_crew_errors_have_four_complete_readable_translations(self):
        expected_codes = {
            "error_break_minutes_invalid",
            "error_crew_capacity_exceeded",
            "error_crew_driver_missing",
            "error_crew_resource_missing",
            "error_crew_shift_missing",
            "error_driver_licence_not_confirmed",
            "error_driver_or_vehicle_already_assigned",
            "error_driver_project_vehicle_locked",
            "error_driver_shift_conflict",
            "error_legacy_driver_or_vehicle_already_assigned",
            "error_passenger_scope_invalid",
            "error_project_not_in_organization",
            "error_replacement_driver_not_in_crew",
            "error_replacement_driver_shift_conflict",
            "error_selected_schedule_days_have_no_shifts",
            "error_shift_time_required",
            "error_substitute_driver_unavailable",
            "error_substitution_date_in_past",
            "error_substitution_requires_driver_absence",
            "error_vehicle_not_available",
            "error_work_dates_required",
            "error_worker_absent_from_crew",
            "error_worker_archived",
            "error_worker_day_off",
            "error_worker_drives_other_crew",
            "error_worker_is_crew_driver",
            "error_worker_not_in_organization",
        }

        for language in ("ru", "en", "pl", "uk"):
            translated = {**COPY[language], **PROJECT_CREW_ERROR_COPY[language]}
            self.assertTrue(expected_codes.issubset(translated))
            for code in expected_codes:
                self.assertNotIn("???", translated[code])
                self.assertNotIn("\ufffd", translated[code])

    def test_preview_is_separate_and_lists_projects(self):
        response = self.client.get(self._list_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Preview project")
        self.assertTemplateUsed(response, "support/project_first_workspace.html")
        self.assertContains(response, self._reset_plan_url())
        self.assertContains(response, 'data-pf-dialog-target="pf-project-add-modal"')
        self.assertContains(response, 'id="pf-project-add-modal"')
        self.assertContains(response, 'value="project_delete"')
        self.assertContains(
            response,
            f'href="{self._list_url()}"',
            html=False,
        )

    def test_fleet_driver_and_vehicle_are_available_and_promoted_to_project_crew(self):
        today = timezone.localdate()
        fleet_assignment = DriverVehicleAssignment.objects.create(
            organization=self.organization,
            driver_connection=self.driver,
            vehicle=self.vehicle,
            starts_on=today,
            state=DriverVehicleAssignment.STATE_PUBLISHED,
            created_by=self.owner,
            published_by=self.owner,
            published_at=timezone.now(),
        )

        response = self.client.get(self._detail_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'value="{self.driver.public_id}"')
        self.assertContains(
            response,
            f'data-vehicle-id="{self.vehicle.public_id}"',
        )
        self.assertContains(response, 'data-vehicle-locked="0"')

        response = self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "crew_create",
                "internal_name": "Fleet crew",
                "driver_id": str(self.driver.public_id),
                "vehicle_id": str(self.vehicle.public_id),
                "starts_on": today.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 302)
        resource = ProjectCrewResourceAssignment.objects.get(
            crew__project=self.project,
        )
        self.assertEqual(resource.driver_connection, self.driver)
        self.assertEqual(resource.vehicle, self.vehicle)
        fleet_assignment.refresh_from_db()
        self.assertEqual(
            fleet_assignment.state,
            DriverVehicleAssignment.STATE_CANCELLED,
        )

    def test_driver_from_another_project_is_listed_with_locked_vehicle_and_busy_day(self):
        today = timezone.localdate()
        first_crew = ProjectCrew.objects.create(
            organization=self.organization,
            project=self.project,
            internal_name="First project crew",
            created_by=self.owner,
        )
        ProjectCrewResourceAssignment.objects.create(
            crew=first_crew,
            driver_connection=self.driver,
            vehicle=self.vehicle,
            starts_on=today,
            created_by=self.owner,
        )
        first_shift = ProjectCrewShift.objects.create(
            crew=first_crew,
            work_date=today,
            starts_at=timezone.make_aware(datetime.combine(today, time(6, 0))),
            ends_at=timezone.make_aware(datetime.combine(today, time(14, 45))),
            state=ProjectCrewShift.STATE_PUBLISHED,
            created_by=self.owner,
        )
        ProjectCrewShiftMember.objects.create(
            shift=first_shift,
            connection=self.driver,
            role=ProjectCrewShiftMember.ROLE_DRIVER,
            vehicle=self.vehicle,
            created_by=self.owner,
        )
        second_worksite = Worksite.objects.create(
            organization=self.organization,
            internal_name="Second worksite",
            country_code="NL",
            city="Urk",
            street="Secondstraat",
            building="2",
            created_by=self.owner,
        )
        second_project = WorkProject.objects.create(
            organization=self.organization,
            worksite=second_worksite,
            internal_name="Second project",
            worker_visible_name="Second project",
            worker_capacity=8,
            created_by=self.owner,
        )
        second_url = (
            reverse(
                "support:project-first-detail",
                kwargs={"project_public_id": second_project.public_id},
            )
            + f"?organization={self.organization.public_id}"
            + f"&month={today:%Y-%m}"
        )

        response = self.client.get(second_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Project: Preview project")
        self.assertContains(response, 'data-vehicle-locked="1"')

        response = self.client.post(
            second_url,
            {
                "organization": str(self.organization.public_id),
                "action": "crew_create",
                "internal_name": "Second project crew",
                "driver_id": str(self.driver.public_id),
                "vehicle_id": str(self.vehicle.public_id),
                "starts_on": today.isoformat(),
                "return_month": f"{today:%Y-%m}",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            ProjectCrewResourceAssignment.objects.filter(
                driver_connection=self.driver,
                ends_on__isnull=True,
            ).count(),
            2,
        )

        response = self.client.get(second_url)
        self.assertContains(response, "is-today has-driver-conflict")

    def test_worker_workspace_uses_project_crew_data_and_marks_unscheduled_worker_free(self):
        today = timezone.localdate()
        work_date = today + timedelta(days=1)
        crew = ProjectCrew.objects.create(
            organization=self.organization,
            project=self.project,
            internal_name="Crew One",
            created_by=self.owner,
        )
        ProjectCrewResourceAssignment.objects.create(
            crew=crew,
            driver_connection=self.driver,
            vehicle=self.vehicle,
            starts_on=today,
            created_by=self.owner,
        )
        shift = ProjectCrewShift.objects.create(
            crew=crew,
            work_date=work_date,
            starts_at=timezone.make_aware(datetime.combine(work_date, time(6, 0))),
            ends_at=timezone.make_aware(datetime.combine(work_date, time(14, 45))),
            created_by=self.owner,
        )
        ProjectCrewShiftMember.objects.create(
            shift=shift,
            connection=self.driver,
            role=ProjectCrewShiftMember.ROLE_DRIVER,
            vehicle=self.vehicle,
            created_by=self.owner,
        )
        ProjectCrewShiftMember.objects.create(
            shift=shift,
            connection=self.passenger,
            role=ProjectCrewShiftMember.ROLE_PASSENGER,
            created_by=self.owner,
        )

        response = self.client.get(
            f"{reverse('support:workers')}?organization={self.organization.public_id}"
        )

        self.assertEqual(response.status_code, 200)
        rows = {row["connection_id"]: row for row in response.context["worker_rows"]}
        driver_row = rows[str(self.driver.public_id)]
        passenger_row = rows[str(self.passenger.public_id)]
        free_row = rows[str(self.second_driver.public_id)]
        self.assertEqual(driver_row["crew_rows"][0]["project_name"], "Preview project")
        self.assertEqual(driver_row["crew_rows"][0]["crew_name"], "Crew One")
        self.assertEqual(driver_row["driver_resource"].vehicle, self.vehicle)
        self.assertFalse(driver_row["is_free"])
        self.assertEqual(driver_row["stage_label"], "Active worker")
        self.assertEqual(passenger_row["work_next_date"], work_date)
        self.assertTrue(free_row["is_free"])
        self.assertContains(response, "Project / crew")
        self.assertContains(response, "Driver / vehicle")
        self.assertContains(response, "Available")

    def test_workers_page_sorts_every_worker_and_displays_transport_roles(self):
        today = timezone.localdate()
        crew = ProjectCrew.objects.create(
            organization=self.organization,
            project=self.project,
            internal_name="Sorting Crew",
            created_by=self.owner,
        )
        ProjectCrewResourceAssignment.objects.create(
            crew=crew,
            driver_connection=self.driver,
            vehicle=self.vehicle,
            starts_on=today,
            created_by=self.owner,
        )
        for index in range(6):
            self._connection(f"sorting-{index}", f"Sorting {index}")

        response = self.client.get(
            f"{reverse('support:workers')}?organization={self.organization.public_id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["worker_rows"]), 9)
        self.assertContains(response, 'class="worker-sort"', count=6)
        self.assertContains(response, 'data-transport="driver_vehicle"')
        self.assertContains(response, 'data-transport="driver"')
        self.assertContains(response, 'data-transport="passenger"')
        self.assertContains(response, "Driver with vehicle")
        self.assertContains(response, "Passenger")
        self.assertContains(response, "Newest")
        self.assertContains(response, "Available first")

    def test_owner_can_create_project_from_project_first_workspace(self):
        response = self.client.post(
            self._list_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "project_create",
                "name": "Fresh project",
                "worker_capacity": "18",
                "country_code": "NL",
                "city": "Dronten",
                "postal_code": "8251AA",
                "street": "Testweg",
                "building": "12",
                "starts_on": "2026-08-13",
                "ends_on": "",
                "contact_name": "Project Contact",
                "contact_phone": "+31600000000",
                "contact_email": "project@example.com",
                "instructions": "Test project created in the new workspace.",
            },
        )

        project = WorkProject.objects.get(
            organization=self.organization,
            internal_name="Fresh project",
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(project.worker_capacity, 18)
        self.assertEqual(project.worksite.city, "Dronten")
        self.assertIn(str(project.public_id), response["Location"])

    def test_project_rows_separate_permanent_and_date_specific_workers(self):
        crew = self._create_crew()
        ProjectCrewPassenger.objects.create(
            crew=crew,
            connection=self.passenger,
            starts_on=timezone.localdate(),
            created_by=self.owner,
        )
        shift = ProjectCrewShift.objects.create(
            crew=crew,
            work_date=timezone.localdate() + timedelta(days=2),
            starts_at=timezone.now() + timedelta(days=2),
            ends_at=timezone.now() + timedelta(days=2, hours=8),
            created_by=self.owner,
        )
        ProjectCrewShiftMember.objects.create(
            shift=shift,
            connection=self.second_driver,
            role=ProjectCrewShiftMember.ROLE_PASSENGER,
            created_by=self.owner,
        )

        response = self.client.get(self._list_url())

        project = next(item for item in response.context["projects"] if item.pk == self.project.pk)
        self.assertEqual(project.permanent_worker_count, 2)
        self.assertEqual(project.temporary_worker_count, 1)
        self.assertContains(response, "2/12")

    def test_project_edit_preserves_shifts_and_rejects_capacity_below_permanent_roster(self):
        crew = self._create_crew()
        ProjectCrewPassenger.objects.create(
            crew=crew,
            connection=self.passenger,
            starts_on=timezone.localdate(),
            created_by=self.owner,
        )
        shift = ProjectCrewShift.objects.create(
            crew=crew,
            work_date=timezone.localdate() + timedelta(days=3),
            starts_at=timezone.now() + timedelta(days=3),
            ends_at=timezone.now() + timedelta(days=3, hours=8),
            created_by=self.owner,
        )
        payload = {
            "organization": str(self.organization.public_id),
            "action": "project_update",
            "name": "Updated preview project",
            "worker_capacity": "1",
            "country_code": "NL",
            "city": "Dronten",
            "postal_code": "8251AA",
            "street": "Updatedlaan",
            "building": "9",
            "starts_on": "2026-08-01",
            "ends_on": "",
            "contact_name": "Updated contact",
            "contact_phone": "+31611111111",
            "contact_email": "updated@example.com",
            "instructions": "Updated instructions",
        }

        response = self.client.post(self._detail_url(), payload)
        self.project.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.project.worker_capacity, 12)
        self.assertEqual(self.project.internal_name, "Preview project")
        self.assertTrue(ProjectCrewShift.objects.filter(pk=shift.pk).exists())

        payload["worker_capacity"] = "5"
        response = self.client.post(self._detail_url(), payload)
        self.project.refresh_from_db()
        self.worksite.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.project.worker_capacity, 5)
        self.assertEqual(self.project.internal_name, "Updated preview project")
        self.assertEqual(self.worksite.city, "Dronten")
        self.assertTrue(ProjectCrewShift.objects.filter(pk=shift.pk).exists())

    def test_owner_deletes_crew_and_releases_all_active_assignments(self):
        crew = self._create_crew()
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "shifts_publish",
                "crew_id": str(crew.public_id),
                "work_dates": ["2026-08-20"],
                "starts_at_time": "06:00",
                "ends_at_time": "14:45",
                "break_minutes": "30",
            },
        )
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "passenger_add",
                "crew_id": str(crew.public_id),
                "connection_id": str(self.passenger.public_id),
                "scope": "future",
            },
        )

        response = self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "crew_delete",
                "crew_id": str(crew.public_id),
            },
        )

        crew.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(crew.state, ProjectCrew.STATE_ARCHIVED)
        self.assertFalse(crew.resource_assignments.filter(ends_on__isnull=True).exists())
        self.assertFalse(crew.passenger_assignments.filter(ends_on__isnull=True).exists())
        self.assertFalse(crew.calendar_shifts.filter(state=ProjectCrewShift.STATE_PUBLISHED).exists())
        self.assertFalse(
            ScheduledWorkShift.objects.filter(
                project_crew_member__shift__crew=crew,
                state=ScheduledWorkShift.STATE_PUBLISHED,
            ).exists()
        )
        self.assertEqual(Vehicle.objects.filter(pk=self.vehicle.pk).count(), 1)
        self.assertEqual(SupportConnection.objects.filter(organization=self.organization).count(), 3)

        recreate_response = self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "crew_create",
                "internal_name": "Crew Two",
                "driver_id": str(self.driver.public_id),
                "vehicle_id": str(self.vehicle.public_id),
                "starts_on": "2026-08-21",
            },
        )
        self.assertEqual(recreate_response.status_code, 302)
        self.assertTrue(
            ProjectCrew.objects.filter(
                project=self.project,
                internal_name="Crew Two",
                state=ProjectCrew.STATE_ACTIVE,
            ).exists()
        )

    def test_owner_deletes_project_from_list_and_releases_its_crews(self):
        crew = self._create_crew()

        response = self.client.post(
            self._list_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "project_delete",
                "project_id": str(self.project.public_id),
            },
        )

        self.project.refresh_from_db()
        crew.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.project.is_active)
        self.assertEqual(crew.state, ProjectCrew.STATE_ARCHIVED)
        self.assertFalse(
            ProjectCrewResourceAssignment.objects.filter(
                crew=crew,
                ends_on__isnull=True,
            ).exists()
        )
        self.assertEqual(Vehicle.objects.filter(pk=self.vehicle.pk).count(), 1)
        self.assertEqual(SupportConnection.objects.filter(organization=self.organization).count(), 3)

    def test_project_detail_shows_confirmed_delete_buttons(self):
        self._create_crew()

        response = self.client.get(self._detail_url())

        self.assertContains(response, 'value="project_delete"')
        self.assertContains(response, 'value="crew_delete"')
        self.assertContains(response, 'data-pf-confirm="')

    @override_settings(SUPPORT_PROJECT_FIRST_ENABLED=False)
    def test_projects_header_keeps_legacy_fallback_when_preview_is_off(self):
        response = self.client.get(
            f"{reverse('support:workspace')}"
            f"?organization={self.organization.public_id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'href="{reverse("support:projects")}?organization={self.organization.public_id}"',
            html=False,
        )
        self.assertNotContains(
            response,
            f'href="{self._list_url()}"',
            html=False,
        )

    def test_reset_plan_is_read_only_and_reports_current_counts(self):
        response = self.client.get(self._reset_plan_url())

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "support/project_first_reset_plan.html")
        self.assertContains(response, "Staging reset preview")
        self.assertContains(response, "Projects")
        self.assertContains(response, f"RESET-{self.organization.public_id}-WITH-WORK-TIME")
        self.assertEqual(WorkProject.objects.filter(organization=self.organization).count(), 1)
        self.assertEqual(Vehicle.objects.filter(organization=self.organization).count(), 1)

    def test_reset_plan_post_is_blocked_without_server_guard(self):
        self.owner.is_staff = True
        self.owner.save(update_fields=["is_staff"])

        response = self.client.post(
            self._reset_plan_url(),
            {
                "organization": str(self.organization.public_id),
                "confirmation": f"RESET-{self.organization.public_id}-WITH-WORK-TIME",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(WorkProject.objects.filter(organization=self.organization).count(), 1)

    @override_settings(SUPPORT_PROJECT_FIRST_RESET_ALLOWED=True)
    def test_owner_staff_can_apply_exact_confirmed_reset(self):
        self.owner.is_staff = True
        self.owner.save(update_fields=["is_staff"])

        response = self.client.post(
            self._reset_plan_url(),
            {
                "organization": str(self.organization.public_id),
                "confirmation": f"RESET-{self.organization.public_id}-WITH-WORK-TIME",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(WorkProject.objects.filter(organization=self.organization).count(), 0)
        self.assertEqual(Vehicle.objects.filter(organization=self.organization).count(), 1)
        self.assertEqual(SupportConnection.objects.filter(organization=self.organization).count(), 3)

    def test_project_detail_uses_calendar_day_selection_instead_of_date_fields(self):
        self._create_crew()

        response = self.client.get(f"{self._detail_url()}&month=2026-08")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-pf-calendar-form')
        self.assertContains(response, 'name="work_dates" value="', count=31)
        self.assertNotContains(response, 'name="work_dates" data-preserve-empty')

    @override_settings(SUPPORT_PROJECT_FIRST_ENABLED=False)
    def test_preview_returns_not_found_when_second_switch_is_off(self):
        response = self.client.get(self._list_url())
        self.assertEqual(response.status_code, 404)

    def test_owner_creates_crew_and_publishes_selected_days(self):
        crew = self._create_crew()

        response = self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "shifts_publish",
                "crew_id": str(crew.public_id),
                "work_dates": ["2026-08-12", "2026-08-13"],
                "starts_at_time": "06:00",
                "ends_at_time": "14:45",
                "break_minutes": "45",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(crew.calendar_shifts.filter(state=ProjectCrewShift.STATE_PUBLISHED).count(), 2)
        self.assertEqual(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                role=ProjectCrewShiftMember.ROLE_DRIVER,
                connection=self.driver,
            ).count(),
            2,
        )

    def test_owner_adds_passenger_to_selected_days_and_releases_day(self):
        crew = self._create_crew()
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "shifts_publish",
                "crew_id": str(crew.public_id),
                "work_dates": ["2026-08-12", "2026-08-13"],
                "starts_at_time": "06:00",
                "ends_at_time": "14:45",
                "break_minutes": "30",
            },
        )

        response = self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "passenger_add",
                "crew_id": str(crew.public_id),
                "connection_id": str(self.passenger.public_id),
                "scope": "selected",
                "work_dates": ["2026-08-12"],
                "effective_on": "2026-08-12",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                shift__work_date=date(2026, 8, 12),
                connection=self.passenger,
                role=ProjectCrewShiftMember.ROLE_PASSENGER,
            ).exists()
        )
        self.assertFalse(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                shift__work_date=date(2026, 8, 13),
                connection=self.passenger,
            ).exists()
        )
        self.assertTrue(
            ScheduledWorkShift.objects.filter(
                connection=self.passenger,
                project_crew_member__shift__crew=crew,
                work_date=date(2026, 8, 12),
                state=ScheduledWorkShift.STATE_PUBLISHED,
            ).exists()
        )
        passenger_page = self.client.get(f"{self._detail_url()}&month=2026-08")
        self.assertContains(passenger_page, self.passenger.candidate.get_full_name())
        self.assertContains(passenger_page, "12.08")
        self.assertIn(
            self.passenger.public_id,
            {
                item.public_id
                for item in passenger_page.context["crews"][0].available_passengers
            },
        )

        response = self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "shifts_release",
                "crew_id": str(crew.public_id),
                "work_dates": ["2026-08-12"],
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            crew.calendar_shifts.get(work_date=date(2026, 8, 12)).state,
            ProjectCrewShift.STATE_CANCELLED,
        )

    def test_owner_adds_passenger_to_entire_crew_schedule_without_selected_dates(self):
        crew = self._create_crew()
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "shifts_publish",
                "crew_id": str(crew.public_id),
                "work_dates": ["2026-08-12", "2026-08-13"],
                "starts_at_time": "06:00",
                "ends_at_time": "14:45",
                "break_minutes": "30",
            },
        )

        response = self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "passenger_add",
                "crew_id": str(crew.public_id),
                "connection_id": str(self.passenger.public_id),
                "scope": "future",
            },
        )

        self.assertEqual(response.status_code, 302)
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

        page = self.client.get(f"{self._detail_url()}&month=2026-08")
        copy = page.context["pf"]
        self.assertContains(
            page,
            f'<option value="future">{copy["future"]}</option>',
            html=False,
        )
        self.assertContains(
            page,
            f'<option value="selected">{copy["selected"]}</option>',
            html=False,
        )
        self.assertContains(page, copy["select_dates_hint"])
        self.assertContains(page, 'data-pf-has-shift="1"', html=False)

    def test_worker_page_releases_only_selected_passenger_day(self):
        crew = self._create_crew()
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "shifts_publish",
                "crew_id": str(crew.public_id),
                "work_dates": ["2026-08-12", "2026-08-13"],
                "starts_at_time": "06:00",
                "ends_at_time": "14:45",
                "break_minutes": "30",
            },
        )
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "passenger_add",
                "crew_id": str(crew.public_id),
                "connection_id": str(self.passenger.public_id),
                "scope": "selected",
                "work_dates": ["2026-08-12", "2026-08-13"],
            },
        )

        worker_page = self.client.get(self._worker_url(self.passenger))
        self.assertEqual(worker_page.status_code, 200)
        self.assertNotContains(
            worker_page, 'value="scheduled_shifts_from_template"'
        )
        self.assertContains(worker_page, 'value="scheduled_shifts_clear"')
        self.assertContains(worker_page, 'data-project-crew-day="2026-08-12"')
        self.assertContains(worker_page, self.project.internal_name)
        self.assertContains(worker_page, crew.internal_name)
        self.assertContains(worker_page, self.driver.candidate.get_full_name())

        response = self.client.post(
            self._worker_url(self.passenger),
            {
                "organization": str(self.organization.public_id),
                "action": "scheduled_shifts_clear",
                "work_dates": ["2026-08-12"],
                "return_tab": "work_transport",
                "return_month": "2026-08",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                shift__work_date=date(2026, 8, 12),
                connection=self.passenger,
            ).exists()
        )
        self.assertTrue(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                shift__work_date=date(2026, 8, 13),
                connection=self.passenger,
            ).exists()
        )
        self.assertTrue(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                shift__work_date=date(2026, 8, 12),
                connection=self.driver,
                role=ProjectCrewShiftMember.ROLE_DRIVER,
            ).exists()
        )
        self.assertEqual(
            crew.calendar_shifts.get(work_date=date(2026, 8, 12)).state,
            ProjectCrewShift.STATE_PUBLISHED,
        )

        project_page = self.client.get(f"{self._detail_url()}&month=2026-08")
        displayed = next(
            item
            for item in project_page.context["crews"][0].display_passengers
            if item["connection"].pk == self.passenger.pk
        )
        self.assertEqual(displayed["work_dates"], [date(2026, 8, 13)])

    def test_future_passenger_shows_released_day_as_absence(self):
        crew = self._create_crew()
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "shifts_publish",
                "crew_id": str(crew.public_id),
                "work_dates": ["2026-08-12", "2026-08-13"],
                "starts_at_time": "06:00",
                "ends_at_time": "14:45",
                "break_minutes": "30",
            },
        )
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "passenger_add",
                "crew_id": str(crew.public_id),
                "connection_id": str(self.passenger.public_id),
                "scope": "future",
                "effective_on": "2026-08-11",
            },
        )
        self.assertTrue(
            ProjectCrewPassenger.objects.filter(
                crew=crew,
                connection=self.passenger,
                ends_on__isnull=True,
            ).exists()
        )

        response = self.client.post(
            self._worker_url(self.passenger),
            {
                "organization": str(self.organization.public_id),
                "action": "scheduled_shifts_clear",
                "work_dates": ["2026-08-12"],
                "return_tab": "work_transport",
                "return_month": "2026-08",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ProjectCrewMemberAbsence.objects.filter(
                crew=crew,
                connection=self.passenger,
                work_date=date(2026, 8, 12),
            ).exists()
        )

        project_page = self.client.get(f"{self._detail_url()}&month=2026-08")
        displayed = next(
            item
            for item in project_page.context["crews"][0].display_passengers
            if item["connection"].pk == self.passenger.pk
        )
        self.assertEqual(displayed["work_dates"], [date(2026, 8, 13)])
        self.assertEqual(displayed["excluded_dates"], [date(2026, 8, 12)])
        self.assertEqual(displayed["excluded_dates_label"], "12.08")
        self.assertContains(project_page, project_page.context["pf"]["absent_dates"])
        self.assertContains(project_page, "12.08")

        worker_page = self.client.get(
            f"{self._worker_url(self.passenger)}&month=2026-08"
        )
        day = next(
            item
            for item in worker_page.context["calendar_days"]
            if item and item["date"] == date(2026, 8, 12)
        )
        self.assertTrue(day["has_crew_absence"])
        self.assertIsNone(day["display_shift"])
        self.assertEqual(day["project_crew_detail"]["crew_name"], crew.internal_name)
        self.assertContains(worker_page, "has-crew-absence")
        self.assertContains(worker_page, 'value="scheduled_shifts_release_cancel"')

        response = self.client.post(
            self._worker_url(self.passenger),
            {
                "organization": str(self.organization.public_id),
                "action": "scheduled_shifts_release_cancel",
                "work_dates": ["2026-08-12"],
                "return_tab": "work_transport",
                "return_month": "2026-08",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            ProjectCrewMemberAbsence.objects.filter(
                crew=crew,
                connection=self.passenger,
                work_date=date(2026, 8, 12),
            ).exists()
        )
        self.assertTrue(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                shift__work_date=date(2026, 8, 12),
                connection=self.passenger,
                role=ProjectCrewShiftMember.ROLE_PASSENGER,
            ).exists()
        )

    def test_worker_marks_day_off_and_project_crew_labels_it(self):
        crew = self._create_crew()
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "shifts_publish",
                "crew_id": str(crew.public_id),
                "work_dates": ["2026-08-12", "2026-08-13"],
                "starts_at_time": "06:00",
                "ends_at_time": "14:45",
                "break_minutes": "30",
            },
        )
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "passenger_add",
                "crew_id": str(crew.public_id),
                "connection_id": str(self.passenger.public_id),
                "scope": "future",
                "effective_on": "2026-08-11",
            },
        )

        worker_page = self.client.get(self._worker_url(self.passenger))
        self.assertContains(worker_page, 'value="scheduled_shifts_day_off"')
        response = self.client.post(
            self._worker_url(self.passenger),
            {
                "organization": str(self.organization.public_id),
                "action": "scheduled_shifts_day_off",
                "work_dates": ["2026-08-12"],
                "return_tab": "work_transport",
                "return_month": "2026-08",
            },
        )
        self.assertEqual(response.status_code, 302)

        worker_page = self.client.get(self._worker_url(self.passenger))
        day = next(
            item
            for item in worker_page.context["calendar_days"]
            if item and item["date"] == date(2026, 8, 12)
        )
        self.assertTrue(day["has_day_off"])
        self.assertIsNone(day["display_shift"])
        self.assertContains(worker_page, "has-day-off")

        project_page = self.client.get(f"{self._detail_url()}&month=2026-08")
        displayed = next(
            item
            for item in project_page.context["crews"][0].display_passengers
            if item["connection"].pk == self.passenger.pk
        )
        self.assertEqual(displayed["day_off_dates"], [date(2026, 8, 12)])
        self.assertEqual(displayed["excluded_dates"], [])
        self.assertContains(project_page, project_page.context["pf"]["day_off"])
        self.assertContains(project_page, "12.08")

        worker_page = self.client.get(self._worker_url(self.passenger))
        self.assertContains(worker_page, 'value="scheduled_shifts_day_off_cancel"')
        response = self.client.post(
            self._worker_url(self.passenger),
            {
                "organization": str(self.organization.public_id),
                "action": "scheduled_shifts_day_off_cancel",
                "work_dates": ["2026-08-12"],
                "return_tab": "work_transport",
                "return_month": "2026-08",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            WorkerScheduleDayOff.objects.filter(
                connection=self.passenger,
                work_date=date(2026, 8, 12),
            ).exists()
        )
        self.assertTrue(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                shift__work_date=date(2026, 8, 12),
                connection=self.passenger,
                role=ProjectCrewShiftMember.ROLE_PASSENGER,
            ).exists()
        )

    def test_active_substitute_is_shown_as_driver_not_absent_passenger(self):
        crew = self._create_crew()
        work_date = self.future_work_date
        return_month = work_date.strftime("%Y-%m")
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "shifts_publish",
                "crew_id": str(crew.public_id),
                "work_dates": [work_date.isoformat()],
                "starts_at_time": "06:00",
                "ends_at_time": "14:45",
                "break_minutes": "0",
            },
        )
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "passenger_add",
                "crew_id": str(crew.public_id),
                "connection_id": str(self.second_driver.public_id),
                "scope": "future",
                "effective_on": work_date.isoformat(),
            },
        )
        self.client.post(
            self._worker_url(self.driver),
            {
                "organization": str(self.organization.public_id),
                "action": "scheduled_shifts_clear",
                "work_dates": [work_date.isoformat()],
                "return_tab": "work_transport",
                "return_month": return_month,
            },
        )
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "driver_substitute",
                "crew_id": str(crew.public_id),
                "driver_id": str(self.second_driver.public_id),
                "work_dates": [work_date.isoformat()],
                "return_month": return_month,
            },
        )

        page = self.client.get(f"{self._detail_url()}&month={return_month}")
        rendered = next(
            item
            for item in page.context["crews"][0].display_passengers
            if item["connection"].pk == self.second_driver.pk
        )
        self.assertEqual(rendered["driver_dates"], [work_date])
        self.assertEqual(rendered["driver_dates_label"], work_date.strftime("%d.%m"))
        self.assertEqual(rendered["excluded_dates"], [])
        self.assertContains(
            page,
            f'data-pf-select-substitution="{work_date.isoformat()}"',
        )

    def test_driver_absence_marks_project_calendar_as_missing_driver(self):
        crew = self._create_crew()
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "shifts_publish",
                "crew_id": str(crew.public_id),
                "work_dates": ["2026-08-12"],
                "starts_at_time": "06:00",
                "ends_at_time": "14:45",
                "break_minutes": "0",
            },
        )

        response = self.client.post(
            self._worker_url(self.driver),
            {
                "organization": str(self.organization.public_id),
                "action": "scheduled_shifts_clear",
                "work_dates": ["2026-08-12"],
                "return_tab": "work_transport",
                "return_month": "2026-08",
            },
        )
        self.assertEqual(response.status_code, 302)

        project_page = self.client.get(f"{self._detail_url()}&month=2026-08")
        calendar_day = next(
            item
            for item in project_page.context["crews"][0].calendar_days
            if item and item["date"] == date(2026, 8, 12)
        )
        self.assertTrue(calendar_day["has_no_driver"])
        self.assertContains(project_page, "has-no-driver")
        self.assertContains(project_page, project_page.context["pf"]["driver_missing"])

    def test_owner_assigns_substitute_driver_for_selected_absence_date(self):
        crew = self._create_crew()
        work_date = self.future_work_date
        return_month = work_date.strftime("%Y-%m")
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "shifts_publish",
                "crew_id": str(crew.public_id),
                "work_dates": [work_date.isoformat()],
                "starts_at_time": "06:00",
                "ends_at_time": "14:45",
                "break_minutes": "0",
            },
        )
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "passenger_add",
                "crew_id": str(crew.public_id),
                "connection_id": str(self.second_driver.public_id),
                "scope": "selected",
                "work_dates": [work_date.isoformat()],
            },
        )
        self.client.post(
            self._worker_url(self.driver),
            {
                "organization": str(self.organization.public_id),
                "action": "scheduled_shifts_clear",
                "work_dates": [work_date.isoformat()],
                "return_tab": "work_transport",
                "return_month": return_month,
            },
        )
        # A red calendar day is authoritative even if an older workflow did
        # not leave a separate absence row behind.
        ProjectCrewMemberAbsence.objects.filter(
            crew=crew,
            connection=self.driver,
            work_date=work_date,
        ).delete()

        page = self.client.get(f"{self._detail_url()}&month={return_month}")
        self.assertContains(page, "data-pf-substitute-form", html=False)
        self.assertContains(page, 'data-pf-driver-absence="1"', html=False)
        self.assertContains(page, str(self.second_driver.public_id))

        response = self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "driver_substitute",
                "crew_id": str(crew.public_id),
                "driver_id": str(self.second_driver.public_id),
                "work_dates": [work_date.isoformat()],
                "return_month": return_month,
            },
        )

        self.assertEqual(response.status_code, 302)
        substitution = ProjectCrewDriverSubstitution.objects.get(
            crew=crew,
            work_date=work_date,
            state=ProjectCrewDriverSubstitution.STATE_ACTIVE,
        )
        self.assertEqual(
            substitution.substitute_driver_connection,
            self.second_driver,
        )
        self.assertTrue(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                shift__work_date=work_date,
                connection=self.second_driver,
                role=ProjectCrewShiftMember.ROLE_DRIVER,
            ).exists()
        )
        page = self.client.get(f"{self._detail_url()}&month={return_month}")
        self.assertContains(page, page.context["pf"]["substitute_driver"])
        self.assertContains(page, self.second_driver.candidate.get_full_name())
        self.assertNotContains(
            page,
            f'{page.context["pf"]["substitute_on"]} {work_date.strftime("%d.%m")}',
        )

    def test_complete_substitute_driver_web_flow_keeps_calendar_and_history_consistent(self):
        crew = self._create_crew()
        work_date = self.future_work_date
        return_month = work_date.strftime("%Y-%m")
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "shifts_publish",
                "crew_id": str(crew.public_id),
                "work_dates": [work_date.isoformat()],
                "starts_at_time": "06:00",
                "ends_at_time": "14:45",
                "break_minutes": "30",
            },
        )
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "passenger_add",
                "crew_id": str(crew.public_id),
                "connection_id": str(self.second_driver.public_id),
                "scope": "selected",
                "work_dates": [work_date.isoformat()],
            },
        )
        self.client.post(
            self._worker_url(self.driver),
            {
                "organization": str(self.organization.public_id),
                "action": "scheduled_shifts_clear",
                "work_dates": [work_date.isoformat()],
                "return_tab": "work_transport",
                "return_month": return_month,
            },
        )

        response = self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "driver_substitute",
                "crew_id": str(crew.public_id),
                "driver_id": str(self.second_driver.public_id),
                "work_dates": [work_date.isoformat()],
                "return_month": return_month,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ScheduledWorkShift.objects.filter(
                connection=self.second_driver,
                work_date=work_date,
                state=ScheduledWorkShift.STATE_PUBLISHED,
            ).exists()
        )

        response = self.client.post(
            self._worker_url(self.second_driver),
            {
                "organization": str(self.organization.public_id),
                "action": "scheduled_shifts_day_off",
                "work_dates": [work_date.isoformat()],
                "return_tab": "work_transport",
                "return_month": return_month,
            },
        )
        self.assertEqual(response.status_code, 302)

        substitution = ProjectCrewDriverSubstitution.objects.get(
            crew=crew,
            work_date=work_date,
        )
        self.assertEqual(
            substitution.state,
            ProjectCrewDriverSubstitution.STATE_CANCELLED,
        )
        self.assertIsNotNone(substitution.ended_at)
        self.assertFalse(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                shift__work_date=work_date,
                role=ProjectCrewShiftMember.ROLE_DRIVER,
            ).exists()
        )

        page = self.client.get(f"{self._detail_url()}&month={return_month}")
        rendered_crew = page.context["crews"][0]
        calendar_day = next(
            item
            for item in rendered_crew.calendar_days
            if item and item["date"] == work_date
        )
        self.assertTrue(calendar_day["has_no_driver"])
        self.assertEqual(rendered_crew.current_substitution_groups, [])
        self.assertEqual(len(rendered_crew.substitution_history), 1)
        self.assertContains(page, page.context["pf"]["substitution_history"])
        self.assertContains(page, page.context["pf"]["substitution_cancelled"])

    def test_passenger_picker_excludes_current_driver_and_existing_passengers(self):
        crew = self._create_crew()
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "shifts_publish",
                "crew_id": str(crew.public_id),
                "work_dates": ["2026-08-12"],
                "starts_at_time": "06:00",
                "ends_at_time": "14:45",
                "break_minutes": "0",
            },
        )
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "passenger_add",
                "crew_id": str(crew.public_id),
                "connection_id": str(self.passenger.public_id),
                "scope": "future",
                "effective_on": "2026-08-11",
            },
        )

        response = self.client.get(self._detail_url())
        rendered_crew = response.context["crews"][0]
        available_ids = {
            connection.id for connection in rendered_crew.available_passengers
        }

        self.assertNotIn(self.driver.id, available_ids)
        self.assertNotIn(self.passenger.id, available_ids)
        self.assertIn(self.second_driver.id, available_ids)
        self.assertContains(response, response.context["pf"]["create_crew"])
        self.assertNotIn("first", response.context["pf"]["create_crew"].lower())

    def test_passenger_picker_labels_and_sorts_worker_availability(self):
        crew = self._create_crew()
        work_date = timezone.localdate() + timedelta(days=2)
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "shifts_publish",
                "crew_id": str(crew.public_id),
                "work_dates": [work_date.isoformat()],
                "starts_at_time": "06:00",
                "ends_at_time": "14:45",
                "break_minutes": "0",
            },
        )
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "passenger_add",
                "crew_id": str(crew.public_id),
                "connection_id": str(self.passenger.public_id),
                "scope": "selected",
                "work_dates": [work_date.isoformat()],
                "effective_on": timezone.localdate().isoformat(),
            },
        )
        assigned_to_crew = self._connection("assigned-passenger", "Assigned")
        other_crew = ProjectCrew.objects.create(
            organization=self.organization,
            project=self.project,
            internal_name="Crew Two",
            created_by=self.owner,
        )
        ProjectCrewPassenger.objects.create(
            crew=other_crew,
            connection=assigned_to_crew,
            starts_on=timezone.localdate(),
            created_by=self.owner,
        )

        response = self.client.get(
            f"{self._detail_url()}&month={work_date:%Y-%m}"
        )
        rendered_crew = next(
            item for item in response.context["crews"] if item.id == crew.id
        )
        copy = response.context["pf"]
        options = rendered_crew.available_passengers
        labels = {item.id: item.passenger_option_label for item in options}

        self.assertEqual(options[0].id, self.second_driver.id)
        self.assertEqual(
            labels[self.second_driver.id],
            f"Second Worker · {copy['passenger_free']}",
        )
        self.assertEqual(
            labels[self.passenger.id],
            f"Passenger Worker · {copy['passenger_busy_dates']} {work_date:%d.%m}",
        )
        self.assertEqual(
            labels[assigned_to_crew.id],
            f"Assigned Worker · {copy['passenger_busy_crew']} · Crew Two",
        )
        self.assertContains(response, labels[self.second_driver.id])
        self.assertContains(response, labels[self.passenger.id])
        self.assertContains(response, labels[assigned_to_crew.id])

    def test_validation_message_is_shown_instead_of_generic_error(self):
        crew = self._create_crew()
        response = self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "shifts_publish",
                "crew_id": str(crew.public_id),
                "starts_at_time": "06:00",
                "ends_at_time": "14:45",
                "break_minutes": "0",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select at least one calendar date")

    def test_owner_replaces_driver_with_licensed_crew_passenger(self):
        crew = self._create_crew()
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "shifts_publish",
                "crew_id": str(crew.public_id),
                "work_dates": ["2026-08-12"],
                "starts_at_time": "06:00",
                "ends_at_time": "14:45",
                "break_minutes": "0",
            },
        )
        self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "passenger_add",
                "crew_id": str(crew.public_id),
                "connection_id": str(self.second_driver.public_id),
                "scope": "future",
                "effective_on": "2026-08-11",
            },
        )

        response = self.client.post(
            self._detail_url(),
            {
                "organization": str(self.organization.public_id),
                "action": "driver_replace",
                "crew_id": str(crew.public_id),
                "driver_id": str(self.second_driver.public_id),
                "effective_on": "2026-08-11",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            crew.resource_assignments.get(ends_on__isnull=True).driver_connection,
            self.second_driver,
        )
        self.assertTrue(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                connection=self.second_driver,
                role=ProjectCrewShiftMember.ROLE_DRIVER,
            ).exists()
        )
        self.assertTrue(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=crew,
                connection=self.driver,
                role=ProjectCrewShiftMember.ROLE_PASSENGER,
            ).exists()
        )
