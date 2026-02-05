import random
import secrets
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone

from jobs.models import Vacancy


class Command(BaseCommand):
    help = "Seed random вакансии для теста пагинации"

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=40)
        parser.add_argument("--clear", action="store_true")

    def handle(self, *args, **options):
        count = int(options["count"])
        if options["clear"]:
            Vacancy.objects.all().delete()
            self.stdout.write(self.style.WARNING("All vacancies cleared."))

        user = User.objects.filter(is_staff=True).first() or User.objects.first()
        if not user:
            user = User.objects.create_user(username="seed", email="seed@example.com", password="seed12345")
            user.is_staff = True
            user.save(update_fields=["is_staff"])
            self.stdout.write(self.style.WARNING("Created user: seed / seed12345"))

        countries = [c for c, _ in Vacancy.COUNTRY_CHOICES]
        categories = [c for c, _ in Vacancy.CATEGORY_CHOICES]
        employments = [c for c, _ in Vacancy.EMPLOYMENT_TYPE_CHOICES]
        experiences = [c for c, _ in Vacancy.EXPERIENCE_CHOICES]
        sources = [c for c, _ in Vacancy.SOURCE_CHOICES]
        housing_types = [c for c, _ in Vacancy.HOUSING_TYPE_CHOICES]

        titles = [
            "Работа на складе",
            "Сборщик на линии",
            "Уборка помещений",
            "Работа на ферме",
            "Помощник на стройке",
            "Курьер",
            "Оператор склада",
            "Сортировщик",
            "Повар-универсал",
            "Горничная",
            "Водитель",
            "Разнорабочий",
        ]
        cities = [
            "Варшава", "Берлин", "Прага", "Брюссель", "Рим", "Мадрид",
            "Амстердам", "Стокгольм", "Хельсинки", "Краков", "Гданьск",
            "Лодзь", "Катовице", "Лиссабон", "Афины",
        ]
        salaries = [
            "1200-2000 €", "1500-2500 €", "2000-3000 €", "18-25 €/час",
            "20-28 €/час", "1000-1600 €", "1600-2200 €",
        ]
        descriptions = [
            "Стабильная работа, дружная команда, обучение на месте.",
            "График сменный, жильё по договоренности, помощь с документами.",
            "Официальное трудоустройство, выплаты без задержек.",
            "Работа подходит без опыта, есть наставник.",
            "Нужна аккуратность и ответственность.",
        ]

        created = 0
        for i in range(count):
            housing = random.choice(housing_types)
            housing_cost = ""
            if housing == "paid":
                housing_cost = f"{random.randint(100, 300)} €/мес"

            Vacancy.objects.create(
                title=f"{random.choice(titles)} #{i + 1}",
                country=random.choice(countries),
                city=random.choice(cities),
                category=random.choice(categories),
                employment_type=random.choice(employments),
                experience_required=random.choice(experiences),
                salary=random.choice(salaries),
                description=random.choice(descriptions),
                phone=f"+48 6{random.randint(10000000, 99999999)}",
                telegram=f"@jobapp_{random.randint(1000, 9999)}",
                whatsapp=f"+48 6{random.randint(10000000, 99999999)}",
                viber="",
                email=f"hr{random.randint(100,999)}@example.com",
                housing_type=housing,
                housing_cost=housing_cost,
                source=random.choice(sources),
                created_by=user,
                creator_token=secrets.token_hex(32),
                expires_at=timezone.now() + timedelta(days=30),
                is_approved=True,
                is_rejected=False,
                rejection_reason="",
            )
            created += 1

        self.stdout.write(self.style.SUCCESS(f"Seeded {created} vacancies."))
