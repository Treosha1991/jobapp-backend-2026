from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .country_choices import VACANCY_COUNTRY_CHOICES
from .driver_licenses import DRIVER_LICENSE_CHOICES as DRIVER_LICENSE_CATEGORY_CHOICES
from .monetization import (
    CONTACT_ACCESS_DURATION_MINUTES_DEFAULT,
    CONTACT_ACCESS_MODE_CHOICES,
    CONTACT_PRICE_PRESET_CHOICES,
    CONTACT_TIMER_PRESET_CHOICES,
    CONTACT_UNLOCK_SOURCE_CHOICES,
    EMPLOYER_DAILY_FREE_SUBMISSIONS_DEFAULT,
    INITIAL_FREE_CREATE_SUBMISSIONS_DEFAULT,
    INITIAL_FREE_EDIT_RESUBMISSIONS_DEFAULT,
    PURCHASE_STATUS_CHOICES,
    SEEKER_CONTACT_DISCOUNT_PERCENT_DEFAULT,
    STORE_PLATFORM_CHOICES,
    STORE_PRODUCT_TYPE_CHOICES,
    TRANSACTION_KIND_CHOICES,
    VACANCY_EDIT_RESUBMIT_PRICE_CREDITS_DEFAULT,
    VACANCY_SUBMIT_PRICE_CREDITS_DEFAULT,
    contact_paid_window_deadline,
)


class Vacancy(models.Model):
    COUNTRY_CHOICES = VACANCY_COUNTRY_CHOICES
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
        ("freelance", "Freelance"),
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
    DRIVER_LICENSE_CHOICES = DRIVER_LICENSE_CATEGORY_CHOICES

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
    city_code = models.CharField(max_length=64, blank=True, default="")
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    audience_country_codes = models.CharField(max_length=160, blank=True, default="")
    employment_type = models.CharField(max_length=20, choices=EMPLOYMENT_TYPE_CHOICES)
    experience_required = models.CharField(
        max_length=10,
        choices=EXPERIENCE_CHOICES,
        blank=True,
        default="",
    )
    driver_license_categories = models.CharField(max_length=48, blank=True, default="")
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
    additional_phone = models.CharField(max_length=30, blank=True)
    hide_primary_phone = models.BooleanField(default=False)
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
    approved_at = models.DateTimeField(blank=True, null=True)
    expires_at = models.DateTimeField()
    revision = models.PositiveIntegerField(default=1)

    is_approved = models.BooleanField(default=False)
    is_rejected = models.BooleanField(default=False)
    rejection_reason = models.TextField(blank=True)
    # Snapshot of vacancy fields at the moment moderator hid/rejected it.
    moderation_baseline = models.JSONField(default=dict, blank=True)
    # Human-readable reason from previous moderator action for resubmissions.
    last_moderator_rejection_reason = models.TextField(blank=True)
    # Soft-delete flag controlled only by moderators.
    is_deleted_by_moderator = models.BooleanField(default=False)
    # Snapshot of moderation state before moderator delete for restore action.
    moderator_deleted_state = models.JSONField(default=dict, blank=True)
    deleted_by_moderator_at = models.DateTimeField(blank=True, null=True)
    # Owner can temporarily hide approved vacancy from public feed.
    is_paused_by_owner = models.BooleanField(default=False)
    paused_by_owner_at = models.DateTimeField(blank=True, null=True)
    last_owner_resume_at = models.DateTimeField(blank=True, null=True)
    owner_resume_day = models.DateField(blank=True, null=True)
    owner_resume_count_day = models.PositiveSmallIntegerField(default=0)
    is_editing = models.BooleanField(default=False)
    editing_started_at = models.DateTimeField(blank=True, null=True)

    @property
    def moderation_status(self):
        if self.is_editing:
            return "editing"
        if self.is_approved and self.is_paused_by_owner:
            return "paused"
        if self.is_approved:
            return "approved"
        if self.is_rejected:
            return "rejected"
        return "pending"

    def is_active(self):
        return self.expires_at > timezone.now()

    def __str__(self):
        return self.title


class VacancyModerationAttempt(models.Model):
    TRIGGER_CHOICES = [
        ("create", "Create"),
        ("edit", "Edit"),
        ("restore", "Restore"),
        ("resume_expired", "Resume expired"),
        ("moderator_resubmit", "Moderator resubmit"),
    ]

    DECISION_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    vacancy = models.ForeignKey(
        Vacancy,
        on_delete=models.CASCADE,
        related_name="moderation_attempts",
    )
    attempt_no = models.PositiveIntegerField()
    trigger_type = models.CharField(max_length=24, choices=TRIGGER_CHOICES)
    submitted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="submitted_vacancy_moderation_attempts",
    )
    submitted_at = models.DateTimeField(default=timezone.now)
    decision = models.CharField(
        max_length=12,
        choices=DECISION_CHOICES,
        default="pending",
    )
    decided_at = models.DateTimeField(blank=True, null=True)
    decided_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="decided_vacancy_moderation_attempts",
    )
    rejection_reason = models.TextField(blank=True)
    extra_context = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-submitted_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("vacancy", "attempt_no"),
                name="uniq_vacancy_moderation_attempt_no",
            ),
        ]

    def __str__(self):
        return f"Vacancy #{self.vacancy_id} moderation attempt #{self.attempt_no}"


class EconomyConfig(models.Model):
    singleton_key = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    vacancy_submit_price_credits = models.PositiveSmallIntegerField(
        default=VACANCY_SUBMIT_PRICE_CREDITS_DEFAULT
    )
    vacancy_edit_resubmit_price_credits = models.PositiveSmallIntegerField(
        default=VACANCY_EDIT_RESUBMIT_PRICE_CREDITS_DEFAULT
    )
    free_create_ad_submissions_limit = models.PositiveSmallIntegerField(
        default=INITIAL_FREE_CREATE_SUBMISSIONS_DEFAULT
    )
    free_edit_ad_resubmissions_limit = models.PositiveSmallIntegerField(
        default=INITIAL_FREE_EDIT_RESUBMISSIONS_DEFAULT
    )
    employer_daily_free_submissions_limit = models.PositiveSmallIntegerField(
        default=EMPLOYER_DAILY_FREE_SUBMISSIONS_DEFAULT
    )
    seeker_contact_discount_percent = models.PositiveSmallIntegerField(
        default=SEEKER_CONTACT_DISCOUNT_PERCENT_DEFAULT
    )
    contact_access_duration_minutes = models.PositiveSmallIntegerField(
        default=CONTACT_ACCESS_DURATION_MINUTES_DEFAULT
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Economy config"
        verbose_name_plural = "Economy config"

    def save(self, *args, **kwargs):
        self.singleton_key = 1
        super().save(*args, **kwargs)

    def __str__(self):
        return "Economy config"


class UserWallet(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="wallet")
    paid_credits = models.PositiveIntegerField(default=0)
    bonus_credits = models.PositiveIntegerField(default=0)
    lifetime_paid_credits = models.PositiveIntegerField(default=0)
    lifetime_bonus_credits = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user_id"]

    @property
    def total_credits(self):
        return self.paid_credits + self.bonus_credits

    def __str__(self):
        return f"Wallet user={self.user_id} balance={self.total_credits}"


class WalletTransaction(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="wallet_transactions",
    )
    wallet = models.ForeignKey(
        UserWallet,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    kind = models.CharField(max_length=40, choices=TRANSACTION_KIND_CHOICES)
    delta_paid_credits = models.IntegerField(default=0)
    delta_bonus_credits = models.IntegerField(default=0)
    balance_paid_after = models.PositiveIntegerField(default=0)
    balance_bonus_after = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=255, blank=True, default="")
    related_vacancy = models.ForeignKey(
        Vacancy,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="wallet_transactions",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["kind", "created_at"]),
        ]

    def __str__(self):
        return (
            f"WalletTransaction user={self.user_id} kind={self.kind} "
            f"paid={self.delta_paid_credits} bonus={self.delta_bonus_credits}"
        )


class StoreProduct(models.Model):
    code = models.CharField(max_length=80, unique=True)
    title = models.CharField(max_length=120)
    product_type = models.CharField(max_length=32, choices=STORE_PRODUCT_TYPE_CHOICES)
    platform = models.CharField(
        max_length=16,
        choices=STORE_PLATFORM_CHOICES,
        default="shared",
    )
    store_product_id = models.CharField(max_length=160, blank=True, default="")
    credit_amount = models.PositiveIntegerField(default=0)
    duration_days = models.PositiveSmallIntegerField(default=0)
    price_label = models.CharField(max_length=80, blank=True, default="")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "id"]
        indexes = [
            models.Index(fields=["product_type", "is_active"]),
            models.Index(fields=["platform", "is_active"]),
        ]

    def __str__(self):
        return f"{self.code} ({self.platform})"


class PurchaseRecord(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="purchase_records",
    )
    product = models.ForeignKey(
        StoreProduct,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchases",
    )
    platform = models.CharField(max_length=16, choices=STORE_PLATFORM_CHOICES)
    product_type = models.CharField(max_length=32, choices=STORE_PRODUCT_TYPE_CHOICES)
    store_product_id = models.CharField(max_length=160, blank=True, default="")
    external_transaction_id = models.CharField(max_length=255, unique=True)
    purchase_token = models.CharField(max_length=512, blank=True, default="")
    status = models.CharField(max_length=20, choices=PURCHASE_STATUS_CHOICES, default="pending")
    credits_granted = models.PositiveIntegerField(default=0)
    entitlement_started_at = models.DateTimeField(blank=True, null=True)
    entitlement_expires_at = models.DateTimeField(blank=True, null=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    validated_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"PurchaseRecord user={self.user_id} tx={self.external_transaction_id}"


class UserMonetizationProfile(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="monetization_profile",
    )
    employer_subscription_until = models.DateTimeField(blank=True, null=True)
    seeker_subscription_until = models.DateTimeField(blank=True, null=True)
    free_create_ad_submissions_used = models.PositiveSmallIntegerField(default=0)
    free_edit_ad_resubmissions_used = models.PositiveSmallIntegerField(default=0)
    employer_daily_submission_date = models.DateField(blank=True, null=True)
    employer_daily_submissions_used = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user_id"]

    def has_employer_subscription(self):
        return bool(
            self.employer_subscription_until
            and self.employer_subscription_until > timezone.now()
        )

    def has_seeker_subscription(self):
        return bool(
            self.seeker_subscription_until
            and self.seeker_subscription_until > timezone.now()
        )

    def __str__(self):
        return f"MonetizationProfile user={self.user_id}"


class VacancyContactAccessPolicy(models.Model):
    vacancy = models.OneToOneField(
        Vacancy,
        on_delete=models.CASCADE,
        related_name="contact_access_policy",
    )
    contact_unlock_mode = models.CharField(
        max_length=20,
        choices=CONTACT_ACCESS_MODE_CHOICES,
        default="ad_forever",
    )
    contact_unlock_timer_hours = models.PositiveSmallIntegerField(
        choices=CONTACT_TIMER_PRESET_CHOICES,
        blank=True,
        null=True,
    )
    contact_unlock_price_credits = models.PositiveSmallIntegerField(
        default=CONTACT_PRICE_PRESET_CHOICES[1][0],
    )
    set_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="set_vacancy_contact_policies",
    )
    set_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["vacancy_id"]

    def paid_window_deadline(self):
        if self.contact_unlock_mode != "paid_then_ad":
            return None
        return contact_paid_window_deadline(
            self.vacancy.approved_at,
            self.contact_unlock_timer_hours,
        )

    def __str__(self):
        return f"VacancyContactAccessPolicy vacancy={self.vacancy_id}"


class UnlockedContact(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    vacancy = models.ForeignKey(Vacancy, on_delete=models.CASCADE)
    opened_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(blank=True, null=True)
    unlock_source = models.CharField(
        max_length=20,
        choices=CONTACT_UNLOCK_SOURCE_CHOICES,
        default="paid",
    )
    charged_credits = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)

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
    nickname = models.CharField(max_length=32, blank=True, default="")
    description = models.CharField(max_length=160, blank=True, default="")
    phone_e164 = models.CharField(max_length=20, blank=True, null=True, unique=True)
    phone_verified = models.BooleanField(default=False)
    phone_verified_at = models.DateTimeField(blank=True, null=True)
    # Object key in Cloudflare R2 bucket (public URL is derived in API layer).
    avatar_key = models.CharField(max_length=500, blank=True, default="")
    avatar_updated_at = models.DateTimeField(blank=True, null=True)

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


class Complaint(models.Model):
    REASON_CHOICES = [
        ("spam", "Spam/advertising"),
        ("fake", "Fake"),
        ("abuse", "Abuse"),
        ("wrong_info", "Wrong information"),
        ("contacts", "Invalid contacts"),
        ("not_actual", "Not actual"),
        ("other", "Other"),
    ]

    STATUS_CHOICES = [
        ("new", "New"),
        ("in_review", "In review"),
        ("resolved", "Resolved"),
        ("rejected", "Rejected"),
    ]

    vacancy = models.ForeignKey(Vacancy, on_delete=models.CASCADE, related_name="complaints")
    reporter = models.ForeignKey(User, on_delete=models.CASCADE, related_name="complaints")
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    vacancy_revision_snapshot = models.PositiveIntegerField(default=1)
    message = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="new")
    handled_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="handled_complaints",
    )
    handled_at = models.DateTimeField(blank=True, null=True)
    resolution_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["vacancy", "status"]),
            models.Index(fields=["reason", "created_at"]),
        ]

    def __str__(self):
        return f"Complaint #{self.id} vacancy={self.vacancy_id} reason={self.reason}"


class ComplaintActionLog(models.Model):
    ACTION_CHOICES = [
        ("delete_forever", "Delete vacancy forever"),
        ("reject", "Reject vacancy"),
        ("restore", "Restore vacancy"),
    ]

    complaint = models.ForeignKey(Complaint, on_delete=models.CASCADE, related_name="action_logs")
    vacancy = models.ForeignKey(Vacancy, on_delete=models.CASCADE, related_name="complaint_action_logs")
    actor = models.ForeignKey(User, on_delete=models.CASCADE, related_name="complaint_action_logs")
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    note = models.TextField(blank=True)
    before_state = models.JSONField(default=dict, blank=True)
    after_state = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["vacancy", "created_at"]),
        ]

    def __str__(self):
        return f"ComplaintActionLog #{self.id} action={self.action} vacancy={self.vacancy_id}"


class UserBlock(models.Model):
    blocker = models.ForeignKey(User, on_delete=models.CASCADE, related_name="outgoing_blocks")
    blocked_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="incoming_blocks")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["blocker", "blocked_user"], name="jobs_unique_user_block"),
        ]
        indexes = [
            models.Index(fields=["blocker", "created_at"]),
            models.Index(fields=["blocked_user", "created_at"]),
        ]

    def __str__(self):
        return f"UserBlock blocker={self.blocker_id} blocked={self.blocked_user_id}"


class EmployerSubscription(models.Model):
    subscriber = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="employer_subscriptions",
    )
    employer = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="employer_followers",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["subscriber", "employer"],
                name="jobs_unique_employer_subscription",
            ),
        ]
        indexes = [
            models.Index(fields=["subscriber", "created_at"]),
            models.Index(fields=["employer", "created_at"]),
        ]

    def __str__(self):
        return f"EmployerSubscription subscriber={self.subscriber_id} employer={self.employer_id}"


class AccountDeletionRequest(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="account_deletion_requests",
    )
    user_id_snapshot = models.PositiveIntegerField(db_index=True)
    email_snapshot = models.EmailField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True)
    confirmed_via = models.CharField(max_length=20, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    execute_after = models.DateTimeField(db_index=True)
    processed_at = models.DateTimeField(blank=True, null=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-requested_at"]
        indexes = [
            models.Index(fields=["status", "execute_after"]),
        ]

    def __str__(self):
        return f"AccountDeletionRequest #{self.id} user={self.user_id_snapshot} status={self.status}"


class PushDevice(models.Model):
    PLATFORM_CHOICES = [
        ("android", "Android"),
        ("ios", "iOS"),
        ("web", "Web"),
        ("other", "Other"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="push_devices",
    )
    token = models.CharField(max_length=512, db_index=True)
    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES, default="android")
    app_language = models.CharField(max_length=10, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_seen_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "token"], name="jobs_unique_push_device_per_user"),
        ]
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["token", "is_active"]),
        ]

    def __str__(self):
        return f"PushDevice user={self.user_id} platform={self.platform} active={self.is_active}"


class VacancyAlertSubscription(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="vacancy_alert_subscription",
    )
    enabled = models.BooleanField(default=False)
    country = models.CharField(max_length=10, blank=True, default="")
    city = models.CharField(max_length=80, blank=True, default="")
    city_code = models.CharField(max_length=64, blank=True, default="")
    category = models.CharField(max_length=30, blank=True, default="")
    audience_country_codes = models.CharField(max_length=255, blank=True, default="")
    employment_type = models.CharField(max_length=20, blank=True, default="")
    housing_type = models.CharField(max_length=10, blank=True, default="")
    driver_license_categories = models.CharField(max_length=48, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["enabled", "updated_at"]),
        ]

    def __str__(self):
        return f"VacancyAlertSubscription user={self.user_id} enabled={self.enabled}"


class VacancyAlertDelivery(models.Model):
    STATUS_CHOICES = [
        ("sent", "Sent"),
        ("failed", "Failed"),
        ("skipped_no_device", "Skipped (no device)"),
        ("skipped_not_configured", "Skipped (provider not configured)"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="vacancy_alert_deliveries",
    )
    subscription = models.ForeignKey(
        VacancyAlertSubscription,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deliveries",
    )
    vacancy = models.ForeignKey(
        Vacancy,
        on_delete=models.CASCADE,
        related_name="alert_deliveries",
    )
    status = models.CharField(max_length=40, choices=STATUS_CHOICES)
    device_platform = models.CharField(max_length=20, blank=True, default="")
    device_token_tail = models.CharField(max_length=12, blank=True, default="")
    provider_message_id = models.CharField(max_length=255, blank=True, default="")
    error_text = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "vacancy"], name="jobs_unique_alert_delivery_user_vacancy"),
        ]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["vacancy", "created_at"]),
        ]

    def __str__(self):
        return f"VacancyAlertDelivery user={self.user_id} vacancy={self.vacancy_id} status={self.status}"


@receiver(post_save, sender=User)
def ensure_user_economy_objects(sender, instance, created, **kwargs):
    if not created:
        return
    UserWallet.objects.get_or_create(user=instance)
    UserMonetizationProfile.objects.get_or_create(user=instance)
