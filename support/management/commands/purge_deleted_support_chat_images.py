from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Max
from django.utils import timezone

from support.models import SupportChatImage
from support.services.chat_media import delete_chat_image


class Command(BaseCommand):
    help = "Permanently delete private chat images 30 days after all messages were deleted."

    def handle(self, *args, **options):
        now = timezone.now()
        retention = timedelta(days=30)
        candidates = (
            SupportChatImage.objects.filter(purged_at__isnull=True)
            .annotate(
                last_message_deleted_at=Max("message_links__message__deleted_at"),
            )
            .filter(last_message_deleted_at__isnull=False)
            .exclude(message_links__message__deleted_at__isnull=True)
            .distinct()
        )
        purged = 0
        scheduled = 0
        for image in candidates.iterator():
            purge_after = image.last_message_deleted_at + retention
            if image.purge_after != purge_after:
                image.purge_after = purge_after
                image.save(update_fields=["purge_after"])
                scheduled += 1
            if purge_after > now:
                continue
            # Repeat the live-link check immediately before deleting the object.
            if image.message_links.filter(message__deleted_at__isnull=True).exists():
                continue
            delete_chat_image(image.object_key)
            image.purged_at = now
            image.save(update_fields=["purged_at"])
            purged += 1
        self.stdout.write(
            self.style.SUCCESS(f"scheduled={scheduled} purged={purged}")
        )
