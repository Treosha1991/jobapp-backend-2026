from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Vacancy(models.Model):

    COUNTRY_CHOICES = [
        ("PL", "Poland"),
        ("BY", "Belarus"),
        ("UA", "Ukraine"),
        ("OTHER", "Other"),
    ]

    CATEGORY_CHOICES = [
        ("business", "Business"),
        ("construction", "Construction"),
        ("agriculture", "Agriculture"),
        ("service", "Service"),
        ("tourism", "Tourism"),
    ]

    EMPLOYMENT_TYPE_CHOICES = [
        ("full", "Full-time"),
        ("part", "Part-time"),
        ("shift", "Shift"),
        ("contract", "Contract"),
    ]

    SOURCE_CHOICES = [
        ("direct", "Direct employer"),
        ("agency", "Agency"),
        ("other", "Other"),
    ]

    # Основное
    title = models.CharField(max_length=120)
    country = models.CharField(max_length=10, choices=COUNTRY_CHOICES)
    city = models.CharField(max_length=80)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    employment_type = models.CharField(max_length=20, choices=EMPLOYMENT_TYPE_CHOICES)
    salary = models.CharField(max_length=80)
    description = models.TextField(max_length=3000)

    # Контакты (скрытые)
    phone = models.CharField(max_length=30, blank=True)
    whatsapp = models.CharField(max_length=100, blank=True)
    viber = models.CharField(max_length=100, blank=True)
    telegram = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)

    # Служебное
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    published_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    is_approved = models.BooleanField(default=False)

    def is_active(self):
        return self.expires_at > timezone.now()

    def __str__(self):
        return self.title
    
class UnlockedContact(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    vacancy = models.ForeignKey(Vacancy, on_delete=models.CASCADE)
    opened_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "vacancy")

    def __str__(self):
        return f"{self.user.username} unlocked {self.vacancy.id}"
    
import secrets
from datetime import timedelta

class UnlockRequest(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    vacancy = models.ForeignKey(Vacancy, on_delete=models.CASCADE)
    token = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    @staticmethod
    def create_for(user, vacancy):
        tok = secrets.token_urlsafe(32)
        return UnlockRequest.objects.create(
            user=user,
            vacancy=vacancy,
            token=tok,
            expires_at=timezone.now() + timedelta(minutes=2)
        )

    def is_valid(self):
        return self.expires_at > timezone.now()

    def __str__(self):
        return f"UnlockRequest {self.user.username} {self.vacancy.id}"


