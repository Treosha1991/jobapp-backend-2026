from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from unittest.mock import patch

from .economy import build_contact_access_state, ensure_free_contact_policy
from .currency_catalog import CURRENCY_CODES
from .serializers import VacancyCreateSerializer
from .web_forms import EmployerVacancyForm
from .models import (
    ChatConversation,
    ChatMessage,
    ChatReport,
    EmployerBoardPublishingAuthorization,
    EmployerBoardPublishingEvent,
    ModeratorNotificationDelivery,
    PushDevice,
    UserProfile,
    Vacancy,
    VacancyContactAccessPolicy,
    VacancyModerationAttempt,
)
from .board_publishing import accept_authorization, request_authorization, revoke_authorization
from .service_sources import SERVICE_BOARD_USERNAME


class CurrencyCatalogTests(TestCase):
    def test_catalog_includes_currencies_for_supported_work_destinations(self):
        for code in ("NOK", "ISK", "RSD", "BAM", "MKD", "ALL", "MDL", "TRY"):
            self.assertIn(code, CURRENCY_CODES)
            self.assertIn((code, code), Vacancy.SALARY_CURRENCY_CHOICES)


class VerifiedEmployerApiTests(TestCase):
    def setUp(self):
        self.employer = User.objects.create_user(
            username="verified-employer",
            email="verified-employer@example.com",
            password="password",
        )
        self.viewer = User.objects.create_user(
            username="verified-viewer",
            email="verified-viewer@example.com",
            password="password",
        )
        UserProfile.objects.create(user=self.employer, employer_verified=True)
        UserProfile.objects.create(user=self.viewer)
        self.vacancy = Vacancy.objects.create(
            created_by=self.employer,
            title="Verified employer vacancy",
            country="DE",
            city="Berlin",
            category="warehouse",
            employment_type="full",
            description="A vacancy published by a verified employer.",
            housing_type="none",
            source="direct",
            is_approved=True,
            published_at=timezone.now(),
            expires_at=timezone.now() + timezone.timedelta(days=30),
        )
        self.client = APIClient()
        self.client.force_authenticate(self.viewer)

    def test_verified_employer_flag_is_present_in_vacancy_and_profile_payloads(self):
        vacancies = self.client.get("/api/vacancies/")
        self.assertEqual(vacancies.status_code, 200)
        self.assertTrue(vacancies.data["results"][0]["employer_verified"])

        detail = self.client.get(f"/api/vacancies/{self.vacancy.id}/")
        self.assertEqual(detail.status_code, 200)
        self.assertTrue(detail.data["employer_verified"])

        profile = self.client.get(f"/api/employers/{self.employer.id}/profile/")
        self.assertEqual(profile.status_code, 200)
        self.assertTrue(profile.data["employer"]["is_verified"])


class VacancyPromotionFeedTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="promotion-owner",
            email="promotion-owner@example.com",
            password="password",
        )
        UserProfile.objects.create(user=self.owner)
        self.now = timezone.now()

    def _vacancy(self, *, title, published_at, pinned_from=None, pinned_until=None):
        vacancy = Vacancy.objects.create(
            created_by=self.owner,
            title=title,
            country="DE",
            city="Berlin",
            category="warehouse",
            employment_type="full",
            description="Promotion ordering test vacancy.",
            housing_type="none",
            source="direct",
            is_approved=True,
            expires_at=self.now + timezone.timedelta(days=30),
            pinned_from=pinned_from,
            pinned_until=pinned_until,
        )
        Vacancy.objects.filter(pk=vacancy.pk).update(published_at=published_at)
        return Vacancy.objects.get(pk=vacancy.pk)

    def test_active_pin_is_first_and_exposes_promotion_payload(self):
        regular = self._vacancy(
            title="New regular vacancy",
            published_at=self.now,
        )
        pinned = self._vacancy(
            title="Pinned vacancy",
            published_at=self.now - timezone.timedelta(days=2),
            pinned_from=self.now - timezone.timedelta(hours=1),
            pinned_until=self.now + timezone.timedelta(days=1),
        )
        pinned.promotion_kind = "premium"
        pinned.save(update_fields=["promotion_kind"])

        response = APIClient().get("/api/vacancies/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["id"], pinned.id)
        self.assertTrue(response.data["results"][0]["is_pinned"])
        self.assertEqual(response.data["results"][0]["promotion_kind"], "premium")
        self.assertFalse(
            next(item for item in response.data["results"] if item["id"] == regular.id)[
                "is_pinned"
            ]
        )


class EmployerPortalVacancyWorkflowTests(TestCase):
    """Keep the browser vacancy flow aligned with the mobile submission flow."""

    def setUp(self):
        self.employer = User.objects.create_user(
            username="portal-employer",
            email="portal-employer@example.com",
            password="password",
        )
        self.profile, _ = UserProfile.objects.get_or_create(user=self.employer)
        self.profile.phone_verified = True
        self.profile.phone_e164 = "+48111111111"
        self.profile.save(update_fields=["phone_verified", "phone_e164"])
        self.client.login(username="portal-employer", password="password")

    def _valid_payload(self):
        return {
            "title": "Warehouse worker",
            "country": "DE",
            "city": "Berlin",
            "category": "warehouse",
            "audience_countries": ["UA", "PL"],
            "employment_type": "full",
            "experience_required": "without",
            "salary_from": "15",
            "salary_to": "17",
            "salary_currency": "EUR",
            "salary_tax_type": "netto",
            "salary_hours_month": "168",
            "description": "Warehouse work with accommodation support.",
            "housing_type": "paid",
            "housing_cost": "200",
            "housing_cost_currency": "EUR",
            "housing_cost_period": "month",
            "phone": "+48111111111",
            "source": "direct",
            "telegram_username_1": "jobhub_employer",
        }

    def test_vacancy_form_renders_all_telegram_username_fields(self):
        response = self.client.get("/employer/vacancies/new/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="telegram_username_1"')
        self.assertContains(response, 'name="telegram_username_2"')
        self.assertContains(response, 'name="telegram_username_3"')

    def test_employer_can_accept_and_revoke_board_publishing_request(self):
        authorization = request_authorization(self.employer)

        response = self.client.get("/employer/jobhub-board/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Allow publishing with JobHub")

        accepted = self.client.post(
            "/employer/jobhub-board/",
            {"action": "accept", "confirm_terms": "1"},
        )
        self.assertEqual(accepted.status_code, 302)
        authorization.refresh_from_db()
        self.assertEqual(authorization.status, "active")
        self.assertIsNotNone(authorization.accepted_at)
        active_page = self.client.get("/employer/jobhub-board/")
        self.assertContains(active_page, authorization.board_code)

        revoked = self.client.post("/employer/jobhub-board/", {"action": "revoke"})
        self.assertEqual(revoked.status_code, 302)
        authorization.refresh_from_db()
        self.assertEqual(authorization.status, "revoked")
        self.assertTrue(
            EmployerBoardPublishingEvent.objects.filter(
                authorization=authorization,
                action="revoked",
            ).exists()
        )

    def test_edit_form_keeps_city_that_is_missing_from_the_current_catalog(self):
        vacancy = Vacancy.objects.create(
            created_by=self.employer,
            title="Legacy city vacancy",
            country="DE",
            city="Oldtown",
            category="warehouse",
            employment_type="full",
            description="Existing vacancy description.",
            housing_type="none",
            source="direct",
            expires_at=timezone.now() + timezone.timedelta(days=30),
        )

        form = EmployerVacancyForm(instance=vacancy, user=self.employer, lang="ru")
        self.assertIn(("Oldtown", "Oldtown"), form.fields["city"].choices)

        response = self.client.get(f"/employer/vacancies/{vacancy.pk}/edit/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'const initialCity = "Oldtown";')

    def test_description_up_to_1500_characters_is_accepted_everywhere(self):
        payload = self._valid_payload() | {"description": "x" * 1500}
        web_form = EmployerVacancyForm(data=payload, user=self.employer, lang="ru")
        self.assertTrue(web_form.is_valid(), web_form.errors)

        api_payload = payload.copy()
        api_payload.pop("telegram_username_1")
        serializer = VacancyCreateSerializer(data=api_payload)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_empty_draft_can_be_saved_from_browser(self):
        response = self.client.post("/employer/vacancies/new/", {"save_draft": "1"})

        self.assertEqual(response.status_code, 302)
        vacancy = Vacancy.objects.get(created_by=self.employer)
        self.assertTrue(vacancy.is_editing)
        self.assertFalse(vacancy.is_approved)
        self.assertFalse(vacancy.moderation_attempts.exists())

    @patch("jobs.web_views._notify_moderators_about_pending_vacancy_safe")
    @patch("jobs.web_views._apply_web_submission_action")
    def test_submit_creates_pending_moderation_attempt(self, apply_submission, notify):
        payload = self._valid_payload() | {"submit": "1"}

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post("/employer/vacancies/new/", payload)

        self.assertEqual(response.status_code, 302)
        vacancy = Vacancy.objects.get(created_by=self.employer)
        self.assertFalse(vacancy.is_editing)
        self.assertFalse(vacancy.is_approved)
        self.assertEqual(vacancy.moderation_attempts.count(), 1)
        self.assertEqual(vacancy.moderation_attempts.first().trigger_type, "create")
        self.assertEqual(vacancy.housing_cost, "200 EUR/month")
        self.assertEqual(vacancy.telegram_username, "jobhub_employer")
        apply_submission.assert_called_once()
        notify.assert_called_once_with(vacancy)

    @patch("jobs.web_views._notify_moderators_about_pending_vacancy_safe")
    @patch("jobs.web_views._apply_web_submission_action")
    def test_draft_can_be_edited_and_submitted(self, apply_submission, notify):
        draft = self.client.post("/employer/vacancies/new/", {"save_draft": "1"})
        self.assertEqual(draft.status_code, 302)
        vacancy = Vacancy.objects.get(created_by=self.employer)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"/employer/vacancies/{vacancy.id}/edit/",
                self._valid_payload() | {"submit": "1"},
            )

        self.assertEqual(response.status_code, 302)
        vacancy.refresh_from_db()
        self.assertFalse(vacancy.is_editing)
        self.assertEqual(vacancy.moderation_attempts.count(), 1)
        self.assertEqual(vacancy.moderation_attempts.first().trigger_type, "create")
        apply_submission.assert_called_once()
        notify.assert_called_once_with(vacancy)


class EmployerPortalPasswordResetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="phone-login-user",
            email="phone-login@example.com",
            password="unneeded-password",
        )
        UserProfile.objects.create(
            user=self.user,
            phone_e164="+48123456789",
            phone_verified=True,
        )

    @patch("jobs.web_views._twilio_verify_start", return_value=(True, None, 200))
    def test_login_only_offers_password_recovery_by_phone(self, _start):
        response = self.client.get("/employer/login/")
        self.assertContains(response, 'href="/employer/password-reset/"')
        self.assertNotContains(response, "phone_login_request_code")

        response = self.client.post(
            "/employer/password-reset/",
            {"action": "request_code", "phone": "+48123456789"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session["employer_password_reset_phone"], "+48123456789")

    @patch("jobs.web_views._twilio_verify_check", return_value=(True, None, 200))
    @patch("jobs.web_views._twilio_verify_start", return_value=(True, None, 200))
    def test_verified_phone_can_reset_password(self, _start, _check):
        self.client.post(
            "/employer/password-reset/",
            {"action": "request_code", "phone": "+48123456789"},
        )
        response = self.client.post(
            "/employer/password-reset/",
            {
                "action": "confirm",
                "code": "123456",
                "password": "New-password-123",
                "confirm_password": "New-password-123",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("New-password-123"))

    def test_portal_email_login_uses_the_same_account_as_mobile(self):
        response = self.client.post(
            "/employer/login/",
            {"username": "phone-login@example.com", "password": "unneeded-password"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.id)


class EmployerPortalChatTests(TestCase):
    def setUp(self):
        self.employer = User.objects.create_user(
            username="web-chat-employer",
            email="web-chat-employer@example.com",
            password="password",
        )
        self.candidate = User.objects.create_user(
            username="web-chat-candidate",
            email="web-chat-candidate@example.com",
            password="password",
        )
        UserProfile.objects.create(user=self.employer, nickname="Web Employer")
        UserProfile.objects.create(user=self.candidate, nickname="Web Candidate")
        self.conversation = ChatConversation.objects.create(
            candidate=self.candidate,
            employer=self.employer,
            initial_vacancy_title="Warehouse worker",
        )
        self.incoming = ChatMessage.objects.create(
            conversation=self.conversation,
            sender=self.candidate,
            body="Hello from the app",
        )
        self.conversation.last_message_at = self.incoming.created_at
        self.conversation.save(update_fields=["last_message_at"])
        self.client.login(username="web-chat-employer", password="password")

    def test_employer_can_see_shared_chat_and_read_it(self):
        response = self.client.get("/employer/chats/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Web Candidate")
        self.assertContains(response, "Hello from the app")

        response = self.client.get(f"/employer/chats/{self.conversation.id}/")
        self.assertEqual(response.status_code, 200)
        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.employer_last_read_at, self.incoming.created_at)

    @patch("jobs.web_views._send_chat_push_safe")
    def test_employer_can_reply_from_web_into_shared_conversation(self, notify):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                f"/employer/chats/{self.conversation.id}/",
                {"action": "send", "body": "Reply from browser"},
            )
        self.assertEqual(response.status_code, 302)
        sent = ChatMessage.objects.get(conversation=self.conversation, sender=self.employer)
        self.assertEqual(sent.body, "Reply from browser")
        notify.assert_called_once()


class ChatAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.candidate = User.objects.create_user(
            username="candidate",
            email="candidate@example.com",
            password="password",
        )
        UserProfile.objects.create(user=self.candidate, nickname="Candidate")
        self.employer = User.objects.create_user(
            username="employer",
            email="employer@example.com",
            password="password",
        )
        UserProfile.objects.create(
            user=self.employer,
            nickname="Employer",
            employer_verified=True,
        )
        self.vacancy = Vacancy.objects.create(
            created_by=self.employer,
            title="Warehouse worker",
            country="PL",
            city="Poznan",
            city_code="poznan",
            category="warehouse",
            audience_country_codes="UA",
            employment_type="shift",
            experience_required="without",
            salary="27 PLN netto",
            salary_from=27,
            salary_to=27,
            salary_currency="PLN",
            salary_tax_type="netto",
            salary_hours_month=168,
            description="Visible description",
            housing_type="none",
            phone="+48111111111",
            source="direct",
            creator_token="chat-api-test",
            is_approved=True,
            expires_at=timezone.now() + timezone.timedelta(days=30),
        )

    def _start_chat(self):
        self.client.force_authenticate(user=self.candidate)
        response = self.client.post(
            "/api/chats/start/",
            {"vacancy_id": self.vacancy.id},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        return response.data["conversation"]["id"]

    def test_one_chat_is_reused_from_vacancy_and_employer_profile(self):
        conversation_id = self._start_chat()

        initial = self.client.get(f"/api/chats/{conversation_id}/").data[
            "conversation"
        ]["initial_context"]
        self.assertEqual(initial["employer"]["nickname"], "Employer")
        self.assertTrue(initial["employer"]["is_verified_employer"])
        self.assertEqual(initial["vacancy"]["title"], self.vacancy.title)

        self.assertTrue(
            self.client.get(f"/api/chats/{conversation_id}/").data["conversation"][
                "other_user"
            ]["is_verified_employer"]
        )

        response = self.client.post(
            "/api/chats/start/",
            {"vacancy_id": self.vacancy.id},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["created"])
        self.assertEqual(response.data["conversation"]["id"], conversation_id)

        response = self.client.post(
            "/api/chats/start/",
            {"employer_user_id": self.employer.id},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["conversation"]["id"], conversation_id)
        self.assertEqual(ChatConversation.objects.count(), 1)

    def test_message_unread_read_and_external_link_flag(self):
        conversation_id = self._start_chat()
        response = self.client.post(
            f"/api/chats/{conversation_id}/messages/",
            {
                "body": "Please see https://example.com/job",
                "client_message_id": "candidate-1",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        message_id = response.data["message"]["id"]
        self.assertTrue(response.data["message"]["has_external_links"])

        self.client.force_authenticate(user=self.employer)
        response = self.client.get("/api/chats/unread-count/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["unread_count"], 1)

        response = self.client.post(
            f"/api/chats/{conversation_id}/read/",
            {"last_message_id": message_id},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/chats/unread-count/").data["unread_count"], 0)

    def test_retry_with_same_client_message_id_does_not_duplicate_message(self):
        conversation_id = self._start_chat()
        payload = {"body": "Connection retry safe", "client_message_id": "candidate-retry-1"}
        first = self.client.post(
            f"/api/chats/{conversation_id}/messages/",
            payload,
            format="json",
        )
        second = self.client.post(
            f"/api/chats/{conversation_id}/messages/",
            payload,
            format="json",
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.data["created"])
        self.assertEqual(ChatMessage.objects.filter(conversation_id=conversation_id).count(), 1)

    def test_block_keeps_chat_visible_and_can_be_reversed(self):
        conversation_id = self._start_chat()
        self.client.post(
            f"/api/chats/{conversation_id}/messages/",
            {"body": "Hello"},
            format="json",
        )
        response = self.client.post(f"/api/chats/{conversation_id}/block/", format="json")
        self.assertEqual(response.status_code, 200)
        chat = self.client.get("/api/chats/").data["results"][0]
        self.assertTrue(chat["blocked_by_me"])
        self.assertFalse(chat["can_send"])

        self.client.force_authenticate(user=self.employer)
        response = self.client.post(
            f"/api/chats/{conversation_id}/messages/",
            {"body": "Reply"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

        self.client.force_authenticate(user=self.candidate)
        response = self.client.post(f"/api/chats/{conversation_id}/unblock/", format="json")
        self.assertEqual(response.status_code, 200)

        self.client.force_authenticate(user=self.employer)
        response = self.client.post(
            f"/api/chats/{conversation_id}/messages/",
            {"body": "Reply after unblock"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)

    def test_list_does_not_mark_messages_as_read(self):
        conversation_id = self._start_chat()
        self.client.post(
            f"/api/chats/{conversation_id}/messages/",
            {"body": "Unread until the conversation opens"},
            format="json",
        )
        self.client.force_authenticate(user=self.employer)

        first_list = self.client.get("/api/chats/")
        second_list = self.client.get("/api/chats/")
        self.assertEqual(first_list.data["unread_count"], 1)
        self.assertEqual(second_list.data["unread_count"], 1)
        self.assertEqual(self.client.get("/api/chats/unread-count/").data["unread_count"], 1)

    def test_unread_message_can_be_edited_deleted_and_replied_to(self):
        conversation_id = self._start_chat()
        created = self.client.post(
            f"/api/chats/{conversation_id}/messages/",
            {"body": "Initial message"},
            format="json",
        )
        message_id = created.data["message"]["id"]

        edited = self.client.patch(
            f"/api/chats/{conversation_id}/messages/{message_id}/",
            {"body": "Edited before it is read"},
            format="json",
        )
        self.assertEqual(edited.status_code, 200)
        self.assertTrue(edited.data["message"]["can_modify"])

        reply = self.client.post(
            f"/api/chats/{conversation_id}/messages/",
            {"body": "Replying to the message", "reply_to_message_id": message_id},
            format="json",
        )
        self.assertEqual(reply.status_code, 201)
        self.assertEqual(reply.data["message"]["reply_to"]["id"], message_id)

        deleted = self.client.delete(f"/api/chats/{conversation_id}/messages/{message_id}/")
        self.assertEqual(deleted.status_code, 200)

        detail = self.client.get(f"/api/chats/{conversation_id}/")
        first_message = detail.data["messages"][0]
        self.assertTrue(first_message["is_deleted"])
        self.assertEqual(first_message["body"], "")

    def test_generated_public_nickname_never_uses_email(self):
        anonymous = User.objects.create_user(
            username="anonymous@example.com",
            email="anonymous@example.com",
            password="password",
        )
        profile = UserProfile.objects.create(user=anonymous)
        self.assertEqual(profile.nickname, f"User {1000 + anonymous.id}")

    def test_report_is_available_for_the_other_participant_message(self):
        conversation_id = self._start_chat()
        self.client.post(
            f"/api/chats/{conversation_id}/messages/",
            {"body": "Suspicious message"},
            format="json",
        )
        message = ChatMessage.objects.get(conversation_id=conversation_id)

        self.client.force_authenticate(user=self.employer)
        response = self.client.post(
            f"/api/chats/{conversation_id}/report/",
            {"reason": "spam", "reported_message_id": message.id},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            ChatReport.objects.filter(
                conversation_id=conversation_id,
                reporter=self.employer,
                reported_user=self.candidate,
            ).exists()
        )

    def test_cannot_start_chat_for_expired_vacancy(self):
        self.vacancy.expires_at = timezone.now() - timezone.timedelta(seconds=1)
        self.vacancy.save(update_fields=["expires_at"])
        self.client.force_authenticate(user=self.candidate)
        response = self.client.post(
            "/api/chats/start/",
            {"vacancy_id": self.vacancy.id},
            format="json",
        )
        self.assertEqual(response.status_code, 404)


class ContactAccessPolicyTests(TestCase):
    def _create_vacancy(self):
        owner = User.objects.create_user(
            username="owner",
            email="owner@example.com",
            password="password",
        )
        vacancy = Vacancy.objects.create(
            created_by=owner,
            title="Warehouse worker",
            country="PL",
            city="Poznan",
            city_code="poznan",
            category="warehouse",
            audience_country_codes="UA",
            employment_type="shift",
            experience_required="without",
            salary="27 PLN",
            salary_currency="PLN",
            salary_tax_type="netto",
            description="Visible description",
            housing_type="none",
            phone="+48111111111",
            source="direct",
            creator_token="contact-policy-test",
            expires_at=timezone.now() + timezone.timedelta(days=30),
        )
        return owner, vacancy

    def test_user_vacancy_ad_policy_contacts_are_free(self):
        owner, vacancy = self._create_vacancy()
        policy = VacancyContactAccessPolicy.objects.create(vacancy=vacancy)

        self.assertEqual(policy.contact_unlock_mode, "ad_forever")
        self.assertEqual(policy.contact_unlock_price_credits, 3)

        ensure_free_contact_policy(vacancy, set_by=owner)

        policy.refresh_from_db()
        self.assertEqual(policy.contact_unlock_mode, "ad_forever")
        self.assertEqual(policy.contact_unlock_price_credits, 0)
        self.assertEqual(policy.contact_unlock_timer_hours, None)
        self.assertEqual(policy.contact_unlock_paid_click_limit, None)
        self.assertEqual(policy.set_by, owner)

        state = build_contact_access_state(owner, vacancy)
        self.assertTrue(state["is_unlocked"])
        self.assertEqual(state["current_action"], "already_unlocked")
        self.assertEqual(state["base_price_credits"], 0.0)
        self.assertEqual(state["effective_price_credits"], 0.0)

    def test_manual_paid_policy_is_not_overwritten(self):
        owner, vacancy = self._create_vacancy()
        policy = VacancyContactAccessPolicy.objects.create(
            vacancy=vacancy,
            contact_unlock_mode="paid_forever",
            contact_unlock_price_credits=5,
        )

        ensure_free_contact_policy(vacancy, set_by=owner)

        policy.refresh_from_db()
        self.assertEqual(policy.contact_unlock_mode, "paid_forever")
        self.assertEqual(policy.contact_unlock_price_credits, 5)
        self.assertEqual(policy.set_by, None)


class InternalVacancyImportAPITest(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.request_factory = RequestFactory()
        self.url = "/api/internal/import-vacancy/"
        self.payload = {
            "title": "Warehouse worker",
            "country": "PL",
            "city": "Poznan",
            "city_code": "poznan",
            "category": "warehouse",
            "audience_countries": ["UA"],
            "employment_type": "shift",
            "experience_required": "without",
            "salary": "27-35 PLN netto",
            "salary_from": 27,
            "salary_to": 35,
            "salary_currency": "PLN",
            "salary_tax_type": "netto",
            "description": (
                "Imported vacancy from a verified source text.\n"
                "Contacts: +48661590180\n"
                "Important details stay visible."
            ),
            "housing_type": "paid",
            "housing_cost": "Hostel provided, cost not specified",
            "phone": "+48661590180",
            "whatsapp": "+48661590180",
            "viber": "+48661590180",
            "telegram_username": "jobhub_test",
            "source": "agency",
            "source_text": "Original external vacancy text",
            "extraction_notes": "Schedule is not specified.",
        }

    @override_settings(INTERNAL_IMPORT_TOKEN="secret-token")
    def test_rejects_missing_token(self):
        response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Vacancy.objects.count(), 0)

    @override_settings(INTERNAL_IMPORT_TOKEN="secret-token")
    def test_creates_approved_service_board_vacancy(self):
        response = self.client.post(
            self.url,
            self.payload,
            format="json",
            HTTP_X_INTERNAL_IMPORT_TOKEN="secret-token",
        )

        self.assertEqual(response.status_code, 201)
        vacancy = Vacancy.objects.get(id=response.data["vacancy_id"])
        self.assertEqual(vacancy.created_by.username, SERVICE_BOARD_USERNAME)
        self.assertTrue(vacancy.is_approved)
        self.assertFalse(vacancy.is_rejected)
        self.assertFalse(vacancy.is_editing)
        self.assertEqual(vacancy.moderation_status, "approved")
        self.assertIsNotNone(vacancy.approved_at)
        self.assertNotIn("+48661590180", vacancy.description)
        self.assertIn("Important details stay visible.", vacancy.description)
        self.assertEqual(vacancy.telegram_username, "jobhub_test")
        self.assertFalse(VacancyModerationAttempt.objects.filter(vacancy=vacancy).exists())

    @override_settings(INTERNAL_IMPORT_TOKEN="secret-token")
    def test_publishes_for_employer_with_active_private_board_code(self):
        employer = User.objects.create_user(
            username="delegated-employer",
            email="delegated@example.com",
            password="password",
        )
        UserProfile.objects.create(user=employer)
        authorization = request_authorization(employer)
        accepted = accept_authorization(
            authorization,
            request=self.request_factory.get("/"),
        )
        self.assertTrue(accepted)
        authorization.refresh_from_db()
        self.assertEqual(authorization.status, "active")

        response = self.client.post(
            self.url,
            {**self.payload, "employer_board_code": authorization.board_code},
            format="json",
            HTTP_X_INTERNAL_IMPORT_TOKEN="secret-token",
        )

        self.assertEqual(response.status_code, 201)
        vacancy = Vacancy.objects.get(id=response.data["vacancy_id"])
        self.assertEqual(vacancy.created_by, employer)
        self.assertTrue(vacancy.is_approved)
        self.assertEqual(response.data["published_for_employer_id"], employer.id)
        self.assertTrue(
            EmployerBoardPublishingEvent.objects.filter(
                authorization=authorization,
                vacancy=vacancy,
                action="published",
            ).exists()
        )

    @override_settings(INTERNAL_IMPORT_TOKEN="secret-token")
    def test_rejects_revoked_employer_board_code(self):
        employer = User.objects.create_user(username="revoked-employer", password="password")
        authorization = EmployerBoardPublishingAuthorization.objects.create(
            employer=employer,
            status="active",
        )
        self.assertTrue(
            revoke_authorization(
                authorization,
                request=self.request_factory.get("/"),
            )
        )

        response = self.client.post(
            self.url,
            {**self.payload, "employer_board_code": authorization.board_code},
            format="json",
            HTTP_X_INTERNAL_IMPORT_TOKEN="secret-token",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"], "employer_board_authorization_inactive")

    @override_settings(INTERNAL_IMPORT_TOKEN="secret-token")
    def test_legacy_telegram_phone_is_not_promoted_to_public_username(self):
        payload = {**self.payload, "telegram_username": "", "telegram": "+48661590180"}
        response = self.client.post(
            self.url,
            payload,
            format="json",
            HTTP_X_INTERNAL_IMPORT_TOKEN="secret-token",
        )

        self.assertEqual(response.status_code, 201)
        vacancy = Vacancy.objects.get(id=response.data["vacancy_id"])
        self.assertEqual(vacancy.telegram, "+48661590180")
        self.assertEqual(vacancy.telegram_username, "")

        service_user = User.objects.get(username=SERVICE_BOARD_USERNAME)
        self.assertFalse(service_user.has_usable_password())

        policy = vacancy.contact_access_policy
        self.assertEqual(policy.contact_unlock_mode, "ad_forever")
        self.assertEqual(policy.contact_unlock_price_credits, 0)
        self.assertEqual(policy.set_by, service_user)

    @override_settings(INTERNAL_IMPORT_TOKEN="secret-token", PUSH_PROVIDER="log")
    def test_can_create_pending_service_board_vacancy(self):
        payload = {**self.payload, "moderation_status": "pending"}
        moderator = User.objects.create_user(
            username="moderator",
            email="moderator@example.com",
            password="password",
            is_staff=True,
        )
        PushDevice.objects.create(
            user=moderator,
            token="moderator-device-token",
            platform="android",
            app_language="en",
        )
        PushDevice.objects.create(
            user=moderator,
            token="moderator-ios-token",
            platform="ios",
            app_language="ru",
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.url,
                payload,
                format="json",
                HTTP_X_INTERNAL_IMPORT_TOKEN="secret-token",
            )

        self.assertEqual(response.status_code, 201)
        vacancy = Vacancy.objects.get(id=response.data["vacancy_id"])
        self.assertEqual(vacancy.created_by.username, SERVICE_BOARD_USERNAME)
        self.assertFalse(vacancy.is_approved)
        self.assertFalse(vacancy.is_rejected)
        self.assertFalse(vacancy.is_editing)
        self.assertEqual(vacancy.moderation_status, "pending")
        self.assertIsNone(vacancy.approved_at)
        self.assertIsNotNone(vacancy.editing_started_at)
        self.assertEqual(
            VacancyModerationAttempt.objects.filter(
                vacancy=vacancy,
                decision="pending",
            ).count(),
            1,
        )
        delivery = ModeratorNotificationDelivery.objects.get(
            user=moderator,
            vacancy=vacancy,
            kind="vacancy_pending",
        )
        self.assertEqual(delivery.status, "sent")
        self.assertEqual(delivery.device_platform, "android,ios")

    @override_settings(INTERNAL_IMPORT_TOKEN="secret-token")
    def test_soft_deletes_only_service_board_vacancies(self):
        create_response = self.client.post(
            self.url,
            self.payload,
            format="json",
            HTTP_X_INTERNAL_IMPORT_TOKEN="secret-token",
        )
        service_vacancy = Vacancy.objects.get(id=create_response.data["vacancy_id"])
        other_user = User.objects.create_user(username="other", password="password")
        other_vacancy = Vacancy.objects.create(
            created_by=other_user,
            title="Other vacancy",
            country="PL",
            city="Poznan",
            city_code="poznan",
            category="warehouse",
            audience_country_codes="UA",
            employment_type="shift",
            experience_required="without",
            salary="27 PLN",
            salary_currency="PLN",
            description="Visible description",
            housing_type="none",
            phone="+48111111111",
            source="agency",
            expires_at=timezone.now() + timezone.timedelta(days=30),
        )

        response = self.client.post(
            "/api/internal/delete-vacancies/",
            {"vacancy_ids": [service_vacancy.id, other_vacancy.id]},
            format="json",
            HTTP_X_INTERNAL_IMPORT_TOKEN="secret-token",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["deleted"], [service_vacancy.id])
        self.assertEqual(response.data["skipped"], [other_vacancy.id])

        service_vacancy.refresh_from_db()
        other_vacancy.refresh_from_db()
        self.assertTrue(service_vacancy.is_deleted_by_moderator)
        self.assertFalse(service_vacancy.is_approved)
        self.assertTrue(service_vacancy.is_rejected)
        self.assertFalse(other_vacancy.is_deleted_by_moderator)


class EmployerBoardPublishingAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="board-api-employer",
            email="board-api@example.com",
            password="password",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.request_factory = RequestFactory()
        self.url = "/api/employer/board-publishing/"

    def test_returns_no_request_for_employer_without_authorization(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"status": "none", "has_pending_request": False})

    def test_accepts_pending_request_and_returns_private_code(self):
        request_authorization(self.user)

        response = self.client.post(
            self.url,
            {"action": "accept", "confirm_terms": True},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "active")
        self.assertTrue(response.data["board_code"].startswith("N-"))
        self.assertIn("authorization_text", response.data)

    def test_revoke_blocks_code_but_keeps_authorization_archive(self):
        authorization = request_authorization(self.user)
        accept_authorization(authorization, request=self.request_factory.get("/"))

        response = self.client.post(self.url, {"action": "revoke"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "revoked")
        authorization.refresh_from_db()
        self.assertIsNotNone(authorization.revoked_at)
