from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from support.models import (
    DriverVehicleAssignment,
    HousingAssignment,
    HousingRoom,
    HousingSite,
    NotificationOutbox,
    RouteStop,
    SupportAccessGrant,
    SupportApplication,
    SupportConnection,
    SupportVacancy,
    TransportRoute,
    TransportPassengerAssignment,
    Vehicle,
    WorkerProjectAssignment,
)
from support.services.operations import (
    add_route_passenger,
    create_driver_vehicle_assignment,
    create_transport_route,
    delete_transport_route_draft,
    delete_driver_vehicle_assignment_draft,
    edit_driver_vehicle_assignment_draft,
    publish_driver_vehicle_assignment,
    publish_transport_route,
)
from support.services.organizations import create_organization


@override_settings(SUPPORT_FEATURE_ENABLED=True)
class SupportOperationsTests(TestCase):
    """The first operational slice stays hidden behind the Support feature flag.

    These tests cover the important boundary: no worker sees an assignment and
    no push event is created before a staff member explicitly publishes it.
    """

    def setUp(self):
        self.operator = User.objects.create_user(
            username="operations-operator",
            email="operations-operator@example.com",
            password="password",
            is_staff=True,
        )
        self.owner = User.objects.create_user(
            username="operations-owner",
            email="operations-owner@example.com",
            password="password",
        )
        self.candidate = User.objects.create_user(
            username="operations-candidate",
            email="operations-candidate@example.com",
            password="password",
        )
        self.passenger = User.objects.create_user(
            username="operations-passenger",
            email="operations-passenger@example.com",
            password="password",
        )
        self.outsider = User.objects.create_user(
            username="operations-outsider",
            email="operations-outsider@example.com",
            password="password",
        )
        self.organization, _ = create_organization(
            jobhub_operator=self.operator,
            legal_name="Operations Agency sp. z o.o.",
            display_name="Operations Agency",
            owner_email=self.owner.email,
        )
        self.owner_client = APIClient()
        self.owner_client.force_authenticate(self.owner)
        self.candidate_client = APIClient()
        self.candidate_client.force_authenticate(self.candidate)
        self.outsider_client = APIClient()
        self.outsider_client.force_authenticate(self.outsider)
        self.connection = self._create_connection(self.candidate, suffix="primary")
        self.passenger_connection = self._create_connection(
            self.passenger,
            suffix="passenger",
        )
        SupportAccessGrant.objects.create(
            user=self.candidate,
            organization=self.organization,
            granted_by=self.operator,
            ends_at=timezone.now() + timedelta(days=7),
            reason=SupportAccessGrant.REASON_TECHNICAL,
        )
        self.base_url = f"/api/v2/support/organizations/{self.organization.public_id}/operations"

    def _create_connection(self, candidate, *, suffix):
        vacancy = SupportVacancy.objects.create(
            organization=self.organization,
            internal_title=f"Warehouse {suffix}",
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
            stage=SupportConnection.STAGE_COORDINATOR,
        )

    def _create_housing_place(self, *, capacity=1):
        site = self.owner_client.post(
            f"{self.base_url}/housing-sites/",
            {
                "internal_name": "Lelystad house",
                "country_code": "NL",
                "city": "Lelystad",
                "postal_code": "8223XP",
                "street": "Blauwe Slank",
                "building": "31B",
            },
            format="json",
        )
        self.assertEqual(site.status_code, 201, site.data)
        room = self.owner_client.post(
            f"{self.base_url}/housing-rooms/",
            {"site_id": site.data["housing_site"]["id"], "label": "Room 3", "capacity": capacity},
            format="json",
        )
        self.assertEqual(room.status_code, 201, room.data)
        place = room.data["housing_room"]["places"][0]
        return site.data["housing_site"], room.data["housing_room"], place

    def test_creating_room_creates_all_free_places(self):
        site = self.owner_client.post(
            f"{self.base_url}/housing-sites/",
            {
                "internal_name": "Automatic places house",
                "country_code": "NL",
                "city": "Lelystad",
                "street": "Auto street",
                "building": "8",
            },
            format="json",
        )
        self.assertEqual(site.status_code, 201, site.data)

        room = self.owner_client.post(
            f"{self.base_url}/housing-rooms/",
            {"site_id": site.data["housing_site"]["id"], "label": "Room 8", "capacity": 3},
            format="json",
        )

        self.assertEqual(room.status_code, 201, room.data)
        self.assertEqual(
            [item["label"] for item in room.data["housing_room"]["places"]],
            ["1", "2", "3"],
        )
        room_model = HousingRoom.objects.get(public_id=room.data["housing_room"]["id"])
        self.assertEqual(room_model.places.filter(is_active=True).count(), 3)
        self.assertFalse(HousingAssignment.objects.filter(place__room=room_model).exists())

    def test_mobile_housing_workspace_assigns_and_schedules_check_out(self):
        site, _, place = self._create_housing_place(capacity=1)
        check_in_at = (timezone.now() + timedelta(days=1)).replace(microsecond=0)

        assigned = self.owner_client.post(
            f"{self.base_url}/housing-assignments/assign/",
            {
                "connection_id": str(self.connection.public_id),
                "place_id": place["id"],
                "check_in_at": check_in_at.isoformat(),
            },
            format="json",
        )

        self.assertEqual(assigned.status_code, 201, assigned.data)
        assignment_data = assigned.data["housing_assignment"]
        self.assertEqual(assignment_data["state"], HousingAssignment.STATE_PUBLISHED)
        self.assertEqual(
            assignment_data["worker"]["connection_id"],
            str(self.connection.public_id),
        )

        workspace = self.owner_client.get(f"{self.base_url}/housing-sites/")
        self.assertEqual(workspace.status_code, 200, workspace.data)
        site_data = next(
            item for item in workspace.data["results"] if item["id"] == site["id"]
        )
        place_data = site_data["rooms"][0]["places"][0]
        self.assertEqual(len(place_data["assignments"]), 1)
        self.assertEqual(
            place_data["assignments"][0]["worker"]["display_name"],
            self.candidate.username,
        )

        check_out_at = check_in_at + timedelta(days=30)
        checked_out = self.owner_client.post(
            f"/api/v2/support/housing-assignments/{assignment_data['id']}/check-out/",
            {"check_out_at": check_out_at.isoformat()},
            format="json",
        )

        self.assertEqual(checked_out.status_code, 200, checked_out.data)
        assignment = HousingAssignment.objects.get(public_id=assignment_data["id"])
        self.assertEqual(assignment.check_out_at, check_out_at)

    def test_housing_is_drafted_before_publish_and_room_capacity_is_enforced(self):
        _, room, place = self._create_housing_place(capacity=1)
        second_place = self.owner_client.post(
            f"{self.base_url}/housing-places/",
            {"room_id": room["id"], "label": "Bed 2"},
            format="json",
        )
        self.assertEqual(second_place.status_code, 400)
        self.assertEqual(second_place.data["place"], "housing_room_capacity_reached")

        starts_at = (timezone.now() + timedelta(days=1)).replace(microsecond=0)
        draft = self.owner_client.post(
            f"{self.base_url}/housing-assignments/",
            {
                "connection_id": str(self.connection.public_id),
                "place_id": place["id"],
                "check_in_at": starts_at.isoformat(),
            },
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.data)
        assignment_id = draft.data["housing_assignment"]["id"]
        self.assertEqual(draft.data["housing_assignment"]["state"], "draft")
        self.assertFalse(
            NotificationOutbox.objects.filter(
                notification_code="housing.assignment_published"
            ).exists()
        )

        published = self.owner_client.post(
            f"/api/v2/support/housing-assignments/{assignment_id}/publish/"
        )
        self.assertEqual(published.status_code, 200, published.data)
        self.assertEqual(published.data["housing_assignment"]["state"], "published")
        self.assertEqual(
            HousingAssignment.objects.get(public_id=assignment_id).state,
            HousingAssignment.STATE_PUBLISHED,
        )
        notification = NotificationOutbox.objects.get(
            notification_code="housing.assignment_published"
        )
        self.assertEqual(notification.recipient, self.candidate)
        self.assertEqual(notification.target_key, f"support:housing-assignment:{assignment_id}")

        cancelled = self.owner_client.post(
            f"/api/v2/support/housing-assignments/{assignment_id}/cancel/"
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        self.assertEqual(cancelled.data["housing_assignment"]["state"], "cancelled")
        self.assertEqual(
            NotificationOutbox.objects.filter(
                notification_code="housing.assignment_published",
                recipient=self.candidate,
            ).count(),
            2,
        )

    def test_work_assignment_conflict_is_rejected_across_projects(self):
        worksite = self.owner_client.post(
            f"{self.base_url}/worksites/",
            {
                "internal_name": "Flevosap site",
                "country_code": "NL",
                "city": "Biddinghuizen",
                "street": "Zuurlaan",
                "building": "22",
            },
            format="json",
        )
        self.assertEqual(worksite.status_code, 201, worksite.data)
        project = self.owner_client.post(
            f"{self.base_url}/work-projects/",
            {
                "worksite_id": worksite.data["worksite"]["id"],
                "internal_name": "Flevosap line A",
                "worker_visible_name": "Flevosap",
            },
            format="json",
        )
        self.assertEqual(project.status_code, 201, project.data)
        starts_at = (timezone.now() + timedelta(days=2)).replace(microsecond=0)
        payload = {
            "connection_id": str(self.connection.public_id),
            "project_id": project.data["work_project"]["id"],
            "worker_role": "Operator",
            "starts_at": starts_at.isoformat(),
        }
        first = self.owner_client.post(f"{self.base_url}/work-assignments/", payload, format="json")
        second = self.owner_client.post(f"{self.base_url}/work-assignments/", payload, format="json")
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)

        first_published = self.owner_client.post(
            f"/api/v2/support/work-assignments/{first.data['work_assignment']['id']}/publish/"
        )
        self.assertEqual(first_published.status_code, 200, first_published.data)
        blocked = self.owner_client.post(
            f"/api/v2/support/work-assignments/{second.data['work_assignment']['id']}/publish/"
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(
            blocked.data["assignment"],
            "work_assignment_conflicts_with_published_assignment",
        )
        self.assertEqual(WorkerProjectAssignment.objects.filter(state="published").count(), 1)

        other_project = self.owner_client.post(
            f"{self.base_url}/work-projects/",
            {
                "worksite_id": worksite.data["worksite"]["id"],
                "internal_name": "Flevosap line B",
                "worker_visible_name": "Flevosap B",
            },
            format="json",
        )
        self.assertEqual(other_project.status_code, 201, other_project.data)
        other_payload = {
            **payload,
            "project_id": other_project.data["work_project"]["id"],
        }
        other_assignment = self.owner_client.post(
            f"{self.base_url}/work-assignments/",
            other_payload,
            format="json",
        )
        self.assertEqual(other_assignment.status_code, 201, other_assignment.data)
        other_published = self.owner_client.post(
            f"/api/v2/support/work-assignments/"
            f"{other_assignment.data['work_assignment']['id']}/publish/"
        )
        self.assertEqual(other_published.status_code, 400, other_published.data)
        self.assertEqual(
            other_published.data["assignment"],
            "work_assignment_conflicts_with_published_assignment",
        )
        self.assertEqual(WorkerProjectAssignment.objects.filter(state="published").count(), 1)

    def test_route_publishes_driver_and_only_notifies_assigned_people(self):
        _, _, place = self._create_housing_place()
        vehicle = self.owner_client.post(
            f"{self.base_url}/vehicles/",
            {
                "internal_name": "Transit 1",
                "registration_identifier": "JH-TR-01",
                "seat_capacity": 2,
            },
            format="json",
        )
        self.assertEqual(vehicle.status_code, 201, vehicle.data)
        starts_on = date.today() + timedelta(days=3)
        driver = self.owner_client.post(
            f"{self.base_url}/driver-vehicle-assignments/",
            {
                "driver_connection_id": str(self.connection.public_id),
                "vehicle_id": vehicle.data["vehicle"]["id"],
                "starts_on": starts_on.isoformat(),
            },
            format="json",
        )
        self.assertEqual(driver.status_code, 201, driver.data)
        route = self.owner_client.post(
            f"{self.base_url}/transport-routes/",
            {
                "internal_name": "Morning route 1",
                "driver_vehicle_assignment_id": driver.data["driver_vehicle_assignment"]["id"],
                "starts_on": starts_on.isoformat(),
                "reservation_expires_at": (timezone.now() + timedelta(hours=1)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(route.status_code, 201, route.data)
        route_id = route.data["transport_route"]["id"]
        pickup = self.owner_client.post(
            f"/api/v2/support/transport-routes/{route_id}/stops/",
            {"sequence": 1, "kind": "pickup", "label": "Lelystad house"},
            format="json",
        )
        dropoff = self.owner_client.post(
            f"/api/v2/support/transport-routes/{route_id}/stops/",
            {"sequence": 2, "kind": "dropoff", "label": "Worksite"},
            format="json",
        )
        self.assertEqual(pickup.status_code, 201, pickup.data)
        self.assertEqual(dropoff.status_code, 201, dropoff.data)
        passenger = self.owner_client.post(
            f"/api/v2/support/transport-routes/{route_id}/passengers/",
            {
                "connection_id": str(self.passenger_connection.public_id),
                "pickup_stop_id": pickup.data["route_stop"]["id"],
                "dropoff_stop_id": dropoff.data["route_stop"]["id"],
                "boarding_order": 1,
            },
            format="json",
        )
        self.assertEqual(passenger.status_code, 201, passenger.data)

        published = self.owner_client.post(
            f"/api/v2/support/transport-routes/{route_id}/publish/"
        )
        self.assertEqual(published.status_code, 200, published.data)
        self.assertEqual(published.data["transport_route"]["state"], "published")
        route_model = TransportRoute.objects.get(public_id=route_id)
        self.assertEqual(route_model.state, TransportRoute.STATE_PUBLISHED)
        self.assertEqual(route_model.driver_vehicle_assignment.state, "published")
        notifications = NotificationOutbox.objects.filter(
            notification_code="transport.route_published"
        )
        self.assertEqual(notifications.count(), 2)
        self.assertEqual(
            set(notifications.values_list("recipient_id", flat=True)),
            {self.candidate.id, self.passenger.id},
        )

        cancelled = self.owner_client.post(
            f"/api/v2/support/transport-routes/{route_id}/cancel/"
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        route_model.refresh_from_db()
        route_model.driver_vehicle_assignment.refresh_from_db()
        self.assertEqual(route_model.state, TransportRoute.STATE_CANCELLED)
        self.assertEqual(route_model.driver_vehicle_assignment.state, "cancelled")
        self.assertEqual(
            NotificationOutbox.objects.filter(
                notification_code="transport.route_published",
                target_public_id=route_model.public_id,
            ).count(),
            4,
        )

    def test_route_with_only_driver_can_be_published(self):
        starts_on = date.today() + timedelta(days=3)
        vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Solo transport",
            registration_identifier="JH-SOLO-01",
            seat_capacity=2,
            created_by=self.owner,
        )
        driver_assignment = DriverVehicleAssignment.objects.create(
            organization=self.organization,
            driver_connection=self.connection,
            vehicle=vehicle,
            starts_on=starts_on,
            created_by=self.owner,
        )
        route = TransportRoute.objects.create(
            organization=self.organization,
            internal_name="Solo route",
            driver_vehicle_assignment=driver_assignment,
            starts_on=starts_on,
            reservation_expires_at=timezone.now() + timedelta(hours=1),
            created_by=self.owner,
        )
        RouteStop.objects.create(
            route=route,
            sequence=1,
            kind=RouteStop.KIND_PICKUP,
            label="Driver home",
        )
        RouteStop.objects.create(
            route=route,
            sequence=2,
            kind=RouteStop.KIND_DROPOFF,
            label="Worksite",
        )

        published = publish_transport_route(actor=self.owner, route=route)

        self.assertEqual(published.state, TransportRoute.STATE_PUBLISHED)
        driver_assignment.refresh_from_db()
        self.assertEqual(driver_assignment.state, DriverVehicleAssignment.STATE_PUBLISHED)
        notifications = NotificationOutbox.objects.filter(
            notification_code="transport.route_published",
            target_public_id=route.public_id,
        )
        self.assertEqual(notifications.count(), 1)
        self.assertEqual(notifications.get().recipient, self.candidate)

    def test_driver_vehicle_assignment_can_be_published_before_route_exists(self):
        starts_on = date.today() + timedelta(days=3)
        vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Published car",
            registration_identifier="JH-PUBLISHED-01",
            seat_capacity=3,
            created_by=self.owner,
        )
        assignment = create_driver_vehicle_assignment(
            actor=self.owner,
            organization=self.organization,
            driver_connection=self.connection,
            vehicle=vehicle,
            starts_on=starts_on,
        )

        published = publish_driver_vehicle_assignment(actor=self.owner, assignment=assignment)

        self.assertEqual(published.state, DriverVehicleAssignment.STATE_PUBLISHED)
        self.assertEqual(published.published_by, self.owner)
        self.assertTrue(published.published_at)
        notification = NotificationOutbox.objects.get(
            notification_code="transport.assignment_published",
            target_public_id=published.public_id,
        )
        self.assertEqual(notification.recipient, self.candidate)

        route = create_transport_route(
            actor=self.owner,
            organization=self.organization,
            internal_name="Route added later",
            driver_vehicle_assignment=published,
            starts_on=starts_on,
        )
        self.assertEqual(route.state, TransportRoute.STATE_DRAFT)

    def test_driver_vehicle_draft_can_be_edited_before_publication(self):
        starts_on = date.today() + timedelta(days=4)
        vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Editable fleet car",
            registration_identifier="JH-EDIT-01",
            seat_capacity=3,
            created_by=self.owner,
        )
        assignment = DriverVehicleAssignment.objects.create(
            organization=self.organization,
            driver_connection=self.connection,
            vehicle=vehicle,
            starts_on=starts_on,
            created_by=self.owner,
        )

        updated = edit_driver_vehicle_assignment_draft(
            actor=self.owner,
            assignment=assignment,
            driver_connection=self.passenger_connection,
            starts_on=starts_on + timedelta(days=1),
            ends_on=starts_on + timedelta(days=4),
        )

        self.assertEqual(updated.state, DriverVehicleAssignment.STATE_DRAFT)
        self.assertEqual(updated.driver_connection, self.passenger_connection)
        self.assertEqual(updated.starts_on, starts_on + timedelta(days=1))
        self.assertEqual(updated.ends_on, starts_on + timedelta(days=4))

    def test_publishing_new_driver_for_same_vehicle_replaces_previous_driver(self):
        starts_on = date.today() + timedelta(days=3)
        vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Replacement car",
            registration_identifier="JH-REPLACE-01",
            seat_capacity=3,
            created_by=self.owner,
        )
        previous_assignment = create_driver_vehicle_assignment(
            actor=self.owner,
            organization=self.organization,
            driver_connection=self.connection,
            vehicle=vehicle,
            starts_on=starts_on,
        )
        publish_driver_vehicle_assignment(actor=self.owner, assignment=previous_assignment)
        route = TransportRoute.objects.create(
            organization=self.organization,
            internal_name="Route stays with previous driver",
            driver_vehicle_assignment=previous_assignment,
            starts_on=starts_on,
            state=TransportRoute.STATE_PUBLISHED,
            reservation_expires_at=timezone.now() + timedelta(hours=1),
            created_by=self.owner,
        )
        replacement = create_driver_vehicle_assignment(
            actor=self.owner,
            organization=self.organization,
            driver_connection=self.passenger_connection,
            vehicle=vehicle,
            starts_on=starts_on,
            ends_on=starts_on + timedelta(days=1),
        )

        published = publish_driver_vehicle_assignment(actor=self.owner, assignment=replacement)

        previous_assignment.refresh_from_db()
        route.refresh_from_db()
        self.assertEqual(published.state, DriverVehicleAssignment.STATE_PUBLISHED)
        self.assertIsNone(published.ends_on)
        self.assertEqual(previous_assignment.state, DriverVehicleAssignment.STATE_CANCELLED)
        self.assertIsNone(previous_assignment.ends_on)
        self.assertEqual(route.driver_vehicle_assignment, previous_assignment)

    def test_publishing_driver_change_moves_driver_from_other_vehicle_without_route(self):
        starts_on = date.today() + timedelta(days=3)
        old_vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Old driver car",
            registration_identifier="JH-MOVE-OLD",
            seat_capacity=3,
            created_by=self.owner,
        )
        target_vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Target driver car",
            registration_identifier="JH-MOVE-TARGET",
            seat_capacity=4,
            created_by=self.owner,
        )
        driver_previous = create_driver_vehicle_assignment(
            actor=self.owner,
            organization=self.organization,
            driver_connection=self.passenger_connection,
            vehicle=old_vehicle,
            starts_on=starts_on,
        )
        publish_driver_vehicle_assignment(actor=self.owner, assignment=driver_previous)
        target_previous = create_driver_vehicle_assignment(
            actor=self.owner,
            organization=self.organization,
            driver_connection=self.connection,
            vehicle=target_vehicle,
            starts_on=starts_on,
        )
        publish_driver_vehicle_assignment(actor=self.owner, assignment=target_previous)
        target_route = TransportRoute.objects.create(
            organization=self.organization,
            internal_name="Target crew route",
            driver_vehicle_assignment=target_previous,
            starts_on=starts_on,
            state=TransportRoute.STATE_PUBLISHED,
            reservation_expires_at=timezone.now() + timedelta(hours=1),
            created_by=self.owner,
        )
        replacement = create_driver_vehicle_assignment(
            actor=self.owner,
            organization=self.organization,
            driver_connection=self.passenger_connection,
            vehicle=target_vehicle,
            starts_on=starts_on,
        )

        published = publish_driver_vehicle_assignment(actor=self.owner, assignment=replacement)

        driver_previous.refresh_from_db()
        target_previous.refresh_from_db()
        target_route.refresh_from_db()
        self.assertEqual(published.state, DriverVehicleAssignment.STATE_PUBLISHED)
        self.assertEqual(driver_previous.state, DriverVehicleAssignment.STATE_CANCELLED)
        self.assertEqual(target_previous.state, DriverVehicleAssignment.STATE_CANCELLED)
        self.assertEqual(target_route.driver_vehicle_assignment, target_previous)

    def test_driver_move_transfers_previous_route_and_passengers(self):
        starts_on = date.today() + timedelta(days=3)
        old_vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Routed old car",
            registration_identifier="JH-MOVE-ROUTE",
            seat_capacity=3,
            created_by=self.owner,
        )
        target_vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="New driver car",
            registration_identifier="JH-MOVE-NEW",
            seat_capacity=4,
            created_by=self.owner,
        )
        driver_previous = create_driver_vehicle_assignment(
            actor=self.owner,
            organization=self.organization,
            driver_connection=self.passenger_connection,
            vehicle=old_vehicle,
            starts_on=starts_on,
        )
        publish_driver_vehicle_assignment(actor=self.owner, assignment=driver_previous)
        route = TransportRoute.objects.create(
            organization=self.organization,
            internal_name="Crew follows driver",
            driver_vehicle_assignment=driver_previous,
            starts_on=starts_on,
            state=TransportRoute.STATE_PUBLISHED,
            reservation_expires_at=timezone.now() + timedelta(hours=1),
            created_by=self.owner,
        )
        pickup = RouteStop.objects.create(
            route=route,
            sequence=1,
            kind=RouteStop.KIND_PICKUP,
            label="Worker housing",
        )
        dropoff = RouteStop.objects.create(
            route=route,
            sequence=2,
            kind=RouteStop.KIND_DROPOFF,
            label="Work project",
        )
        passenger = TransportPassengerAssignment.objects.create(
            route=route,
            connection=self.connection,
            pickup_stop=pickup,
            dropoff_stop=dropoff,
        )
        replacement = create_driver_vehicle_assignment(
            actor=self.owner,
            organization=self.organization,
            driver_connection=self.passenger_connection,
            vehicle=target_vehicle,
            starts_on=starts_on,
        )

        published = publish_driver_vehicle_assignment(actor=self.owner, assignment=replacement)

        driver_previous.refresh_from_db()
        route.refresh_from_db()
        passenger.refresh_from_db()
        self.assertEqual(published.state, DriverVehicleAssignment.STATE_PUBLISHED)
        self.assertEqual(driver_previous.state, DriverVehicleAssignment.STATE_CANCELLED)
        self.assertEqual(route.state, TransportRoute.STATE_PUBLISHED)
        self.assertEqual(route.driver_vehicle_assignment, published)
        self.assertEqual(passenger.route, route)

    def test_new_driver_does_not_take_previous_drivers_route(self):
        starts_on = date.today() + timedelta(days=3)
        vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Driverless crew car",
            registration_identifier="JH-DRIVERLESS",
            seat_capacity=4,
            created_by=self.owner,
        )
        previous_assignment = create_driver_vehicle_assignment(
            actor=self.owner,
            organization=self.organization,
            driver_connection=self.connection,
            vehicle=vehicle,
            starts_on=starts_on,
        )
        publish_driver_vehicle_assignment(actor=self.owner, assignment=previous_assignment)
        route = TransportRoute.objects.create(
            organization=self.organization,
            internal_name="Route waiting for driver",
            driver_vehicle_assignment=previous_assignment,
            starts_on=starts_on,
            state=TransportRoute.STATE_PUBLISHED,
            created_by=self.owner,
        )
        previous_assignment.state = DriverVehicleAssignment.STATE_CANCELLED
        previous_assignment.cancelled_at = timezone.now()
        previous_assignment.save(update_fields=["state", "cancelled_at", "updated_at"])
        replacement = create_driver_vehicle_assignment(
            actor=self.owner,
            organization=self.organization,
            driver_connection=self.passenger_connection,
            vehicle=vehicle,
            starts_on=starts_on,
        )

        published = publish_driver_vehicle_assignment(actor=self.owner, assignment=replacement)

        route.refresh_from_db()
        self.assertEqual(route.driver_vehicle_assignment, previous_assignment)

    def test_driver_move_requires_exclusions_when_new_vehicle_is_too_small(self):
        starts_on = date.today() + timedelta(days=3)
        old_vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Large crew car",
            registration_identifier="JH-LARGE-CREW",
            seat_capacity=4,
            created_by=self.owner,
        )
        small_vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Small crew car",
            registration_identifier="JH-SMALL-CREW",
            seat_capacity=2,
            created_by=self.owner,
        )
        driver_previous = create_driver_vehicle_assignment(
            actor=self.owner,
            organization=self.organization,
            driver_connection=self.passenger_connection,
            vehicle=old_vehicle,
            starts_on=starts_on,
        )
        publish_driver_vehicle_assignment(actor=self.owner, assignment=driver_previous)
        route = TransportRoute.objects.create(
            organization=self.organization,
            internal_name="Crew that needs two passenger seats",
            driver_vehicle_assignment=driver_previous,
            starts_on=starts_on,
            state=TransportRoute.STATE_PUBLISHED,
            created_by=self.owner,
        )
        pickup = RouteStop.objects.create(
            route=route,
            sequence=1,
            kind=RouteStop.KIND_PICKUP,
            label="Worker housing",
        )
        dropoff = RouteStop.objects.create(
            route=route,
            sequence=2,
            kind=RouteStop.KIND_DROPOFF,
            label="Work project",
        )
        first_passenger = TransportPassengerAssignment.objects.create(
            route=route,
            connection=self.connection,
            pickup_stop=pickup,
            dropoff_stop=dropoff,
        )
        extra_user = User.objects.create_user(
            username="operations-extra-passenger",
            email="operations-extra-passenger@example.com",
            password="password",
        )
        extra_connection = self._create_connection(extra_user, suffix="extra-passenger")
        second_passenger = TransportPassengerAssignment.objects.create(
            route=route,
            connection=extra_connection,
            pickup_stop=pickup,
            dropoff_stop=dropoff,
        )
        replacement = create_driver_vehicle_assignment(
            actor=self.owner,
            organization=self.organization,
            driver_connection=self.passenger_connection,
            vehicle=small_vehicle,
            starts_on=starts_on,
        )

        with self.assertRaises(ValidationError) as error:
            publish_driver_vehicle_assignment(actor=self.owner, assignment=replacement)

        self.assertIn("driver_crew_capacity_exceeded", str(error.exception.detail))
        replacement.refresh_from_db()
        route.refresh_from_db()
        self.assertEqual(replacement.state, DriverVehicleAssignment.STATE_DRAFT)
        self.assertEqual(route.driver_vehicle_assignment, driver_previous)
        self.assertTrue(TransportPassengerAssignment.objects.filter(pk=first_passenger.pk).exists())
        self.assertTrue(TransportPassengerAssignment.objects.filter(pk=second_passenger.pk).exists())

        published = publish_driver_vehicle_assignment(
            actor=self.owner,
            assignment=replacement,
            excluded_passenger_public_ids=[second_passenger.public_id],
        )

        route.refresh_from_db()
        self.assertEqual(route.driver_vehicle_assignment, published)
        self.assertTrue(TransportPassengerAssignment.objects.filter(pk=first_passenger.pk).exists())
        self.assertFalse(TransportPassengerAssignment.objects.filter(pk=second_passenger.pk).exists())

    def test_transport_draft_can_be_deleted_without_deleting_vehicle_assignment(self):
        starts_on = date.today() + timedelta(days=3)
        vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Draft deletion car",
            registration_identifier="JH-DRAFT-DELETE-01",
            seat_capacity=3,
            created_by=self.owner,
        )
        assignment = create_driver_vehicle_assignment(
            actor=self.owner,
            organization=self.organization,
            driver_connection=self.connection,
            vehicle=vehicle,
            starts_on=starts_on,
        )
        route = create_transport_route(
            actor=self.owner,
            organization=self.organization,
            internal_name="Discarded route",
            driver_vehicle_assignment=assignment,
            starts_on=starts_on,
        )

        delete_transport_route_draft(actor=self.owner, route=route)

        self.assertFalse(TransportRoute.objects.filter(pk=route.pk).exists())
        self.assertTrue(DriverVehicleAssignment.objects.filter(pk=assignment.pk).exists())

    def test_driver_vehicle_draft_deletes_its_draft_route(self):
        starts_on = date.today() + timedelta(days=3)
        vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Delete whole draft car",
            registration_identifier="JH-DELETE-WHOLE-01",
            seat_capacity=3,
            created_by=self.owner,
        )
        assignment = DriverVehicleAssignment.objects.create(
            organization=self.organization,
            driver_connection=self.connection,
            vehicle=vehicle,
            starts_on=starts_on,
            created_by=self.owner,
        )
        route = TransportRoute.objects.create(
            organization=self.organization,
            internal_name="Draft route removed with driver",
            driver_vehicle_assignment=assignment,
            starts_on=starts_on,
            state=TransportRoute.STATE_PUBLISHED,
            reservation_expires_at=timezone.now() + timedelta(hours=1),
            created_by=self.owner,
        )

        delete_driver_vehicle_assignment_draft(actor=self.owner, assignment=assignment)

        self.assertFalse(TransportRoute.objects.filter(pk=route.pk).exists())
        self.assertFalse(DriverVehicleAssignment.objects.filter(pk=assignment.pk).exists())

    def test_worker_cannot_be_driver_and_passenger_in_the_same_period(self):
        starts_on = date.today() + timedelta(days=3)
        driver_vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Driver vehicle",
            registration_identifier="JH-CREW-01",
            seat_capacity=3,
            created_by=self.owner,
        )
        passenger_vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Passenger vehicle",
            registration_identifier="JH-CREW-02",
            seat_capacity=3,
            created_by=self.owner,
        )
        driver_assignment = DriverVehicleAssignment.objects.create(
            organization=self.organization,
            driver_connection=self.connection,
            vehicle=driver_vehicle,
            starts_on=starts_on,
            created_by=self.owner,
        )
        route = TransportRoute.objects.create(
            organization=self.organization,
            internal_name="Crew rule route",
            driver_vehicle_assignment=driver_assignment,
            starts_on=starts_on,
            reservation_expires_at=timezone.now() + timedelta(hours=1),
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
            label="Drop-off",
        )
        create_driver_vehicle_assignment(
            actor=self.owner,
            organization=self.organization,
            driver_connection=self.passenger_connection,
            vehicle=passenger_vehicle,
            starts_on=starts_on,
        )
        with self.assertRaises(ValidationError):
            add_route_passenger(
                actor=self.owner,
                route=route,
                connection=self.passenger_connection,
                pickup_stop=pickup,
                dropoff_stop=dropoff,
                boarding_order=1,
            )

        TransportPassengerAssignment.objects.create(
            route=route,
            connection=self.passenger_connection,
            pickup_stop=pickup,
            dropoff_stop=dropoff,
            boarding_order=1,
        )
        with self.assertRaises(ValidationError):
            create_driver_vehicle_assignment(
                actor=self.owner,
                organization=self.organization,
                driver_connection=self.passenger_connection,
                vehicle=passenger_vehicle,
                starts_on=starts_on,
            )

    def test_transport_workspace_api_exposes_only_transport_builder_data(self):
        self.organization.status = "active"
        self.organization.save(update_fields=["status", "updated_at"])
        site, _, _ = self._create_housing_place()
        starts_on = date.today() + timedelta(days=3)
        vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Builder vehicle",
            registration_identifier="JH-BUILD-01",
            seat_capacity=3,
            created_by=self.owner,
        )
        driver_assignment = DriverVehicleAssignment.objects.create(
            organization=self.organization,
            driver_connection=self.connection,
            vehicle=vehicle,
            starts_on=starts_on,
            created_by=self.owner,
        )
        route = TransportRoute.objects.create(
            organization=self.organization,
            internal_name="Builder route",
            driver_vehicle_assignment=driver_assignment,
            starts_on=starts_on,
            reservation_expires_at=timezone.now() + timedelta(hours=1),
            created_by=self.owner,
        )
        pickup = RouteStop.objects.create(
            route=route,
            sequence=1,
            kind=RouteStop.KIND_PICKUP,
            label="Lelystad house",
            housing_site=HousingSite.objects.get(public_id=site["id"]),
        )
        dropoff = RouteStop.objects.create(
            route=route,
            sequence=2,
            kind=RouteStop.KIND_DROPOFF,
            label="Factory gate",
        )
        TransportPassengerAssignment.objects.create(
            route=route,
            connection=self.passenger_connection,
            pickup_stop=pickup,
            dropoff_stop=dropoff,
            boarding_order=1,
        )

        response = self.owner_client.get(
            f"/api/v2/support/organizations/{self.organization.public_id}/"
            "transport-workspace/"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["vehicles"][0]["seat_capacity"], 3)
        self.assertNotIn("street", response.data["housing_sites"][0])
        self.assertNotIn("postal_code", response.data["housing_sites"][0])
        route_data = response.data["routes"][0]
        self.assertEqual(route_data["id"], str(route.public_id))
        self.assertEqual(route_data["driver_assignment"]["driver"]["id"], str(self.connection.public_id))
        self.assertEqual(route_data["passengers"][0]["worker"]["id"], str(self.passenger_connection.public_id))
        self.assertEqual(route_data["available_seat_count"], 1)
        self.assertTrue(route_data["is_reservation_active"])

        hidden = self.outsider_client.get(
            f"/api/v2/support/organizations/{self.organization.public_id}/"
            "transport-workspace/"
        )
        self.assertEqual(hidden.status_code, 404)

    def test_driver_manifest_shows_only_current_routes_and_their_passengers(self):
        today = date.today()
        vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Driver manifest vehicle",
            registration_identifier="JH-MANIFEST-01",
            seat_capacity=3,
            created_by=self.owner,
        )
        driver_assignment = DriverVehicleAssignment.objects.create(
            organization=self.organization,
            driver_connection=self.connection,
            vehicle=vehicle,
            starts_on=today,
            state=DriverVehicleAssignment.STATE_PUBLISHED,
            created_by=self.owner,
        )
        route = TransportRoute.objects.create(
            organization=self.organization,
            internal_name="Published driver route",
            driver_vehicle_assignment=driver_assignment,
            starts_on=today,
            state=TransportRoute.STATE_PUBLISHED,
            created_by=self.owner,
        )
        pickup = RouteStop.objects.create(
            route=route,
            sequence=1,
            kind=RouteStop.KIND_PICKUP,
            label="Passenger housing",
        )
        dropoff = RouteStop.objects.create(
            route=route,
            sequence=2,
            kind=RouteStop.KIND_DROPOFF,
            label="Factory gate",
        )
        TransportPassengerAssignment.objects.create(
            route=route,
            connection=self.passenger_connection,
            pickup_stop=pickup,
            dropoff_stop=dropoff,
            boarding_order=1,
        )

        response = self.candidate_client.get(
            f"/api/v2/support/connections/{self.connection.public_id}/"
            "driver-manifest/mine/"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["results"]), 1)
        manifest_route = response.data["results"][0]
        self.assertEqual(manifest_route["vehicle"], "JH-MANIFEST-01")
        self.assertEqual(manifest_route["seat_capacity"], 3)
        self.assertEqual(
            manifest_route["passengers"],
            [
                {
                    "name": "operations-passenger",
                    "pickup": "Passenger housing",
                    "dropoff": "Factory gate",
                    "boarding_order": 1,
                }
            ],
        )

    def test_outsider_cannot_discover_or_publish_operational_assignment(self):
        _, _, place = self._create_housing_place()
        draft = self.owner_client.post(
            f"{self.base_url}/housing-assignments/",
            {
                "connection_id": str(self.connection.public_id),
                "place_id": place["id"],
                "check_in_at": (timezone.now() + timedelta(days=1)).isoformat(),
            },
            format="json",
        )
        self.assertEqual(draft.status_code, 201, draft.data)
        blocked = self.outsider_client.post(
            f"/api/v2/support/housing-assignments/{draft.data['housing_assignment']['id']}/publish/"
        )
        self.assertEqual(blocked.status_code, 404)
