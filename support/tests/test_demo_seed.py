from os import environ
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from support.management.commands.seed_support_demo import (
    DEMO_EXTRA_WORKERS,
    DEMO_OWNER_EMAIL,
)
from support.models import (
    SupportAccessGrant,
    SupportConnection,
    SupportConversation,
    SupportConversationMember,
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
        self.assertEqual(SupportConnection.objects.count(), expected_worker_count)
        self.assertEqual(SupportConversation.objects.count(), expected_worker_count)
        self.assertEqual(
            SupportAccessGrant.objects.filter(status=SupportAccessGrant.STATUS_ACTIVE).count(),
            expected_worker_count,
        )
        self.assertEqual(
            SupportConversationMember.objects.filter(user=owner).count(),
            expected_worker_count,
        )
