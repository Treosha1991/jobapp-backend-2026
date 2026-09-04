from importlib import import_module
from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.apps import apps
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from jobs.models import UserProfile

from support.models import (
    OrganizationMembership,
    PermissionGrant,
    ProjectCrew,
    ProjectCrewResourceAssignment,
    SupportApplication,
    SupportAccessGrant,
    SupportConnection,
    SupportConversation,
    SupportConversationMember,
    SupportChatImage,
    SupportMessage,
    SupportVacancy,
    Vehicle,
    WorkerAccessScope,
    WorkProject,
    Worksite,
)
from support.permission_codes import CHAT_MANAGE, WORKER_VIEW
from support.services.organizations import create_organization


@override_settings(
    SUPPORT_FEATURE_ENABLED=True,
    AVATAR_PUBLIC_BASE_URL="https://cdn.example.test",
)
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
        UserProfile.objects.create(user=self.owner, avatar_key="avatars/owner.png")
        UserProfile.objects.create(user=self.manager, avatar_key="avatars/manager.png")
        UserProfile.objects.create(user=self.worker, avatar_key="avatars/worker.png")
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
        worker = next(item for item in results if item["target_type"] == "worker")
        self.assertEqual(worker["first_name"], "Worker")
        self.assertEqual(worker["last_name"], "Three")
        self.assertEqual(
            worker["avatar_url"],
            "https://cdn.example.test/avatars/worker.png",
        )
        self.assertNotIn("avatar_key", str(response.data))
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
        participant = response.data["conversation"]["participants"][0]
        self.assertEqual(participant["first_name"], "Worker")
        self.assertEqual(
            participant["avatar_url"],
            "https://cdn.example.test/avatars/worker.png",
        )

        sent = self.client.post(
            f"/api/v2/support/conversations/{response.data['conversation']['id']}/messages/send/",
            {"body": "Hello", "original_language": "ru"},
            format="json",
        )
        self.assertEqual(sent.status_code, 201, sent.data)
        self.assertEqual(sent.data["message"]["sender_first_name"], "Owner")
        self.assertEqual(
            sent.data["message"]["sender_avatar_url"],
            "https://cdn.example.test/avatars/owner.png",
        )

    def test_message_reply_and_internal_forward_use_existing_chats(self):
        worker_chat = self.client.post(
            f"{self.organization_url}/chat-directory/open/",
            {
                "target_type": "worker",
                "target_id": str(self.connection.public_id),
            },
            format="json",
        )
        staff_chat = self.client.post(
            f"{self.organization_url}/chat-directory/open/",
            {
                "target_type": "staff",
                "target_id": str(self.manager_membership.public_id),
            },
            format="json",
        )
        source = self.client.post(
            f"/api/v2/support/conversations/"
            f"{worker_chat.data['conversation']['id']}/messages/send/",
            {"body": "Original message", "original_language": "en"},
            format="json",
        )
        reply = self.client.post(
            f"/api/v2/support/conversations/"
            f"{worker_chat.data['conversation']['id']}/messages/send/",
            {
                "body": "Reply message",
                "original_language": "en",
                "reply_to_message_id": source.data["message"]["id"],
            },
            format="json",
        )
        self.assertEqual(reply.status_code, 201, reply.data)
        self.assertEqual(
            reply.data["message"]["reply_to"]["body"],
            "Original message",
        )

        forwarded = self.client.post(
            f"/api/v2/support/conversations/"
            f"{worker_chat.data['conversation']['id']}/messages/"
            f"{source.data['message']['id']}/forward/",
            {"target_conversation_id": staff_chat.data["conversation"]["id"]},
            format="json",
        )
        self.assertEqual(forwarded.status_code, 201, forwarded.data)
        self.assertEqual(
            forwarded.data["conversation"]["id"],
            staff_chat.data["conversation"]["id"],
        )
        self.assertTrue(forwarded.data["message"]["is_forwarded"])
        self.assertEqual(forwarded.data["message"]["body"], "Original message")

        invalid = self.client.post(
            f"/api/v2/support/conversations/"
            f"{worker_chat.data['conversation']['id']}/messages/"
            f"{source.data['message']['id']}/forward/",
            {"target_conversation_id": worker_chat.data["conversation"]["id"]},
            format="json",
        )
        self.assertEqual(invalid.status_code, 400, invalid.data)

    @override_settings(
        CHAT_MEDIA_R2_BUCKET="private-chat-test",
        CHAT_MEDIA_R2_ENDPOINT_URL="https://r2.example.test",
        CHAT_MEDIA_R2_ACCESS_KEY_ID="test-key",
        CHAT_MEDIA_R2_SECRET_ACCESS_KEY="test-secret",
    )
    @patch("support.api_views.signed_chat_image_url")
    @patch("support.api_views.upload_chat_image")
    def test_private_image_message_and_forward_share_one_asset(
        self,
        upload_chat_image_mock,
        signed_url_mock,
    ):
        from PIL import Image

        signed_url_mock.return_value = "https://signed.example.test/private-image"
        worker_chat = self.client.post(
            f"{self.organization_url}/chat-directory/open/",
            {"target_type": "worker", "target_id": str(self.connection.public_id)},
            format="json",
        )
        staff_chat = self.client.post(
            f"{self.organization_url}/chat-directory/open/",
            {
                "target_type": "staff",
                "target_id": str(self.manager_membership.public_id),
            },
            format="json",
        )
        image_bytes = BytesIO()
        Image.new("RGB", (40, 30), "blue").save(image_bytes, format="JPEG")
        upload = SimpleUploadedFile(
            "photo.jpg",
            image_bytes.getvalue(),
            content_type="image/jpeg",
        )

        sent = self.client.post(
            f"/api/v2/support/conversations/"
            f"{worker_chat.data['conversation']['id']}/messages/send-images/",
            {
                "body": "Маршрут на сегодня",
                "original_language": "ru",
                "images": [upload],
            },
            format="multipart",
        )

        self.assertEqual(sent.status_code, 201, sent.data)
        self.assertEqual(len(sent.data["message"]["images"]), 1)
        self.assertEqual(
            sent.data["message"]["images"][0]["download_url"],
            "https://signed.example.test/private-image",
        )
        self.assertEqual(SupportChatImage.objects.count(), 1)
        upload_chat_image_mock.assert_called_once()

        forwarded = self.client.post(
            f"/api/v2/support/conversations/"
            f"{worker_chat.data['conversation']['id']}/messages/"
            f"{sent.data['message']['id']}/forward/",
            {"target_conversation_id": staff_chat.data["conversation"]["id"]},
            format="json",
        )
        self.assertEqual(forwarded.status_code, 201, forwarded.data)
        self.assertEqual(len(forwarded.data["message"]["images"]), 1)
        self.assertEqual(SupportChatImage.objects.count(), 1)
        asset = SupportChatImage.objects.get()
        self.assertEqual(asset.message_links.count(), 2)

        source_message = SupportMessage.objects.get(
            public_id=sent.data["message"]["id"]
        )
        forwarded_message = SupportMessage.objects.get(
            public_id=forwarded.data["message"]["id"]
        )
        old_deleted_at = timezone.now() - timedelta(days=31)
        source_message.deleted_at = old_deleted_at
        source_message.save(update_fields=["deleted_at"])
        with patch(
            "support.management.commands.purge_deleted_support_chat_images.delete_chat_image"
        ) as delete_mock:
            call_command("purge_deleted_support_chat_images")
            delete_mock.assert_not_called()
        forwarded_message.deleted_at = old_deleted_at
        forwarded_message.save(update_fields=["deleted_at"])
        with patch(
            "support.management.commands.purge_deleted_support_chat_images.delete_chat_image"
        ) as delete_mock:
            call_command("purge_deleted_support_chat_images")
            delete_mock.assert_called_once_with(asset.object_key)
        asset.refresh_from_db()
        self.assertIsNotNone(asset.purged_at)

        outsider = User.objects.create_user(
            username="chat-image-outsider",
            password="password",
        )
        outsider_client = APIClient()
        outsider_client.force_authenticate(outsider)
        denied = outsider_client.get(
            f"/api/v2/support/conversations/"
            f"{worker_chat.data['conversation']['id']}/messages/"
        )
        self.assertEqual(denied.status_code, 404)

    def test_conversations_are_sorted_only_by_latest_message(self):
        staff_chat = self.client.post(
            f"{self.organization_url}/chat-directory/open/",
            {
                "target_type": "staff",
                "target_id": str(self.manager_membership.public_id),
            },
            format="json",
        )
        worker_chat = self.client.post(
            f"{self.organization_url}/chat-directory/open/",
            {
                "target_type": "worker",
                "target_id": str(self.connection.public_id),
            },
            format="json",
        )
        self.assertEqual(staff_chat.status_code, 201, staff_chat.data)
        self.assertEqual(worker_chat.status_code, 201, worker_chat.data)

        manager_client = APIClient()
        manager_client.force_authenticate(self.manager)
        older_unread = manager_client.post(
            f"/api/v2/support/conversations/{staff_chat.data['conversation']['id']}/messages/send/",
            {"body": "Older unread", "original_language": "en"},
            format="json",
        )
        newer_read = self.client.post(
            f"/api/v2/support/conversations/{worker_chat.data['conversation']['id']}/messages/send/",
            {"body": "Newer reply", "original_language": "en"},
            format="json",
        )
        self.assertEqual(older_unread.status_code, 201, older_unread.data)
        self.assertEqual(newer_read.status_code, 201, newer_read.data)

        conversations = self.client.get("/api/v2/support/conversations/mine/")

        self.assertEqual(conversations.status_code, 200, conversations.data)
        results = conversations.data["results"]
        self.assertEqual(
            [item["id"] for item in results],
            [
                worker_chat.data["conversation"]["id"],
                staff_chat.data["conversation"]["id"],
            ],
        )
        self.assertEqual(results[0]["unread_count"], 0)
        self.assertEqual(results[1]["unread_count"], 1)

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
            owner_summary.data["connection"]["candidate"]["avatar_url"],
            "https://cdn.example.test/avatars/worker.png",
        )
        self.assertNotIn("avatar_key", str(owner_summary.data))
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
        self.assertEqual(worker["candidate"]["first_name"], "Worker")
        self.assertEqual(
            worker["candidate"]["avatar_url"],
            "https://cdn.example.test/avatars/worker.png",
        )
        self.assertEqual(worker["project_crews"][0]["project_name"], "Packing project")
        self.assertEqual(worker["project_crews"][0]["crew_name"], "Morning crew")
        self.assertEqual(worker["project_crews"][0]["role"], "driver")

    def test_manager_shares_contact_and_worker_opens_private_peer_chat(self):
        second_worker = User.objects.create_user(
            username="chat-directory-second-worker",
            first_name="Second",
            last_name="Driver",
            email="chat-directory-second-worker@example.com",
            password="password",
        )
        UserProfile.objects.create(
            user=second_worker,
            avatar_key="avatars/second-worker.png",
        )
        second_vacancy = SupportVacancy.objects.create(
            organization=self.organization,
            internal_title="Second warehouse worker",
            created_by=self.owner,
        )
        second_application = SupportApplication.objects.create(
            vacancy=second_vacancy,
            candidate=second_worker,
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
            candidate=second_worker,
            stage=SupportConnection.STAGE_ACTIVE_WORKER,
        )
        for worker in (self.worker, second_worker):
            SupportAccessGrant.objects.create(
                user=worker,
                organization=self.organization,
                granted_by=self.owner,
                ends_at=timezone.now() + timedelta(days=7),
                reason=SupportAccessGrant.REASON_TECHNICAL,
            )

        opened = self.client.post(
            f"{self.organization_url}/chat-directory/open/",
            {"target_type": "worker", "target_id": str(self.connection.public_id)},
            format="json",
        )
        self.assertEqual(opened.status_code, 201, opened.data)
        conversation_id = opened.data["conversation"]["id"]
        options = self.client.get(
            f"/api/v2/support/conversations/{conversation_id}/contact-options/"
        )
        self.assertEqual(options.status_code, 200, options.data)
        self.assertTrue(
            any(
                item["target_id"] == str(second_connection.public_id)
                for item in options.data["results"]
            )
        )
        shared = self.client.post(
            f"/api/v2/support/conversations/{conversation_id}/messages/share-contact/",
            {
                "target_type": "worker",
                "target_id": str(second_connection.public_id),
                "original_language": "ru",
            },
            format="json",
        )
        self.assertEqual(shared.status_code, 201, shared.data)
        self.assertEqual(shared.data["message"]["kind"], "contact")
        self.assertEqual(
            shared.data["message"]["shared_contact"]["display_name"],
            "Second Driver",
        )
        self.assertEqual(
            shared.data["message"]["shared_contact"]["avatar_url"],
            "https://cdn.example.test/avatars/second-worker.png",
        )
        self.assertNotIn("avatar_key", str(shared.data))

        outsider = User.objects.create_user(
            username="chat-directory-contact-outsider",
            email="chat-directory-contact-outsider@example.com",
            password="password",
        )
        outsider_client = APIClient()
        outsider_client.force_authenticate(outsider)
        outsider_options = outsider_client.get(
            f"/api/v2/support/conversations/{conversation_id}/contact-options/"
        )
        outsider_open = outsider_client.post(
            f"/api/v2/support/conversations/{conversation_id}/messages/"
            f"{shared.data['message']['id']}/open-contact/"
        )
        self.assertEqual(outsider_options.status_code, 404, outsider_options.data)
        self.assertEqual(outsider_open.status_code, 404, outsider_open.data)

        worker_client = APIClient()
        worker_client.force_authenticate(self.worker)
        direct = worker_client.post(
            f"/api/v2/support/conversations/{conversation_id}/messages/"
            f"{shared.data['message']['id']}/open-contact/"
        )
        self.assertEqual(direct.status_code, 201, direct.data)
        direct_conversation = SupportConversation.objects.get(
            public_id=direct.data["conversation"]["id"]
        )
        self.assertEqual(direct_conversation.kind, SupportConversation.KIND_DRIVER)
        self.assertEqual(
            set(
                direct_conversation.members.filter(left_at__isnull=True).values_list(
                    "user_id", flat=True
                )
            ),
            {self.worker.id, second_worker.id},
        )
