from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from support.models import (
    AuditEvent,
    DelegablePermissionGrant,
    OrganizationMembership,
    PermissionGrant,
)
from support.permission_codes import (
    CHAT_MANAGE,
    HOUSING_MANAGE,
    MEMBER_DELEGATE_PERMISSIONS,
)
from support.services.organizations import (
    create_organization,
    grant_permission,
)


@override_settings(SUPPORT_FEATURE_ENABLED=True)
class SupportOrganizationAccessTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(
            username="support-operator",
            email="operator@example.com",
            password="password",
            is_staff=True,
        )
        self.owner = User.objects.create_user(
            username="support-owner",
            email="owner@example.com",
            password="password",
        )
        self.deputy = User.objects.create_user(
            username="support-deputy",
            email="deputy@example.com",
            password="password",
        )
        self.staff_member = User.objects.create_user(
            username="support-staff",
            email="staff@example.com",
            password="password",
        )
        self.organization, self.owner_membership = create_organization(
            jobhub_operator=self.operator,
            legal_name="Example Agency sp. z o.o.",
            display_name="Example Agency",
            owner_email=self.owner.email,
        )

    def test_only_one_owner_can_exist_for_an_organization(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrganizationMembership.objects.create(
                organization=self.organization,
                user=self.deputy,
                is_owner=True,
                accepted_at=timezone.now(),
            )

    def test_owner_invites_existing_account_and_account_accepts_in_app(self):
        owner_client = APIClient()
        owner_client.force_authenticate(self.owner)
        response = owner_client.post(
            f"/api/v2/support/organizations/{self.organization.public_id}/invitations/",
            {
                "email": self.staff_member.email,
                "display_role": "Coordinator",
                "permission_codes": [CHAT_MANAGE],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        invitation_id = response.data["invitation"]["id"]
        self.assertEqual(response.data["invitation"]["permission_codes"], [CHAT_MANAGE])

        staff_client = APIClient()
        staff_client.force_authenticate(self.staff_member)
        pending = staff_client.get("/api/v2/support/invitations/mine/")
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(len(pending.data["results"]), 1)
        accepted = staff_client.post(f"/api/v2/support/invitations/{invitation_id}/accept/")

        self.assertEqual(accepted.status_code, 200)
        membership = OrganizationMembership.objects.get(
            organization=self.organization,
            user=self.staff_member,
        )
        self.assertTrue(
            PermissionGrant.objects.filter(
                membership=membership,
                permission_code=CHAT_MANAGE,
                is_active=True,
            ).exists()
        )

    def test_deputy_can_grant_only_owner_authorized_permissions(self):
        deputy_membership = OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.deputy,
            display_role="Deputy",
            accepted_at=timezone.now(),
        )
        target_membership = OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.staff_member,
            display_role="Coordinator",
            accepted_at=timezone.now(),
        )
        PermissionGrant.objects.create(
            membership=deputy_membership,
            permission_code=MEMBER_DELEGATE_PERMISSIONS,
            granted_by=self.owner,
        )
        DelegablePermissionGrant.objects.create(
            membership=deputy_membership,
            permission_code=CHAT_MANAGE,
            granted_by=self.owner,
        )

        grant, created = grant_permission(
            actor=self.deputy,
            organization=self.organization,
            membership=target_membership,
            permission_code=CHAT_MANAGE,
        )

        self.assertTrue(created)
        self.assertEqual(grant.permission_code, CHAT_MANAGE)
        self.assertTrue(
            AuditEvent.objects.filter(
                organization=self.organization,
                action="permission.granted",
                actor=self.deputy,
            ).exists()
        )
        with self.assertRaises(PermissionDenied):
            grant_permission(
                actor=self.deputy,
                organization=self.organization,
                membership=target_membership,
                permission_code=HOUSING_MANAGE,
            )

    def test_only_jobhub_operator_can_create_organization_over_api(self):
        client = APIClient()
        client.force_authenticate(self.owner)
        forbidden = client.post(
            "/api/v2/support/organizations/",
            {
                "legal_name": "Other Agency",
                "display_name": "Other",
                "owner_email": self.owner.email,
            },
            format="json",
        )
        self.assertEqual(forbidden.status_code, 403)

        client.force_authenticate(self.operator)
        created = client.post(
            "/api/v2/support/organizations/",
            {
                "legal_name": "Second Agency",
                "display_name": "Second",
                "owner_email": self.staff_member.email,
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data["organization"]["display_name"], "Second")

    def test_member_cannot_discover_another_organization_by_public_id(self):
        outside_owner = User.objects.create_user(
            username="outside-owner",
            email="outside-owner@example.com",
            password="password",
        )
        outside_organization, _ = create_organization(
            jobhub_operator=self.operator,
            legal_name="Outside Agency",
            display_name="Outside",
            owner_email=outside_owner.email,
        )
        client = APIClient()
        client.force_authenticate(self.owner)

        response = client.get(f"/api/v2/support/organizations/{outside_organization.public_id}/")

        self.assertEqual(response.status_code, 404)

    def test_operator_temporary_grant_changes_only_support_access_snapshot(self):
        worker = User.objects.create_user(
            username="support-worker",
            email="worker@example.com",
            password="password",
        )
        client = APIClient()
        client.force_authenticate(self.operator)
        response = client.post(
            "/api/v2/support/operator/temporary-access-grants/",
            {
                "user_email": worker.email,
                "duration_days": 7,
                "reason": "technical_help",
                "organization_public_id": str(self.organization.public_id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)

        worker_client = APIClient()
        worker_client.force_authenticate(worker)
        bootstrap = worker_client.get("/api/v2/support/bootstrap/")
        self.assertEqual(bootstrap.status_code, 200)
        self.assertEqual(bootstrap.data["mode"], "worker")
        self.assertEqual(bootstrap.data["support_access"]["state"], "active")
        self.assertEqual(bootstrap.data["support_access"]["source"], "temporary_grant")
