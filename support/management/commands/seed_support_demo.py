"""Create an idempotent, non-production-only Support visual-test workspace.

The command has no effect until SUPPORT_DEMO_SEED=1 is configured explicitly.
It never belongs in the production service build command.
"""

from datetime import timedelta
from os import environ

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from jobs.models import Vacancy
from support.models import (
    BotContentRevision,
    EmploymentExclusivityLock,
    OrganizationMembership,
    SupportAccessGrant,
    SupportApplication,
    SupportConnection,
    SupportConversation,
    SupportConversationMember,
    SupportWorkerDocumentReference,
    DocumentRequestPackage,
    SupportOrganization,
    SupportVacancy,
)
from support.services.organizations import activate_organization, create_organization


DEMO_OWNER_EMAIL = "support-owner@jobhub.test"
DEMO_WORKER_EMAIL = "support-worker@jobhub.test"
DEMO_COORDINATOR_EMAIL = "support-coordinator@jobhub.test"
DEMO_CANDIDATE_EMAIL = "support-candidate@jobhub.test"
DEMO_SECOND_CANDIDATE_EMAIL = "support-candidate-02@jobhub.test"
DEMO_EXTRA_WORKERS = (
    ("support-demo-worker-01@jobhub.test", "Алина", "Бондарь", "coordinator"),
    ("support-demo-worker-02@jobhub.test", "Игорь", "Коваль", "coordinator"),
    ("support-demo-worker-03@jobhub.test", "Марина", "Левченко", "coordinator"),
    ("support-demo-worker-04@jobhub.test", "Олег", "Савчук", "active"),
    ("support-demo-worker-05@jobhub.test", "Наталья", "Мельник", "active"),
    ("support-demo-worker-06@jobhub.test", "Артём", "Романюк", "active"),
    ("support-demo-worker-07@jobhub.test", "Виктория", "Ткаченко", "active"),
    ("support-demo-worker-08@jobhub.test", "Денис", "Шевченко", "active"),
)


class Command(BaseCommand):
    help = "Seed an isolated JobHub Support demo workspace when explicitly enabled."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset-candidate-flow",
            action="store_true",
            help="Delete only the demo candidate's application so the public flow can be repeated.",
        )

    @staticmethod
    def _ensure_user(*, user_model, email, first_name, last_name, password, staff=False):
        user, created = user_model.objects.get_or_create(
            username=email,
            defaults={
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "is_active": True,
                "is_staff": staff,
            },
        )
        changed_fields = []
        expected = {
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "is_active": True,
            "is_staff": staff,
        }
        for field, value in expected.items():
            if getattr(user, field) != value:
                setattr(user, field, value)
                changed_fields.append(field)
        if created or changed_fields or not user.check_password(password):
            user.set_password(password)
            user.save(update_fields=[*changed_fields, "password"])
        return user

    @staticmethod
    def _ensure_access_grant(*, user, organization, owner):
        access_grant = (
            SupportAccessGrant.objects.filter(
                user=user,
                organization=organization,
                reason=SupportAccessGrant.REASON_TECHNICAL,
            )
            .order_by("-ends_at", "-id")
            .first()
        )
        access_end = timezone.now() + timedelta(days=365)
        if access_grant is None:
            SupportAccessGrant.objects.create(
                user=user,
                organization=organization,
                granted_by=owner,
                starts_at=timezone.now(),
                ends_at=access_end,
                reason=SupportAccessGrant.REASON_TECHNICAL,
                status=SupportAccessGrant.STATUS_ACTIVE,
            )
            return
        access_grant.granted_by = owner
        access_grant.starts_at = timezone.now()
        access_grant.ends_at = access_end
        access_grant.status = SupportAccessGrant.STATUS_ACTIVE
        access_grant.revoked_at = None
        access_grant.revoked_by = None
        access_grant.save(
            update_fields=[
                "granted_by",
                "starts_at",
                "ends_at",
                "status",
                "revoked_at",
                "revoked_by",
                "updated_at",
            ]
        )

    @staticmethod
    def _ensure_worker_chat(*, owner, membership, connection):
        conversation, _ = SupportConversation.objects.get_or_create(
            organization=connection.organization,
            connection=connection,
            kind=SupportConversation.KIND_COORDINATOR,
            defaults={
                "title": f"Чат: {connection.candidate.get_full_name() or connection.candidate.username}",
                "created_by": owner,
            },
        )
        SupportConversationMember.objects.get_or_create(
            conversation=conversation,
            user=owner,
            defaults={
                "organization_membership": membership,
                "role": SupportConversationMember.ROLE_STAFF,
            },
        )
        SupportConversationMember.objects.get_or_create(
            conversation=conversation,
            user=connection.candidate,
            defaults={"role": SupportConversationMember.ROLE_WORKER},
        )

    def handle(self, *args, **options):
        if environ.get("SUPPORT_DEMO_SEED", "").strip() != "1":
            self.stdout.write("Support demo seed is disabled.")
            return

        password = environ.get("SUPPORT_DEMO_PASSWORD", "")
        if len(password) < 8:
            raise CommandError("support_demo_password_is_missing_or_too_short")

        user_model = get_user_model()
        owner = self._ensure_user(
            user_model=user_model,
            email=DEMO_OWNER_EMAIL,
            first_name="Demo",
            last_name="Manager",
            password=password,
            staff=True,
        )
        worker = self._ensure_user(
            user_model=user_model,
            email=DEMO_WORKER_EMAIL,
            first_name="Demo",
            last_name="Worker",
            password=password,
        )
        coordinator = self._ensure_user(
            user_model=user_model,
            email=DEMO_COORDINATOR_EMAIL,
            first_name="Demo",
            last_name="Coordinator",
            password=password,
        )
        candidate = self._ensure_user(
            user_model=user_model,
            email=DEMO_CANDIDATE_EMAIL,
            first_name="Demo",
            last_name="Candidate",
            password=password,
        )
        second_candidate = self._ensure_user(
            user_model=user_model,
            email=DEMO_SECOND_CANDIDATE_EMAIL,
            first_name="Demo",
            last_name="Candidate Two",
            password=password,
        )

        organization, created = SupportOrganization.objects.get_or_create(
            legal_name="JobHub Support Demo B.V.",
            defaults={
                "display_name": "JobHub Support — demo",
                "created_by": owner,
            },
        )
        if created:
            membership = OrganizationMembership.objects.create(
                organization=organization,
                user=owner,
                display_role="Demo owner",
                is_owner=True,
                accepted_at=timezone.now(),
                created_by=owner,
            )
        else:
            membership, _ = OrganizationMembership.objects.get_or_create(
                organization=organization,
                user=owner,
                defaults={
                    "display_role": "Demo owner",
                    "is_owner": True,
                    "accepted_at": timezone.now(),
                    "created_by": owner,
                },
            )
            if not membership.is_owner or not membership.is_active:
                membership.is_owner = True
                membership.state = OrganizationMembership.STATE_ACTIVE
                membership.save(update_fields=["is_owner", "state", "updated_at"])

        if organization.status != SupportOrganization.STATUS_ACTIVE:
            activate_organization(jobhub_operator=owner, organization=organization)
            organization.refresh_from_db()

        vacancy, _ = SupportVacancy.objects.get_or_create(
            organization=organization,
            internal_title="Демонстрация: склад в Нидерландах",
            defaults={
                "status": SupportVacancy.STATUS_PUBLISHED,
                "internal_position_limit": 10,
                "created_by": owner,
                "published_at": timezone.now(),
            },
        )
        public_vacancy, _ = Vacancy.objects.update_or_create(
            created_by=owner,
            title="JobHub Support: работа на складе в Нидерландах",
            defaults={
                "country": "NL",
                "city": "Lelystad",
                "category": "warehouse",
                "employment_type": "full",
                "description": (
                    "Работа на складе с сопровождением через JobHub Support. "
                    "Откройте информационный помощник в карточке вакансии, "
                    "изучите условия и отправьте анкету менеджеру."
                ),
                "housing_type": "paid",
                "source": "agency",
                "is_approved": True,
                "is_rejected": False,
                "approved_at": timezone.now(),
                "expires_at": timezone.now() + timedelta(days=90),
                "salary": "По условиям проекта",
                "email": owner.email,
            },
        )
        vacancy.public_vacancy = public_vacancy
        vacancy.status = SupportVacancy.STATUS_PUBLISHED
        vacancy.published_at = vacancy.published_at or timezone.now()
        vacancy.save(update_fields=["public_vacancy", "status", "published_at", "updated_at"])

        bot_content = {
            "ru": {
                "title": "Путь кандидата в JobHub Support",
                "intro": "Узнайте об оформлении, переезде и поддержке, затем отправьте короткую анкету.",
                "steps": [
                    "Прочитайте основную информацию и ответы на частые вопросы.",
                    "Заполните анкету без паспортов, банковских данных и фотографий документов.",
                    "Дождитесь решения менеджера и следите за этапом в приложении.",
                ],
                "faq": [
                    {
                        "question": "Анкета гарантирует трудоустройство?",
                        "answer": "Нет. Анкета передаёт работодателю ваше желание обсудить вакансию.",
                    },
                    {
                        "question": "Где передавать документы?",
                        "answer": "Только по инструкции работодателя. В анкету документы загружать не нужно.",
                    },
                ],
            },
            "en": {
                "title": "Your JobHub Support candidate journey",
                "intro": "Review the employment, travel and support information, then send a short application.",
                "steps": [
                    "Read the main information and frequently asked questions.",
                    "Complete the form without passports, bank data or document photos.",
                    "Wait for the manager's decision and follow your stage in the app.",
                ],
                "faq": [
                    {
                        "question": "Does an application guarantee employment?",
                        "answer": "No. It tells the employer that you want to discuss this vacancy.",
                    },
                    {
                        "question": "Where should I send documents?",
                        "answer": "Only through the employer's instructions. Do not upload documents in this form.",
                    },
                ],
            },
            "pl": {
                "title": "Droga kandydata w JobHub Support",
                "intro": "Sprawdź informacje o zatrudnieniu, przeprowadzce i wsparciu, a następnie wyślij krótką ankietę.",
                "steps": [
                    "Przeczytaj główne informacje i odpowiedzi na częste pytania.",
                    "Wypełnij ankietę bez paszportów, danych bankowych i zdjęć dokumentów.",
                    "Poczekaj na decyzję menedżera i obserwuj etap w aplikacji.",
                ],
                "faq": [
                    {
                        "question": "Czy ankieta gwarantuje zatrudnienie?",
                        "answer": "Nie. Informuje pracodawcę, że chcesz omówić tę ofertę.",
                    },
                    {
                        "question": "Gdzie przekazać dokumenty?",
                        "answer": "Tylko zgodnie z instrukcją pracodawcy. Nie dodawaj dokumentów do ankiety.",
                    },
                ],
            },
            "uk": {
                "title": "Шлях кандидата в JobHub Support",
                "intro": "Ознайомтеся з оформленням, переїздом і підтримкою, а потім надішліть коротку анкету.",
                "steps": [
                    "Прочитайте основну інформацію та відповіді на часті запитання.",
                    "Заповніть анкету без паспортів, банківських даних і фотографій документів.",
                    "Дочекайтеся рішення менеджера та стежте за етапом у застосунку.",
                ],
                "faq": [
                    {
                        "question": "Анкета гарантує працевлаштування?",
                        "answer": "Ні. Вона повідомляє роботодавцю про ваше бажання обговорити вакансію.",
                    },
                    {
                        "question": "Куди передавати документи?",
                        "answer": "Лише за інструкцією роботодавця. Не завантажуйте документи в анкету.",
                    },
                ],
            },
        }
        BotContentRevision.objects.filter(
            vacancy=vacancy,
            status=BotContentRevision.STATUS_PUBLISHED,
        ).exclude(version=1).update(status=BotContentRevision.STATUS_ARCHIVED)
        bot_revision, _ = BotContentRevision.objects.update_or_create(
            vacancy=vacancy,
            version=1,
            defaults={
                "source_language": BotContentRevision.LANGUAGE_RU,
                "content": bot_content,
                "status": BotContentRevision.STATUS_PUBLISHED,
                "created_by": owner,
                "published_by": owner,
                "published_at": timezone.now(),
            },
        )
        if bot_revision.status != BotContentRevision.STATUS_PUBLISHED:
            bot_revision.status = BotContentRevision.STATUS_PUBLISHED
            bot_revision.published_by = owner
            bot_revision.published_at = timezone.now()
            bot_revision.save(
                update_fields=["status", "published_by", "published_at", "updated_at"]
            )

        self._ensure_access_grant(user=candidate, organization=organization, owner=owner)
        self._ensure_access_grant(
            user=second_candidate,
            organization=organization,
            owner=owner,
        )
        if options["reset_candidate_flow"]:
            SupportApplication.objects.filter(
                vacancy=vacancy,
                candidate__in=(candidate, second_candidate),
            ).delete()
        application, _ = SupportApplication.objects.get_or_create(
            vacancy=vacancy,
            candidate=worker,
            revision=1,
            defaults={
                "preferred_language": "ru",
                "citizenship_country_code": "UA",
                "current_country_code": "PL",
                "availability_note": "Демонстрационный аккаунт для визуального теста.",
                "consent_version": "demo-v1",
                "consented_at": timezone.now(),
                "status": SupportApplication.STATUS_APPROVED,
            },
        )
        connection, _ = SupportConnection.objects.get_or_create(
            application=application,
            defaults={
                "organization": organization,
                "vacancy": vacancy,
                "candidate": worker,
                "assigned_manager": membership,
                "stage": SupportConnection.STAGE_ACTIVE_WORKER,
                "visible_stage": "Работа и поддержка",
            },
        )
        if connection.stage != SupportConnection.STAGE_ACTIVE_WORKER:
            connection.stage = SupportConnection.STAGE_ACTIVE_WORKER
            connection.visible_stage = "Работа и поддержка"
            connection.assigned_manager = membership
            connection.save(
                update_fields=["stage", "visible_stage", "assigned_manager", "updated_at"]
            )
        EmploymentExclusivityLock.objects.get_or_create(
            candidate=worker,
            connection=connection,
            defaults={"state": EmploymentExclusivityLock.STATE_ACTIVE},
        )

        self._ensure_access_grant(user=worker, organization=organization, owner=owner)
        self._ensure_worker_chat(owner=owner, membership=membership, connection=connection)

        for email, first_name, last_name, demo_stage in DEMO_EXTRA_WORKERS:
            demo_worker = self._ensure_user(
                user_model=user_model,
                email=email,
                first_name=first_name,
                last_name=last_name,
                password=password,
            )
            application, _ = SupportApplication.objects.get_or_create(
                vacancy=vacancy,
                candidate=demo_worker,
                revision=1,
                defaults={
                    "preferred_language": "ru",
                    "citizenship_country_code": "UA",
                    "current_country_code": "PL",
                    "availability_note": "Демонстрационный аккаунт для маршрутов, жилья и чатов.",
                    "consent_version": "demo-v1",
                    "consented_at": timezone.now(),
                    "status": SupportApplication.STATUS_APPROVED,
                },
            )
            stage = (
                SupportConnection.STAGE_COORDINATOR
                if demo_stage == "coordinator"
                else SupportConnection.STAGE_ACTIVE_WORKER
            )
            visible_stage = (
                "Подготовка и координация"
                if stage == SupportConnection.STAGE_COORDINATOR
                else "Работа и поддержка"
            )
            demo_connection, _ = SupportConnection.objects.get_or_create(
                application=application,
                defaults={
                    "organization": organization,
                    "vacancy": vacancy,
                    "candidate": demo_worker,
                    "assigned_manager": membership,
                    "stage": stage,
                    "visible_stage": visible_stage,
                },
            )
            changed_connection_fields = []
            for field, value in {
                "organization": organization,
                "vacancy": vacancy,
                "candidate": demo_worker,
                "assigned_manager": membership,
                "stage": stage,
                "visible_stage": visible_stage,
                "is_archived": False,
                "has_driving_license": email == "support-demo-worker-08@jobhub.test",
            }.items():
                if getattr(demo_connection, field) != value:
                    setattr(demo_connection, field, value)
                    changed_connection_fields.append(field)
            if changed_connection_fields:
                demo_connection.save(update_fields=[*changed_connection_fields, "updated_at"])
            EmploymentExclusivityLock.objects.get_or_create(
                candidate=demo_worker,
                connection=demo_connection,
                defaults={"state": EmploymentExclusivityLock.STATE_ACTIVE},
            )
            self._ensure_access_grant(
                user=demo_worker,
                organization=organization,
                owner=owner,
            )
            self._ensure_worker_chat(
                owner=owner,
                membership=membership,
                connection=demo_connection,
            )
            if email == "support-demo-worker-08@jobhub.test":
                reference, _ = SupportWorkerDocumentReference.objects.get_or_create(
                    user=demo_worker,
                    defaults={"reference_code": f"JH-DEMO-DL-{demo_worker.id}"},
                )
                DocumentRequestPackage.objects.get_or_create(
                    organization=organization,
                    connection=demo_connection,
                    requested_items=[{"type": "driving_license", "custom_label": ""}],
                    defaults={
                        "recipient_email": owner.email,
                        "account_reference": reference,
                        "status": DocumentRequestPackage.STATUS_COMPLETED,
                        "created_by": owner,
                        "reviewed_by": owner,
                        "reviewed_at": timezone.now(),
                    },
                )

        self.stdout.write(
            self.style.SUCCESS(
                "JobHub Support demo workspace is ready: "
                f"owner={DEMO_OWNER_EMAIL}, worker={DEMO_WORKER_EMAIL}, "
                f"candidate={DEMO_CANDIDATE_EMAIL}, "
                f"second_candidate={DEMO_SECOND_CANDIDATE_EMAIL}, "
                f"coordinator={DEMO_COORDINATOR_EMAIL}, "
                f"public_vacancy={public_vacancy.id}, extra_workers={len(DEMO_EXTRA_WORKERS)}."
            )
        )
