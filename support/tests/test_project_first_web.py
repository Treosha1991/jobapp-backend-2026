from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from support.models import (
    ProjectCrew,
    ProjectCrewShift,
    ProjectCrewShiftMember,
    ScheduledWorkShift,
    SupportApplication,
    SupportConnection,
    SupportVacancy,
    Vehicle,
    WorkProject,
    Worksite,
)
from support.services.organizations import activate_organization, create_organization


@override_settings(
    SUPPORT_FEATURE_ENABLED=True,
    SUPPORT_PROJECT_FIRST_ENABLED=True,
)
class ProjectFirstWorkspaceTests(TestCase):
    def setUp(self):
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

    def test_preview_is_separate_and_lists_projects(self):
        response = self.client.get(self._list_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Preview project")
        self.assertTemplateUsed(response, "support/project_first_workspace.html")
        self.assertContains(response, self._reset_plan_url())
        self.assertContains(
            response,
            f'href="{self._list_url()}"',
            html=False,
        )

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
