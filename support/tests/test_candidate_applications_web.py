from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from support.models import (
    ApplicationDecisionEvent,
    SupportAccessGrant,
    SupportApplication,
    SupportConnection,
    SupportVacancy,
)
from support.services.conversations import open_manager_conversation
from support.services.organizations import activate_organization, create_organization
from support.services.pipeline import submit_application


@override_settings(SUPPORT_FEATURE_ENABLED=True)
class CandidateApplicationsWorkspaceTests(TestCase):
    """The employer can take one real application through the whole pipeline."""

    def setUp(self):
        self.operator = User.objects.create_user(
            username="candidate-web-operator",
            email="candidate-web-operator@example.com",
            password="password",
            is_staff=True,
        )
        self.owner = User.objects.create_user(
            username="candidate-web-owner",
            email="candidate-web-owner@example.com",
            password="password",
            first_name="Maria",
            last_name="Manager",
        )
        self.candidate = User.objects.create_user(
            username="candidate-web-candidate",
            email="candidate-web-candidate@example.com",
            password="password",
            first_name="Pavel",
            last_name="Candidate",
        )
        self.organization, _ = create_organization(
            jobhub_operator=self.operator,
            legal_name="Candidate Flow Agency sp. z o.o.",
            display_name="Candidate Flow Agency",
            owner_email=self.owner.email,
        )
        activate_organization(
            jobhub_operator=self.operator,
            organization=self.organization,
        )
        self.vacancy = SupportVacancy.objects.create(
            organization=self.organization,
            internal_title="Warehouse worker in the Netherlands",
            status=SupportVacancy.STATUS_PUBLISHED,
            published_at=timezone.now(),
            created_by=self.owner,
        )
        SupportAccessGrant.objects.create(
            user=self.candidate,
            organization=self.organization,
            granted_by=self.operator,
            ends_at=timezone.now() + timedelta(days=30),
            reason=SupportAccessGrant.REASON_TECHNICAL,
        )
        self.application = submit_application(
            candidate=self.candidate,
            vacancy=self.vacancy,
            preferred_language="ru",
            citizenship_country_code="BY",
            current_country_code="PL",
            availability_note="Готов приступить в следующем месяце.",
            partner_reference_code="",
            consent_version="support-application-v1",
        )
        self.client.force_login(self.owner)
        self.client.cookies["jobhub_employer_lang"] = "ru"
        self.url = (
            reverse("support:candidate-applications")
            + f"?organization={self.organization.public_id}"
        )

    def post_action(self, payload, *, status_filter="open"):
        data = {"filter": status_filter, **payload}
        return self.client.post(self.url, data=data, follow=True)

    def test_manager_reviews_and_moves_candidate_to_active_worker(self):
        opened = self.client.get(self.url)
        self.assertEqual(opened.status_code, 200)
        self.assertContains(opened, "Pavel Candidate")
        self.assertContains(opened, "Заявки и оформление")
        self.assertContains(opened, "Запросы")

        clarified = self.post_action(
            {
                "action": "application_clarify",
                "application_id": self.application.public_id,
                "note": "Уточните дату приезда.",
            }
        )
        self.assertEqual(clarified.status_code, 200)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, SupportApplication.STATUS_UNDER_REVIEW)
        self.assertTrue(
            ApplicationDecisionEvent.objects.filter(
                application=self.application,
                action=ApplicationDecisionEvent.ACTION_CLARIFICATION_REQUESTED,
                note="Уточните дату приезда.",
            ).exists()
        )

        approved = self.post_action(
            {
                "action": "application_approve",
                "application_id": self.application.public_id,
            },
            status_filter="approved",
        )
        self.assertEqual(approved.status_code, 200)
        self.application.refresh_from_db()
        connection = self.application.support_connection
        self.assertEqual(self.application.status, SupportApplication.STATUS_APPROVED)
        self.assertEqual(connection.stage, SupportConnection.STAGE_MANAGER)

        conversation, created = open_manager_conversation(
            candidate=self.candidate,
            connection=connection,
        )
        self.assertTrue(created)
        approved_page = self.client.get(f"{self.url}&filter=approved")
        self.assertContains(approved_page, "Открыть чат")
        self.assertContains(approved_page, str(conversation.public_id))

        documents = self.post_action(
            {
                "action": "connection_documents",
                "connection_id": connection.public_id,
            },
            status_filter="approved",
        )
        self.assertEqual(documents.status_code, 200)
        connection.refresh_from_db()
        self.assertEqual(connection.stage, SupportConnection.STAGE_DOCUMENTS)

        workers_url = (
            reverse("support:workers")
            + f"?organization={self.organization.public_id}"
        )
        workers_page = self.client.get(workers_url)
        self.assertEqual(workers_page.status_code, 200)
        self.assertNotContains(workers_page, "Pavel Candidate")

        processing_page = self.client.get(f"{self.url}&view=processing")
        self.assertEqual(processing_page.status_code, 200)
        self.assertContains(processing_page, "Оформление")
        self.assertContains(processing_page, "Pavel Candidate")
        self.assertContains(processing_page, "Ожидает оформления")
        self.assertContains(processing_page, "Запросы документов")

        coordinator = self.post_action(
            {
                "action": "connection_coordinator",
                "connection_id": connection.public_id,
            },
            status_filter="approved",
        )
        self.assertEqual(coordinator.status_code, 200)
        connection.refresh_from_db()
        self.assertEqual(connection.stage, SupportConnection.STAGE_COORDINATOR)

        coordinator_workers_page = self.client.get(workers_url)
        self.assertContains(coordinator_workers_page, "Pavel Candidate")

        active = self.post_action(
            {
                "action": "connection_active_worker",
                "connection_id": connection.public_id,
            },
            status_filter="approved",
        )
        self.assertEqual(active.status_code, 200)
        connection.refresh_from_db()
        self.assertEqual(connection.stage, SupportConnection.STAGE_ACTIVE_WORKER)

        final_page = self.client.get(f"{self.url}&filter=approved")
        self.assertContains(final_page, "Активный работник")
        self.assertContains(final_page, "История заявки и этапов")

    def test_clarification_and_decline_require_manager_note(self):
        missing_note = self.post_action(
            {
                "action": "application_decline",
                "application_id": self.application.public_id,
                "note": "",
            }
        )
        self.assertContains(
            missing_note,
            "Для уточнения или отказа укажите комментарий менеджера.",
        )
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, SupportApplication.STATUS_SUBMITTED)

        declined = self.post_action(
            {
                "action": "application_decline",
                "application_id": self.application.public_id,
                "note": "Сейчас нет подходящего места.",
            },
            status_filter="closed",
        )
        self.assertEqual(declined.status_code, 200)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, SupportApplication.STATUS_DECLINED)
        self.assertContains(declined, "Заявка отклонена.")

    def test_onboarding_tab_can_create_manager_chat(self):
        self.post_action(
            {
                "action": "application_approve",
                "application_id": self.application.public_id,
            },
            status_filter="approved",
        )
        connection = self.application.support_connection
        self.post_action(
            {
                "action": "connection_documents",
                "connection_id": connection.public_id,
            },
            status_filter="approved",
        )

        processing_page = self.client.get(f"{self.url}&view=processing")
        self.assertContains(processing_page, "Открыть чат")

        opened_chat = self.post_action(
            {
                "action": "connection_chat",
                "connection_id": connection.public_id,
                "view": "processing",
            },
            status_filter="approved",
        )
        self.assertEqual(opened_chat.status_code, 200)
        self.assertTrue(connection.conversations.filter(archived_at__isnull=True).exists())
        self.assertIn("/employer/support/conversations/", opened_chat.request["PATH_INFO"])

    def test_candidate_can_answer_manager_clarification_once(self):
        self.post_action(
            {
                "action": "application_clarify",
                "application_id": self.application.public_id,
                "note": "Сколько вам полных лет?",
            }
        )

        api_client = APIClient()
        api_client.force_authenticate(self.candidate)
        response = api_client.post(
            f"/api/v2/support/applications/{self.application.public_id}/clarification-response/",
            {"answer": "Мне 34 года."},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["application"]["clarification"]["question"], "Сколько вам полных лет?")
        self.assertEqual(response.data["application"]["clarification"]["answer"], "Мне 34 года.")
        self.assertFalse(response.data["application"]["clarification"]["requires_response"])
        self.assertTrue(
            ApplicationDecisionEvent.objects.filter(
                application=self.application,
                action=ApplicationDecisionEvent.ACTION_CLARIFICATION_ANSWERED,
                actor=self.candidate,
                note="Мне 34 года.",
            ).exists()
        )

        duplicate = api_client.post(
            f"/api/v2/support/applications/{self.application.public_id}/clarification-response/",
            {"answer": "Повторный ответ."},
            format="json",
        )
        self.assertEqual(duplicate.status_code, 400)

        manager_page = self.client.get(self.url)
        self.assertContains(manager_page, "Кандидат ответил на уточнение")
        self.assertContains(manager_page, "Мне 34 года.")

    def test_manager_sees_structured_answers_and_can_filter_them(self):
        self.application.questionnaire_version = "support-questionnaire-v2"
        self.application.questionnaire_answers = {
            "adult_confirmed": True,
            "legal_status": "polish_work_visa",
            "current_city": "Warszawa",
            "available_from": "2026-09-01",
            "planned_duration": "6_12m",
            "experience_sectors": ["warehouse", "logistics"],
            "experience_duration": "1_3y",
            "english_level": "instructions",
            "polish_level": "conversation",
            "dutch_level": "none",
            "has_driving_license": True,
            "driving_license_categories": ["B"],
            "qualifications": ["forklift"],
            "work_conditions": {
                "standing": "yes", "repetitive": "yes", "lifting": "discuss",
                "cold": "no", "outdoor": "discuss", "night": "yes",
                "long_shift": "discuss", "height": "no",
            },
            "shift_preferences": ["day", "night"],
            "needs_housing": True,
            "needs_transport": False,
            "travelling_with_partner": False,
            "safety_policy_accepted": True,
        }
        self.application.save(update_fields=["questionnaire_version", "questionnaire_answers"])

        matching = self.client.get(f"{self.url}&experience=warehouse&license=B&needs_housing=yes")
        self.assertEqual(matching.status_code, 200)
        self.assertContains(matching, "Pavel Candidate")
        self.assertContains(matching, "Польская рабочая виза")
        self.assertContains(matching, "Погрузчик")
        self.assertContains(matching, "Ответы анкеты")
        self.assertContains(matching, "Документы и готовность")
        self.assertContains(matching, "Опыт работы")
        self.assertContains(matching, "Языки")
        self.assertContains(matching, "Права и квалификации")
        self.assertContains(matching, "Условия и смены")
        self.assertContains(matching, "Переезд и быт")
        self.assertContains(matching, 'name="available_by" value=""')

        excluded = self.client.get(f"{self.url}&experience=construction")
        self.assertNotContains(excluded, "Pavel Candidate")
