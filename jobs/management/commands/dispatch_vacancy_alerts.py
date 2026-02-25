from django.core.management.base import BaseCommand, CommandError

from jobs.alerts import dispatch_vacancy_alerts, preview_vacancy_alerts
from jobs.models import Vacancy


class Command(BaseCommand):
    help = "Dispatch push alerts for one approved vacancy (or preview recipients)."

    def add_arguments(self, parser):
        parser.add_argument("--vacancy-id", type=int, required=True)
        parser.add_argument("--preview", action="store_true")

    def handle(self, *args, **options):
        vacancy_id = int(options["vacancy_id"])
        preview = bool(options["preview"])

        vacancy = Vacancy.objects.filter(id=vacancy_id).first()
        if not vacancy:
            raise CommandError(f"vacancy_not_found: {vacancy_id}")

        if preview:
            summary = preview_vacancy_alerts(vacancy)
            self.stdout.write(f"Preview vacancy alerts: {summary}")
            return

        summary = dispatch_vacancy_alerts(vacancy)
        self.stdout.write(self.style.SUCCESS(f"Dispatched vacancy alerts: {summary}"))
