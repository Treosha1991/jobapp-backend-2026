from datetime import date, datetime, time, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from support.models import (
    AuditEvent,
    HousingAssignment,
    HousingPlace,
    HousingRoom,
    HousingSite,
    OrganizationMembership,
    PermissionGrant,
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
from support.permission_codes import SCHEDULE_MANAGE, TRANSPORT_MANAGE
from support.services.organizations import activate_organization, create_organization


@override_settings(SUPPORT_FEATURE_ENABLED=True, SUPPORT_PROJECT_FIRST_ENABLED=True)
class ProjectFirstReadAPITests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(
            username="project-api-operator",
            email="project-api-operator@example.com",
            password="password",
            is_staff=True,
        )
        self.owner = User.objects.create_user(
            username="project-api-owner",
            email="project-api-owner@example.com",
            password="password",
        )
        self.organization, _ = create_organization(
            jobhub_operator=self.operator,
            legal_name="Project API Agency sp. z o.o.",
            display_name="Project API Agency",
            owner_email=self.owner.email,
        )
        activate_organization(
            jobhub_operator=self.operator,
            organization=self.organization,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.worksite = Worksite.objects.create(
            organization=self.organization,
            internal_name="Lelystad plant",
            country_code="NL",
            city="Lelystad",
            postal_code="8223AA",
            street="Projectstraat",
            building="10",
            created_by=self.owner,
        )
        self.project = WorkProject.objects.create(
            organization=self.organization,
            worksite=self.worksite,
            internal_name="Food project",
            worker_visible_name="Food project",
            worker_capacity=12,
            starts_on=date(2026, 8, 1),
            created_by=self.owner,
        )
        self.driver = self._connection("driver", "Driver", has_driving_license=True)
        self.passenger = self._connection("passenger", "Passenger")
        self.substitute = self._connection(
            "substitute",
            "Substitute",
            has_driving_license=True,
        )
        self.vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Vivaro",
            registration_identifier="API-01",
            seat_capacity=9,
            created_by=self.owner,
        )
        self.crew = ProjectCrew.objects.create(
            organization=self.organization,
            project=self.project,
            internal_name="Crew North",
            created_by=self.owner,
        )
        ProjectCrewResourceAssignment.objects.create(
            crew=self.crew,
            driver_connection=self.driver,
            vehicle=self.vehicle,
            starts_on=date(2026, 8, 1),
            created_by=self.owner,
        )
        ProjectCrewPassenger.objects.create(
            crew=self.crew,
            connection=self.passenger,
            starts_on=date(2026, 8, 1),
            created_by=self.owner,
        )
        starts_at = timezone.make_aware(datetime(2026, 8, 18, 6, 0))
        self.shift = ProjectCrewShift.objects.create(
            crew=self.crew,
            work_date=date(2026, 8, 18),
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=8, minutes=45),
            break_minutes=45,
            created_by=self.owner,
        )
        ProjectCrewShiftMember.objects.create(
            shift=self.shift,
            connection=self.driver,
            role=ProjectCrewShiftMember.ROLE_DRIVER,
            vehicle=self.vehicle,
            created_by=self.owner,
        )
        ProjectCrewShiftMember.objects.create(
            shift=self.shift,
            connection=self.passenger,
            role=ProjectCrewShiftMember.ROLE_PASSENGER,
            created_by=self.owner,
        )
        ProjectCrewMemberAbsence.objects.create(
            organization=self.organization,
            crew=self.crew,
            connection=self.passenger,
            work_date=date(2026, 8, 19),
            created_by=self.owner,
        )
        WorkerScheduleDayOff.objects.create(
            organization=self.organization,
            connection=self.passenger,
            work_date=date(2026, 8, 20),
            created_by=self.owner,
        )
        ProjectCrewDriverSubstitution.objects.create(
            organization=self.organization,
            crew=self.crew,
            work_date=date(2026, 8, 21),
            primary_driver_connection=self.driver,
            substitute_driver_connection=self.substitute,
            vehicle=self.vehicle,
            created_by=self.owner,
        )
        self.list_url = (
            f"/api/v2/support/organizations/{self.organization.public_id}/"
            "project-first/projects/"
        )
        self.workspace_url = (
            f"{self.list_url}{self.project.public_id}/workspace/?month=2026-08"
        )
        self.detail_url = f"{self.list_url}{self.project.public_id}/"
        self.crew_list_url = f"{self.detail_url}crews/"
        self.crew_detail_url = (
            f"/api/v2/support/organizations/{self.organization.public_id}/"
            f"project-first/crews/{self.crew.public_id}/"
        )
        self.shift_replace_url = f"{self.crew_detail_url}shifts/replace/"
        self.shift_release_url = f"{self.crew_detail_url}shifts/release/"
        self.passenger_apply_url = f"{self.crew_detail_url}passengers/apply/"
        self.passenger_remove_url = f"{self.crew_detail_url}passengers/remove/"
        self.driver_replace_url = f"{self.crew_detail_url}driver/replace/"
        self.driver_absence_url = f"{self.crew_detail_url}driver/absence/"
        self.driver_substitute_url = f"{self.crew_detail_url}driver/substitute/"

    def _connection(self, suffix, first_name, *, has_driving_license=False):
        candidate = User.objects.create_user(
            username=f"project-api-{suffix}",
            email=f"project-api-{suffix}@example.com",
            password="password",
            first_name=first_name,
            last_name="Worker",
        )
        vacancy = SupportVacancy.objects.create(
            organization=self.organization,
            internal_title=f"Vacancy {suffix}",
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
            has_driving_license=has_driving_license,
        )

    def _project_input(self, **overrides):
        data = {
            "name": "New warehouse",
            "country_code": "nl",
            "city": "Dronten",
            "postal_code": "8251AA",
            "street": "Nieuweweg",
            "building": "20",
            "worker_capacity": 18,
            "starts_on": "2026-09-01",
            "ends_on": None,
            "contact_name": "Project Contact",
            "contact_phone": "+31123456789",
            "contact_email": "project@example.com",
            "instructions": "Use the north entrance.",
        }
        data.update(overrides)
        return data

    def test_vehicle_list_includes_current_project_crew_driver(self):
        resource = self.crew.resource_assignments.get()
        resource.starts_on = min(resource.starts_on, timezone.localdate())
        resource.save(update_fields=["starts_on", "updated_at"])

        response = self.client.get(
            f"/api/v2/support/organizations/{self.organization.public_id}/"
            "operations/vehicles/"
        )

        self.assertEqual(response.status_code, 200, response.data)
        vehicle = next(
            item
            for item in response.data["results"]
            if item["id"] == str(self.vehicle.public_id)
        )
        self.assertEqual(
            vehicle["current_driver"]["id"],
            str(self.driver.public_id),
        )
        self.assertEqual(
            vehicle["current_driver"]["display_name"],
            "Driver Worker",
        )
        self.assertEqual(vehicle["current_driver"]["project"], "Food project")

    def test_worker_summary_shows_upcoming_project_and_published_housing(self):
        future_date = timezone.localdate() + timedelta(days=2)
        future_start = timezone.make_aware(datetime.combine(future_date, time(6, 0)))
        future_shift = ProjectCrewShift.objects.create(
            crew=self.crew,
            work_date=future_date,
            starts_at=future_start,
            ends_at=future_start + timedelta(hours=8),
            created_by=self.owner,
        )
        ProjectCrewShiftMember.objects.create(
            shift=future_shift,
            connection=self.passenger,
            role=ProjectCrewShiftMember.ROLE_PASSENGER,
            created_by=self.owner,
        )
        housing_site = HousingSite.objects.create(
            organization=self.organization,
            internal_name="Future worker home",
            country_code="NL",
            city="Lelystad",
            street="Housingstraat",
            building="8",
            created_by=self.owner,
        )
        room = HousingRoom.objects.create(site=housing_site, label="2A", capacity=1)
        place = HousingPlace.objects.create(room=room, label="1")
        HousingAssignment.objects.create(
            organization=self.organization,
            connection=self.passenger,
            place=place,
            check_in_at=timezone.now() + timedelta(days=1),
            state=HousingAssignment.STATE_PUBLISHED,
            created_by=self.owner,
        )

        response = self.client.get(
            f"/api/v2/support/organizations/{self.organization.public_id}/"
            f"connections/{self.passenger.public_id}/summary/"
        )

        self.assertEqual(response.status_code, 200, response.data)
        header = response.data["profile_header"]
        self.assertEqual(header["current_project"]["name"], "Food project")
        self.assertEqual(header["current_project"]["crew_name"], "Crew North")
        self.assertEqual(header["current_housing"]["site_name"], "Future worker home")
        self.assertEqual(header["current_housing"]["room_label"], "2A")

    def test_worker_summary_shows_active_crew_without_a_shift_today(self):
        ProjectCrewShift.objects.filter(crew=self.crew).delete()

        response = self.client.get(
            f"/api/v2/support/organizations/{self.organization.public_id}/"
            f"connections/{self.passenger.public_id}/summary/"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data["profile_header"]["current_project"]["name"],
            "Food project",
        )

    def _crew_input(self, driver, vehicle, **overrides):
        data = {
            "internal_name": "Crew South",
            "driver_connection_id": str(driver.public_id),
            "vehicle_id": str(vehicle.public_id),
            "starts_on": "2026-09-01",
        }
        data.update(overrides)
        return data

    def _available_crew_resources(self, suffix="new"):
        driver = self._connection(
            f"crew-{suffix}",
            f"Crew {suffix.title()}",
            has_driving_license=True,
        )
        vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name=f"Vehicle {suffix.title()}",
            registration_identifier=f"CREW-{suffix.upper()}",
            seat_capacity=7,
            created_by=self.owner,
        )
        return driver, vehicle

    def test_project_list_returns_only_canonical_organization_projects(self):
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["organization"]["id"], str(self.organization.public_id))
        self.assertEqual(len(response.data["projects"]), 1)
        project = response.data["projects"][0]
        self.assertEqual(project["id"], str(self.project.public_id))
        self.assertEqual(project["summary"]["crew_count"], 1)
        self.assertEqual(project["summary"]["permanent_worker_count"], 2)
        self.assertEqual(project["summary"]["published_shift_count"], 1)
        creation_options = response.data["creation_options"]
        driver_options = {
            item["id"]: item for item in creation_options["drivers"]
        }
        self.assertIn(str(self.driver.public_id), driver_options)
        driver_option = driver_options[str(self.driver.public_id)]
        self.assertEqual(
            driver_option["preferred_vehicle_id"],
            str(self.vehicle.public_id),
        )
        self.assertTrue(driver_option["project_vehicle_locked"])
        self.assertIn(
            str(self.vehicle.public_id),
            [item["id"] for item in creation_options["vehicles"]],
        )

    def test_project_create_is_idempotent_for_same_key_and_payload(self):
        request_id = "d32a4174-aa6b-4e9a-af29-0904088c981f"
        headers = {"HTTP_IDEMPOTENCY_KEY": request_id}

        first = self.client.post(
            self.list_url,
            self._project_input(),
            format="json",
            **headers,
        )
        second = self.client.post(
            self.list_url,
            self._project_input(),
            format="json",
            **headers,
        )

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(first.data["project"]["id"], second.data["project"]["id"])
        self.assertEqual(
            WorkProject.objects.filter(organization=self.organization).count(),
            2,
        )
        project = WorkProject.objects.get(public_id=first.data["project"]["id"])
        self.assertEqual(project.worksite.country_code, "NL")
        self.assertEqual(
            AuditEvent.objects.filter(
                organization=self.organization,
                action="work.project_created",
                request_id=request_id,
            ).count(),
            1,
        )

    def test_project_create_rejects_reused_key_with_different_payload(self):
        request_id = "a56c93ae-9897-4e93-a27b-9862661d1e07"
        headers = {"HTTP_IDEMPOTENCY_KEY": request_id}
        first = self.client.post(
            self.list_url,
            self._project_input(),
            format="json",
            **headers,
        )

        response = self.client.post(
            self.list_url,
            self._project_input(city="Emmeloord"),
            format="json",
            **headers,
        )

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "idempotency_key_reused")
        self.assertEqual(
            WorkProject.objects.filter(organization=self.organization).count(),
            2,
        )

    def test_project_create_requires_valid_idempotency_key(self):
        missing = self.client.post(self.list_url, self._project_input(), format="json")
        invalid = self.client.post(
            self.list_url,
            self._project_input(),
            format="json",
            HTTP_IDEMPOTENCY_KEY="not-a-uuid",
        )

        self.assertEqual(missing.status_code, 400, missing.data)
        self.assertEqual(missing.data["code"], "idempotency_key_required")
        self.assertEqual(invalid.status_code, 400, invalid.data)
        self.assertEqual(invalid.data["code"], "idempotency_key_invalid")

    def test_project_create_rejects_unknown_fields_with_stable_error(self):
        response = self.client.post(
            self.list_url,
            self._project_input(secret="must-not-be-accepted"),
            format="json",
            HTTP_IDEMPOTENCY_KEY="84a42ef7-b49d-4ef1-9871-7f180d0cb26c",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "unsupported_support_field")
        self.assertEqual(
            response.data["field_errors"]["non_field_errors"],
            ["unsupported_support_field"],
        )

    def test_project_patch_updates_project_and_address_without_touching_shifts(self):
        shift_id = self.shift.pk

        response = self.client.patch(
            self.detail_url,
            {
                "name": "Updated food project",
                "city": "Dronten",
                "worker_capacity": 20,
                "contact_email": "updated@example.com",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.project.refresh_from_db()
        self.project.worksite.refresh_from_db()
        self.assertEqual(self.project.internal_name, "Updated food project")
        self.assertEqual(self.project.worksite.internal_name, "Updated food project")
        self.assertEqual(self.project.worksite.city, "Dronten")
        self.assertEqual(self.project.contact_email, "updated@example.com")
        self.assertTrue(ProjectCrewShift.objects.filter(pk=shift_id).exists())

    def test_project_patch_rejects_capacity_below_permanent_roster(self):
        response = self.client.patch(
            self.detail_url,
            {"worker_capacity": 1},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(
            response.data["code"],
            "project_capacity_below_permanent_roster",
        )
        self.assertEqual(response.data["minimum"], 2)
        self.project.refresh_from_db()
        self.assertEqual(self.project.worker_capacity, 12)

    def test_project_delete_archives_project_and_is_safe_to_repeat(self):
        first = self.client.delete(self.detail_url, {}, format="json")
        second = self.client.delete(self.detail_url, {}, format="json")

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertTrue(first.data["deleted"])
        self.project.refresh_from_db()
        self.crew.refresh_from_db()
        self.assertFalse(self.project.is_active)
        self.assertEqual(self.crew.state, ProjectCrew.STATE_ARCHIVED)
        self.assertFalse(
            ProjectCrewShift.objects.filter(
                pk=self.shift.pk,
                state=ProjectCrewShift.STATE_PUBLISHED,
            ).exists()
        )

    def test_project_patch_errors_can_be_localized(self):
        response = self.client.patch(
            self.detail_url,
            {"worker_capacity": 1},
            format="json",
            HTTP_ACCEPT_LANGUAGE="pl-PL",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(
            response.data["message"],
            "Liczba miejsc nie może być mniejsza niż stały skład projektu.",
        )

    def test_crew_create_is_idempotent_for_same_key_and_payload(self):
        driver, vehicle = self._available_crew_resources("idempotent")
        request_id = "9393db35-18d0-46bb-b9b8-88d18045cc70"
        headers = {"HTTP_IDEMPOTENCY_KEY": request_id}

        first = self.client.post(
            self.crew_list_url,
            self._crew_input(driver, vehicle),
            format="json",
            **headers,
        )
        second = self.client.post(
            self.crew_list_url,
            self._crew_input(driver, vehicle),
            format="json",
            **headers,
        )

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(first.data["crew"]["id"], second.data["crew"]["id"])
        self.assertEqual(
            ProjectCrew.objects.filter(
                project=self.project,
                internal_name="Crew South",
            ).count(),
            1,
        )
        self.assertEqual(
            AuditEvent.objects.filter(
                organization=self.organization,
                action="project_crew.created",
                request_id=request_id,
            ).count(),
            1,
        )

    def test_crew_create_rejects_reused_key_with_different_payload(self):
        driver, vehicle = self._available_crew_resources("reused")
        request_id = "2a906f5d-8f29-45fb-876a-e3dcabfd8002"
        headers = {"HTTP_IDEMPOTENCY_KEY": request_id}
        first = self.client.post(
            self.crew_list_url,
            self._crew_input(driver, vehicle),
            format="json",
            **headers,
        )

        response = self.client.post(
            self.crew_list_url,
            self._crew_input(driver, vehicle, internal_name="Different crew"),
            format="json",
            **headers,
        )

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "crew_idempotency_key_reused")

    def test_crew_create_requires_valid_idempotency_key_and_strict_body(self):
        driver, vehicle = self._available_crew_resources("strict")
        payload = self._crew_input(driver, vehicle)

        missing = self.client.post(self.crew_list_url, payload, format="json")
        invalid = self.client.post(
            self.crew_list_url,
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="not-a-uuid",
        )
        unknown = self.client.post(
            self.crew_list_url,
            {**payload, "secret": "must-not-be-accepted"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="35c4bad5-5b10-4583-8822-659891ad14b0",
        )

        self.assertEqual(missing.status_code, 400, missing.data)
        self.assertEqual(missing.data["code"], "crew_idempotency_key_required")
        self.assertEqual(invalid.status_code, 400, invalid.data)
        self.assertEqual(invalid.data["code"], "crew_idempotency_key_invalid")
        self.assertEqual(unknown.status_code, 400, unknown.data)
        self.assertEqual(unknown.data["code"], "unsupported_support_field")

    def test_crew_create_rejects_unlicensed_driver_and_rolls_back(self):
        vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Unlicensed vehicle",
            registration_identifier="NO-LICENCE",
            seat_capacity=5,
            created_by=self.owner,
        )
        before = ProjectCrew.objects.count()

        response = self.client.post(
            self.crew_list_url,
            self._crew_input(self.passenger, vehicle),
            format="json",
            HTTP_IDEMPOTENCY_KEY="7a54f065-7836-46ff-ae92-8a78a34ed437",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "driver_licence_not_confirmed")
        self.assertEqual(ProjectCrew.objects.count(), before)
        self.assertFalse(
            ProjectCrewResourceAssignment.objects.filter(vehicle=vehicle).exists()
        )

    def test_crew_patch_renames_without_touching_resource_roster_or_shift(self):
        resource_id = self.crew.resource_assignments.get().pk
        passenger_id = self.crew.passenger_assignments.get().pk
        shift_id = self.shift.pk

        response = self.client.patch(
            self.crew_detail_url,
            {"internal_name": "Renamed crew"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.crew.refresh_from_db()
        self.assertEqual(self.crew.internal_name, "Renamed crew")
        self.assertTrue(ProjectCrewResourceAssignment.objects.filter(pk=resource_id).exists())
        self.assertTrue(ProjectCrewPassenger.objects.filter(pk=passenger_id).exists())
        self.assertTrue(ProjectCrewShift.objects.filter(pk=shift_id).exists())
        self.assertEqual(response.data["crew"]["internal_name"], "Renamed crew")

    def test_crew_patch_rejects_empty_body(self):
        response = self.client.patch(self.crew_detail_url, {}, format="json")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "crew_patch_empty")

    def test_crew_delete_archives_releases_assignments_and_is_safe_to_repeat(self):
        first = self.client.delete(self.crew_detail_url, {}, format="json")
        second = self.client.delete(self.crew_detail_url, {}, format="json")

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertTrue(first.data["deleted"])
        self.crew.refresh_from_db()
        self.shift.refresh_from_db()
        self.assertEqual(self.crew.state, ProjectCrew.STATE_ARCHIVED)
        self.assertEqual(self.shift.state, ProjectCrewShift.STATE_CANCELLED)
        self.assertFalse(
            ProjectCrewResourceAssignment.objects.filter(
                crew=self.crew,
                ends_on__isnull=True,
            ).exists()
        )
        self.assertFalse(
            ProjectCrewPassenger.objects.filter(
                crew=self.crew,
                ends_on__isnull=True,
            ).exists()
        )

    def test_crew_create_errors_can_be_localized(self):
        vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Polish error vehicle",
            registration_identifier="PL-ERROR",
            seat_capacity=5,
            created_by=self.owner,
        )

        response = self.client.post(
            self.crew_list_url,
            self._crew_input(self.passenger, vehicle),
            format="json",
            HTTP_ACCEPT_LANGUAGE="pl-PL",
            HTTP_IDEMPOTENCY_KEY="24066774-e3de-4f06-942a-a34354655657",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(
            response.data["message"],
            "Wybrany pracownik nie ma potwierdzonego prawa jazdy.",
        )

    def test_shift_replace_publishes_selected_days_and_worker_calendars(self):
        response = self.client.post(
            self.shift_replace_url,
            {
                "work_dates": ["2026-08-22", "2026-08-23", "2026-08-22"],
                "starts_at_time": "07:00",
                "ends_at_time": "15:30",
                "break_minutes": 30,
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="b40d4275-bb66-4502-8954-6b834256fd6c",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["affected_dates"], ["2026-08-22", "2026-08-23"])
        self.assertEqual(len(response.data["days"]), 2)
        self.assertTrue(all(day["shift"]["state"] == "published" for day in response.data["days"]))
        shifts = ProjectCrewShift.objects.filter(
            crew=self.crew,
            work_date__in=(date(2026, 8, 22), date(2026, 8, 23)),
            state=ProjectCrewShift.STATE_PUBLISHED,
        )
        self.assertEqual(shifts.count(), 2)
        self.assertEqual(
            ScheduledWorkShift.objects.filter(
                project_crew_member__shift__in=shifts,
                state=ScheduledWorkShift.STATE_PUBLISHED,
            ).count(),
            4,
        )

    def test_shift_replace_replaces_existing_day_and_mirror(self):
        response = self.client.post(
            self.shift_replace_url,
            {
                "work_dates": ["2026-08-18"],
                "starts_at_time": "08:15",
                "ends_at_time": "17:00",
                "break_minutes": 20,
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="6bbcb872-1795-49d8-a49f-b6e55a042971",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.shift.refresh_from_db()
        self.assertEqual(timezone.localtime(self.shift.starts_at).time(), time(8, 15))
        self.assertEqual(self.shift.break_minutes, 20)
        mirrors = ScheduledWorkShift.objects.filter(
            project_crew_member__shift=self.shift,
            state=ScheduledWorkShift.STATE_PUBLISHED,
        )
        self.assertEqual(mirrors.count(), 2)
        self.assertTrue(all(timezone.localtime(item.starts_at).time() == time(8, 15) for item in mirrors))

    def test_shift_replace_is_idempotent_and_rejects_reused_key(self):
        request_id = "3cb51dd9-ece6-4f2d-a821-c151539bcbb7"
        payload = {
            "work_dates": ["2026-08-24"],
            "starts_at_time": "06:00",
            "ends_at_time": "14:00",
            "break_minutes": 15,
        }
        headers = {"HTTP_IDEMPOTENCY_KEY": request_id}

        first = self.client.post(self.shift_replace_url, payload, format="json", **headers)
        second = self.client.post(self.shift_replace_url, payload, format="json", **headers)
        reused = self.client.post(
            self.shift_replace_url,
            {**payload, "ends_at_time": "15:00"},
            format="json",
            **headers,
        )

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(reused.status_code, 400, reused.data)
        self.assertEqual(reused.data["code"], "shift_idempotency_key_reused")
        self.assertEqual(
            ProjectCrewShift.objects.filter(crew=self.crew, work_date=date(2026, 8, 24)).count(),
            1,
        )
        self.assertEqual(
            AuditEvent.objects.filter(
                organization=self.organization,
                action="project_crew.shifts_published",
                request_id=request_id,
            ).count(),
            1,
        )

    def test_shift_replace_requires_key_strict_body_and_dates(self):
        payload = {
            "work_dates": ["2026-08-25"],
            "starts_at_time": "06:00",
            "ends_at_time": "14:00",
            "break_minutes": 15,
        }
        missing_key = self.client.post(self.shift_replace_url, payload, format="json")
        unknown = self.client.post(
            self.shift_replace_url,
            {**payload, "secret": "no"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="742085a1-b2a7-4134-b274-7d25fcc4ae8f",
        )
        no_dates = self.client.post(
            self.shift_replace_url,
            {**payload, "work_dates": []},
            format="json",
            HTTP_IDEMPOTENCY_KEY="0a9e90c7-b9dc-4d4e-bf34-b6edb09ece0c",
        )

        self.assertEqual(missing_key.status_code, 400, missing_key.data)
        self.assertEqual(missing_key.data["code"], "shift_idempotency_key_required")
        self.assertEqual(unknown.status_code, 400, unknown.data)
        self.assertEqual(unknown.data["code"], "unsupported_support_field")
        self.assertEqual(no_dates.status_code, 400, no_dates.data)
        self.assertEqual(no_dates.data["code"], "work_dates_required")

    def test_shift_replace_rolls_back_all_days_when_capacity_is_exceeded(self):
        extra_passenger = self._connection("capacity-extra", "Capacity Extra")
        ProjectCrewPassenger.objects.create(
            crew=self.crew,
            connection=extra_passenger,
            starts_on=date(2026, 8, 1),
            created_by=self.owner,
        )
        self.vehicle.seat_capacity = 2
        self.vehicle.save(update_fields=("seat_capacity", "updated_at"))

        response = self.client.post(
            self.shift_replace_url,
            {
                "work_dates": ["2026-08-25", "2026-08-26"],
                "starts_at_time": "06:00",
                "ends_at_time": "14:00",
                "break_minutes": 15,
            },
            format="json",
            HTTP_ACCEPT_LANGUAGE="pl-PL",
            HTTP_IDEMPOTENCY_KEY="8af16a21-934c-4208-85c0-cfef2acdb94d",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "crew_capacity_exceeded")
        self.assertEqual(
            response.data["message"],
            "W jednym z wybranych dni przekroczono liczbę miejsc w samochodzie.",
        )
        self.assertFalse(
            ProjectCrewShift.objects.filter(
                crew=self.crew,
                work_date__in=(date(2026, 8, 25), date(2026, 8, 26)),
            ).exists()
        )

    def test_shift_release_cancels_day_and_worker_calendars_idempotently(self):
        # Ensure the legacy worker-calendar projection exists before release.
        replace = self.client.post(
            self.shift_replace_url,
            {
                "work_dates": ["2026-08-18"],
                "starts_at_time": "06:00",
                "ends_at_time": "14:45",
                "break_minutes": 45,
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="c47ca5aa-9990-4663-a23a-d18ebd57f8b6",
        )
        self.assertEqual(replace.status_code, 200, replace.data)
        request_id = "a2145ae6-cd8b-47e7-a893-3764c9dd76c9"
        payload = {"work_dates": ["2026-08-18"]}
        headers = {"HTTP_IDEMPOTENCY_KEY": request_id}

        first = self.client.post(self.shift_release_url, payload, format="json", **headers)
        second = self.client.post(self.shift_release_url, payload, format="json", **headers)
        reused = self.client.post(
            self.shift_release_url,
            {"work_dates": ["2026-08-19"]},
            format="json",
            **headers,
        )

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(reused.status_code, 400, reused.data)
        self.assertEqual(reused.data["code"], "shift_idempotency_key_reused")
        self.assertEqual(first.data["days"][0]["shift"]["state"], "cancelled")
        self.shift.refresh_from_db()
        self.assertEqual(self.shift.state, ProjectCrewShift.STATE_CANCELLED)
        self.assertFalse(
            ScheduledWorkShift.objects.filter(
                project_crew_member__shift=self.shift,
            ).exclude(state=ScheduledWorkShift.STATE_CANCELLED).exists()
        )
        self.assertEqual(
            AuditEvent.objects.filter(
                organization=self.organization,
                action="project_crew.shifts_released",
                request_id=request_id,
            ).count(),
            1,
        )

    def test_passenger_apply_selected_dates_updates_daily_crew_and_calendar(self):
        extra = self._connection("selected-passenger", "Selected Passenger")
        replace = self.client.post(
            self.shift_replace_url,
            {
                "work_dates": ["2026-08-22", "2026-08-23"],
                "starts_at_time": "06:00",
                "ends_at_time": "14:45",
                "break_minutes": 45,
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="c2e26d2d-bcab-49e7-b873-59d8ef0d7201",
        )
        self.assertEqual(replace.status_code, 200, replace.data)

        response = self.client.post(
            self.passenger_apply_url,
            {
                "connection_id": str(extra.public_id),
                "scope": "selected_dates",
                "work_dates": ["2026-08-23", "2026-08-22", "2026-08-22"],
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="9e4344cb-d25a-42d5-ad78-4d9b55af79e5",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["scope"], "selected_dates")
        self.assertEqual(response.data["affected_dates"], ["2026-08-22", "2026-08-23"])
        self.assertEqual(len(response.data["days"]), 2)
        self.assertFalse(
            ProjectCrewPassenger.objects.filter(crew=self.crew, connection=extra).exists()
        )
        memberships = ProjectCrewShiftMember.objects.filter(
            shift__crew=self.crew,
            shift__work_date__in=(date(2026, 8, 22), date(2026, 8, 23)),
            connection=extra,
            role=ProjectCrewShiftMember.ROLE_PASSENGER,
        )
        self.assertEqual(memberships.count(), 2)
        self.assertEqual(
            ScheduledWorkShift.objects.filter(
                project_crew_member__in=memberships,
                state=ScheduledWorkShift.STATE_PUBLISHED,
            ).count(),
            2,
        )

    def test_passenger_apply_all_future_creates_roster_and_is_idempotent(self):
        extra = self._connection("future-passenger", "Future Passenger")
        replace = self.client.post(
            self.shift_replace_url,
            {
                "work_dates": ["2026-08-24", "2026-08-25"],
                "starts_at_time": "07:00",
                "ends_at_time": "15:00",
                "break_minutes": 30,
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="9c7b6216-497e-42bb-9a38-e547f9dc68d8",
        )
        self.assertEqual(replace.status_code, 200, replace.data)
        payload = {
            "connection_id": str(extra.public_id),
            "scope": "all_future",
            "effective_on": "2026-08-24",
        }
        headers = {"HTTP_IDEMPOTENCY_KEY": "9140f24f-b6bd-485f-9eaf-21c09ae7f536"}

        first = self.client.post(self.passenger_apply_url, payload, format="json", **headers)
        second = self.client.post(self.passenger_apply_url, payload, format="json", **headers)
        reused = self.client.post(
            self.passenger_apply_url,
            {**payload, "effective_on": "2026-08-25"},
            format="json",
            **headers,
        )

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(reused.status_code, 400, reused.data)
        self.assertEqual(reused.data["code"], "passenger_idempotency_key_reused")
        self.assertEqual(first.data["affected_dates"], ["2026-08-24", "2026-08-25"])
        self.assertEqual(
            ProjectCrewPassenger.objects.filter(
                crew=self.crew,
                connection=extra,
                ends_on__isnull=True,
            ).count(),
            1,
        )
        self.assertEqual(
            AuditEvent.objects.filter(
                organization=self.organization,
                action="project_crew.passenger_assigned",
                request_id=headers["HTTP_IDEMPOTENCY_KEY"],
            ).count(),
            1,
        )

    def test_passenger_remove_selected_keeps_future_roster(self):
        # The fixture passenger is permanent and present on 18 August.
        response = self.client.post(
            self.passenger_remove_url,
            {
                "connection_id": str(self.passenger.public_id),
                "scope": "selected_dates",
                "work_dates": ["2026-08-18"],
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="16d99edf-42fa-4bb9-bec9-bab9ebf1496e",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(
            ProjectCrewPassenger.objects.filter(
                crew=self.crew,
                connection=self.passenger,
                ends_on__isnull=True,
            ).exists()
        )
        self.assertFalse(
            ProjectCrewShiftMember.objects.filter(
                shift=self.shift,
                connection=self.passenger,
                role=ProjectCrewShiftMember.ROLE_PASSENGER,
            ).exists()
        )

    def test_passenger_remove_all_future_closes_roster_and_is_idempotent(self):
        payload = {
            "connection_id": str(self.passenger.public_id),
            "scope": "all_future",
            "effective_on": "2026-08-18",
        }
        headers = {"HTTP_IDEMPOTENCY_KEY": "24f804ef-0bc4-4798-a30f-cc65b34e7d5f"}

        first = self.client.post(self.passenger_remove_url, payload, format="json", **headers)
        second = self.client.post(self.passenger_remove_url, payload, format="json", **headers)

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertFalse(
            ProjectCrewPassenger.objects.filter(
                crew=self.crew,
                connection=self.passenger,
                ends_on__isnull=True,
            ).exists()
        )
        self.assertFalse(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=self.crew,
                shift__work_date__gte=date(2026, 8, 18),
                connection=self.passenger,
                role=ProjectCrewShiftMember.ROLE_PASSENGER,
            ).exists()
        )

    def test_passenger_write_requires_key_and_strict_scope_fields(self):
        base = {
            "connection_id": str(self.passenger.public_id),
            "scope": "selected_dates",
            "work_dates": ["2026-08-18"],
        }
        missing_key = self.client.post(self.passenger_apply_url, base, format="json")
        invalid_key = self.client.post(
            self.passenger_apply_url,
            base,
            format="json",
            HTTP_IDEMPOTENCY_KEY="bad-key",
        )
        unknown = self.client.post(
            self.passenger_apply_url,
            {**base, "secret": "no"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="cae414b4-dc88-47a4-bb31-757268b150b4",
        )
        missing_dates = self.client.post(
            self.passenger_apply_url,
            {"connection_id": str(self.passenger.public_id), "scope": "selected_dates"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="91b8dc21-b747-4172-bf38-5ba7623196fa",
        )
        mixed_scope = self.client.post(
            self.passenger_apply_url,
            {
                "connection_id": str(self.passenger.public_id),
                "scope": "all_future",
                "effective_on": "2026-08-18",
                "work_dates": ["2026-08-18"],
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="1042d927-2bf2-4a54-8c9e-ef87a16c9154",
        )

        self.assertEqual(missing_key.data["code"], "passenger_idempotency_key_required")
        self.assertEqual(invalid_key.data["code"], "passenger_idempotency_key_invalid")
        self.assertEqual(unknown.data["code"], "unsupported_support_field")
        self.assertEqual(missing_dates.data["code"], "passenger_work_dates_required")
        self.assertEqual(mixed_scope.data["code"], "passenger_work_dates_not_allowed")

    def test_passenger_apply_rolls_back_every_selected_day_on_capacity_error(self):
        extra = self._connection("rollback-passenger", "Rollback Passenger")
        replace = self.client.post(
            self.shift_replace_url,
            {
                "work_dates": ["2026-08-26", "2026-08-27"],
                "starts_at_time": "06:00",
                "ends_at_time": "14:00",
                "break_minutes": 15,
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="d424b36a-86bb-4235-b8f6-27e67f46e220",
        )
        self.assertEqual(replace.status_code, 200, replace.data)
        second_shift = ProjectCrewShift.objects.get(crew=self.crew, work_date=date(2026, 8, 27))
        crowd = self._connection("crowd", "Crowd Passenger")
        ProjectCrewShiftMember.objects.create(
            shift=second_shift,
            connection=crowd,
            role=ProjectCrewShiftMember.ROLE_PASSENGER,
            created_by=self.owner,
        )
        self.vehicle.seat_capacity = 3
        self.vehicle.save(update_fields=("seat_capacity", "updated_at"))

        response = self.client.post(
            self.passenger_apply_url,
            {
                "connection_id": str(extra.public_id),
                "scope": "selected_dates",
                "work_dates": ["2026-08-26", "2026-08-27"],
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="673ae363-383e-4124-8336-dc6a56de0a35",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "crew_capacity_exceeded")
        self.assertFalse(
            ProjectCrewShiftMember.objects.filter(
                shift__crew=self.crew,
                connection=extra,
            ).exists()
        )

    def test_driver_replace_transfers_vehicle_and_future_daily_roles(self):
        self.passenger.has_driving_license = True
        self.passenger.save(update_fields=("has_driving_license", "updated_at"))

        response = self.client.post(
            self.driver_replace_url,
            {
                "new_driver_connection_id": str(self.passenger.public_id),
                "effective_on": "2026-08-18",
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="c62b3c54-eb7a-4480-ac86-ff880dfd9b17",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data["replacement"]["driver_id"],
            str(self.passenger.public_id),
        )
        self.assertEqual(
            response.data["replacement"]["vehicle_id"],
            str(self.vehicle.public_id),
        )
        self.assertEqual(response.data["affected_dates"], ["2026-08-18"])
        replacement = ProjectCrewResourceAssignment.objects.get(
            public_id=response.data["replacement"]["id"]
        )
        self.assertEqual(replacement.driver_connection, self.passenger)
        self.assertEqual(replacement.vehicle, self.vehicle)
        self.assertEqual(replacement.starts_on, date(2026, 8, 18))
        self.assertTrue(
            ProjectCrewShiftMember.objects.filter(
                shift=self.shift,
                connection=self.passenger,
                role=ProjectCrewShiftMember.ROLE_DRIVER,
                vehicle=self.vehicle,
            ).exists()
        )
        self.assertTrue(
            ProjectCrewShiftMember.objects.filter(
                shift=self.shift,
                connection=self.driver,
                role=ProjectCrewShiftMember.ROLE_PASSENGER,
            ).exists()
        )

        workspace = self.client.get(self.workspace_url)
        self.assertEqual(workspace.status_code, 200, workspace.data)
        crew = workspace.data["crews"][0]
        self.assertEqual(
            crew["current_resource"]["driver"]["id"],
            str(self.passenger.public_id),
        )
        self.assertEqual(
            crew["resources"][0]["driver"]["id"],
            str(self.passenger.public_id),
        )
        self.assertEqual(
            [item["worker"]["id"] for item in crew["default_passengers"]],
            [str(self.driver.public_id)],
        )

    def test_driver_replace_is_idempotent_and_rejects_reused_key(self):
        self.passenger.has_driving_license = True
        self.passenger.save(update_fields=("has_driving_license", "updated_at"))
        payload = {
            "new_driver_connection_id": str(self.passenger.public_id),
            "effective_on": "2026-08-18",
        }
        headers = {
            "HTTP_IDEMPOTENCY_KEY": "7762eb88-775d-4613-a17e-7fab494b4759"
        }

        first = self.client.post(self.driver_replace_url, payload, format="json", **headers)
        second = self.client.post(self.driver_replace_url, payload, format="json", **headers)
        reused = self.client.post(
            self.driver_replace_url,
            {**payload, "effective_on": "2026-08-19"},
            format="json",
            **headers,
        )

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data["replacement"], second.data["replacement"])
        self.assertEqual(reused.status_code, 400, reused.data)
        self.assertEqual(
            reused.data["code"],
            "driver_replacement_idempotency_key_reused",
        )
        self.assertEqual(
            AuditEvent.objects.filter(
                organization=self.organization,
                action="project_crew.driver_replaced",
                request_id=headers["HTTP_IDEMPOTENCY_KEY"],
            ).count(),
            1,
        )

    def test_driver_replace_requires_key_and_strict_payload(self):
        self.passenger.has_driving_license = True
        self.passenger.save(update_fields=("has_driving_license", "updated_at"))
        payload = {
            "new_driver_connection_id": str(self.passenger.public_id),
            "effective_on": "2026-08-18",
        }

        missing = self.client.post(self.driver_replace_url, payload, format="json")
        invalid = self.client.post(
            self.driver_replace_url,
            payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="not-a-uuid",
        )
        unknown = self.client.post(
            self.driver_replace_url,
            {**payload, "vehicle_id": str(self.vehicle.public_id)},
            format="json",
            HTTP_IDEMPOTENCY_KEY="4381a095-b6b7-43fa-a55c-41564177e43c",
        )

        self.assertEqual(
            missing.data["code"],
            "driver_replacement_idempotency_key_required",
        )
        self.assertEqual(
            invalid.data["code"],
            "driver_replacement_idempotency_key_invalid",
        )
        self.assertEqual(unknown.data["code"], "unsupported_support_field")

    def test_driver_replace_rejects_non_passenger_and_rolls_back(self):
        outsider = self._connection(
            "driver-outsider",
            "Driver Outsider",
            has_driving_license=True,
        )

        response = self.client.post(
            self.driver_replace_url,
            {
                "new_driver_connection_id": str(outsider.public_id),
                "effective_on": "2026-08-18",
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="084c62a7-23ec-42da-8498-25785f1f9759",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "replacement_driver_not_in_crew")
        self.assertTrue(
            ProjectCrewResourceAssignment.objects.filter(
                crew=self.crew,
                driver_connection=self.driver,
                vehicle=self.vehicle,
                ends_on__isnull=True,
            ).exists()
        )
        self.assertTrue(
            ProjectCrewShiftMember.objects.filter(
                shift=self.shift,
                connection=self.driver,
                role=ProjectCrewShiftMember.ROLE_DRIVER,
            ).exists()
        )

    def test_driver_replace_conflict_rolls_back_every_change(self):
        self.passenger.has_driving_license = True
        self.passenger.save(update_fields=("has_driving_license", "updated_at"))
        other_project = WorkProject.objects.create(
            organization=self.organization,
            worksite=self.worksite,
            internal_name="Other shift project",
            worker_visible_name="Other shift project",
            starts_on=date(2026, 8, 1),
            created_by=self.owner,
        )
        other_crew = ProjectCrew.objects.create(
            organization=self.organization,
            project=other_project,
            internal_name="Other shift crew",
            created_by=self.owner,
        )
        other_vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Other Vivaro",
            registration_identifier="API-02",
            seat_capacity=9,
            created_by=self.owner,
        )
        other_shift = ProjectCrewShift.objects.create(
            crew=other_crew,
            work_date=date(2026, 8, 18),
            starts_at=self.shift.starts_at,
            ends_at=self.shift.ends_at,
            break_minutes=30,
            created_by=self.owner,
        )
        ProjectCrewShiftMember.objects.create(
            shift=other_shift,
            connection=self.passenger,
            role=ProjectCrewShiftMember.ROLE_DRIVER,
            vehicle=other_vehicle,
            created_by=self.owner,
        )

        response = self.client.post(
            self.driver_replace_url,
            {
                "new_driver_connection_id": str(self.passenger.public_id),
                "effective_on": "2026-08-18",
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="50b9533e-c2c1-4e78-8097-30bc5268f375",
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "replacement_driver_shift_conflict")
        self.assertTrue(
            ProjectCrewResourceAssignment.objects.filter(
                crew=self.crew,
                driver_connection=self.driver,
                vehicle=self.vehicle,
                ends_on__isnull=True,
            ).exists()
        )
        self.assertFalse(
            ProjectCrewResourceAssignment.objects.filter(
                crew=self.crew,
                driver_connection=self.passenger,
            ).exists()
        )

    @patch(
        "support.services.project_crews.timezone.localdate",
        return_value=date(2026, 8, 17),
    )
    def test_driver_absence_and_substitution_full_lifecycle(self, _mock_localdate):
        absence_payload = {"work_dates": ["2026-08-18"]}
        absence_response = self.client.post(
            self.driver_absence_url,
            absence_payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="7e2a71cb-f026-44c7-8da4-2c51f891d5e1",
        )

        self.assertEqual(absence_response.status_code, 200, absence_response.data)
        self.assertEqual(absence_response.data["affected_dates"], ["2026-08-18"])
        self.assertEqual(len(absence_response.data["driver_absences"]), 1)
        self.assertFalse(absence_response.data["days"][0]["shift"]["has_driver"])
        self.assertFalse(
            ProjectCrewShiftMember.objects.filter(
                shift=self.shift,
                connection=self.driver,
                role=ProjectCrewShiftMember.ROLE_DRIVER,
            ).exists()
        )

        substitution_response = self.client.post(
            self.driver_substitute_url,
            {
                "substitute_driver_connection_id": str(self.substitute.public_id),
                "work_dates": ["2026-08-18"],
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="767b7697-c255-4903-80dc-c0fc5931fd3d",
        )

        self.assertEqual(
            substitution_response.status_code,
            200,
            substitution_response.data,
        )
        self.assertEqual(len(substitution_response.data["driver_substitutions"]), 1)
        self.assertEqual(
            substitution_response.data["driver_substitutions"][0][
                "substitute_driver"
            ]["id"],
            str(self.substitute.public_id),
        )
        self.assertTrue(substitution_response.data["days"][0]["shift"]["has_driver"])

        cancel_substitution = self.client.delete(
            self.driver_substitute_url,
            absence_payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="cb9c98fd-79ee-412b-b0ab-cf4089b5bbfe",
        )
        self.assertEqual(cancel_substitution.status_code, 200, cancel_substitution.data)
        self.assertEqual(cancel_substitution.data["driver_substitutions"], [])
        self.assertEqual(len(cancel_substitution.data["driver_absences"]), 1)
        self.assertFalse(cancel_substitution.data["days"][0]["shift"]["has_driver"])

        cancel_absence = self.client.delete(
            self.driver_absence_url,
            absence_payload,
            format="json",
            HTTP_IDEMPOTENCY_KEY="39757be2-cae5-4331-b5bd-85634104227a",
        )
        self.assertEqual(cancel_absence.status_code, 200, cancel_absence.data)
        self.assertEqual(cancel_absence.data["driver_absences"], [])
        self.assertTrue(cancel_absence.data["days"][0]["shift"]["has_driver"])
        self.assertTrue(
            ProjectCrewShiftMember.objects.filter(
                shift=self.shift,
                connection=self.driver,
                role=ProjectCrewShiftMember.ROLE_DRIVER,
                vehicle=self.vehicle,
            ).exists()
        )

    @patch(
        "support.services.project_crews.timezone.localdate",
        return_value=date(2026, 8, 17),
    )
    def test_driver_substitute_candidates_are_available_and_passengers_sort_first(
        self,
        _mock_localdate,
    ):
        self.passenger.has_driving_license = True
        self.passenger.save(update_fields=("has_driving_license", "updated_at"))
        absence_response = self.client.post(
            self.driver_absence_url,
            {"work_dates": ["2026-08-18"]},
            format="json",
            HTTP_IDEMPOTENCY_KEY="bc4f6385-5773-48cc-a44b-afdaf255928d",
        )

        response = self.client.get(
            f"{self.driver_substitute_url}?work_date=2026-08-18"
        )
        invalid = self.client.get(self.driver_substitute_url)

        self.assertEqual(absence_response.status_code, 200, absence_response.data)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["work_dates"], ["2026-08-18"])
        self.assertEqual(
            [item["connection_id"] for item in response.data["results"]],
            [str(self.passenger.public_id), str(self.substitute.public_id)],
        )
        self.assertTrue(response.data["results"][0]["is_current_crew_passenger"])
        self.assertFalse(response.data["results"][1]["is_current_crew_passenger"])
        self.assertEqual(invalid.status_code, 400, invalid.data)

    def test_driver_absence_is_idempotent_and_rejects_reused_key(self):
        headers = {
            "HTTP_IDEMPOTENCY_KEY": "3d7bf0ac-868c-4355-a2c1-4885f6224b78"
        }
        payload = {"work_dates": ["2026-08-18"]}

        first = self.client.post(
            self.driver_absence_url,
            payload,
            format="json",
            **headers,
        )
        second = self.client.post(
            self.driver_absence_url,
            payload,
            format="json",
            **headers,
        )
        reused = self.client.post(
            self.driver_absence_url,
            {"work_dates": ["2026-08-19"]},
            format="json",
            **headers,
        )

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data, second.data)
        self.assertEqual(reused.status_code, 400, reused.data)
        self.assertEqual(
            reused.data["code"],
            "driver_absence_idempotency_key_reused",
        )
        self.assertEqual(
            ProjectCrewMemberAbsence.objects.filter(
                crew=self.crew,
                connection=self.driver,
                work_date=date(2026, 8, 18),
            ).count(),
            1,
        )

    @patch(
        "support.services.project_crews.timezone.localdate",
        return_value=date(2026, 8, 17),
    )
    def test_driver_substitution_is_idempotent_and_requires_absence(
        self,
        _mock_localdate,
    ):
        no_absence = self.client.post(
            self.driver_substitute_url,
            {
                "substitute_driver_connection_id": str(self.substitute.public_id),
                "work_dates": ["2026-08-18"],
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="4e074998-d1ef-4a1d-a6f3-f11361d4d607",
        )
        self.assertEqual(no_absence.status_code, 400, no_absence.data)
        self.assertEqual(
            no_absence.data["code"],
            "substitution_requires_driver_absence",
        )

        self.client.post(
            self.driver_absence_url,
            {"work_dates": ["2026-08-18"]},
            format="json",
            HTTP_IDEMPOTENCY_KEY="bd01fb02-a0d5-44f6-824a-2a91b85bbbfa",
        )
        payload = {
            "substitute_driver_connection_id": str(self.substitute.public_id),
            "work_dates": ["2026-08-18"],
        }
        headers = {
            "HTTP_IDEMPOTENCY_KEY": "ae2a06e0-4e75-4398-a5d9-357464886fa4"
        }
        first = self.client.post(
            self.driver_substitute_url,
            payload,
            format="json",
            **headers,
        )
        second = self.client.post(
            self.driver_substitute_url,
            payload,
            format="json",
            **headers,
        )
        reused = self.client.post(
            self.driver_substitute_url,
            {
                **payload,
                "substitute_driver_connection_id": str(self.passenger.public_id),
            },
            format="json",
            **headers,
        )

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(first.data, second.data)
        self.assertEqual(reused.status_code, 400, reused.data)
        self.assertEqual(
            reused.data["code"],
            "driver_substitution_idempotency_key_reused",
        )
        self.assertEqual(
            ProjectCrewDriverSubstitution.objects.filter(
                crew=self.crew,
                work_date=date(2026, 8, 18),
                state=ProjectCrewDriverSubstitution.STATE_ACTIVE,
            ).count(),
            1,
        )

    def test_driver_exception_endpoints_require_keys_and_strict_payloads(self):
        missing = self.client.post(
            self.driver_absence_url,
            {"work_dates": ["2026-08-18"]},
            format="json",
        )
        invalid = self.client.post(
            self.driver_substitute_url,
            {
                "substitute_driver_connection_id": str(self.substitute.public_id),
                "work_dates": ["2026-08-18"],
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="not-a-uuid",
        )
        unknown = self.client.post(
            self.driver_absence_url,
            {"work_dates": ["2026-08-18"], "connection_id": str(self.driver.public_id)},
            format="json",
            HTTP_IDEMPOTENCY_KEY="c1591ddb-a4c9-4d11-a403-731904779522",
        )

        self.assertEqual(
            missing.data["code"],
            "driver_absence_idempotency_key_required",
        )
        self.assertEqual(
            invalid.data["code"],
            "driver_substitution_idempotency_key_invalid",
        )
        self.assertEqual(unknown.data["code"], "unsupported_support_field")

    def test_workspace_returns_exact_month_shift_members_and_exceptions(self):
        response = self.client.get(self.workspace_url)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["month"], "2026-08")
        self.assertEqual(response.data["project"]["summary"]["crew_count"], 1)
        self.assertEqual(
            response.data["project"]["summary"]["permanent_worker_count"],
            2,
        )
        crew = response.data["crews"][0]
        self.assertEqual(crew["internal_name"], "Crew North")
        day = next(item for item in crew["calendar"] if item["date"] == "2026-08-18")
        self.assertTrue(day["shift"]["has_driver"])
        self.assertEqual(
            {item["role"] for item in day["shift"]["members"]},
            {"driver", "passenger"},
        )
        self.assertEqual(crew["absences"][0]["work_date"], "2026-08-19")
        self.assertEqual(crew["driver_substitutions"][0]["work_date"], "2026-08-21")
        self.assertEqual(
            response.data["worker_days_off"][str(self.passenger.public_id)],
            ["2026-08-20"],
        )
        self.assertNotIn(str(self.passenger.id), response.data["worker_days_off"])

    def test_invalid_month_is_explicit_validation_error(self):
        response = self.client.get(
            self.workspace_url.replace("2026-08", "2026-99")
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["month"], "invalid_month")

    @override_settings(SUPPORT_PROJECT_FIRST_ENABLED=False)
    def test_project_first_flag_hides_api(self):
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 404)

    def test_scoped_manager_cannot_read_whole_crews(self):
        manager = User.objects.create_user(
            username="project-api-manager",
            email="project-api-manager@example.com",
            password="password",
        )
        membership = OrganizationMembership.objects.create(
            organization=self.organization,
            user=manager,
            display_role="Manager",
            state=OrganizationMembership.STATE_ACTIVE,
        )
        for code in (SCHEDULE_MANAGE, TRANSPORT_MANAGE):
            PermissionGrant.objects.create(
                membership=membership,
                permission_code=code,
                scope_kind=PermissionGrant.SCOPE_ORGANIZATION,
                is_active=True,
                granted_by=self.owner,
            )
        client = APIClient()
        client.force_authenticate(manager)

        response = client.get(self.list_url)
        write_response = client.post(
            self.list_url,
            self._project_input(),
            format="json",
            HTTP_IDEMPOTENCY_KEY="eeb25526-339d-4e9d-8edf-b42cf7f4992d",
        )
        crew_write_response = client.post(
            self.crew_list_url,
            self._crew_input(self.driver, self.vehicle),
            format="json",
            HTTP_IDEMPOTENCY_KEY="0f203a45-9a62-4d7b-ae14-e767d79ea29f",
        )
        shift_write_response = client.post(
            self.shift_replace_url,
            {
                "work_dates": ["2026-08-24"],
                "starts_at_time": "06:00",
                "ends_at_time": "14:45",
                "break_minutes": 30,
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="dfdcf7fc-6271-4ac7-84a2-e5806b7adfb3",
        )
        passenger_write_response = client.post(
            self.passenger_apply_url,
            {
                "connection_id": str(self.passenger.public_id),
                "scope": "selected_dates",
                "work_dates": ["2026-08-18"],
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="34d5164c-c3cc-4906-95fc-f0fc2169ce9a",
        )
        driver_write_response = client.post(
            self.driver_replace_url,
            {
                "new_driver_connection_id": str(self.passenger.public_id),
                "effective_on": "2026-08-18",
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="35d7e709-35b5-44af-8ee7-fd3521387ddc",
        )
        absence_write_response = client.post(
            self.driver_absence_url,
            {"work_dates": ["2026-08-18"]},
            format="json",
            HTTP_IDEMPOTENCY_KEY="391bc90c-cee7-4e06-bc03-e40bd363230b",
        )
        substitute_read_response = client.get(
            f"{self.driver_substitute_url}?work_date=2026-08-18"
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(write_response.status_code, 404)
        self.assertEqual(crew_write_response.status_code, 404)
        self.assertEqual(shift_write_response.status_code, 404)
        self.assertEqual(passenger_write_response.status_code, 404)
        self.assertEqual(driver_write_response.status_code, 404)
        self.assertEqual(absence_write_response.status_code, 404)
        self.assertEqual(substitute_read_response.status_code, 404)

    def test_cross_organization_project_id_is_not_disclosed(self):
        other_owner = User.objects.create_user(
            username="project-api-other-owner",
            email="project-api-other-owner@example.com",
            password="password",
        )
        other_organization, _ = create_organization(
            jobhub_operator=self.operator,
            legal_name="Other Project API Agency sp. z o.o.",
            display_name="Other Project API Agency",
            owner_email=other_owner.email,
        )
        other_worksite = Worksite.objects.create(
            organization=other_organization,
            internal_name="Other site",
            country_code="NL",
            city="Almere",
            street="Otherstraat",
            building="1",
            created_by=other_owner,
        )
        other_project = WorkProject.objects.create(
            organization=other_organization,
            worksite=other_worksite,
            internal_name="Other project",
            worker_visible_name="Other project",
            starts_on=date(2026, 8, 1),
            created_by=other_owner,
        )
        other_crew = ProjectCrew.objects.create(
            organization=other_organization,
            project=other_project,
            internal_name="Other crew",
            created_by=other_owner,
        )
        response = self.client.get(
            f"/api/v2/support/organizations/{other_organization.public_id}/"
            f"project-first/projects/{self.project.public_id}/workspace/?month=2026-08"
        )
        write_response = self.client.patch(
            f"{self.list_url}{other_project.public_id}/",
            {"name": "Probe"},
            format="json",
        )
        crew_write_response = self.client.patch(
            f"/api/v2/support/organizations/{self.organization.public_id}/"
            f"project-first/crews/{other_crew.public_id}/",
            {"internal_name": "Probe crew"},
            format="json",
        )
        shift_write_response = self.client.post(
            f"/api/v2/support/organizations/{self.organization.public_id}/"
            f"project-first/crews/{other_crew.public_id}/shifts/replace/",
            {
                "work_dates": ["2026-08-24"],
                "starts_at_time": "06:00",
                "ends_at_time": "14:45",
                "break_minutes": 30,
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="5e53ea33-5f80-44b7-b765-737f98f7c1ac",
        )
        passenger_write_response = self.client.post(
            f"/api/v2/support/organizations/{self.organization.public_id}/"
            f"project-first/crews/{other_crew.public_id}/passengers/apply/",
            {
                "connection_id": str(self.passenger.public_id),
                "scope": "selected_dates",
                "work_dates": ["2026-08-24"],
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="d9bbc579-2890-4219-bd33-35329eb217bf",
        )
        driver_write_response = self.client.post(
            f"/api/v2/support/organizations/{self.organization.public_id}/"
            f"project-first/crews/{other_crew.public_id}/driver/replace/",
            {
                "new_driver_connection_id": str(self.passenger.public_id),
                "effective_on": "2026-08-24",
            },
            format="json",
            HTTP_IDEMPOTENCY_KEY="409ddc60-a256-44ea-bd5f-cd73c6639693",
        )
        absence_write_response = self.client.post(
            f"/api/v2/support/organizations/{self.organization.public_id}/"
            f"project-first/crews/{other_crew.public_id}/driver/absence/",
            {"work_dates": ["2026-08-24"]},
            format="json",
            HTTP_IDEMPOTENCY_KEY="970d7d91-d2a1-4d5a-8622-d1185de470da",
        )
        substitute_read_response = self.client.get(
            f"/api/v2/support/organizations/{self.organization.public_id}/"
            f"project-first/crews/{other_crew.public_id}/driver/substitute/"
            "?work_date=2026-08-24"
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(write_response.status_code, 404)
        self.assertEqual(crew_write_response.status_code, 404)
        self.assertEqual(shift_write_response.status_code, 404)
        self.assertEqual(passenger_write_response.status_code, 404)
        self.assertEqual(driver_write_response.status_code, 404)
        self.assertEqual(absence_write_response.status_code, 404)
        self.assertEqual(substitute_read_response.status_code, 404)
