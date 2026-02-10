from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Vacancy(models.Model):
    COUNTRY_CHOICES = [
        ("PL", "Poland"),
        ("DE", "Germany"),
        ("FR", "France"),
        ("ES", "Spain"),
        ("IT", "Italy"),
        ("NL", "Netherlands"),
        ("BE", "Belgium"),
        ("AT", "Austria"),
        ("SE", "Sweden"),
        ("FI", "Finland"),
        ("DK", "Denmark"),
        ("IE", "Ireland"),
        ("PT", "Portugal"),
        ("GR", "Greece"),
        ("CZ", "Czechia"),
        ("SK", "Slovakia"),
        ("HU", "Hungary"),
        ("RO", "Romania"),
        ("BG", "Bulgaria"),
        ("HR", "Croatia"),
        ("SI", "Slovenia"),
        ("LT", "Lithuania"),
        ("LV", "Latvia"),
        ("EE", "Estonia"),
        ("LU", "Luxembourg"),
        ("MT", "Malta"),
        ("CY", "Cyprus"),
        ("UK", "United Kingdom"),
        ("CH", "Switzerland"),
        ("US", "USA"),
        ("CA", "Canada"),
        ("UA", "Ukraine"),
        ("BY", "Belarus"),
        ("OTHER", "Other"),
    ]
    CATEGORY_CHOICES = [
        ("construction", "Construction"),
        ("agriculture", "Agriculture"),
        ("warehouse", "Warehouse"),
        ("logistics", "Logistics"),
        ("manufacturing", "Manufacturing"),
        ("hospitality", "Hospitality"),
        ("cleaning", "Cleaning"),
        ("retail", "Retail"),
        ("transport", "Transport"),
        ("healthcare", "Healthcare"),
        ("it", "IT"),
        ("service", "Service"),
        ("other", "Other"),
    ]
    EMPLOYMENT_TYPE_CHOICES = [
        ("full", "Full-time"),
        ("part", "Part-time"),
        ("shift", "Shift"),
        ("contract", "Contract"),
        ("seasonal", "Seasonal"),
        ("temporary", "Temporary"),
        ("internship", "Internship"),
    ]

    EXPERIENCE_CHOICES = [
        ("with", "With experience"),
        ("without", "Without experience"),
    ]

    SOURCE_CHOICES = [
        ("direct", "Direct employer"),
        ("agency", "Agency"),
        ("other", "Other"),
    ]

    HOUSING_TYPE_CHOICES = [
        ("free", "Free"),
        ("paid", "Paid"),
        ("none", "None"),
    ]

    SALARY_CURRENCY_CHOICES = [
        ("EUR", "EUR"),
        ("PLN", "PLN"),
        ("USD", "USD"),
        ("CAD", "CAD"),
        ("CHF", "CHF"),
        ("GBP", "GBP"),
        ("UAH", "UAH"),
        ("BYN", "BYN"),
        ("CZK", "CZK"),
        ("HUF", "HUF"),
        ("RON", "RON"),
        ("BGN", "BGN"),
        ("SEK", "SEK"),
        ("DKK", "DKK"),
    ]

    SALARY_TAX_TYPE_CHOICES = [
        ("brutto", "Brutto"),
        ("netto", "Netto"),
    ]


    # Основное
    title = models.CharField(max_length=120)
    country = models.CharField(max_length=10, choices=COUNTRY_CHOICES)
    city = models.CharField(max_length=80)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    employment_type = models.CharField(max_length=20, choices=EMPLOYMENT_TYPE_CHOICES)
    experience_required = models.CharField(max_length=10, choices=EXPERIENCE_CHOICES, default="without")
    salary = models.CharField(max_length=80)
    salary_from = models.PositiveSmallIntegerField(blank=True, null=True)
    salary_to = models.PositiveSmallIntegerField(blank=True, null=True)
    salary_currency = models.CharField(
        max_length=3,
        choices=SALARY_CURRENCY_CHOICES,
        blank=True,
    )
    salary_tax_type = models.CharField(
        max_length=6,
        choices=SALARY_TAX_TYPE_CHOICES,
        blank=True,
    )
    salary_hours_month = models.PositiveSmallIntegerField(blank=True, null=True)
    description = models.TextField(max_length=3000)

    # Контакты (скрытые)
    phone = models.CharField(max_length=30, blank=True)
    whatsapp = models.CharField(max_length=100, blank=True)
    viber = models.CharField(max_length=100, blank=True)
    telegram = models.CharField(max_length=100, blank=True)
    email = models.EmailField(blank=True)

    # Housing
    housing_type = models.CharField(max_length=10, choices=HOUSING_TYPE_CHOICES, default="none")
    housing_cost = models.CharField(max_length=80, blank=True)


    # Служебное
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    creator_token = models.CharField(max_length=64, unique=True, blank=True, null=True)
    published_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    is_approved = models.BooleanField(default=False)
    is_rejected = models.BooleanField(default=False)
    rejection_reason = models.TextField(blank=True)
    is_editing = models.BooleanField(default=False)
    editing_started_at = models.DateTimeField(blank=True, null=True)

    @property
    def moderation_status(self):
        if self.is_editing:
            return "editing"
        if self.is_approved:
            return "approved"
        if self.is_rejected:
            return "rejected"
        return "pending"

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


class EmailVerification(models.Model):
    PURPOSE_CHOICES = [
        ("register", "Register"),
        ("reset", "Reset password"),
        ("link_email", "Link email"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    code = models.CharField(max_length=6)
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES, default="register")
    target_email = models.EmailField(blank=True)
    pending_password = models.CharField(max_length=128, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    def is_valid(self):
        return (not self.is_used) and self.expires_at > timezone.now()

    def __str__(self):
        return f"EmailVerification {self.user.username} {self.purpose}"


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    phone_e164 = models.CharField(max_length=20, blank=True, null=True, unique=True)
    phone_verified = models.BooleanField(default=False)
    phone_verified_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"UserProfile {self.user.username}"


class PhoneVerification(models.Model):
    PURPOSE_CHOICES = [
        ("verify_phone", "Verify phone"),
        ("login", "Login"),
        ("reset", "Reset password"),
    ]

    phone_e164 = models.CharField(max_length=20, db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, blank=True, null=True)
    code = models.CharField(max_length=6)
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    attempts = models.PositiveSmallIntegerField(default=0)

    def is_valid(self):
        return (not self.is_used) and self.expires_at > timezone.now() and self.attempts < 5

    def __str__(self):
        return f"PhoneVerification {self.phone_e164} {self.purpose}"


