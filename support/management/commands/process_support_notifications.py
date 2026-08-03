from django.core.management.base import BaseCommand

from support.services.entitlements import expire_elapsed_temporary_access_grants
from support.services.notifications import dispatch_pending_outbox


class Command(BaseCommand):
    help = "Expires elapsed temporary Support access and retries durable Support notifications."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        limit = max(1, min(int(options["limit"]), 1000))
        expired = expire_elapsed_temporary_access_grants(limit=limit)
        results = dispatch_pending_outbox(limit=limit)
        sent = sum(item.get("sent", 0) for item in results)
        failed = sum(item.get("failed", 0) for item in results)
        skipped = sum(item.get("skipped", 0) for item in results)
        self.stdout.write(
            self.style.SUCCESS(
                "Support notifications processed: "
                f"expired_access={expired}, events={len(results)}, "
                f"sent={sent}, failed={failed}, skipped={skipped}"
            )
        )
