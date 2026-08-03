from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from support.services.organizations import create_organization


class Command(BaseCommand):
    help = (
        "Create a draft JobHub Support organization and its verified owner. "
        "This is a controlled JobHub operator action, not employer self-signup."
    )

    def add_arguments(self, parser):
        parser.add_argument("--operator-email", required=True)
        parser.add_argument("--owner-email", required=True)
        parser.add_argument("--legal-name", required=True)
        parser.add_argument("--display-name", required=True)

    def handle(self, *args, **options):
        user_model = get_user_model()
        operator = user_model.objects.filter(
            email__iexact=options["operator_email"].strip(),
            is_staff=True,
            is_active=True,
        ).first()
        if operator is None:
            raise CommandError("active_jobhub_operator_not_found")

        try:
            organization, membership = create_organization(
                jobhub_operator=operator,
                legal_name=options["legal_name"],
                display_name=options["display_name"],
                owner_email=options["owner_email"],
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                "Created Support organization "
                f"{organization.public_id} with owner membership {membership.public_id}."
            )
        )
