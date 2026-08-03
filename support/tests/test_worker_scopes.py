from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from support.models import (
    HousingPlace,
    HousingRoom,
    HousingSite,
    SupportApplication,
    SupportConnection,
    SupportVacancy,
    WorkerAccessScope,
)
from support.permission_codes import HOUSING_MANAGE, WORKER_VIEW
from support.services.organizations import (
    activate_organization,
    create_organization,
    grant_permission,
)


@override_settings(SUPPORT_FEATURE_ENABLED=True)
class WorkerScopeTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(
            username="scope-operator",
            email="scope-operator@example.com",
            password="password",
            is_staff=True,
        )
        self.owner = User.objects.create_user(
            username="scope-owner",
            email="scope-owner@example.com",
            password="password",
        )
        self.manager = User.objects.create_user(
            username="scope-manager",
            email="scope-manager@example.com",
            password="password",
        )
        self.first_worker = User.objects.create_user(
            username="scope-first-worker",
            first_name="Ihor",
            last_name="First",
            email="scope-first-worker@example.com",
            password="password",
        )
        self.second_worker = User.objects.create_user(
            username="scope-second-worker",
            first_name="Olena",
            last_name="Second",
            email="scope-second-worker@example.com",
            password="password",
        )
        self.organization, _ = create_organization(
            jobhub_operator=self.operator,
            legal_name="Scope Agency sp. z o.o.",
            display_name="Scope Agency",
            owner_email=self.owner.email,
        )
        activate_organization(
            jobhub_operator=self.operator,
            organization=self.organization,
        )
        from support.models import OrganizationMembership

        self.manager_membership = OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.manager,
            display_role="Housing coordinator",
            created_by=self.owner,
            accepted_at=timezone.now(),
        )
        for permission_code in (WORKER_VIEW, HOUSING_MANAGE):
            grant_permission(
                actor=self.owner,
                organization=self.organization,
                membership=self.manager_membership,
                permission_code=permission_code,
            )
        self.first_connection = self._create_connection(self.first_worker, "first")
        self.second_connection = self._create_connection(self.second_worker, "second")
        site = HousingSite.objects.create(
            organization=self.organization,
            internal_name="Scope home",
            country_code="NL",
            city="Lelystad",
            street="Private street",
            building="1",
            created_by=self.owner,
        )
        room = HousingRoom.objects.create(site=site, label="Room", capacity=2)
        self.place = HousingPlace.objects.create(room=room, label="Bed 1")
        self.owner_client = APIClient()
        self.owner_client.force_authenticate(self.owner)
        self.manager_client = APIClient()
        self.manager_client.force_authenticate(self.manager)
        self.base_url = (
            f"/api/v2/support/organizations/{self.organization.public_id}/operations"
        )

    def _create_connection(self, worker, suffix):
        vacancy = SupportVacancy.objects.create(
            organization=self.organization,
            internal_title=f"Scope vacancy {suffix}",
            created_by=self.owner,
        )
        application = SupportApplication.objects.create(
            vacancy=vacancy,
            candidate=worker,
            revision=1,
            preferred_language="ru",
            consent_version="support-application-v1",
            consented_at=timezone.now(),
            status=SupportApplication.STATUS_APPROVED,
        )
        return SupportConnection.objects.create(
            organization=self.organization,
            vacancy=vacancy,
            application=application,
            candidate=worker,
            stage=SupportConnection.STAGE_COORDINATOR,
        )

    def test_scope_hides_unassigned_workers_and_blocks_direct_operation_api(self):
        self.client.force_login(self.manager)
        first_card_url = (
            f"/employer/support/workers/{self.first_connection.public_id}/"
        )
        second_card_url = (
            f"/employer/support/workers/{self.second_connection.public_id}/"
        )

        self.assertEqual(self.client.get(first_card_url).status_code, 404)
        self.assertEqual(self.client.get(second_card_url).status_code, 404)
        first_summary_url = (
            f"/api/v2/support/organizations/{self.organization.public_id}/connections/"
            f"{self.first_connection.public_id}/summary/"
        )
        second_summary_url = (
            f"/api/v2/support/organizations/{self.organization.public_id}/connections/"
            f"{self.second_connection.public_id}/summary/"
        )
        self.assertEqual(self.manager_client.get(first_summary_url).status_code, 404)
        self.assertEqual(self.manager_client.get(second_summary_url).status_code, 404)
        workspace = self.client.get("/employer/support/")
        self.assertNotContains(workspace, "Ihor First")
        self.assertNotContains(workspace, "Olena Second")

        blocked = self.manager_client.post(
            f"{self.base_url}/housing-assignments/",
            {
                "connection_id": str(self.second_connection.public_id),
                "place_id": str(self.place.public_id),
                "check_in_at": "2026-09-01T10:00:00Z",
            },
            format="json",
        )
        self.assertEqual(blocked.status_code, 403)

        created = self.owner_client.post(
            f"/api/v2/support/organizations/{self.organization.public_id}/members/"
            f"{self.manager_membership.public_id}/worker-scopes/",
            {"connection_id": str(self.first_connection.public_id)},
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        scope_id = created.data["worker_scope"]["id"]

        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(first_card_url).status_code, 200)
        self.assertEqual(self.client.get(second_card_url).status_code, 404)
        summary = self.manager_client.get(first_summary_url)
        self.assertEqual(summary.status_code, 200, summary.data)
        self.assertEqual(
            summary.data["connection"]["candidate"]["display_name"], "Ihor First"
        )
        self.assertTrue(summary.data["available_sections"]["housing"])
        self.assertFalse(summary.data["available_sections"]["time"])
        self.assertTrue(summary.data["available_actions"]["create_housing"])
        self.assertEqual(
            summary.data["available_resources"]["housing_places"][0]["place_label"],
            "Bed 1",
        )
        self.assertEqual(self.manager_client.get(second_summary_url).status_code, 404)
        scoped_workspace = self.client.get("/employer/support/")
        self.assertContains(scoped_workspace, "Ihor First")
        self.assertNotContains(scoped_workspace, "Olena Second")

        allowed = self.manager_client.post(
            f"{self.base_url}/housing-assignments/",
            {
                "connection_id": str(self.first_connection.public_id),
                "place_id": str(self.place.public_id),
                "check_in_at": "2026-09-01T10:00:00Z",
            },
            format="json",
        )
        self.assertEqual(allowed.status_code, 201, allowed.data)
        summary_with_housing = self.manager_client.get(first_summary_url)
        self.assertEqual(summary_with_housing.status_code, 200, summary_with_housing.data)
        self.assertEqual(len(summary_with_housing.data["housing"]), 1)
        self.assertEqual(
            summary_with_housing.data["housing"][0]["place"]["street"],
            "Private street",
        )

        revoked = self.owner_client.post(
            f"/api/v2/support/organizations/{self.organization.public_id}/worker-scopes/"
            f"{scope_id}/revoke/"
        )
        self.assertEqual(revoked.status_code, 200, revoked.data)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(first_card_url).status_code, 404)

    def test_owner_can_manage_worker_scopes_from_the_protected_team_screen(self):
        team_url = (
            "/employer/support/team/"
            f"?organization={self.organization.public_id}"
            f"&member={self.manager_membership.public_id}"
        )
        self.client.force_login(self.owner)

        workspace = self.client.get(
            f"/employer/support/?organization={self.organization.public_id}"
        )
        self.assertEqual(workspace.status_code, 200)
        self.assertContains(workspace, "/employer/support/team/")

        team = self.client.get(team_url)
        self.assertEqual(team.status_code, 200)
        self.assertContains(team, "scope-manager")
        self.assertContains(team, "Ihor First")

        granted = self.client.post(
            team_url,
            {
                "action": "scope_grant",
                "membership_id": str(self.manager_membership.public_id),
                "connection_id": str(self.first_connection.public_id),
            },
        )
        self.assertEqual(granted.status_code, 302)
        scope = WorkerAccessScope.objects.get(
            membership=self.manager_membership,
            connection=self.first_connection,
            is_active=True,
        )

        self.client.force_login(self.manager)
        allowed_card = self.client.get(
            f"/employer/support/workers/{self.first_connection.public_id}/"
        )
        self.assertEqual(allowed_card.status_code, 200)
        self.assertEqual(self.client.get(team_url).status_code, 404)

        self.client.force_login(self.owner)
        revoked = self.client.post(
            team_url,
            {
                "action": "scope_revoke",
                "membership_id": str(self.manager_membership.public_id),
                "scope_id": str(scope.public_id),
            },
        )
        self.assertEqual(revoked.status_code, 302)
        scope.refresh_from_db()
        self.assertFalse(scope.is_active)

        self.client.force_login(self.manager)
        self.assertEqual(
            self.client.get(
                f"/employer/support/workers/{self.first_connection.public_id}/"
            ).status_code,
            404,
        )
