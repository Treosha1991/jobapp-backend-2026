from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import ModeratorNotificationDelivery, PushDevice, Vacancy, VacancyModerationAttempt
from .service_sources import SERVICE_BOARD_USERNAME


class InternalVacancyImportAPITest(TestCase):
    def setUp(self):
        self.client = APIClient()
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
            "telegram": "+48661590180",
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
        self.assertFalse(VacancyModerationAttempt.objects.filter(vacancy=vacancy).exists())

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
