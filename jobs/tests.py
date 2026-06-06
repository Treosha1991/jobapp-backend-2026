from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import Vacancy, VacancyModerationAttempt
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
    def test_creates_pending_service_board_vacancy(self):
        response = self.client.post(
            self.url,
            self.payload,
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
        self.assertNotIn("+48661590180", vacancy.description)
        self.assertIn("Important details stay visible.", vacancy.description)

        attempt = VacancyModerationAttempt.objects.get(vacancy=vacancy)
        self.assertEqual(attempt.decision, "pending")
        self.assertEqual(attempt.trigger_type, "create")
        self.assertEqual(attempt.submitted_by, vacancy.created_by)
        self.assertEqual(attempt.extra_context["import_source"], "internal_import")
        self.assertEqual(attempt.extra_context["source_text"], "Original external vacancy text")

        service_user = User.objects.get(username=SERVICE_BOARD_USERNAME)
        self.assertFalse(service_user.has_usable_password())

        policy = vacancy.contact_access_policy
        self.assertEqual(policy.contact_unlock_mode, "ad_forever")
        self.assertEqual(policy.contact_unlock_price_credits, 0)
        self.assertEqual(policy.set_by, service_user)
