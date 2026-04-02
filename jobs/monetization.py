from datetime import timedelta


INITIAL_FREE_CREATE_SUBMISSIONS_DEFAULT = 3
INITIAL_FREE_EDIT_RESUBMISSIONS_DEFAULT = 3
EMPLOYER_DAILY_FREE_SUBMISSIONS_DEFAULT = 3
SEEKER_CONTACT_DISCOUNT_PERCENT_DEFAULT = 70
CONTACT_ACCESS_DURATION_MINUTES_DEFAULT = 60
VACANCY_SUBMIT_PRICE_CREDITS_DEFAULT = 3
VACANCY_EDIT_RESUBMIT_PRICE_CREDITS_DEFAULT = 2


CONTACT_ACCESS_MODE_CHOICES = [
    ("paid_then_ad", "Paid -> ad after timer"),
    ("paid_forever", "Always paid"),
    ("ad_forever", "Always ad"),
]


CONTACT_TIMER_PRESET_CHOICES = [
    (6, "6 hours"),
    (12, "12 hours"),
    (24, "24 hours"),
    (72, "72 hours"),
]


CONTACT_PRICE_PRESET_CHOICES = [
    (1, "1 credit"),
    (3, "3 credits"),
    (5, "5 credits"),
    (8, "8 credits"),
    (13, "13 credits"),
]


STORE_PRODUCT_TYPE_CHOICES = [
    ("credits", "Credits pack"),
    ("employer_subscription", "Employer subscription"),
    ("seeker_subscription", "Seeker subscription"),
]


STORE_PLATFORM_CHOICES = [
    ("android", "Android"),
    ("ios", "iOS"),
    ("shared", "Shared"),
]


PURCHASE_STATUS_CHOICES = [
    ("pending", "Pending"),
    ("validated", "Validated"),
    ("rejected", "Rejected"),
    ("refunded", "Refunded"),
    ("cancelled", "Cancelled"),
]


TRANSACTION_KIND_CHOICES = [
    ("purchase_credit_pack", "Purchase credit pack"),
    ("manual_grant", "Manual grant"),
    ("manual_charge", "Manual charge"),
    ("rewarded_ad", "Rewarded ad"),
    ("vacancy_submit", "Vacancy submit"),
    ("vacancy_edit_resubmit", "Vacancy edit resubmit"),
    ("contact_unlock", "Contact unlock"),
    ("subscription_activation", "Subscription activation"),
    ("subscription_bonus", "Subscription bonus"),
    ("refund", "Refund"),
    ("system_adjustment", "System adjustment"),
]


CONTACT_UNLOCK_SOURCE_CHOICES = [
    ("paid", "Paid credits"),
    ("ad", "Rewarded ad"),
    ("subscription", "Subscription"),
    ("admin", "Admin"),
]


def contact_paid_window_deadline(approved_at, timer_hours):
    if approved_at is None or not timer_hours:
        return None
    return approved_at + timedelta(hours=timer_hours)
