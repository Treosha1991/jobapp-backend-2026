from datetime import date, datetime, timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from support.models import (
    HousingAssignment,
    HousingPlace,
    HousingRoom,
    HousingSite,
    MembershipInvitation,
    DriverVehicleAssignment,
    NotificationOutbox,
    OrganizationMembership,
    ProjectScheduleTemplate,
    RouteStop,
    ScheduledWorkShift,
    SupportApplication,
    SupportConnection,
    SupportConversation,
    SupportConversationMember,
    SupportMessage,
    SupportVacancy,
    TransportRoute,
    TransportPassengerAssignment,
    Vehicle,
    WorkerProjectAssignment,
    WorkProject,
    Worksite,
)
from support.services.organizations import activate_organization, create_organization


@override_settings(SUPPORT_FEATURE_ENABLED=True)
class SupportWorkspaceWebTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(
            username="workspace-operator",
            email="workspace-operator@example.com",
            password="password",
            is_staff=True,
        )
        self.owner = User.objects.create_user(
            username="workspace-owner",
            first_name="Olena",
            last_name="Owner",
            email="workspace-owner@example.com",
            password="password",
        )
        self.candidate = User.objects.create_user(
            username="workspace-candidate",
            first_name="Andrei",
            last_name="Worker",
            email="workspace-candidate@example.com",
            password="password",
        )
        self.limited_member = User.objects.create_user(
            username="workspace-limited",
            email="workspace-limited@example.com",
            password="password",
        )
        self.outsider = User.objects.create_user(
            username="workspace-outsider",
            email="workspace-outsider@example.com",
            password="password",
        )
        self.organization, _ = create_organization(
            jobhub_operator=self.operator,
            legal_name="Workspace Agency sp. z o.o.",
            display_name="Workspace Agency",
            owner_email=self.owner.email,
        )
        activate_organization(jobhub_operator=self.operator, organization=self.organization)
        OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.limited_member,
            display_role="Limited",
            created_by=self.owner,
            accepted_at=timezone.now(),
        )
        self._create_candidate_records()

    def _create_candidate_records(self):
        vacancy = SupportVacancy.objects.create(
            organization=self.organization,
            internal_title="Packing helper",
            created_by=self.owner,
        )
        SupportApplication.objects.create(
            vacancy=vacancy,
            candidate=self.candidate,
            revision=1,
            preferred_language="ru",
            citizenship_country_code="BY",
            current_country_code="PL",
            consent_version="support-application-v1",
            consented_at=timezone.now(),
        )
        working_application = SupportApplication.objects.create(
            vacancy=vacancy,
            candidate=User.objects.create_user(
                username="workspace-active-worker",
                email="workspace-active-worker@example.com",
                password="password",
            ),
            revision=1,
            preferred_language="ru",
            consent_version="support-application-v1",
            consented_at=timezone.now(),
            status=SupportApplication.STATUS_APPROVED,
        )
        self.worker_connection = SupportConnection.objects.create(
            organization=self.organization,
            vacancy=vacancy,
            application=working_application,
            candidate=working_application.candidate,
            stage=SupportConnection.STAGE_COORDINATOR,
        )

    def test_owner_can_invite_registered_staff_from_team_screen(self):
        invited_staff = User.objects.create_user(
            username="workspace-invited-staff",
            email="workspace-invited-staff@example.com",
            password="password",
        )
        team_url = f"/employer/support/team/?organization={self.organization.public_id}"
        self.client.force_login(self.owner)

        page = self.client.get(team_url)

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Invite a staff member")
        self.assertContains(page, 'name="permission_groups"')

        response = self.client.post(
            team_url,
            {
                "action": "member_invite",
                "invited_email": invited_staff.email,
                "display_role": "Transport coordinator",
                "permission_groups": ["chats", "transport"],
            },
        )

        self.assertRedirects(response, team_url)
        invitation = MembershipInvitation.objects.get(
            organization=self.organization,
            invited_user=invited_staff,
        )
        self.assertEqual(invitation.display_role, "Transport coordinator")
        self.assertEqual(invitation.state, MembershipInvitation.STATUS_PENDING)
        self.assertEqual(
            set(invitation.permission_grants.values_list("permission_code", flat=True)),
            {"chat.manage", "transport.manage"},
        )

        pending_page = self.client.get(team_url)
        self.assertContains(pending_page, invited_staff.email)
        self.assertContains(pending_page, "Waiting for confirmation")

    def test_owner_sees_only_approved_workspace_information_and_navigation_link(self):
        self.client.force_login(self.owner)

        response = self.client.get("/employer/support/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Workspace Agency")
        self.assertContains(response, "Andrei Worker")
        self.assertContains(response, "Packing helper")
        self.assertContains(response, "JobHub Support")
        self.assertNotContains(response, "Blauwe Slank")
        self.assertContains(
            response,
            f"/employer/support/workers/{self.worker_connection.public_id}/",
        )

    def test_support_member_opens_support_by_default_but_can_open_vacancies_from_menu(self):
        self.client.force_login(self.owner)

        default_response = self.client.get("/employer/")

        self.assertRedirects(
            default_response,
            f"/employer/support/?organization={self.organization.public_id}",
        )
        vacancies_response = self.client.get("/employer/?view=vacancies")
        self.assertEqual(vacancies_response.status_code, 200)
        self.assertContains(vacancies_response, "Menu")

    def test_owner_sees_support_first_header_and_only_assigned_staff_chats(self):
        membership = OrganizationMembership.objects.get(
            organization=self.organization,
            user=self.owner,
        )
        conversation = SupportConversation.objects.create(
            organization=self.organization,
            kind=SupportConversation.KIND_GROUP,
            title="Transport coordinators",
            created_by=self.owner,
        )
        SupportConversationMember.objects.create(
            conversation=conversation,
            user=self.owner,
            organization_membership=membership,
            role=SupportConversationMember.ROLE_STAFF,
        )
        self.client.force_login(self.owner)
        workspace_url = f"/employer/support/?organization={self.organization.public_id}"

        workspace = self.client.get(workspace_url)
        self.assertEqual(workspace.status_code, 200)
        self.assertContains(workspace, "Workers")
        self.assertContains(workspace, "Requests")
        self.assertContains(workspace, "Registry")
        self.assertContains(workspace, "Staff chat")
        self.assertContains(workspace, "General chat")
        self.assertContains(workspace, "Menu")

        conversations_url = (
            f"/employer/support/conversations/?organization={self.organization.public_id}"
        )
        conversations = self.client.get(conversations_url)
        self.assertEqual(conversations.status_code, 200)
        self.assertContains(conversations, "Transport coordinators")

        detail_url = (
            f"/employer/support/conversations/{conversation.public_id}/"
            f"?organization={self.organization.public_id}"
        )
        sent = self.client.post(detail_url, {"body": "Driver route is ready."})
        self.assertRedirects(sent, detail_url)
        self.assertTrue(
            SupportMessage.objects.filter(
                conversation=conversation,
                body="Driver route is ready.",
                sender=self.owner,
            ).exists()
        )

    def test_limited_member_gets_workspace_but_no_candidate_or_worker_data(self):
        self.client.force_login(self.limited_member)

        response = self.client.get("/employer/support/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Andrei Worker")
        self.assertNotContains(response, "Packing helper")
        self.assertContains(response, "There are no operational sections available for your role.")
        blocked_card = self.client.get(
            f"/employer/support/workers/{self.worker_connection.public_id}/"
        )
        self.assertEqual(blocked_card.status_code, 404)
        blocked_registry = self.client.get("/employer/support/registries/")
        self.assertEqual(blocked_registry.status_code, 404)

    def test_outsider_cannot_open_workspace(self):
        self.client.force_login(self.outsider)

        response = self.client.get("/employer/support/")

        self.assertEqual(response.status_code, 404)

    def test_owner_switches_organization_without_receiving_other_company_data(self):
        other_organization, _ = create_organization(
            jobhub_operator=self.operator,
            legal_name="Other Workspace Agency sp. z o.o.",
            display_name="Other Workspace Agency",
            owner_email=self.owner.email,
        )
        activate_organization(jobhub_operator=self.operator, organization=other_organization)
        other_candidate = User.objects.create_user(
            username="workspace-other-candidate",
            first_name="Other",
            last_name="Candidate",
            email="workspace-other-candidate@example.com",
            password="password",
        )
        other_vacancy = SupportVacancy.objects.create(
            organization=other_organization,
            internal_title="Other vacancy",
            created_by=self.owner,
        )
        SupportApplication.objects.create(
            vacancy=other_vacancy,
            candidate=other_candidate,
            revision=1,
            preferred_language="ru",
            consent_version="support-application-v1",
            consented_at=timezone.now(),
        )
        self.client.force_login(self.owner)

        main_response = self.client.get(
            f"/employer/support/?organization={self.organization.public_id}"
        )
        other_response = self.client.get(
            f"/employer/support/?organization={other_organization.public_id}"
        )

        self.assertEqual(main_response.status_code, 200)
        self.assertContains(main_response, "Andrei Worker")
        self.assertNotContains(main_response, "Other Candidate")
        self.assertEqual(other_response.status_code, 200)
        self.assertContains(other_response, "Other Candidate")
        self.assertNotContains(other_response, "Andrei Worker")

    def test_owner_creates_then_publishes_housing_draft_from_worker_card(self):
        site = HousingSite.objects.create(
            organization=self.organization,
            internal_name="Lelystad home",
            country_code="NL",
            city="Lelystad",
            postal_code="8223XP",
            street="Blauwe Slank",
            building="31B",
            created_by=self.owner,
        )
        room = HousingRoom.objects.create(site=site, label="Room 3", capacity=1)
        place = HousingPlace.objects.create(room=room, label="Bed 1")
        self.client.force_login(self.owner)
        card_url = f"/employer/support/workers/{self.worker_connection.public_id}/?tab=housing"

        card = self.client.get(card_url)
        self.assertEqual(card.status_code, 200)
        self.assertContains(card, "Lelystad home")
        self.assertContains(card, "Blauwe Slank")
        self.assertContains(card, 'name="check_in_on" type="date"')
        self.assertNotContains(card, 'name="check_out_at"')

        drafted = self.client.post(
            card_url,
            {
                "action": "housing_draft",
                "place_id": str(place.public_id),
                "check_in_on": "2026-09-01",
            },
            follow=True,
        )
        self.assertEqual(drafted.status_code, 200)
        assignment = HousingAssignment.objects.get(connection=self.worker_connection)
        self.assertEqual(assignment.state, HousingAssignment.STATE_DRAFT)
        self.assertEqual(assignment.check_in_at.date(), date(2026, 9, 1))
        self.assertFalse(
            NotificationOutbox.objects.filter(
                notification_code="housing.assignment_published"
            ).exists()
        )
        self.assertContains(drafted, "The draft was saved. The worker cannot see it yet.")

        edited = self.client.post(
            card_url,
            {
                "action": "housing_draft_edit",
                "assignment_id": str(assignment.public_id),
                "check_in_on": "2026-09-02",
                "return_tab": "housing",
                "return_site": str(site.public_id),
            },
            follow=True,
        )
        self.assertEqual(edited.status_code, 200)
        assignment.refresh_from_db()
        self.assertEqual(assignment.check_in_at.date(), date(2026, 9, 2))
        self.assertIsNone(assignment.check_out_at)

        published = self.client.post(
            card_url,
            {
                "action": "publish_housing",
                "assignment_id": str(assignment.public_id),
                "return_tab": "housing",
                "return_site": str(site.public_id),
            },
            follow=True,
        )
        self.assertEqual(published.status_code, 200)
        assignment.refresh_from_db()
        self.assertEqual(assignment.state, HousingAssignment.STATE_PUBLISHED)
        self.assertTrue(
            NotificationOutbox.objects.filter(
                notification_code="housing.assignment_published",
                recipient=self.worker_connection.candidate,
            ).exists()
        )
        self.assertContains(published, "The assignment was published. The worker will receive a notification.")
        self.assertContains(published, "Set check-out date")

        checked_out = self.client.post(
            card_url,
            {
                "action": "housing_check_out",
                "assignment_id": str(assignment.public_id),
                "check_out_on": "2026-09-10",
                "return_tab": "housing",
                "return_site": str(site.public_id),
            },
            follow=True,
        )
        self.assertEqual(checked_out.status_code, 200)
        assignment.refresh_from_db()
        self.assertEqual(assignment.check_out_at.date(), date(2026, 9, 10))
        self.assertEqual(
            NotificationOutbox.objects.filter(
                notification_code="housing.assignment_published",
                recipient=self.worker_connection.candidate,
            ).count(),
            2,
        )
        self.assertContains(checked_out, "The check-out date was saved.")

        cancelled = self.client.post(
            card_url,
            {"action": "cancel_housing", "assignment_id": str(assignment.public_id)},
            follow=True,
        )
        self.assertEqual(cancelled.status_code, 200)
        assignment.refresh_from_db()
        self.assertEqual(assignment.state, HousingAssignment.STATE_CANCELLED)
        self.assertContains(cancelled, "The assignment was cancelled. Its history was kept;")

    def test_owner_can_open_fleet_and_see_vehicle_assignment_history(self):
        vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Fleet test car",
            registration_identifier="FLEET-123",
            seat_capacity=4,
            created_by=self.owner,
        )
        DriverVehicleAssignment.objects.create(
            organization=self.organization,
            vehicle=vehicle,
            driver_connection=self.worker_connection,
            starts_on=date.today() - timedelta(days=1),
            state=DriverVehicleAssignment.STATE_PUBLISHED,
            created_by=self.owner,
        )
        self.client.force_login(self.owner)

        response = self.client.get(
            f"/employer/support/fleet/?organization={self.organization.public_id}&vehicle={vehicle.public_id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fleet test car")
        self.assertContains(response, "FLEET-123")
        self.assertContains(response, "workspace-active-worker")

    def test_fleet_shows_project_address_and_modal_vehicle_actions(self):
        vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Project crew car",
            registration_identifier="PROJECT-123",
            seat_capacity=4,
            created_by=self.owner,
        )
        driver_assignment = DriverVehicleAssignment.objects.create(
            organization=self.organization,
            vehicle=vehicle,
            driver_connection=self.worker_connection,
            starts_on=date.today() - timedelta(days=1),
            state=DriverVehicleAssignment.STATE_PUBLISHED,
            created_by=self.owner,
        )
        worksite = Worksite.objects.create(
            organization=self.organization,
            internal_name="Hidden worksite name",
            country_code="NL",
            city="Lelystad",
            postal_code="8223 XP",
            street="Zandbang",
            building="22",
            created_by=self.owner,
        )
        project = WorkProject.objects.create(
            organization=self.organization,
            worksite=worksite,
            internal_name="Internal project name",
            worker_visible_name="Apple Project BV",
            starts_on=date.today(),
            created_by=self.owner,
        )
        template = ProjectScheduleTemplate.objects.create(
            project=project,
            name="Morning",
            starts_at_time=datetime.strptime("06:00", "%H:%M").time(),
            ends_at_time=datetime.strptime("14:00", "%H:%M").time(),
            created_by=self.owner,
        )
        TransportRoute.objects.create(
            organization=self.organization,
            internal_name="Crew encrypted internal identifier",
            worksite=worksite,
            schedule_template=template,
            driver_vehicle_assignment=driver_assignment,
            starts_on=date.today() - timedelta(days=1),
            state=TransportRoute.STATE_PUBLISHED,
            created_by=self.owner,
        )
        self.client.force_login(self.owner)

        response = self.client.get(
            f"/employer/support/fleet/?organization={self.organization.public_id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Apple Project BV")
        self.assertContains(response, "Lelystad, Zandbang 22")
        self.assertNotContains(response, "Crew encrypted internal identifier")
        self.assertContains(
            response,
            f'data-fleet-dialog-target="fleet-edit-{vehicle.public_id}"',
        )
        self.assertContains(
            response,
            f'data-fleet-dialog-target="fleet-history-{vehicle.public_id}"',
        )
        self.assertNotContains(response, "fleet-selected")

    def test_owner_can_add_vehicle_from_fleet_workspace(self):
        self.client.force_login(self.owner)
        fleet_url = (
            f"/employer/support/fleet/?organization={self.organization.public_id}"
        )

        page = self.client.get(fleet_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Add vehicle")
        self.assertContains(page, 'id="fleet-add-vehicle-modal"')

        created = self.client.post(
            fleet_url,
            {
                "action": "vehicle_create",
                "internal_name": "Crew van",
                "registration_identifier": "NL-CREW-10",
                "seat_capacity": "9",
            },
            follow=True,
        )

        self.assertEqual(created.status_code, 200)
        self.assertContains(created, "Crew van")
        self.assertContains(created, "NL-CREW-10")
        vehicle = Vehicle.objects.get(
            organization=self.organization,
            registration_identifier="NL-CREW-10",
        )
        self.assertEqual(vehicle.seat_capacity, 9)

    def test_fleet_marks_driverless_vehicle_without_counting_previous_drivers_crew(self):
        vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Driverless crew car",
            registration_identifier="FLEET-NO-DRIVER",
            seat_capacity=4,
            created_by=self.owner,
        )
        assignment = DriverVehicleAssignment.objects.create(
            organization=self.organization,
            vehicle=vehicle,
            driver_connection=self.worker_connection,
            starts_on=date.today() - timedelta(days=1),
            state=DriverVehicleAssignment.STATE_CANCELLED,
            cancelled_at=timezone.now(),
            created_by=self.owner,
        )
        route = TransportRoute.objects.create(
            organization=self.organization,
            internal_name="Crew route without driver",
            driver_vehicle_assignment=assignment,
            starts_on=date.today() - timedelta(days=1),
            state=TransportRoute.STATE_PUBLISHED,
            created_by=self.owner,
        )
        pickup = RouteStop.objects.create(
            route=route,
            sequence=1,
            kind=RouteStop.KIND_PICKUP,
            label="Housing",
        )
        dropoff = RouteStop.objects.create(
            route=route,
            sequence=2,
            kind=RouteStop.KIND_DROPOFF,
            label="Project",
        )
        passenger_user = User.objects.create_user(
            username="driverless-crew-passenger",
            email="driverless-crew-passenger@example.com",
            password="password",
        )
        passenger_application = SupportApplication.objects.create(
            vacancy=self.worker_connection.vacancy,
            candidate=passenger_user,
            revision=1,
            preferred_language="ru",
            consent_version="support-application-v1",
            consented_at=timezone.now(),
            status=SupportApplication.STATUS_APPROVED,
        )
        passenger_connection = SupportConnection.objects.create(
            organization=self.organization,
            vacancy=self.worker_connection.vacancy,
            application=passenger_application,
            candidate=passenger_user,
            stage=SupportConnection.STAGE_COORDINATOR,
        )
        TransportPassengerAssignment.objects.create(
            route=route,
            connection=passenger_connection,
            pickup_stop=pickup,
            dropoff_stop=dropoff,
        )
        self.client.force_login(self.owner)

        response = self.client.get(
            f"/employer/support/fleet/?organization={self.organization.public_id}&vehicle={vehicle.public_id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Driver absent")
        self.assertContains(response, "Crew route without driver")
        self.assertContains(response, "4/4")

    def test_fleet_lists_passengers_to_exclude_when_driver_moves_to_smaller_car(self):
        starts_on = date.today() - timedelta(days=1)
        source_vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Large crew car",
            registration_identifier="FLEET-LARGE",
            seat_capacity=4,
            created_by=self.owner,
        )
        target_vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Small crew car",
            registration_identifier="FLEET-SMALL",
            seat_capacity=2,
            created_by=self.owner,
        )
        source_assignment = DriverVehicleAssignment.objects.create(
            organization=self.organization,
            vehicle=source_vehicle,
            driver_connection=self.worker_connection,
            starts_on=starts_on,
            state=DriverVehicleAssignment.STATE_PUBLISHED,
            created_by=self.owner,
        )
        DriverVehicleAssignment.objects.create(
            organization=self.organization,
            vehicle=target_vehicle,
            driver_connection=self.worker_connection,
            starts_on=date.today(),
            state=DriverVehicleAssignment.STATE_DRAFT,
            created_by=self.owner,
        )
        route = TransportRoute.objects.create(
            organization=self.organization,
            internal_name="Crew follows driver",
            driver_vehicle_assignment=source_assignment,
            starts_on=starts_on,
            state=TransportRoute.STATE_PUBLISHED,
            created_by=self.owner,
        )
        pickup = RouteStop.objects.create(
            route=route,
            sequence=1,
            kind=RouteStop.KIND_PICKUP,
            label="Housing",
        )
        dropoff = RouteStop.objects.create(
            route=route,
            sequence=2,
            kind=RouteStop.KIND_DROPOFF,
            label="Project",
        )
        for index, name in enumerate(("Anna Passenger", "Boris Passenger"), start=1):
            first_name, last_name = name.split()
            passenger_user = User.objects.create_user(
                username=f"fleet-capacity-passenger-{index}",
                first_name=first_name,
                last_name=last_name,
                email=f"fleet-capacity-passenger-{index}@example.com",
                password="password",
            )
            passenger_application = SupportApplication.objects.create(
                vacancy=self.worker_connection.vacancy,
                candidate=passenger_user,
                revision=1,
                preferred_language="ru",
                consent_version="support-application-v1",
                consented_at=timezone.now(),
                status=SupportApplication.STATUS_APPROVED,
            )
            passenger_connection = SupportConnection.objects.create(
                organization=self.organization,
                vacancy=self.worker_connection.vacancy,
                application=passenger_application,
                candidate=passenger_user,
                stage=SupportConnection.STAGE_COORDINATOR,
            )
            TransportPassengerAssignment.objects.create(
                route=route,
                connection=passenger_connection,
                pickup_stop=pickup,
                dropoff_stop=dropoff,
                boarding_order=index,
            )
        self.client.force_login(self.owner)

        response = self.client.get(
            f"/employer/support/fleet/?organization={self.organization.public_id}&vehicle={target_vehicle.public_id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The new vehicle does not have enough seats")
        self.assertContains(response, "Anna Passenger")
        self.assertContains(response, "Boris Passenger")
        self.assertContains(response, "Exclude selected and publish")

    def test_transport_manager_can_toggle_worker_driving_license_mark(self):
        self.client.force_login(self.owner)
        url = f"/employer/support/workers/{self.worker_connection.public_id}/?tab=transport"

        enabled = self.client.post(
            url,
            {"action": "driving_license_set", "has_driving_license": "1", "return_tab": "transport"},
            follow=True,
        )
        self.assertEqual(enabled.status_code, 200)
        self.worker_connection.refresh_from_db()
        self.assertTrue(self.worker_connection.has_driving_license)
        self.assertContains(enabled, "Driving licence")

        disabled = self.client.post(
            url,
            {"action": "driving_license_set", "return_tab": "transport"},
            follow=True,
        )
        self.assertEqual(disabled.status_code, 200)
        self.worker_connection.refresh_from_db()
        self.assertFalse(self.worker_connection.has_driving_license)

    def test_housing_layout_marks_another_workers_draft_without_marking_it_occupied(self):
        site = HousingSite.objects.create(
            organization=self.organization,
            internal_name="Draft layout home",
            country_code="NL",
            city="Lelystad",
            street="Draft street",
            building="1",
            created_by=self.owner,
        )
        room = HousingRoom.objects.create(site=site, label="Room 2", capacity=1)
        place = HousingPlace.objects.create(room=room, label="1")
        other_candidate = User.objects.create_user(
            username="workspace-draft-resident",
            first_name="Victoria",
            last_name="Draft",
            email="workspace-draft-resident@example.com",
            password="password",
        )
        other_application = SupportApplication.objects.create(
            vacancy=self.worker_connection.vacancy,
            candidate=other_candidate,
            revision=1,
            preferred_language="ru",
            citizenship_country_code="BY",
            current_country_code="PL",
            consent_version="support-application-v1",
            consented_at=timezone.now(),
            status=SupportApplication.STATUS_APPROVED,
        )
        other_connection = SupportConnection.objects.create(
            organization=self.organization,
            vacancy=self.worker_connection.vacancy,
            application=other_application,
            candidate=other_candidate,
            stage=SupportConnection.STAGE_COORDINATOR,
        )
        HousingAssignment.objects.create(
            organization=self.organization,
            connection=other_connection,
            place=place,
            check_in_at=timezone.now() - timedelta(hours=1),
            state=HousingAssignment.STATE_DRAFT,
            created_by=self.owner,
        )
        self.client.force_login(self.owner)

        response = self.client.get(
            f"/employer/support/workers/{self.worker_connection.public_id}/?tab=housing&site={site.public_id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Draft · Victoria Draft")
        published = HousingAssignment.objects.create(
            organization=self.organization,
            connection=self.worker_connection,
            place=place,
            check_in_at=timezone.now() + timedelta(days=1),
            state=HousingAssignment.STATE_PUBLISHED,
            created_by=self.owner,
        )
        response = self.client.get(
            f"/employer/support/workers/{self.worker_connection.public_id}/?tab=housing&site={site.public_id}"
        )
        self.assertContains(response, "Occupied · workspace-active-worker · Selected worker")
        self.assertContains(response, "Draft · Victoria Draft")
        self.assertEqual(published.state, HousingAssignment.STATE_PUBLISHED)

    def test_owner_creates_work_draft_from_worker_card(self):
        worksite = Worksite.objects.create(
            organization=self.organization,
            internal_name="Flevosap site",
            country_code="NL",
            city="Biddinghuizen",
            street="Zuurlaan",
            building="22",
            created_by=self.owner,
        )
        project = WorkProject.objects.create(
            organization=self.organization,
            worksite=worksite,
            internal_name="Flevosap line A",
            worker_visible_name="Flevosap",
            created_by=self.owner,
        )
        self.client.force_login(self.owner)
        card_url = f"/employer/support/workers/{self.worker_connection.public_id}/"

        response = self.client.post(
            card_url,
            {
                "action": "work_draft",
                "project_id": str(project.public_id),
                "worker_role": "Operator",
                "starts_at": "2026-09-01T06:00",
                "ends_at": "",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        assignment = WorkerProjectAssignment.objects.get(connection=self.worker_connection)
        self.assertEqual(assignment.state, WorkerProjectAssignment.STATE_DRAFT)
        self.assertContains(response, "The draft was saved. The worker cannot see it yet.")

    def test_owner_sees_exact_error_for_overlapping_work_publication(self):
        worksite = Worksite.objects.create(
            organization=self.organization,
            internal_name="First site",
            country_code="NL",
            city="Lelystad",
            street="Main street",
            building="1",
            created_by=self.owner,
        )
        first_project = WorkProject.objects.create(
            organization=self.organization,
            worksite=worksite,
            internal_name="First project",
            worker_visible_name="First project",
            created_by=self.owner,
        )
        later = timezone.now() + timedelta(days=2)
        WorkerProjectAssignment.objects.create(
            organization=self.organization,
            connection=self.worker_connection,
            project=first_project,
            starts_at=later,
            ends_at=later + timedelta(hours=8),
            state=WorkerProjectAssignment.STATE_PUBLISHED,
            created_by=self.owner,
        )
        conflicting_project = WorkProject.objects.create(
            organization=self.organization,
            worksite=Worksite.objects.create(
                organization=self.organization,
                internal_name="Second site",
                country_code="NL",
                city="Lelystad",
                street="Other street",
                building="2",
                created_by=self.owner,
            ),
            internal_name="Second project",
            worker_visible_name="Second project",
            created_by=self.owner,
        )
        draft = WorkerProjectAssignment.objects.create(
            organization=self.organization,
            connection=self.worker_connection,
            project=conflicting_project,
            starts_at=later - timedelta(days=1),
            state=WorkerProjectAssignment.STATE_DRAFT,
            created_by=self.owner,
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            f"/employer/support/workers/{self.worker_connection.public_id}/",
            {
                "action": "publish_work",
                "assignment_id": str(draft.public_id),
                "return_tab": "company",
            },
            follow=True,
        )

        self.assertContains(response, "Cannot publish: the worker already has a published assignment")
        draft.refresh_from_db()
        self.assertEqual(draft.state, WorkerProjectAssignment.STATE_DRAFT)

    def test_quick_shift_lists_an_unassigned_project_and_assigns_it_on_publish(self):
        self.client.force_login(self.owner)
        worksite = Worksite.objects.create(
            organization=self.organization,
            internal_name="New packing site",
            country_code="NL",
            city="Lelystad",
            street="Korenstraat",
            building="14",
            created_by=self.owner,
        )
        project = WorkProject.objects.create(
            organization=self.organization,
            worksite=worksite,
            internal_name="New packing project",
            worker_visible_name="New packing project",
            worker_capacity=8,
            starts_on=timezone.localdate(),
            created_by=self.owner,
        )
        template = ProjectScheduleTemplate.objects.create(
            project=project,
            name="Late packing",
            starts_at_time="14:00",
            ends_at_time="22:00",
            created_by=self.owner,
        )
        worker_url = f"/employer/support/workers/{self.worker_connection.public_id}/"

        page = self.client.get(worker_url)
        self.assertContains(
            page,
            f'<option value="{project.public_id}">{project.worker_visible_name}</option>',
        )

        response = self.client.post(
            worker_url,
            {
                "action": "scheduled_shift_from_template",
                "work_date": timezone.localdate().isoformat(),
                "project_id": str(project.public_id),
                "schedule_template_id": str(template.public_id),
                "return_tab": "company",
                "return_month": timezone.localdate().strftime("%Y-%m"),
            },
            follow=True,
        )
        assignment = WorkerProjectAssignment.objects.get(
            connection=self.worker_connection,
            project=project,
        )
        self.assertEqual(assignment.state, WorkerProjectAssignment.STATE_PUBLISHED)
        self.assertTrue(
            ScheduledWorkShift.objects.filter(
                connection=self.worker_connection,
                work_assignment=assignment,
                state=ScheduledWorkShift.STATE_PUBLISHED,
            ).exists()
        )
        self.assertContains(
            response,
            "The shift was added and published immediately from the selected template.",
        )

    def test_quick_shift_replaces_a_current_shift_with_another_project(self):
        """A one-day move may change the project without creating a second shift."""

        self.client.force_login(self.owner)
        work_date = timezone.localdate()
        first_worksite = Worksite.objects.create(
            organization=self.organization,
            internal_name="Existing packing site",
            country_code="NL",
            city="Lelystad",
            street="Korenstraat",
            building="15",
            created_by=self.owner,
        )
        first_project = WorkProject.objects.create(
            organization=self.organization,
            worksite=first_worksite,
            internal_name="Existing packing project",
            worker_visible_name="Existing packing project",
            worker_capacity=8,
            starts_on=work_date,
            created_by=self.owner,
        )
        first_assignment = WorkerProjectAssignment.objects.create(
            organization=self.organization,
            connection=self.worker_connection,
            project=first_project,
            starts_at=timezone.make_aware(datetime.combine(work_date, datetime.min.time())),
            state=WorkerProjectAssignment.STATE_PUBLISHED,
            created_by=self.owner,
            published_by=self.owner,
            published_at=timezone.now(),
        )
        old_shift = ScheduledWorkShift.objects.create(
            organization=self.organization,
            connection=self.worker_connection,
            work_assignment=first_assignment,
            work_date=work_date,
            starts_at=timezone.make_aware(datetime.combine(work_date, datetime.min.time())),
            ends_at=timezone.make_aware(datetime.combine(work_date, datetime.min.time()))
            + timedelta(hours=8),
            break_minutes=30,
            state=ScheduledWorkShift.STATE_PUBLISHED,
            created_by=self.owner,
            published_by=self.owner,
            published_at=timezone.now(),
        )
        second_worksite = Worksite.objects.create(
            organization=self.organization,
            internal_name="Temporary packing site",
            country_code="NL",
            city="Lelystad",
            street="Korenstraat",
            building="16",
            created_by=self.owner,
        )
        second_project = WorkProject.objects.create(
            organization=self.organization,
            worksite=second_worksite,
            internal_name="Temporary packing project",
            worker_visible_name="Temporary packing project",
            worker_capacity=8,
            starts_on=work_date,
            created_by=self.owner,
        )
        template = ProjectScheduleTemplate.objects.create(
            project=second_project,
            name="Evening packing",
            starts_at_time="14:00",
            ends_at_time="22:00",
            created_by=self.owner,
        )

        worker_url = f"/employer/support/workers/{self.worker_connection.public_id}/"
        response = self.client.post(
            worker_url,
            {
                "action": "scheduled_shift_from_template",
                "work_date": work_date.isoformat(),
                # Simulate a quick manager change: the visible template is
                # correct, while the browser's project filter still carries
                # the previous project id.  The template must win.
                "project_id": str(first_project.public_id),
                "schedule_template_id": str(template.public_id),
                "return_tab": "company",
                "return_month": work_date.strftime("%Y-%m"),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        old_shift.refresh_from_db()
        self.assertEqual(old_shift.state, ScheduledWorkShift.STATE_CANCELLED)
        replacement = ScheduledWorkShift.objects.get(
            connection=self.worker_connection,
            work_date=work_date,
            state=ScheduledWorkShift.STATE_PUBLISHED,
        )
        self.assertEqual(replacement.starts_at.strftime("%H:%M"), "14:00")
        self.assertEqual(replacement.work_assignment.project_id, second_project.id)
        self.assertContains(
            response,
            "The shift for this day was replaced and published immediately from the selected template.",
        )

    def test_owner_creates_date_free_templates_and_applies_them_to_selected_worker_days(self):
        """Templates store one shift; the worker calendar owns its actual dates."""

        self.client.force_login(self.owner)
        projects_url = f"/employer/support/projects/?organization={self.organization.public_id}"
        project_starts = timezone.localdate()
        projects_page = self.client.get(projects_url)
        self.assertEqual(projects_page.status_code, 200)
        self.assertContains(projects_page, "data-project-modal-open")
        self.assertContains(projects_page, 'id="project-add-modal"')
        self.assertContains(projects_page, "data-project-modal-close")
        rendered_projects_page = projects_page.content.decode()
        self.assertLess(
            rendered_projects_page.index('class="projects-section"'),
            rendered_projects_page.index('id="project-add-modal"'),
        )
        response = self.client.post(
            projects_url,
            {
                "action": "project_create",
                "name": "Flevosap BV",
                "country_code": "NL",
                "city": "Biddinghuizen",
                "postal_code": "8256TA",
                "street": "Zuurlaan",
                "building": "22",
                "worker_capacity": "12",
                "starts_on": project_starts.isoformat(),
                "ends_on": "",
                "contact_name": "Rafal",
                "contact_phone": "+31600000000",
                "contact_email": "rafal@example.com",
                "instructions": "Bring safety shoes.",
            },
        )
        project = WorkProject.objects.get(organization=self.organization, internal_name="Flevosap BV")
        detail_url = f"/employer/support/projects/{project.public_id}/?organization={self.organization.public_id}"
        self.assertRedirects(response, detail_url)

        response = self.client.post(
            detail_url,
            {
                "action": "project_schedule_template_create",
                "name": "Morning shift",
                "starts_at_time": "06:00",
                "ends_at_time": "14:30",
                "break_minutes": "30",
            },
        )
        self.assertRedirects(response, detail_url)
        morning = ProjectScheduleTemplate.objects.get(project=project, name="Morning shift")
        self.assertEqual(morning.worker_label, "")
        project_page = self.client.get(detail_url)
        self.assertContains(project_page, 'name="name" maxlength="30"')
        self.assertNotContains(project_page, 'name="worker_label"')
        self.client.post(
            detail_url,
            {
                "action": "project_schedule_template_create",
                "name": "X" * 31,
                "starts_at_time": "08:00",
                "ends_at_time": "16:00",
                "break_minutes": "15",
            },
        )
        self.assertFalse(ProjectScheduleTemplate.objects.filter(project=project, name="X" * 31).exists())
        evening = ProjectScheduleTemplate.objects.create(
            project=project,
            name="Evening shift",
            starts_at_time="14:30",
            ends_at_time="23:00",
            created_by=self.owner,
        )

        worker_url = f"/employer/support/workers/{self.worker_connection.public_id}/"
        worker_page = self.client.get(worker_url)
        self.assertContains(worker_page, 'name="work_dates"')
        self.assertNotContains(worker_page, 'name="calendar_dates"')
        self.assertContains(worker_page, 'value="scheduled_shifts_from_template"')
        self.assertContains(worker_page, 'data-calendar-actions')
        self.assertContains(worker_page, 'data-worker-calendar-section')
        self.assertContains(worker_page, 'data-calendar-navigation>', count=3)
        self.assertContains(worker_page, "window.scrollTo(scrollX, scrollY)")
        self.assertContains(worker_page, 'class="worker-calendar-checkbox"')
        self.assertNotContains(worker_page, '<details data-calendar-day')
        self.assertNotContains(worker_page, 'value="work_draft"')
        self.assertNotContains(worker_page, 'value="scheduled_shift_create"')
        self.assertContains(worker_page, 'data-worker-assignment-history')
        self.assertContains(worker_page, 'data-quick-shift-project')
        self.assertContains(worker_page, 'data-quick-shift-template')
        self.assertContains(worker_page, 'data-template-submit')
        self.assertContains(worker_page, "Select a project")
        self.assertContains(worker_page, "Select a shift template")

        first_day = project_starts + timedelta(days=1)
        second_day = project_starts + timedelta(days=3)
        response = self.client.post(
            worker_url,
            {
                "action": "scheduled_shifts_from_template",
                "project_id": str(project.public_id),
                "schedule_template_id": str(morning.public_id),
                "work_dates": [first_day.isoformat(), second_day.isoformat()],
                "return_tab": "company",
                "return_month": first_day.strftime("%Y-%m"),
            },
            follow=True,
        )
        shifts = ScheduledWorkShift.objects.filter(
            connection=self.worker_connection,
            work_date__in=(first_day, second_day),
            state=ScheduledWorkShift.STATE_PUBLISHED,
        ).order_by("work_date")
        self.assertEqual(shifts.count(), 2)
        self.assertEqual({item.starts_at.strftime("%H:%M") for item in shifts}, {"06:00"})
        self.assertContains(response, "selected days")
        self.assertContains(response, 'class="worker-calendar-time">06:00–14:30</span>')

        response = self.client.post(
            worker_url,
            {
                "action": "scheduled_shifts_from_template",
                "project_id": str(project.public_id),
                "schedule_template_id": str(evening.public_id),
                "work_dates": [second_day.isoformat()],
                "return_tab": "company",
                "return_month": second_day.strftime("%Y-%m"),
            },
            follow=True,
        )
        replacement = ScheduledWorkShift.objects.get(
            connection=self.worker_connection,
            work_date=second_day,
            state=ScheduledWorkShift.STATE_PUBLISHED,
        )
        self.assertEqual(replacement.starts_at.strftime("%H:%M"), "14:30")
        self.assertEqual(
            ScheduledWorkShift.objects.filter(
                connection=self.worker_connection,
                work_date=second_day,
                state__in=(
                    ScheduledWorkShift.STATE_DRAFT,
                    ScheduledWorkShift.STATE_PUBLISHED,
                ),
            ).count(),
            1,
        )
        self.assertContains(response, "replaced")

        third_day = project_starts + timedelta(days=4)
        response = self.client.post(
            worker_url,
            {
                "action": "scheduled_shifts_from_template",
                "project_id": str(project.public_id),
                "schedule_template_id": str(morning.public_id),
                "work_dates": [second_day.isoformat(), third_day.isoformat()],
                "return_tab": "company",
                "return_month": second_day.strftime("%Y-%m"),
            },
            follow=True,
        )
        mixed_shifts = ScheduledWorkShift.objects.filter(
            connection=self.worker_connection,
            work_date__in=(second_day, third_day),
            state=ScheduledWorkShift.STATE_PUBLISHED,
        )
        self.assertEqual(mixed_shifts.count(), 2)
        self.assertEqual({item.starts_at.strftime("%H:%M") for item in mixed_shifts}, {"06:00"})
        self.assertContains(response, "replaced")

        draft_day = project_starts + timedelta(days=5)
        clearable_draft = ScheduledWorkShift.objects.create(
            organization=self.organization,
            connection=self.worker_connection,
            work_assignment=None,
            work_date=draft_day,
            starts_at=timezone.make_aware(
                datetime.combine(draft_day, datetime.min.time())
            ),
            ends_at=timezone.make_aware(
                datetime.combine(draft_day, datetime.min.time())
            )
            + timedelta(hours=3),
            state=ScheduledWorkShift.STATE_DRAFT,
            created_by=self.owner,
        )
        response = self.client.post(
            worker_url,
            {
                "action": "scheduled_shifts_from_template",
                "project_id": str(project.public_id),
                "schedule_template_id": str(evening.public_id),
                "work_dates": [draft_day.isoformat()],
                "return_tab": "company",
                "return_month": draft_day.strftime("%Y-%m"),
            },
            follow=True,
        )
        self.assertFalse(
            ScheduledWorkShift.objects.filter(pk=clearable_draft.pk).exists()
        )
        replacement_for_draft = ScheduledWorkShift.objects.get(
            connection=self.worker_connection,
            work_date=draft_day,
            state=ScheduledWorkShift.STATE_PUBLISHED,
        )
        self.assertEqual(replacement_for_draft.starts_at.strftime("%H:%M"), "14:30")
        self.assertContains(response, "replaced")

        response = self.client.post(
            worker_url,
            {
                "action": "scheduled_shifts_clear",
                "work_dates": [
                    first_day.isoformat(),
                    second_day.isoformat(),
                    draft_day.isoformat(),
                ],
                "return_tab": "company",
                "return_month": first_day.strftime("%Y-%m"),
            },
            follow=True,
        )
        self.assertFalse(
            ScheduledWorkShift.objects.filter(
                connection=self.worker_connection,
                work_date__in=(first_day, second_day, draft_day),
                state__in=(ScheduledWorkShift.STATE_DRAFT, ScheduledWorkShift.STATE_PUBLISHED),
            ).exists()
        )
        self.assertFalse(
            ScheduledWorkShift.objects.filter(
                connection=self.worker_connection,
                work_date=draft_day,
                state__in=(
                    ScheduledWorkShift.STATE_DRAFT,
                    ScheduledWorkShift.STATE_PUBLISHED,
                ),
            ).exists()
        )
        self.assertContains(response, "cleared")

    def test_owner_builds_registry_items_for_safe_worker_assignment_forms(self):
        self.client.force_login(self.owner)
        registry_url = (
            f"/employer/support/registries/?organization={self.organization.public_id}"
        )

        response = self.client.post(
            registry_url,
            {
                "action": "housing_site_create",
                "internal_name": "Lelystad home",
                "country_code": "nl",
                "city": "Lelystad",
                "postal_code": "8223XP",
                "street": "Blauwe Slank",
                "building": "31B",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "The registry entry was added.")
        site = HousingSite.objects.get(
            organization=self.organization,
            internal_name="Lelystad home",
        )
        self.assertEqual(site.country_code, "NL")

        self.client.post(
            registry_url,
            {
                "action": "housing_room_create",
                "site_id": str(site.public_id),
                "label": "Room 3",
                "capacity": "1",
            },
            follow=True,
        )
        room = HousingRoom.objects.get(site=site, label="Room 3")
        self.client.post(
            registry_url,
            {
                "action": "housing_place_create",
                "room_id": str(room.public_id),
                "label": "Bed 1",
            },
            follow=True,
        )
        self.assertEqual(HousingPlace.objects.filter(room=room).count(), 1)

        capacity_response = self.client.post(
            registry_url,
            {
                "action": "housing_place_create",
                "room_id": str(room.public_id),
                "label": "Bed 2",
            },
            follow=True,
        )
        self.assertEqual(HousingPlace.objects.filter(room=room).count(), 1)
        self.assertContains(capacity_response, "The entry could not be added.")

        self.client.post(
            registry_url,
            {
                "action": "worksite_create",
                "internal_name": "Flevosap site",
                "country_code": "NL",
                "city": "Biddinghuizen",
                "postal_code": "",
                "street": "Zuurlaan",
                "building": "22",
            },
            follow=True,
        )
        worksite = Worksite.objects.get(
            organization=self.organization,
            internal_name="Flevosap site",
        )
        self.client.post(
            registry_url,
            {
                "action": "work_project_create",
                "worksite_id": str(worksite.public_id),
                "internal_name": "Flevosap line A",
                "worker_visible_name": "Flevosap",
            },
            follow=True,
        )
        self.assertTrue(
            WorkProject.objects.filter(
                organization=self.organization,
                worksite=worksite,
                internal_name="Flevosap line A",
            ).exists()
        )

        self.client.post(
            registry_url,
            {
                "action": "vehicle_create",
                "internal_name": "Transport 1",
                "registration_identifier": "NL-TR-01",
                "seat_capacity": "8",
            },
            follow=True,
        )
        vehicle = Vehicle.objects.get(
            organization=self.organization,
            registration_identifier="NL-TR-01",
        )
        self.assertEqual(vehicle.seat_capacity, 8)

        company_card = self.client.get(
            f"/employer/support/workers/{self.worker_connection.public_id}/?tab=company"
        )
        housing_card = self.client.get(
            f"/employer/support/workers/{self.worker_connection.public_id}/?tab=housing"
        )
        transport_card = self.client.get(
            f"/employer/support/workers/{self.worker_connection.public_id}/?tab=transport"
        )
        self.assertContains(company_card, "Flevosap line A")
        self.assertContains(housing_card, "Lelystad home")
        # Unassigned vehicles are managed in Fleet and no longer clutter the
        # worker's schedule-first Transport tab.
        self.assertNotContains(transport_card, "Transport 1")

    def test_transport_staff_builds_and_publishes_one_complete_route(self):
        vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Transport 1",
            registration_identifier="NL-TR-01",
            seat_capacity=3,
            created_by=self.owner,
        )
        passenger_user = User.objects.create_user(
            username="workspace-route-passenger",
            first_name="Ihor",
            last_name="Passenger",
            email="workspace-route-passenger@example.com",
            password="password",
        )
        passenger_application = SupportApplication.objects.create(
            vacancy=self.worker_connection.vacancy,
            candidate=passenger_user,
            revision=1,
            preferred_language="uk",
            consent_version="support-application-v1",
            consented_at=timezone.now(),
            status=SupportApplication.STATUS_APPROVED,
        )
        passenger_connection = SupportConnection.objects.create(
            organization=self.organization,
            vacancy=self.worker_connection.vacancy,
            application=passenger_application,
            candidate=passenger_user,
            stage=SupportConnection.STAGE_COORDINATOR,
        )
        starts_on = date.today() + timedelta(days=7)
        driver_assignment = DriverVehicleAssignment.objects.create(
            organization=self.organization,
            driver_connection=self.worker_connection,
            vehicle=vehicle,
            starts_on=starts_on,
            created_by=self.owner,
        )
        self.client.force_login(self.owner)
        transport_url = (
            f"/employer/support/transport/?organization={self.organization.public_id}"
        )

        page = self.client.get(transport_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Transport 1")

        created = self.client.post(
            transport_url,
            {
                "action": "route_create",
                "driver_vehicle_assignment_id": str(driver_assignment.public_id),
                "internal_name": "Morning route 1",
                "worksite_id": "",
                "starts_on": starts_on.isoformat(),
                "ends_on": "",
                "departure_time": "",
            },
            follow=True,
        )
        self.assertEqual(created.status_code, 200)
        route = TransportRoute.objects.get(
            organization=self.organization,
            internal_name="Morning route 1",
        )
        self.assertEqual(route.state, TransportRoute.STATE_DRAFT)
        self.assertContains(created, "The route draft was created.")

        for sequence, kind, label in (
            (1, "pickup", "Lelystad meeting point"),
            (2, "dropoff", "Flevosap entrance"),
        ):
            response = self.client.post(
                transport_url,
                {
                    "action": "route_stop_create",
                    "route_id": str(route.public_id),
                    "sequence": str(sequence),
                    "kind": kind,
                    "label": label,
                    "housing_site_id": "",
                },
                follow=True,
            )
            self.assertEqual(response.status_code, 200)

        route.refresh_from_db()
        stops = list(route.stops.order_by("sequence"))
        edited_stop = self.client.post(
            transport_url,
            {
                "action": "route_stop_edit",
                "route_id": str(route.public_id),
                "stop_id": str(stops[0].public_id),
                "sequence": "1",
                "kind": "pickup",
                "label": "Updated meeting point",
                "housing_site_id": "",
            },
            follow=True,
        )
        self.assertEqual(edited_stop.status_code, 200)
        self.assertContains(edited_stop, "Stop updated.")
        stops[0].refresh_from_db()
        self.assertEqual(stops[0].label, "Updated meeting point")
        passenger_response = self.client.post(
            transport_url,
            {
                "action": "route_passenger_create",
                "route_id": str(route.public_id),
                "connection_id": str(passenger_connection.public_id),
                "pickup_stop_id": str(stops[0].public_id),
                "dropoff_stop_id": str(stops[1].public_id),
                "boarding_order": "1",
            },
            follow=True,
        )
        self.assertEqual(passenger_response.status_code, 200)
        self.assertContains(
            passenger_response,
            "The passenger was added and a seat was temporarily reserved.",
        )

        published = self.client.post(
            transport_url,
            {"action": "route_publish", "route_id": str(route.public_id)},
            follow=True,
        )
        self.assertEqual(published.status_code, 200)
        route.refresh_from_db()
        driver_assignment.refresh_from_db()
        self.assertEqual(route.state, TransportRoute.STATE_PUBLISHED)
        self.assertEqual(driver_assignment.state, DriverVehicleAssignment.STATE_PUBLISHED)
        recipients = set(
            NotificationOutbox.objects.filter(
                notification_code="transport.route_published",
                target_public_id=route.public_id,
            ).values_list("recipient_id", flat=True)
        )
        self.assertEqual(recipients, {self.worker_connection.candidate_id, passenger_user.id})
        self.assertContains(
            published,
            "The route was published. The driver and passengers will receive a notification.",
        )

    def test_owner_builds_a_route_and_adds_a_passenger_from_driver_card(self):
        today = timezone.localdate()
        worksite = Worksite.objects.create(
            organization=self.organization,
            internal_name="Crew worksite",
            country_code="NL",
            city="Lelystad",
            street="Work road",
            building="10",
            created_by=self.owner,
        )
        project = WorkProject.objects.create(
            organization=self.organization,
            worksite=worksite,
            internal_name="Crew project",
            worker_visible_name="Crew project",
            worker_capacity=20,
            starts_on=today,
            created_by=self.owner,
        )
        template = ProjectScheduleTemplate.objects.create(
            project=project,
            name="Morning crew",
            starts_at_time=datetime.strptime("06:00", "%H:%M").time(),
            ends_at_time=datetime.strptime("14:00", "%H:%M").time(),
            break_minutes=30,
            created_by=self.owner,
        )
        vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Driver car",
            registration_identifier="NL-DR-01",
            seat_capacity=3,
            created_by=self.owner,
        )
        passenger_user = User.objects.create_user(
            username="worker-card-passenger",
            first_name="Ihor",
            last_name="Passenger",
            email="worker-card-passenger@example.com",
            password="password",
        )
        passenger_application = SupportApplication.objects.create(
            vacancy=self.worker_connection.vacancy,
            candidate=passenger_user,
            revision=1,
            preferred_language="ru",
            consent_version="support-application-v1",
            consented_at=timezone.now(),
            status=SupportApplication.STATUS_APPROVED,
        )
        passenger_connection = SupportConnection.objects.create(
            organization=self.organization,
            vacancy=self.worker_connection.vacancy,
            application=passenger_application,
            candidate=passenger_user,
            stage=SupportConnection.STAGE_COORDINATOR,
        )
        housing_site = HousingSite.objects.create(
            organization=self.organization,
            internal_name="Passenger house",
            country_code="NL",
            city="Lelystad",
            street="Home road",
            building="2",
            created_by=self.owner,
        )
        room = HousingRoom.objects.create(site=housing_site, label="2A", capacity=2)
        place = HousingPlace.objects.create(room=room, label="1")
        HousingAssignment.objects.create(
            organization=self.organization,
            connection=passenger_connection,
            place=place,
            check_in_at=timezone.now() - timedelta(days=1),
            state=HousingAssignment.STATE_PUBLISHED,
            created_by=self.owner,
            published_by=self.owner,
            published_at=timezone.now(),
        )
        driver_work = WorkerProjectAssignment.objects.create(
            organization=self.organization,
            connection=self.worker_connection,
            project=project,
            starts_at=timezone.now(),
            state=WorkerProjectAssignment.STATE_PUBLISHED,
            created_by=self.owner,
            published_by=self.owner,
            published_at=timezone.now(),
        )
        shift_date = today + timedelta(days=1)
        shift_start = timezone.make_aware(datetime.combine(shift_date, template.starts_at_time))
        shift_end = timezone.make_aware(datetime.combine(shift_date, template.ends_at_time))
        ScheduledWorkShift.objects.create(
            organization=self.organization,
            connection=self.worker_connection,
            work_assignment=driver_work,
            schedule_template=template,
            work_date=shift_date,
            starts_at=shift_start,
            ends_at=shift_end,
            break_minutes=template.break_minutes,
            state=ScheduledWorkShift.STATE_PUBLISHED,
            created_by=self.owner,
            published_by=self.owner,
            published_at=timezone.now(),
        )
        driver_assignment = DriverVehicleAssignment.objects.create(
            organization=self.organization,
            driver_connection=self.worker_connection,
            vehicle=vehicle,
            starts_on=today,
            state=DriverVehicleAssignment.STATE_PUBLISHED,
            created_by=self.owner,
            published_by=self.owner,
            published_at=timezone.now(),
        )
        card_url = (
            f"/employer/support/workers/{self.worker_connection.public_id}/"
            f"?tab=transport&transport_template={template.public_id}"
        )
        self.client.force_login(self.owner)

        card = self.client.get(card_url)
        self.assertEqual(card.status_code, 200)
        self.assertContains(card, "Morning crew")
        self.assertContains(card, "Ihor Passenger")
        self.assertNotContains(card, "Create route draft")
        passenger_added = self.client.post(
            card_url,
            {
                "action": "transport_schedule_passenger_add",
                "driver_connection_id": str(self.worker_connection.public_id),
                "passenger_connection_id": str(passenger_connection.public_id),
                "schedule_template_id": str(template.public_id),
                "return_tab": "transport",
                "return_transport_template": str(template.public_id),
            },
            follow=True,
        )
        self.assertEqual(passenger_added.status_code, 200)
        self.assertContains(passenger_added, "Passenger added")
        route = TransportRoute.objects.get(
            organization=self.organization,
            driver_vehicle_assignment=driver_assignment,
            schedule_template=template,
        )
        self.assertEqual(route.state, TransportRoute.STATE_PUBLISHED)
        self.assertTrue(
            TransportPassengerAssignment.objects.filter(
                route=route,
                connection=passenger_connection,
                pickup_stop__housing_site=housing_site,
                dropoff_stop__label__contains="Crew project",
            ).exists()
        )
        passenger_shift = ScheduledWorkShift.objects.get(
            connection=passenger_connection,
            work_date=shift_date,
            state=ScheduledWorkShift.STATE_PUBLISHED,
        )
        self.assertEqual(passenger_shift.schedule_template, template)
        self.assertEqual(passenger_shift.starts_at, shift_start)
        self.assertTrue(
            WorkerProjectAssignment.objects.filter(
                connection=passenger_connection,
                project=project,
                state=WorkerProjectAssignment.STATE_PUBLISHED,
            ).exists()
        )

    def test_worker_transport_card_lists_each_passenger_crew_as_a_tab(self):
        today = timezone.localdate()

        def connection_for(username, first_name, last_name):
            user = User.objects.create_user(
                username=username,
                first_name=first_name,
                last_name=last_name,
                email=f"{username}@example.com",
                password="password",
            )
            application = SupportApplication.objects.create(
                vacancy=self.worker_connection.vacancy,
                candidate=user,
                revision=1,
                preferred_language="ru",
                consent_version="support-application-v1",
                consented_at=timezone.now(),
                status=SupportApplication.STATUS_APPROVED,
            )
            return SupportConnection.objects.create(
                organization=self.organization,
                vacancy=self.worker_connection.vacancy,
                application=application,
                candidate=user,
                stage=SupportConnection.STAGE_ACTIVE_WORKER,
            )

        passenger = connection_for("multi-crew-worker", "Multi", "Crew Worker")
        second_driver = connection_for("second-crew-driver", "Second", "Driver")

        def crew_for(*, suffix, driver, project_name, start_time, end_time):
            worksite = Worksite.objects.create(
                organization=self.organization,
                internal_name=f"Crew site {suffix}",
                country_code="NL",
                city="Lelystad",
                street=f"Project road {suffix}",
                building="10",
                created_by=self.owner,
            )
            project = WorkProject.objects.create(
                organization=self.organization,
                worksite=worksite,
                internal_name=project_name,
                worker_visible_name=project_name,
                worker_capacity=20,
                starts_on=today,
                created_by=self.owner,
            )
            schedule_template = ProjectScheduleTemplate.objects.create(
                project=project,
                name=f"Schedule {suffix}",
                starts_at_time=datetime.strptime(start_time, "%H:%M").time(),
                ends_at_time=datetime.strptime(end_time, "%H:%M").time(),
                break_minutes=30,
                created_by=self.owner,
            )
            vehicle = Vehicle.objects.create(
                organization=self.organization,
                internal_name=f"Crew vehicle {suffix}",
                registration_identifier=f"CREW-{suffix}",
                seat_capacity=4,
                created_by=self.owner,
            )
            driver_assignment = DriverVehicleAssignment.objects.create(
                organization=self.organization,
                driver_connection=driver,
                vehicle=vehicle,
                starts_on=today,
                state=DriverVehicleAssignment.STATE_PUBLISHED,
                created_by=self.owner,
                published_by=self.owner,
                published_at=timezone.now(),
            )
            route = TransportRoute.objects.create(
                organization=self.organization,
                internal_name=f"Crew route {suffix}",
                worksite=worksite,
                schedule_template=schedule_template,
                driver_vehicle_assignment=driver_assignment,
                starts_on=today,
                ends_on=today + timedelta(days=14),
                state=TransportRoute.STATE_PUBLISHED,
                created_by=self.owner,
                published_by=self.owner,
                published_at=timezone.now(),
            )
            pickup = RouteStop.objects.create(
                route=route,
                kind=RouteStop.KIND_PICKUP,
                sequence=1,
                label=f"Pickup {suffix}",
            )
            dropoff = RouteStop.objects.create(
                route=route,
                kind=RouteStop.KIND_DROPOFF,
                sequence=2,
                label=f"Dropoff {suffix}",
            )
            TransportPassengerAssignment.objects.create(
                route=route,
                connection=passenger,
                pickup_stop=pickup,
                dropoff_stop=dropoff,
                boarding_order=1,
            )
            return driver_assignment, schedule_template

        first_assignment, first_template = crew_for(
            suffix="A",
            driver=self.worker_connection,
            project_name="Alpha project",
            start_time="06:00",
            end_time="14:00",
        )
        second_assignment, second_template = crew_for(
            suffix="B",
            driver=second_driver,
            project_name="Beta project",
            start_time="15:00",
            end_time="23:00",
        )
        self.client.force_login(self.owner)
        base_url = f"/employer/support/workers/{passenger.public_id}/?tab=transport"

        page = self.client.get(base_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Alpha project")
        self.assertContains(page, "Beta project")
        first_key = f"{first_assignment.public_id}.{first_template.public_id}"
        second_key = f"{second_assignment.public_id}.{second_template.public_id}"
        self.assertContains(page, f"transport_crew={first_key}")
        self.assertContains(page, f"transport_crew={second_key}")

        second_page = self.client.get(f"{base_url}&transport_crew={second_key}")
        self.assertEqual(second_page.status_code, 200)
        self.assertContains(second_page, "Second Driver")
        self.assertContains(second_page, "CREW-B")
        self.assertContains(second_page, "Pickup B")

    def test_worker_card_allows_one_day_route_and_explains_invalid_period(self):
        vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="One-day route vehicle",
            registration_identifier="NL-ONE-01",
            seat_capacity=4,
            created_by=self.owner,
        )
        starts_on = timezone.localdate()
        driver_assignment = DriverVehicleAssignment.objects.create(
            organization=self.organization,
            driver_connection=self.worker_connection,
            vehicle=vehicle,
            starts_on=starts_on,
            created_by=self.owner,
        )
        self.client.force_login(self.owner)
        card_url = (
            f"/employer/support/workers/{self.worker_connection.public_id}/?tab=transport"
        )

        created = self.client.post(
            card_url,
            {
                "action": "route_create",
                "driver_vehicle_assignment_id": str(driver_assignment.public_id),
                "internal_name": "One-day route",
                "worksite_id": "",
                "starts_on": starts_on.isoformat(),
                "ends_on": starts_on.isoformat(),
                "departure_time": "",
                "return_tab": "transport",
            },
            follow=True,
        )
        self.assertEqual(created.status_code, 200)
        self.assertContains(created, "The route draft was created.")
        self.assertTrue(
            TransportRoute.objects.filter(
                organization=self.organization,
                internal_name="One-day route",
                starts_on=starts_on,
                ends_on=starts_on,
            ).exists()
        )

        invalid = self.client.post(
            card_url,
            {
                "action": "route_create",
                "driver_vehicle_assignment_id": str(driver_assignment.public_id),
                "internal_name": "Invalid route",
                "worksite_id": "",
                "starts_on": starts_on.isoformat(),
                "ends_on": (starts_on - timedelta(days=1)).isoformat(),
                "departure_time": "",
                "return_tab": "transport",
            },
            follow=True,
        )
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(
            invalid,
            "The route could not be created: its end date cannot be before its start date.",
        )

    @override_settings(SUPPORT_FEATURE_ENABLED=False)
    def test_workspace_stays_hidden_when_feature_flag_is_off(self):
        self.client.force_login(self.owner)

        response = self.client.get("/employer/support/")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            self.client.get("/employer/support/registries/").status_code,
            404,
        )
