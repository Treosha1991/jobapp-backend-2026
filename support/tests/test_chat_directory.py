from importlib import import_module
from types import SimpleNamespace
from uuid import uuid4

from django.apps import apps
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from support.models import (
    OrganizationMembership,
    PermissionGrant,
    ProjectCrew,
    ProjectCrewResourceAssignment,
    SupportApplication,
    SupportConnection,
    SupportConversation,
    SupportConversationMember,
    SupportMessage,
    SupportVacancy,
    Vehicle,
    WorkerAccessScope,
    WorkProject,
    Worksite,
)
from support.permission_codes import CHAT_MANAGE, WORKER_VIEW
from support.services.organizations import create_organization


@override_settings(SUPPORT_FEATURE_ENABLED=True)
class StaffChatDirectoryTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(
            username="chat-directory-operator",
            email="chat-directory-operator@example.com",
            password="password",
            is_staff=True,
        )
        self.owner = User.objects.create_user(
            username="chat-directory-owner",
            first_name="Owner",
            last_name="One",
            email="chat-directory-owner@example.com",
            password="password",
        )
        self.manager = User.objects.create_user(
            username="chat-directory-manager",
            first_name="Manager",
            last_name="Two",
            email="chat-directory-manager@example.com",
            password="password",
        )
        self.worker = User.objects.create_user(
            username="chat-directory-worker",
            first_name="Worker",
            last_name="Three",
            email="chat-directory-worker@example.com",
            password="password",
        )
        self.organization, _ = create_organization(
            jobhub_operator=self.operator,
            legal_name="Chat Directory Agency sp. z o.o.",
            display_name="Chat Directory Agency",
            owner_email=self.owner.email,
        )
        self.manager_membership = OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.manager,
            display_role="Coordinator",
            created_by=self.owner,
            accepted_at=timezone.now(),
        )
        vacancy = SupportVacancy.objects.create(
            organization=self.organization,
            internal_title="Warehouse worker",
            created_by=self.owner,
        )
        application = SupportApplication.objects.create(
            vacancy=vacancy,
            candidate=self.worker,
            revision=1,
            preferred_language="ru",
            consent_version="support-application-v1",
            consented_at=timezone.now(),
            status=SupportApplication.STATUS_APPROVED,
        )
        self.connection = SupportConnection.objects.create(
            organization=self.organization,
            vacancy=vacancy,
            application=application,
            candidate=self.worker,
            stage=SupportConnection.STAGE_ACTIVE_WORKER,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.organization_url = (
            f"/api/v2/support/organizations/{self.organization.public_id}"
        )

    def test_directory_contains_workers_and_staff_without_existing_chats(self):
        response = self.client.get(f"{self.organization_url}/chat-directory/")

        self.assertEqual(response.status_code, 200, response.data)
        results = response.data["results"]
        self.assertTrue(
            any(
                item["target_type"] == "worker"
                and item["target_id"] == str(self.connection.public_id)
                and item["conversation"] is None
                for item in results
            )
        )
        self.assertTrue(
            any(
                item["target_type"] == "staff"
                and item["target_id"] == str(self.manager_membership.public_id)
                and item["conversation"] is None
                for item in results
            )
        )

    def test_open_staff_chat_is_idempotent_and_accessible_to_target(self):
        payload = {
            "target_type": "staff",
            "target_id": str(self.manager_membership.public_id),
        }
        first = self.client.post(
            f"{self.organization_url}/chat-directory/open/",
            payload,
            format="json",
        )
        second = self.client.post(
            f"{self.organization_url}/chat-directory/open/",
            payload,
            format="json",
        )

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(
            first.data["conversation"]["id"],
            second.data["conversation"]["id"],
        )
        manager_client = APIClient()
        manager_client.force_authenticate(self.manager)
        conversations = manager_client.get("/api/v2/support/conversations/mine/")
        self.assertEqual(conversations.status_code, 200, conversations.data)
        self.assertTrue(
            any(
                item["id"] == first.data["conversation"]["id"]
                for item in conversations.data["results"]
            )
        )

    def test_open_worker_chat_from_active_worker_stage(self):
        response = self.client.post(
            f"{self.organization_url}/chat-directory/open/",
            {
                "target_type": "worker",
                "target_id": str(self.connection.public_id),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["conversation"]["audience"], "workers")
        self.assertEqual(
            response.data["conversation"]["connection_id"],
            str(self.connection.public_id),
        )

    def test_same_worker_across_applications_uses_one_private_manager_chat(self):
        first = self.client.post(
            f"{self.organization_url}/chat-directory/open/",
            {
                "target_type": "worker",
                "target_id": str(self.connection.public_id),
            },
            format="json",
        )
        second_vacancy = SupportVacancy.objects.create(
            organization=self.organization,
            internal_title="Another warehouse worker",
            created_by=self.owner,
        )
        second_application = SupportApplication.objects.create(
            vacancy=second_vacancy,
            candidate=self.worker,
            revision=1,
            preferred_language="ru",
            consent_version="support-application-v1",
            consented_at=timezone.now(),
            status=SupportApplication.STATUS_APPROVED,
        )
        second_connection = SupportConnection.objects.create(
            organization=self.organization,
            vacancy=second_vacancy,
            application=second_application,
            candidate=self.worker,
            stage=SupportConnection.STAGE_ACTIVE_WORKER,
        )
        second = self.client.post(
            f"{self.organization_url}/chat-directory/open/",
            {
                "target_type": "worker",
                "target_id": str(second_connection.public_id),
            },
            format="json",
        )

        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(
            first.data["conversation"]["id"],
            second.data["conversation"]["id"],
        )
        directory = self.client.get(f"{self.organization_url}/chat-directory/")
        worker_rows = [
            item
            for item in directory.data["results"]
            if item["target_type"] == "worker"
        ]
        self.assertEqual(len(worker_rows), 1)
        self.assertEqual(
            worker_rows[0]["conversation"]["id"],
            first.data["conversation"]["id"],
        )

    def test_each_manager_has_a_separate_private_worker_conversation(self):
        self.connection.assigned_manager = self.manager_membership
        self.connection.save(update_fields=("assigned_manager", "updated_at"))
        for permission_code in (CHAT_MANAGE, WORKER_VIEW):
            PermissionGrant.objects.create(
                membership=self.manager_membership,
                permission_code=permission_code,
                granted_by=self.owner,
            )
        WorkerAccessScope.objects.create(
            membership=self.manager_membership,
            connection=self.connection,
            granted_by=self.owner,
        )

        owner_response = self.client.post(
            f"{self.organization_url}/chat-directory/open/",
            {
                "target_type": "worker",
                "target_id": str(self.connection.public_id),
            },
            format="json",
        )
        manager_client = APIClient()
        manager_client.force_authenticate(self.manager)
        manager_response = manager_client.post(
            f"{self.organization_url}/chat-directory/open/",
            {
                "target_type": "worker",
                "target_id": str(self.connection.public_id),
            },
            format="json",
        )

        self.assertEqual(owner_response.status_code, 201, owner_response.data)
        self.assertEqual(manager_response.status_code, 201, manager_response.data)
        self.assertNotEqual(
            owner_response.data["conversation"]["id"],
            manager_response.data["conversation"]["id"],
        )
        owner_conversation = SupportConversation.objects.get(
            public_id=owner_response.data["conversation"]["id"]
        )
        manager_conversation = SupportConversation.objects.get(
            public_id=manager_response.data["conversation"]["id"]
        )
        self.assertEqual(owner_conversation.private_worker, self.worker)
        self.assertEqual(owner_conversation.private_manager, self.owner)
        self.assertEqual(manager_conversation.private_worker, self.worker)
        self.assertEqual(manager_conversation.private_manager, self.manager)
        self.assertEqual(
            set(
                owner_conversation.members.filter(left_at__isnull=True).values_list(
                    "user_id", flat=True
                )
            ),
            {self.owner.id, self.worker.id},
        )
        self.assertEqual(
            set(
                manager_conversation.members.filter(left_at__isnull=True).values_list(
                    "user_id", flat=True
                )
            ),
            {self.manager.id, self.worker.id},
        )
        forbidden = manager_client.get(
            f"/api/v2/support/conversations/{owner_conversation.public_id}/messages/"
        )
        self.assertIn(forbidden.status_code, (403, 404), forbidden.data)

        owner_summary = self.client.get(
            f"{self.organization_url}/connections/{self.connection.public_id}/summary/"
        )
        manager_summary = manager_client.get(
            f"{self.organization_url}/connections/{self.connection.public_id}/summary/"
        )
        self.assertEqual(owner_summary.status_code, 200, owner_summary.data)
        self.assertEqual(manager_summary.status_code, 200, manager_summary.data)
        self.assertEqual(
            owner_summary.data["manager_conversation_id"],
            str(owner_conversation.public_id),
        )
        self.assertEqual(
            manager_summary.data["manager_conversation_id"],
            str(manager_conversation.public_id),
        )

    def test_migration_merges_legacy_private_chats_without_losing_messages(self):
        owner_membership = OrganizationMembership.objects.get(
            organization=self.organization,
            user=self.owner,
        )
        client_message_id = uuid4()
        conversations = []
        for kind, body in (
            (SupportConversation.KIND_MANAGER, "Message from the manager chat"),
            (SupportConversation.KIND_COORDINATOR, "Message from the crew chat"),
        ):
            conversation = SupportConversation.objects.create(
                organization=self.organization,
                connection=self.connection,
                kind=kind,
                created_by=self.owner,
            )
            SupportConversationMember.objects.create(
                conversation=conversation,
                user=self.owner,
                organization_membership=owner_membership,
                role=SupportConversationMember.ROLE_STAFF,
            )
            SupportConversationMember.objects.create(
                conversation=conversation,
                user=self.worker,
                role=SupportConversationMember.ROLE_WORKER,
            )
            SupportMessage.objects.create(
                conversation=conversation,
                sender=self.owner,
                body=body,
                original_language=SupportMessage.LANGUAGE_RU,
                client_message_id=client_message_id,
            )
            conversations.append(conversation)
        SupportConversationMember.objects.create(
            conversation=conversations[1],
            user=self.manager,
            organization_membership=self.manager_membership,
            role=SupportConversationMember.ROLE_STAFF,
        )

        migration = import_module(
            "support.migrations.0036_private_manager_worker_conversations"
        )
        migration.normalize_private_worker_conversations(
            apps,
            SimpleNamespace(connection=SimpleNamespace(alias="default")),
        )

        canonical = SupportConversation.objects.get(
            organization=self.organization,
            kind=SupportConversation.KIND_MANAGER,
            private_worker=self.worker,
            private_manager=self.owner,
        )
        self.assertEqual(canonical.state, SupportConversation.STATE_ACTIVE)
        self.assertEqual(
            set(canonical.messages.values_list("body", flat=True)),
            {"Message from the manager chat", "Message from the crew chat"},
        )
        self.assertEqual(
            set(
                canonical.members.filter(left_at__isnull=True).values_list(
                    "user_id", flat=True
                )
            ),
            {self.owner.id, self.worker.id},
        )
        self.assertEqual(
            SupportConversation.objects.filter(
                organization=self.organization,
                state=SupportConversation.STATE_ARCHIVED,
            ).count(),
            1,
        )

    def test_worker_directory_includes_project_crew_and_driving_licence(self):
        self.connection.has_driving_license = True
        self.connection.save(update_fields=("has_driving_license", "updated_at"))
        worksite = Worksite.objects.create(
            organization=self.organization,
            internal_name="Warehouse address",
            country_code="NL",
            city="Lelystad",
            street="Teststraat",
            building="1",
            created_by=self.owner,
        )
        project = WorkProject.objects.create(
            organization=self.organization,
            worksite=worksite,
            internal_name="Internal project",
            worker_visible_name="Packing project",
            created_by=self.owner,
        )
        crew = ProjectCrew.objects.create(
            organization=self.organization,
            project=project,
            internal_name="Morning crew",
            created_by=self.owner,
        )
        vehicle = Vehicle.objects.create(
            organization=self.organization,
            internal_name="Demo van",
            registration_identifier="TEST-01",
            seat_capacity=4,
            created_by=self.owner,
        )
        ProjectCrewResourceAssignment.objects.create(
            crew=crew,
            driver_connection=self.connection,
            vehicle=vehicle,
            created_by=self.owner,
        )

        response = self.client.get(f"{self.organization_url}/connections/")

        self.assertEqual(response.status_code, 200, response.data)
        worker = response.data["results"][0]
        self.assertTrue(worker["has_driving_license"])
        self.assertEqual(worker["project_crews"][0]["project_name"], "Packing project")
        self.assertEqual(worker["project_crews"][0]["crew_name"], "Morning crew")
        self.assertEqual(worker["project_crews"][0]["role"], "driver")
