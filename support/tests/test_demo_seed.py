from os import environ
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from support.management.commands.seed_support_demo import (
    DEMO_CANDIDATE_EMAIL,
    DEMO_DOCUMENT_EMAIL,
    DEMO_EXTRA_WORKERS,
    DEMO_DRIVER_EMAILS,
    DEMO_OWNER_EMAIL,
    DEMO_SECOND_CANDIDATE_EMAIL,
)
from support.models import (
    BotContentRevision,
    SupportAccessGrant,
    SupportApplication,
    SupportConnection,
    SupportConversation,
    SupportConversationMember,
    SupportOrganization,
)


class SupportDemoSeedTests(TestCase):
    def test_seed_creates_idempotent_workers_and_direct_chats(self):
        with patch.dict(
            environ,
            {"SUPPORT_DEMO_SEED": "1", "SUPPORT_DEMO_PASSWORD": "demo-password-123"},
            clear=False,
        ):
            call_command("seed_support_demo")
            call_command("seed_support_demo")

        expected_worker_count = 1 + len(DEMO_EXTRA_WORKERS)
        owner = get_user_model().objects.get(username=DEMO_OWNER_EMAIL)
        organization = SupportOrganization.objects.get(
            legal_name="JobHub Support Demo B.V."
        )
        self.assertEqual(organization.verified_document_email, DEMO_DOCUMENT_EMAIL)
        self.assertEqual(SupportConnection.objects.count(), expected_worker_count)
        self.assertEqual(SupportConversation.objects.count(), expected_worker_count)
        self.assertEqual(
            set(
                SupportConnection.objects.filter(has_driving_license=True).values_list(
                    "candidate__email", flat=True
                )
            ),
            set(DEMO_DRIVER_EMAILS),
        )
        self.assertEqual(
            SupportAccessGrant.objects.filter(status=SupportAccessGrant.STATUS_ACTIVE).count(),
            expected_worker_count + 2,
        )
        self.assertEqual(
            SupportConversationMember.objects.filter(user=owner).count(),
            expected_worker_count,
        )
        candidate = get_user_model().objects.get(username=DEMO_CANDIDATE_EMAIL)
        revision = BotContentRevision.objects.get(status=BotContentRevision.STATUS_PUBLISHED)
        self.assertIsNotNone(revision.vacancy.public_vacancy_id)
        self.assertEqual(set(revision.content), {"ru", "en", "pl", "uk"})
        self.assertFalse(
            SupportApplication.objects.filter(
                vacancy=revision.vacancy,
                candidate=candidate,
            ).exists()
        )
        second_candidate = get_user_model().objects.get(
            username=DEMO_SECOND_CANDIDATE_EMAIL
        )
        self.assertFalse(
            SupportApplication.objects.filter(
                vacancy=revision.vacancy,
                candidate=second_candidate,
            ).exists()
        )

        with patch.dict(
            environ,
            {"SUPPORT_DEMO_SEED": "1", "SUPPORT_DEMO_PASSWORD": "demo-password-123"},
            clear=False,
        ):
            SupportApplication.objects.create(
                vacancy=revision.vacancy,
                candidate=candidate,
                revision=1,
                preferred_language="ru",
                consent_version="demo-v1",
                consented_at=revision.published_at,
            )
            SupportApplication.objects.create(
                vacancy=revision.vacancy,
                candidate=second_candidate,
                revision=1,
                preferred_language="ru",
                consent_version="demo-v1",
                consented_at=revision.published_at,
            )
            call_command("seed_support_demo", "--reset-candidate-flow")
        self.assertFalse(
            SupportApplication.objects.filter(
                vacancy=revision.vacancy,
                candidate=candidate,
            ).exists()
        )
        self.assertFalse(
            SupportApplication.objects.filter(
                vacancy=revision.vacancy,
                candidate=second_candidate,
            ).exists()
        )
