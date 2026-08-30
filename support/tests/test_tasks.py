from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from support.models import (
    AnnouncementAcknowledgement,
    ContentTemplate,
    NotificationOutbox,
    SupportAccessGrant,
    SupportApplication,
    SupportConnection,
    SupportVacancy,
    TaskAssignment,
    OrganizationMembership,
    PermissionGrant,
    WorkerAccessScope,
    WorkerTask,
)
from support.permission_codes import TASK_MANAGE, WORKER_VIEW
from support.services.organizations import create_organization


@override_settings(SUPPORT_FEATURE_ENABLED=True)
class SupportTaskAndAnnouncementTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(
            username="task-operator",
            email="task-operator@example.com",
            password="password",
            is_staff=True,
        )
        self.owner = User.objects.create_user(
            username="task-owner",
            email="task-owner@example.com",
            password="password",
        )
        self.worker = User.objects.create_user(
            username="task-worker",
            first_name="Oleh",
            last_name="Worker",
            email="task-worker@example.com",
            password="password",
        )
        self.other_worker = User.objects.create_user(
            username="task-other-worker",
            first_name="Iryna",
            last_name="Other",
            email="task-other-worker@example.com",
            password="password",
        )
        self.organization, _ = create_organization(
            jobhub_operator=self.operator,
            legal_name="Task Agency sp. z o.o.",
            display_name="Task Agency",
            owner_email=self.owner.email,
        )
        self.connection = self._connection_for(self.worker, "main")
        self.other_connection = self._connection_for(self.other_worker, "other")
        for worker in (self.worker, self.other_worker):
            SupportAccessGrant.objects.create(
                user=worker,
                organization=self.organization,
                granted_by=self.operator,
                ends_at=timezone.now() + timedelta(days=7),
                reason=SupportAccessGrant.REASON_TECHNICAL,
            )
        self.owner_client = APIClient()
        self.owner_client.force_authenticate(self.owner)
        self.worker_client = APIClient()
        self.worker_client.force_authenticate(self.worker)
        self.other_worker_client = APIClient()
        self.other_worker_client.force_authenticate(self.other_worker)
        self.organization_url = f"/api/v2/support/organizations/{self.organization.public_id}"

    def _connection_for(self, worker, suffix):
        vacancy = SupportVacancy.objects.create(
            organization=self.organization,
            internal_title=f"Task vacancy {suffix}",
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
            stage=SupportConnection.STAGE_ACTIVE_WORKER,
        )

    @staticmethod
    def _task_translations():
        return {
            "ru": {"title": "Встреча", "instructions": "Свяжитесь с координатором."},
            "en": {"title": "Meeting", "instructions": "Contact the coordinator."},
            "pl": {"title": "Spotkanie", "instructions": "Skontaktuj się z koordynatorem."},
            "uk": {"title": "Зустріч", "instructions": "Зв’яжіться з координатором."},
        }

    @staticmethod
    def _announcement_translations():
        return {
            "ru": {"title": "Обновление", "body": "В пятницу изменится время выезда."},
            "en": {"title": "Update", "body": "Departure time changes on Friday."},
            "pl": {"title": "Aktualizacja", "body": "W piątek zmieni się godzina wyjazdu."},
            "uk": {"title": "Оновлення", "body": "У п’ятницю зміниться час виїзду."},
        }

    def test_task_is_published_per_worker_and_completed_then_confirmed(self):
        created = self.owner_client.post(
            f"{self.organization_url}/worker-tasks/",
            {
                "source_language": "ru",
                "translations": self._task_translations(),
                "priority": "important",
                "context_kind": "arrival",
                "connection_ids": [str(self.connection.public_id)],
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        task_id = created.data["worker_task"]["id"]
        self.assertEqual(created.data["worker_task"]["state"], "draft")
        self.assertFalse(TaskAssignment.objects.filter(task__public_id=task_id).first().task.published_at)

        published = self.owner_client.post(f"/api/v2/support/worker-tasks/{task_id}/publish/", {})
        self.assertEqual(published.status_code, 200, published.data)
        self.assertEqual(WorkerTask.objects.get(public_id=task_id).state, "published")
        notification = NotificationOutbox.objects.get(notification_code="worker_task.published")
        self.assertEqual(notification.recipient, self.worker)
        self.assertFalse(notification.push_requested)

        worker_list = self.worker_client.get(
            f"/api/v2/support/connections/{self.connection.public_id}/tasks/mine/"
        )
        self.assertEqual(worker_list.status_code, 200, worker_list.data)
        self.assertEqual(len(worker_list.data["results"]), 1)
        assignment_id = worker_list.data["results"][0]["id"]
        self.assertEqual(worker_list.data["results"][0]["translations"]["uk"]["title"], "Зустріч")

        other_list = self.other_worker_client.get(
            f"/api/v2/support/connections/{self.other_connection.public_id}/tasks/mine/"
        )
        self.assertEqual(other_list.status_code, 200, other_list.data)
        self.assertEqual(other_list.data["results"], [])

        started = self.worker_client.post(
            f"/api/v2/support/task-assignments/{assignment_id}/start/",
            {},
            format="json",
        )
        self.assertEqual(started.status_code, 200, started.data)
        self.assertEqual(started.data["task_assignment"]["status"], "in_progress")
        completed = self.worker_client.post(
            f"/api/v2/support/task-assignments/{assignment_id}/complete/",
            {"worker_note": "Готово"},
            format="json",
        )
        self.assertEqual(completed.status_code, 200, completed.data)
        self.assertEqual(completed.data["task_assignment"]["status"], "completed_by_worker")
        confirmed = self.owner_client.post(
            f"/api/v2/support/task-assignments/{assignment_id}/confirm/",
            {},
            format="json",
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.data)
        self.assertEqual(confirmed.data["task_assignment"]["status"], "confirmed")
        status_notification = NotificationOutbox.objects.get(
            notification_code="worker_task.status_changed"
        )
        self.assertFalse(status_notification.push_requested)

    def test_announcement_is_scoped_and_important_acknowledgement_is_not_signature(self):
        created = self.owner_client.post(
            f"{self.organization_url}/announcements/",
            {
                "source_language": "ru",
                "translations": self._announcement_translations(),
                "importance": "important",
                "requires_acknowledgement": True,
                "connection_ids": [str(self.connection.public_id)],
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        announcement_id = created.data["announcement"]["id"]
        published = self.owner_client.post(
            f"/api/v2/support/announcements/{announcement_id}/publish/",
            {},
            format="json",
        )
        self.assertEqual(published.status_code, 200, published.data)
        notification = NotificationOutbox.objects.get(notification_code="announcement.published")
        self.assertEqual(notification.recipient, self.worker)
        self.assertFalse(notification.push_requested)

        worker_list = self.worker_client.get(
            f"/api/v2/support/connections/{self.connection.public_id}/announcements/mine/"
        )
        self.assertEqual(worker_list.status_code, 200, worker_list.data)
        self.assertEqual(len(worker_list.data["results"]), 1)
        item = worker_list.data["results"][0]
        self.assertEqual(item["translations"]["pl"]["title"], "Aktualizacja")
        self.assertIsNone(item["acknowledged_at"])
        self.assertTrue(item["requires_acknowledgement"])

        other_list = self.other_worker_client.get(
            f"/api/v2/support/connections/{self.other_connection.public_id}/announcements/mine/"
        )
        self.assertEqual(other_list.status_code, 200, other_list.data)
        self.assertEqual(other_list.data["results"], [])

        acknowledged = self.worker_client.post(
            f"/api/v2/support/announcement-recipients/{item['recipient_id']}/acknowledge/",
            {},
            format="json",
        )
        self.assertEqual(acknowledged.status_code, 200, acknowledged.data)
        self.assertIsNotNone(acknowledged.data["announcement"]["acknowledged_at"])
        receipt = AnnouncementAcknowledgement.objects.get(public_id=item["recipient_id"])
        self.assertEqual(receipt.acknowledged_by, self.worker)

    def test_multilingual_content_template_is_staff_only_and_scope_safe(self):
        template_url = f"{self.organization_url}/content-templates/"
        workspace_url = f"{self.organization_url}/content-workspace/"
        task_template = self.owner_client.post(
            template_url,
            {
                "name": "Arrival reminder",
                "kind": "task",
                "source_language": "ru",
                "translations": {
                    language: {"title": item["title"], "body": item["instructions"]}
                    for language, item in self._task_translations().items()
                },
            },
            format="json",
        )
        self.assertEqual(task_template.status_code, 201, task_template.data)
        self.assertEqual(task_template.data["content_template"]["kind"], "task")
        self.assertEqual(ContentTemplate.objects.count(), 1)

        announcement_template = self.owner_client.post(
            template_url,
            {
                "name": "Transport update",
                "kind": "announcement",
                "source_language": "ru",
                "translations": self._announcement_translations(),
            },
            format="json",
        )
        self.assertEqual(announcement_template.status_code, 201, announcement_template.data)

        scoped_manager = User.objects.create_user(
            username="template-scoped-manager",
            email="template-scoped-manager@example.com",
            password="password",
        )
        membership = OrganizationMembership.objects.create(
            organization=self.organization,
            user=scoped_manager,
            display_role="Coordinator",
            created_by=self.owner,
            accepted_at=timezone.now(),
        )
        PermissionGrant.objects.create(
            membership=membership,
            permission_code=TASK_MANAGE,
            granted_by=self.owner,
        )
        WorkerAccessScope.objects.create(
            membership=membership,
            connection=self.connection,
            granted_by=self.owner,
        )
        scoped_client = APIClient()
        scoped_client.force_authenticate(scoped_manager)

        workspace = scoped_client.get(workspace_url)
        self.assertEqual(workspace.status_code, 200, workspace.data)
        self.assertTrue(workspace.data["permissions"]["task_manage"])
        self.assertFalse(workspace.data["permissions"]["announcement_manage"])
        self.assertEqual(len(workspace.data["connections"]), 1)
        self.assertEqual(workspace.data["connections"][0]["id"], str(self.connection.public_id))
        self.assertEqual(
            [item["name"] for item in workspace.data["templates"]],
            ["Arrival reminder"],
        )

        forbidden = scoped_client.post(
            template_url,
            {
                "name": "No right to announce",
                "kind": "announcement",
                "source_language": "ru",
                "translations": self._announcement_translations(),
            },
            format="json",
        )
        self.assertEqual(forbidden.status_code, 403, forbidden.data)

        # A template is wording only: it never creates a task, a recipient,
        # a notification or a published worker-facing item by itself.
        self.assertEqual(WorkerTask.objects.count(), 0)
        self.assertEqual(NotificationOutbox.objects.count(), 0)

    def test_staff_bootstrap_and_workspace_summary_are_organization_scoped(self):
        """A staff account opens the mobile work mode without worker access."""

        bootstrap = self.owner_client.get("/api/v2/support/bootstrap/")
        self.assertEqual(bootstrap.status_code, 200, bootstrap.data)
        self.assertEqual(bootstrap.data["mode"], "staff")
        self.assertEqual(len(bootstrap.data["staff_memberships"]), 1)
        self.assertEqual(
            bootstrap.data["staff_memberships"][0]["id"],
            str(self.organization.public_id),
        )

        summary = self.owner_client.get(
            f"{self.organization_url}/workspace-summary/"
        )
        self.assertEqual(summary.status_code, 200, summary.data)
        self.assertEqual(summary.data["organization"]["id"], str(self.organization.public_id))
        self.assertTrue(summary.data["permissions"]["worker_view"])
        self.assertTrue(summary.data["permissions"]["pipeline_review"])
        self.assertEqual(summary.data["counts"]["workers"], 2)
        self.assertEqual(summary.data["counts"]["pending_applications"], 0)
        self.assertEqual(summary.data["counts"]["onboarding_candidates"], 0)

        directory = self.owner_client.get(
            f"{self.organization_url}/connections/?q=Oleh"
        )
        self.assertEqual(directory.status_code, 200, directory.data)
        self.assertEqual(len(directory.data["results"]), 1)
        self.assertEqual(
            directory.data["results"][0]["candidate"]["display_name"],
            "Oleh Worker",
        )

        scoped_manager = User.objects.create_user(
            username="task-scoped-manager",
            email="task-scoped-manager@example.com",
            password="password",
        )
        scoped_membership = OrganizationMembership.objects.create(
            organization=self.organization,
            user=scoped_manager,
            display_role="Transport coordinator",
            created_by=self.owner,
            accepted_at=timezone.now(),
        )
        PermissionGrant.objects.create(
            membership=scoped_membership,
            permission_code=WORKER_VIEW,
            granted_by=self.owner,
        )
        WorkerAccessScope.objects.create(
            membership=scoped_membership,
            connection=self.connection,
            granted_by=self.owner,
        )
        scoped_client = APIClient()
        scoped_client.force_authenticate(scoped_manager)
        scoped_directory = scoped_client.get(f"{self.organization_url}/connections/")
        self.assertEqual(scoped_directory.status_code, 200, scoped_directory.data)
        self.assertEqual(len(scoped_directory.data["results"]), 1)
        self.assertEqual(
            scoped_directory.data["results"][0]["id"],
            str(self.connection.public_id),
        )
