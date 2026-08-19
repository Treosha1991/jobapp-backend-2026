from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from jobs.models import Vacancy

from support.models import (
    ApplicationDecisionEvent,
    EmploymentExclusivityLock,
    SupportApplication,
    SupportConnection,
    SupportConversation,
)
from support.services.organizations import create_organization


def bot_content(title="Warehouse helper"):
    return {
        language: {
            "title": title,
            "intro": "Simple information about the vacancy.",
            "steps": ["Read the information", "Send an application"],
            "faq": [{"question": "What happens next?", "answer": "A manager reviews it."}],
        }
        for language in ("ru", "en", "pl", "uk")
    }


@override_settings(SUPPORT_FEATURE_ENABLED=True)
class SupportPipelineTests(TestCase):
    def setUp(self):
        self.operator = User.objects.create_user(
            username="pipeline-operator",
            email="pipeline-operator@example.com",
            password="password",
            is_staff=True,
        )
        self.owner = User.objects.create_user(
            username="pipeline-owner",
            email="pipeline-owner@example.com",
            password="password",
        )
        self.other_owner = User.objects.create_user(
            username="pipeline-other-owner",
            email="pipeline-other-owner@example.com",
            password="password",
        )
        self.candidate = User.objects.create_user(
            username="pipeline-candidate",
            email="pipeline-candidate@example.com",
            password="password",
        )
        self.outsider = User.objects.create_user(
            username="pipeline-outsider",
            email="pipeline-outsider@example.com",
            password="password",
        )
        self.organization, _ = create_organization(
            jobhub_operator=self.operator,
            legal_name="Pipeline Agency sp. z o.o.",
            display_name="Pipeline Agency",
            owner_email=self.owner.email,
        )
        self.other_organization, _ = create_organization(
            jobhub_operator=self.operator,
            legal_name="Other Pipeline Agency sp. z o.o.",
            display_name="Other Pipeline Agency",
            owner_email=self.other_owner.email,
        )
        self.owner_client = APIClient()
        self.owner_client.force_authenticate(self.owner)
        self.other_owner_client = APIClient()
        self.other_owner_client.force_authenticate(self.other_owner)
        self.operator_client = APIClient()
        self.operator_client.force_authenticate(self.operator)
        self.candidate_client = APIClient()
        self.candidate_client.force_authenticate(self.candidate)
        self.outsider_client = APIClient()
        self.outsider_client.force_authenticate(self.outsider)
        for organization in (self.organization, self.other_organization):
            activated = self.operator_client.post(
                f"/api/v2/support/operator/organizations/{organization.public_id}/activate/"
            )
            self.assertEqual(activated.status_code, 200, activated.data)

    def create_published_vacancy(
        self,
        *,
        organization=None,
        client=None,
        title="Warehouse helper",
        public_vacancy=None,
    ):
        organization = organization or self.organization
        client = client or self.owner_client
        created = client.post(
            f"/api/v2/support/organizations/{organization.public_id}/vacancies/",
            {
                "internal_title": title,
                "internal_position_limit": 3,
                "public_vacancy_id": public_vacancy.id if public_vacancy else None,
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        vacancy_id = created.data["vacancy"]["id"]
        revision = client.post(
            f"/api/v2/support/vacancies/{vacancy_id}/bot-revisions/",
            {"source_language": "ru", "content": bot_content(title)},
            format="json",
        )
        self.assertEqual(revision.status_code, 201, revision.data)
        published_revision = client.post(
            f"/api/v2/support/bot-revisions/{revision.data['bot_revision']['id']}/publish/"
        )
        self.assertEqual(published_revision.status_code, 200, published_revision.data)
        published_vacancy = client.post(f"/api/v2/support/vacancies/{vacancy_id}/publish/")
        self.assertEqual(published_vacancy.status_code, 200, published_vacancy.data)
        return vacancy_id

    def test_public_vacancy_workflow_exposes_bot_and_candidate_status(self):
        public_vacancy = Vacancy.objects.create(
            created_by=self.owner,
            title="Public warehouse vacancy",
            country="NL",
            city="Lelystad",
            category="warehouse",
            employment_type="full",
            description="A public vacancy connected to Support.",
            housing_type="none",
            source="direct",
            is_approved=True,
            published_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=30),
        )
        support_vacancy_id = self.create_published_vacancy(
            public_vacancy=public_vacancy,
        )

        before = self.candidate_client.get(
            f"/api/v2/support/public-vacancies/{public_vacancy.id}/workflow/?language=ru"
        )
        self.assertEqual(before.status_code, 200, before.data)
        self.assertEqual(before.data["workflow"]["id"], support_vacancy_id)
        self.assertEqual(before.data["workflow"]["vacancy_title"], public_vacancy.title)
        self.assertEqual(before.data["bot"]["content"]["title"], "Warehouse helper")
        self.assertIsNone(before.data["application"])

        submitted = self.submit_application(support_vacancy_id)
        self.assertEqual(submitted.status_code, 201, submitted.data)
        after = self.candidate_client.get(
            f"/api/v2/support/public-vacancies/{public_vacancy.id}/workflow/?language=ru"
        )
        self.assertEqual(after.status_code, 200, after.data)
        self.assertEqual(after.data["application"]["status"], "submitted")
        self.assertEqual(
            after.data["application"]["public_vacancy_id"],
            public_vacancy.id,
        )

    def submit_application(self, vacancy_id, *, extra=None):
        payload = {
            "preferred_language": "ru",
            "citizenship_country_code": "BY",
            "current_country_code": "PL",
            "availability_note": "Available next month.",
            "consent_version": "support-application-v1",
            "consent_accepted": True,
        }
        payload.update(extra or {})
        return self.candidate_client.post(
            f"/api/v2/support/vacancies/{vacancy_id}/applications/",
            payload,
            format="json",
        )

    def test_published_bot_hides_internal_limit_and_application_rejects_document_fields(self):
        vacancy_id = self.create_published_vacancy()

        bot = self.candidate_client.get(f"/api/v2/support/vacancies/{vacancy_id}/bot/?language=ru")

        self.assertEqual(bot.status_code, 200)
        self.assertEqual(bot.data["bot"]["content"]["title"], "Warehouse helper")
        self.assertNotIn("internal_position_limit", str(bot.data))
        self.assertNotIn("internal_title", str(bot.data))

        rejected = self.submit_application(vacancy_id, extra={"passport": "123456"})

        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(SupportApplication.objects.count(), 0)

    def test_structured_questionnaire_is_validated_and_returned_to_staff(self):
        vacancy_id = self.create_published_vacancy()
        questionnaire = {
            "adult_confirmed": True,
            "legal_status": "polish_work_visa",
            "document_valid_until": "2027-05-01",
            "current_city": "Warsaw",
            "available_from": "2026-09-01",
            "planned_duration": "6_12m",
            "experience_sectors": ["warehouse", "logistics"],
            "experience_duration": "1_3y",
            "work_countries": ["PL", "NL"],
            "last_position": "Picker",
            "english_level": "instructions",
            "polish_level": "conversation",
            "dutch_level": "none",
            "has_driving_license": True,
            "driving_license_categories": ["B"],
            "driving_license_valid_in_eu": True,
            "driving_experience": "over_3y",
            "willing_crew_driver": True,
            "has_own_car": False,
            "qualifications": ["forklift"],
            "work_conditions": {
                key: "yes"
                for key in (
                    "standing", "repetitive", "lifting", "cold", "outdoor",
                    "night", "long_shift", "height",
                )
            },
            "shift_preferences": ["day", "rotating"],
            "overtime_willing": "discuss",
            "unavailable_dates_note": "",
            "needs_housing": True,
            "needs_transport": True,
            "travelling_with_partner": False,
            "shared_room_preference": "yes",
            "planned_move_in": "2026-08-31",
            "safety_policy_accepted": True,
            "additional_note": "Ready for cold storage.",
        }

        response = self.submit_application(
            vacancy_id,
            extra={
                "questionnaire_version": "support-questionnaire-v2",
                "questionnaire": questionnaire,
                "consent_version": "support-application-v2",
            },
        )

        self.assertEqual(response.status_code, 201, response.data)
        application = SupportApplication.objects.get(public_id=response.data["application"]["id"])
        self.assertEqual(application.questionnaire_version, "support-questionnaire-v2")
        self.assertEqual(application.questionnaire_answers["available_from"], "2026-09-01")
        queue = self.owner_client.get(
            f"/api/v2/support/organizations/{self.organization.public_id}/applications/"
        )
        self.assertEqual(queue.status_code, 200, queue.data)
        self.assertEqual(queue.data["results"][0]["questionnaire"]["english_level"], "instructions")

    def test_declined_application_can_be_resubmitted_only_after_one_hour(self):
        vacancy_id = self.create_published_vacancy()
        submitted = self.submit_application(vacancy_id)
        self.assertEqual(submitted.status_code, 201, submitted.data)
        application_id = submitted.data["application"]["id"]

        declined = self.owner_client.post(
            f"/api/v2/support/applications/{application_id}/decline/",
            {"note": "Try again later."},
            format="json",
        )
        self.assertEqual(declined.status_code, 200, declined.data)

        mine = self.candidate_client.get("/api/v2/support/applications/mine/")
        self.assertEqual(mine.status_code, 200, mine.data)
        latest = mine.data["results"][0]
        self.assertFalse(latest["can_resubmit"])
        self.assertIsNotNone(latest["resubmit_available_at"])
        self.assertGreater(latest["resubmit_wait_seconds"], 0)

        blocked = self.submit_application(vacancy_id)
        self.assertEqual(blocked.status_code, 400, blocked.data)
        self.assertIn("application_resubmit_cooldown", str(blocked.data))

        ApplicationDecisionEvent.objects.filter(
            application__public_id=application_id,
            action=ApplicationDecisionEvent.ACTION_DECLINED,
        ).update(created_at=timezone.now() - timedelta(minutes=61))

        repeated = self.submit_application(vacancy_id)
        self.assertEqual(repeated.status_code, 201, repeated.data)
        self.assertEqual(
            SupportApplication.objects.filter(candidate=self.candidate).latest("revision").revision,
            2,
        )

    def test_structured_questionnaire_rejects_sensitive_shortcut_and_missing_conditions(self):
        vacancy_id = self.create_published_vacancy()
        response = self.submit_application(
            vacancy_id,
            extra={
                "questionnaire_version": "support-questionnaire-v2",
                "questionnaire": {
                    "adult_confirmed": True,
                    "legal_status": "visa_free",
                    "current_city": "Minsk",
                    "available_from": "2026-09-01",
                    "planned_duration": "3_6m",
                    "experience_sectors": ["no_experience"],
                    "experience_duration": "none",
                    "english_level": "none",
                    "polish_level": "none",
                    "dutch_level": "none",
                    "has_driving_license": False,
                    "driving_experience": "none",
                    "willing_crew_driver": False,
                    "has_own_car": False,
                    "work_conditions": {"standing": "yes"},
                    "shift_preferences": ["day"],
                    "overtime_willing": "no",
                    "needs_housing": True,
                    "needs_transport": True,
                    "travelling_with_partner": False,
                    "shared_room_preference": "discuss",
                    "safety_policy_accepted": True,
                    "alcohol_problem": False,
                },
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(SupportApplication.objects.count(), 0)

    def test_approval_never_creates_chat_and_candidate_opens_it_only_after_support_access(self):
        vacancy_id = self.create_published_vacancy()
        application_response = self.submit_application(vacancy_id)
        self.assertEqual(application_response.status_code, 201, application_response.data)
        application_id = application_response.data["application"]["id"]
        self.assertTrue(application_response.data["applicant_reference_code"].startswith("JH-"))

        approved = self.owner_client.post(f"/api/v2/support/applications/{application_id}/approve/")

        self.assertEqual(approved.status_code, 201, approved.data)
        connection_id = approved.data["connection"]["id"]
        self.assertEqual(approved.data["connection"]["stage"], "awaiting_support")
        self.assertEqual(SupportConversation.objects.count(), 0)

        access_denied = self.candidate_client.post(
            f"/api/v2/support/connections/{connection_id}/open-manager-chat/"
        )
        self.assertEqual(access_denied.status_code, 403)

        grant = self.operator_client.post(
            "/api/v2/support/operator/temporary-access-grants/",
            {
                "user_email": self.candidate.email,
                "duration_days": 7,
                "reason": "technical_help",
                "organization_public_id": str(self.organization.public_id),
            },
            format="json",
        )
        self.assertEqual(grant.status_code, 201, grant.data)

        opened = self.candidate_client.post(
            f"/api/v2/support/connections/{connection_id}/open-manager-chat/"
        )

        self.assertEqual(opened.status_code, 201, opened.data)
        conversation_id = opened.data["conversation"]["id"]
        connection = SupportConnection.objects.get(public_id=connection_id)
        self.assertEqual(connection.stage, SupportConnection.STAGE_MANAGER)
        self.assertEqual(SupportConversation.objects.count(), 1)

        attachment_attempt = self.candidate_client.post(
            f"/api/v2/support/conversations/{conversation_id}/messages/send/",
            {
                "body": "I will send a document",
                "original_language": "ru",
                "attachment_url": "https://unsafe.example/document.jpg",
            },
            format="json",
        )
        self.assertEqual(attachment_attempt.status_code, 400)

        sent = self.candidate_client.post(
            f"/api/v2/support/conversations/{conversation_id}/messages/send/",
            {"body": "Hello", "original_language": "en"},
            format="json",
        )
        self.assertEqual(sent.status_code, 201, sent.data)
        self.assertEqual(sent.data["message"]["body"], "Hello")

        unreadable = self.outsider_client.get(
            f"/api/v2/support/conversations/{conversation_id}/messages/"
        )
        self.assertEqual(unreadable.status_code, 404)

    def test_candidate_in_coordinator_stage_cannot_be_approved_by_a_second_firm(self):
        first_vacancy_id = self.create_published_vacancy()
        first_application = self.submit_application(first_vacancy_id)
        self.assertEqual(first_application.status_code, 201, first_application.data)
        grant = self.operator_client.post(
            "/api/v2/support/operator/temporary-access-grants/",
            {
                "user_email": self.candidate.email,
                "duration_days": 7,
                "reason": "technical_help",
                "organization_public_id": str(self.organization.public_id),
            },
            format="json",
        )
        self.assertEqual(grant.status_code, 201, grant.data)
        first_approved = self.owner_client.post(
            f"/api/v2/support/applications/{first_application.data['application']['id']}/approve/"
        )
        self.assertEqual(first_approved.status_code, 201, first_approved.data)
        first_connection_id = first_approved.data["connection"]["id"]

        documents = self.owner_client.post(
            f"/api/v2/support/connections/{first_connection_id}/transition/",
            {"next_stage": "documents_stage"},
            format="json",
        )
        self.assertEqual(documents.status_code, 200, documents.data)
        processing_queue = self.owner_client.get(
            f"/api/v2/support/organizations/{self.organization.public_id}/applications/"
        )
        self.assertEqual(processing_queue.status_code, 200, processing_queue.data)
        self.assertEqual(
            processing_queue.data["processing_results"][0]["connection_id"],
            first_connection_id,
        )
        self.assertTrue(processing_queue.data["permissions"]["document_request"])
        coordinator = self.owner_client.post(
            f"/api/v2/support/connections/{first_connection_id}/transition/",
            {"next_stage": "coordinator_stage"},
            format="json",
        )
        self.assertEqual(coordinator.status_code, 200, coordinator.data)
        self.assertTrue(
            EmploymentExclusivityLock.objects.filter(
                candidate=self.candidate,
                state=EmploymentExclusivityLock.STATE_ACTIVE,
            ).exists()
        )

        second_vacancy_id = self.create_published_vacancy(
            organization=self.other_organization,
            client=self.other_owner_client,
            title="Packing helper",
        )
        second_application = self.submit_application(second_vacancy_id)
        self.assertEqual(second_application.status_code, 201, second_application.data)

        rejected_approval = self.other_owner_client.post(
            f"/api/v2/support/applications/{second_application.data['application']['id']}/approve/"
        )

        self.assertEqual(rejected_approval.status_code, 400)
        self.assertEqual(
            str(rejected_approval.data["application"]),
            "candidate_has_active_support_assignment",
        )
        self.assertEqual(
            SupportApplication.objects.get(
                public_id=second_application.data["application"]["id"]
            ).status,
            SupportApplication.STATUS_SUBMITTED,
        )
