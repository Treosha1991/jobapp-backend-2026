from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from jobs.models import PushDevice
from support.models import (
    InAppNotification,
    NotificationOutbox,
    SupportAccessGrant,
    SupportConversation,
    SupportConversationMember,
    SupportMessage,
    SupportMessageTranslation,
)
from support.services.entitlements import expire_elapsed_temporary_access_grants
from support.services.notifications import dispatch_outbox_entry, enqueue_support_notification
from support.services.organizations import create_organization


@override_settings(SUPPORT_FEATURE_ENABLED=True)
class SupportNotificationTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(
            username="notification-operator",
            email="notification-operator@example.com",
            password="password",
            is_staff=True,
        )
        self.worker = User.objects.create_user(
            username="notification-worker",
            email="notification-worker@example.com",
            password="password",
        )
        self.outsider = User.objects.create_user(
            username="notification-outsider",
            email="notification-outsider@example.com",
            password="password",
        )
        self.owner = User.objects.create_user(
            username="notification-owner",
            email="notification-owner@example.com",
            password="password",
        )
        self.organization, _ = create_organization(
            jobhub_operator=self.operator,
            legal_name="Notification Agency sp. z o.o.",
            display_name="Notification Agency",
            owner_email=self.owner.email,
        )
        self.worker_client = APIClient()
        self.worker_client.force_authenticate(self.worker)
        self.outsider_client = APIClient()
        self.outsider_client.force_authenticate(self.outsider)

    def _enqueue(self, *, code="conversation.message", push_requested=True):
        return enqueue_support_notification(
            organization=self.organization,
            recipient=self.worker,
            notification_code=code,
            target_kind="conversation",
            target_public_id=self.organization.public_id,
            target_key=f"support:conversation:{self.organization.public_id}",
            collapse_key=f"support:conversation:{self.organization.public_id}",
            dedupe_key=f"test:{code}:{self.organization.public_id}:{push_requested}",
            push_requested=push_requested,
        )

    def test_outbox_keeps_sensitive_message_text_out_of_delivery_payload(self):
        device = PushDevice.objects.create(
            user=self.worker,
            token="notification-test-token",
            platform="android",
            app_language="ru",
        )
        outbox, created = self._enqueue()

        self.assertTrue(created)
        self.assertEqual(InAppNotification.objects.filter(recipient=self.worker).count(), 1)
        self.assertNotIn("passport", str(outbox.safe_context).lower())

        with patch(
            "support.services.notifications.send_push_message",
            return_value=("sent", "provider-message", ""),
        ) as sender:
            result = dispatch_outbox_entry(outbox_public_id=outbox.public_id)

        self.assertEqual(result["sent"], 1)
        payload = sender.call_args.kwargs
        self.assertEqual(payload["token"], device.token)
        self.assertEqual(payload["body"], "Новое сообщение в JobHub Support.")
        self.assertEqual(payload["notification_tag"], f"jobhub:{outbox.target_key}")
        self.assertEqual(payload["data"]["notification_target"], outbox.target_key)
        self.assertNotIn("message_body", payload["data"])
        self.assertNotIn("address", payload["data"])
        outbox.refresh_from_db()
        self.assertEqual(outbox.status, NotificationOutbox.STATUS_DELIVERED)

    def test_notification_center_is_private_and_read_is_idempotent(self):
        outbox, _ = self._enqueue(code="application.approved")
        notification = InAppNotification.objects.get(outbox=outbox)

        listed = self.worker_client.get("/api/v2/support/notifications/mine/")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data["results"][0]["id"], str(notification.public_id))
        self.assertNotIn("body", listed.data["results"][0])

        first_read = self.worker_client.post(
            f"/api/v2/support/notifications/{notification.public_id}/read/"
        )
        second_read = self.worker_client.post(
            f"/api/v2/support/notifications/{notification.public_id}/read/"
        )
        forbidden = self.outsider_client.post(
            f"/api/v2/support/notifications/{notification.public_id}/read/"
        )

        self.assertEqual(first_read.status_code, 200)
        self.assertEqual(second_read.status_code, 200)
        self.assertEqual(forbidden.status_code, 404)

    def test_notification_center_can_exclude_chat_and_returns_category_counts(self):
        self._enqueue(code="conversation.message")
        document_outbox, _ = self._enqueue(code="documents.requested")
        document_notification = InAppNotification.objects.get(outbox=document_outbox)

        listed = self.worker_client.get(
            "/api/v2/support/notifications/mine/?include_chat=0"
        )

        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(listed.data["unread_count"], 1)
        self.assertEqual(listed.data["unread_counts"], {"documents": 1})
        self.assertEqual(len(listed.data["results"]), 1)
        self.assertEqual(
            listed.data["results"][0]["id"], str(document_notification.public_id)
        )
        self.assertEqual(listed.data["results"][0]["category"], "documents")

    def test_translation_is_access_checked_and_never_uses_an_unapproved_provider(self):
        now = timezone.now()
        SupportAccessGrant.objects.create(
            user=self.worker,
            organization=self.organization,
            granted_by=self.operator,
            starts_at=now,
            ends_at=now + timedelta(days=1),
            reason=SupportAccessGrant.REASON_TECHNICAL,
        )
        conversation = SupportConversation.objects.create(
            organization=self.organization,
            kind=SupportConversation.KIND_JOBHUB,
            created_by=self.operator,
        )
        SupportConversationMember.objects.create(
            conversation=conversation,
            user=self.worker,
            role=SupportConversationMember.ROLE_WORKER,
        )
        message = SupportMessage.objects.create(
            conversation=conversation,
            sender=self.operator,
            body="Private source message",
            original_language="ru",
        )
        url = (
            f"/api/v2/support/conversations/{conversation.public_id}/messages/"
            f"{message.public_id}/translations/en/"
        )

        unavailable = self.worker_client.post(url)
        original = self.worker_client.post(url.replace("/en/", "/ru/"))
        forbidden = self.outsider_client.post(url)

        self.assertEqual(unavailable.status_code, 409)
        self.assertEqual(original.status_code, 200)
        self.assertEqual(original.data["translation"]["state"], "original")
        self.assertEqual(original.data["translation"]["translated_text"], message.body)
        self.assertEqual(forbidden.status_code, 404)
        translation = SupportMessageTranslation.objects.get(message=message, target_language="en")
        self.assertEqual(translation.status, SupportMessageTranslation.STATUS_FAILED)
        self.assertEqual(translation.error_code, "translation_provider_not_configured")

    def test_expired_access_creates_one_neutral_notification(self):
        now = timezone.now()
        grant = SupportAccessGrant.objects.create(
            user=self.worker,
            organization=self.organization,
            granted_by=self.operator,
            starts_at=now - timedelta(days=2),
            ends_at=now - timedelta(minutes=1),
            reason=SupportAccessGrant.REASON_TECHNICAL,
        )

        expired = expire_elapsed_temporary_access_grants(at_time=now)

        self.assertEqual(expired, 1)
        grant.refresh_from_db()
        self.assertEqual(grant.status, SupportAccessGrant.STATUS_EXPIRED)
        outbox = NotificationOutbox.objects.get(
            dedupe_key=f"support.access.expired:{grant.public_id}"
        )
        self.assertEqual(outbox.notification_code, "support.access_changed")
