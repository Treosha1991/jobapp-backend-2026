from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from support.models import (
    DocumentRequestPackage,
    DocumentRequestPackageEvent,
    InAppNotification,
    NotificationOutbox,
    OrganizationMembership,
    SupportAccessGrant,
    SupportApplication,
    SupportConnection,
    SupportVacancy,
)
from support.permission_codes import DOCUMENT_REQUEST
from support.services.organizations import (
    activate_organization,
    create_organization,
    grant_permission,
    grant_worker_access_scope,
)


@override_settings(SUPPORT_FEATURE_ENABLED=True)
class DocumentRequestPackageTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(
            username="documents-operator",
            email="documents-operator@example.com",
            password="password",
            is_staff=True,
        )
        self.owner = User.objects.create_user(
            username="documents-owner",
            email="documents-owner@example.com",
            password="password",
        )
        self.manager = User.objects.create_user(
            username="documents-manager",
            email="documents-manager@example.com",
            password="password",
        )
        self.worker = User.objects.create_user(
            username="documents-worker",
            email="documents-worker@example.com",
            password="password",
        )
        self.organization, _ = create_organization(
            jobhub_operator=self.operator,
            legal_name="Document Agency sp. z o.o.",
            display_name="Document Agency",
            owner_email=self.owner.email,
        )
        activate_organization(jobhub_operator=self.operator, organization=self.organization)
        self.organization.verified_document_email = "documents@agency.example"
        self.organization.save(update_fields=["verified_document_email", "updated_at"])
        self.manager_membership = OrganizationMembership.objects.create(
            organization=self.organization,
            user=self.manager,
            display_role="Manager",
            created_by=self.owner,
            accepted_at=timezone.now(),
        )
        grant_permission(
            actor=self.owner,
            organization=self.organization,
            membership=self.manager_membership,
            permission_code=DOCUMENT_REQUEST,
        )
        vacancy = SupportVacancy.objects.create(
            organization=self.organization,
            internal_title="Document vacancy",
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
            stage=SupportConnection.STAGE_DOCUMENTS,
        )
        grant_worker_access_scope(
            actor=self.owner,
            organization=self.organization,
            membership=self.manager_membership,
            connection=self.connection,
        )
        SupportAccessGrant.objects.create(
            user=self.worker,
            organization=self.organization,
            granted_by=self.operator,
            ends_at=timezone.now() + timedelta(days=7),
            reason=SupportAccessGrant.REASON_TECHNICAL,
        )
        self.worker_client = APIClient()
        self.worker_client.force_authenticate(self.worker)
        self.manager_client = APIClient()
        self.manager_client.force_authenticate(self.manager)

    def test_package_uses_verified_employer_email_and_keeps_only_request_metadata(self):
        base_url = f"/api/v2/support/organizations/{self.organization.public_id}/document-packages/"
        created = self.manager_client.post(
            base_url,
            {
                "connection_id": str(self.connection.public_id),
                "requested_items": [
                    {"type": "passport"},
                    {"type": "visa"},
                    {"type": "custom", "custom_label": "Employment form"},
                ],
                "additional_instructions": "Use the account code in the subject.",
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        package = created.data["document_package"]
        self.assertEqual(package["recipient_email"], "documents@agency.example")
        self.assertTrue(package["account_reference_code"].startswith("JH-"))
        self.assertEqual(package["status"], "requested")
        self.assertNotIn("file", package)
        self.assertNotIn("document_number", package)
        requested_notification = NotificationOutbox.objects.get(
            recipient=self.worker,
            notification_code="documents.requested",
        )
        self.assertEqual(requested_notification.target_kind, "connection")
        self.assertEqual(
            requested_notification.target_public_id,
            self.connection.public_id,
        )
        self.assertEqual(
            requested_notification.target_key,
            f"support:connection:{self.connection.public_id}:documents",
        )
        self.assertTrue(
            InAppNotification.objects.filter(
                outbox=requested_notification,
                recipient=self.worker,
            ).exists()
        )

        worker_list_url = (
            f"/api/v2/support/connections/{self.connection.public_id}/document-packages/mine/"
        )
        listed = self.worker_client.get(worker_list_url)
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(listed.data["results"][0]["account_reference_code"], package["account_reference_code"])

        marked = self.worker_client.post(
            f"/api/v2/support/document-packages/{package['id']}/mark-sent/",
            {},
            format="json",
        )
        self.assertEqual(marked.status_code, 200, marked.data)
        self.assertEqual(marked.data["document_package"]["status"], "sent_to_employer")

        correction = self.manager_client.post(
            f"/api/v2/support/document-packages/{package['id']}/needs-correction/",
            {"manager_note": "Please check the requested list and resend by e-mail."},
            format="json",
        )
        self.assertEqual(correction.status_code, 200, correction.data)
        self.assertEqual(correction.data["document_package"]["status"], "needs_correction")
        self.assertTrue(
            NotificationOutbox.objects.filter(
                recipient=self.worker,
                notification_code="documents.needs_correction",
            ).exists()
        )

        resent = self.worker_client.post(
            f"/api/v2/support/document-packages/{package['id']}/mark-sent/",
            {},
            format="json",
        )
        self.assertEqual(resent.status_code, 200, resent.data)
        completed = self.manager_client.post(
            f"/api/v2/support/document-packages/{package['id']}/complete/",
            {"manager_note": "Received by employer."},
            format="json",
        )
        self.assertEqual(completed.status_code, 200, completed.data)
        self.assertEqual(completed.data["document_package"]["status"], "completed")
        item = DocumentRequestPackage.objects.get(public_id=package["id"])
        self.assertEqual(DocumentRequestPackageEvent.objects.filter(package=item).count(), 5)

    def test_creation_rejects_files_and_unknown_fields(self):
        response = self.manager_client.post(
            f"/api/v2/support/organizations/{self.organization.public_id}/document-packages/",
            {
                "connection_id": str(self.connection.public_id),
                "requested_items": [{"type": "passport"}],
                "attachment_url": "https://unsafe.example/passport.jpg",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(DocumentRequestPackage.objects.exists())

    def test_scoped_document_manager_can_create_package_from_worker_card(self):
        self.client.force_login(self.manager)
        url = f"/employer/support/workers/{self.connection.public_id}/"
        opened = self.client.get(url)
        self.assertEqual(opened.status_code, 200)
        self.assertContains(opened, "Documents by e-mail")

        created = self.client.post(
            url,
            {
                "action": "document_package_create",
                "document_type": ["passport", "visa", "custom"],
                "custom_document_label": "Employment form",
                "additional_instructions": "Use the account code in the e-mail subject.",
            },
        )
        self.assertEqual(created.status_code, 302)
        package = DocumentRequestPackage.objects.get()
        self.assertEqual(package.recipient_email, "documents@agency.example")
        self.assertEqual([item["type"] for item in package.requested_items], ["passport", "visa", "custom"])

    def test_worker_card_explains_missing_verified_document_email(self):
        self.organization.verified_document_email = ""
        self.organization.save(update_fields=["verified_document_email", "updated_at"])
        self.client.force_login(self.manager)
        url = f"/employer/support/workers/{self.connection.public_id}/"

        response = self.client.post(
            url,
            {
                "action": "document_package_create",
                "document_type": ["passport"],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "The company has no verified document e-mail. Add the company address and try again.",
        )
        self.assertFalse(DocumentRequestPackage.objects.exists())
