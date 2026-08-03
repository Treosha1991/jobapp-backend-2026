from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import resolve
from rest_framework.test import APIClient

from jobs.api import VacancyListAPIView
from jobs.chat_api import ChatConversationListAPIView

from support.api_views import SupportBootstrapAPIView


class SupportFoundationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="support-foundation-user",
            email="support-foundation@example.com",
            password="password",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_bootstrap_requires_authentication(self):
        anonymous_client = APIClient()

        response = anonymous_client.get("/api/v2/support/bootstrap/")

        self.assertEqual(response.status_code, 401)

    def test_bootstrap_is_hidden_while_feature_is_disabled(self):
        response = self.client.get("/api/v2/support/bootstrap/")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data, {"detail": "support_not_available"})

    @override_settings(SUPPORT_FEATURE_ENABLED=True)
    def test_bootstrap_is_safe_and_empty_when_feature_is_enabled(self):
        response = self.client.get("/api/v2/support/bootstrap/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["feature_enabled"], True)
        self.assertEqual(response.data["mode"], "unconfigured")
        self.assertEqual(
            response.data["support_access"],
            {"state": "not_configured", "source": "none", "ends_at": None},
        )
        self.assertEqual(response.data["connections"], [])
        self.assertEqual(response.data["available_actions"], [])

    def test_existing_public_routes_keep_their_original_views(self):
        self.assertIs(resolve("/api/vacancies/").func.view_class, VacancyListAPIView)
        self.assertIs(resolve("/api/chats/").func.view_class, ChatConversationListAPIView)
        self.assertIs(
            resolve("/api/v2/support/bootstrap/").func.view_class,
            SupportBootstrapAPIView,
        )
