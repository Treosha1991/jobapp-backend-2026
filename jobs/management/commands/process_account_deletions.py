from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from rest_framework.authtoken.models import Token

from jobs.models import (
    AccountDeletionRequest,
    Complaint,
    ComplaintActionLog,
    EmailVerification,
    PhoneVerification,
    UnlockedContact,
    UnlockRequest,
    UserProfile,
    Vacancy,
)


class Command(BaseCommand):
    help = "Process due account deletions (hard delete user data and account)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        now = timezone.now()
        limit = max(1, int(options["limit"]))

        qs = (
            AccountDeletionRequest.objects.select_related("user")
            .filter(status="pending", execute_after__lte=now)
            .order_by("execute_after")[:limit]
        )

        processed = 0
        for req in qs:
            self._process_request(req)
            processed += 1

        self.stdout.write(self.style.SUCCESS(f"Processed account deletions: {processed}"))

    @transaction.atomic
    def _process_request(self, req: AccountDeletionRequest):
        user = req.user

        if user is not None:
            user_id = user.id
            email = (user.email or "").strip()

            Token.objects.filter(user=user).delete()
            EmailVerification.objects.filter(user=user).delete()
            PhoneVerification.objects.filter(user=user).delete()
            UnlockedContact.objects.filter(user=user).delete()
            UnlockRequest.objects.filter(user=user).delete()
            ComplaintActionLog.objects.filter(actor=user).delete()
            Complaint.objects.filter(reporter=user).delete()
            Vacancy.objects.filter(created_by=user).delete()
            UserProfile.objects.filter(user=user).delete()
            User.objects.filter(id=user_id).delete()

            req.user = None
            req.user_id_snapshot = user_id
            req.email_snapshot = email

        req.status = "completed"
        req.processed_at = timezone.now()
        req.save(update_fields=["user", "user_id_snapshot", "email_snapshot", "status", "processed_at"])
