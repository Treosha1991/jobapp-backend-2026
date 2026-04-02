from math import ceil

from django.db import transaction
from django.utils import timezone

from .models import (
    EconomyConfig,
    UnlockedContact,
    UserMonetizationProfile,
    UserWallet,
    VacancyContactAccessPolicy,
    WalletTransaction,
)
from .monetization import (
    CONTACT_ACCESS_DURATION_MINUTES_DEFAULT,
    TRANSACTION_KIND_CHOICES,
)

TRANSACTION_KINDS = {code for code, _ in TRANSACTION_KIND_CHOICES}


class InsufficientCreditsError(Exception):
    pass


def get_economy_config():
    config, _ = EconomyConfig.objects.get_or_create(singleton_key=1)
    return config


def get_or_create_wallet(user):
    wallet, _ = UserWallet.objects.get_or_create(user=user)
    return wallet


def get_or_create_monetization_profile(user):
    profile, _ = UserMonetizationProfile.objects.get_or_create(user=user)
    return profile


def get_or_create_contact_policy(vacancy):
    policy, _ = VacancyContactAccessPolicy.objects.get_or_create(vacancy=vacancy)
    return policy


def _normalize_tx_kind(kind):
    if kind not in TRANSACTION_KINDS:
        return "system_adjustment"
    return kind


@transaction.atomic
def grant_credits(
    user,
    *,
    paid_credits=0,
    bonus_credits=0,
    kind="system_adjustment",
    note="",
    related_vacancy=None,
    metadata=None,
):
    if paid_credits < 0 or bonus_credits < 0:
        raise ValueError("grant_credits expects non-negative deltas")

    wallet = UserWallet.objects.select_for_update().filter(user=user).first()
    if wallet is None:
        wallet = UserWallet.objects.create(user=user)

    wallet.paid_credits += paid_credits
    wallet.bonus_credits += bonus_credits
    wallet.lifetime_paid_credits += paid_credits
    wallet.lifetime_bonus_credits += bonus_credits
    wallet.save(
        update_fields=[
            "paid_credits",
            "bonus_credits",
            "lifetime_paid_credits",
            "lifetime_bonus_credits",
            "updated_at",
        ]
    )

    tx = WalletTransaction.objects.create(
        user=user,
        wallet=wallet,
        kind=_normalize_tx_kind(kind),
        delta_paid_credits=paid_credits,
        delta_bonus_credits=bonus_credits,
        balance_paid_after=wallet.paid_credits,
        balance_bonus_after=wallet.bonus_credits,
        note=(note or "").strip(),
        related_vacancy=related_vacancy,
        metadata=metadata or {},
    )
    return wallet, tx


@transaction.atomic
def set_wallet_balances(
    user,
    *,
    paid_credits,
    bonus_credits,
    note="",
    related_vacancy=None,
    metadata=None,
):
    paid_credits = int(paid_credits)
    bonus_credits = int(bonus_credits)
    if paid_credits < 0 or bonus_credits < 0:
        raise ValueError("wallet balances cannot be negative")

    wallet = UserWallet.objects.select_for_update().filter(user=user).first()
    if wallet is None:
        wallet = UserWallet.objects.create(user=user)

    delta_paid = paid_credits - wallet.paid_credits
    delta_bonus = bonus_credits - wallet.bonus_credits
    if delta_paid == 0 and delta_bonus == 0:
        return wallet, None

    wallet.paid_credits = paid_credits
    wallet.bonus_credits = bonus_credits
    if delta_paid > 0:
        wallet.lifetime_paid_credits += delta_paid
    if delta_bonus > 0:
        wallet.lifetime_bonus_credits += delta_bonus
    wallet.save(
        update_fields=[
            "paid_credits",
            "bonus_credits",
            "lifetime_paid_credits",
            "lifetime_bonus_credits",
            "updated_at",
        ]
    )

    tx = WalletTransaction.objects.create(
        user=user,
        wallet=wallet,
        kind="manual_grant" if (delta_paid > 0 or delta_bonus > 0) else "manual_charge",
        delta_paid_credits=delta_paid,
        delta_bonus_credits=delta_bonus,
        balance_paid_after=wallet.paid_credits,
        balance_bonus_after=wallet.bonus_credits,
        note=(note or "").strip(),
        related_vacancy=related_vacancy,
        metadata=metadata or {},
    )
    return wallet, tx


@transaction.atomic
def spend_credits(
    user,
    *,
    amount,
    kind,
    note="",
    related_vacancy=None,
    metadata=None,
):
    amount = int(amount or 0)
    if amount <= 0:
        raise ValueError("spend amount must be positive")

    wallet = UserWallet.objects.select_for_update().filter(user=user).first()
    if wallet is None:
        wallet = UserWallet.objects.create(user=user)

    if wallet.total_credits < amount:
        raise InsufficientCreditsError("insufficient_credits")

    spend_bonus = min(wallet.bonus_credits, amount)
    spend_paid = amount - spend_bonus
    wallet.bonus_credits -= spend_bonus
    wallet.paid_credits -= spend_paid
    wallet.save(update_fields=["paid_credits", "bonus_credits", "updated_at"])

    tx = WalletTransaction.objects.create(
        user=user,
        wallet=wallet,
        kind=_normalize_tx_kind(kind),
        delta_paid_credits=-spend_paid,
        delta_bonus_credits=-spend_bonus,
        balance_paid_after=wallet.paid_credits,
        balance_bonus_after=wallet.bonus_credits,
        note=(note or "").strip(),
        related_vacancy=related_vacancy,
        metadata=metadata or {},
    )
    return wallet, tx


def _valid_unlocked_contact(unlocked, *, now=None):
    if unlocked is None:
        return None
    current_time = now or timezone.now()
    expires_at = getattr(unlocked, "expires_at", None)
    if expires_at and expires_at <= current_time:
        return None
    return unlocked


def get_active_unlocked_contact(user, vacancy, *, now=None):
    unlocked = (
        UnlockedContact.objects.filter(user=user, vacancy=vacancy)
        .order_by("-opened_at", "-id")
        .first()
    )
    return _valid_unlocked_contact(unlocked, now=now)


def build_contact_access_state(user, vacancy, *, now=None):
    current_time = now or timezone.now()
    policy = get_or_create_contact_policy(vacancy)
    config = get_economy_config()
    profile = get_or_create_monetization_profile(user) if getattr(user, "is_authenticated", False) else None
    wallet = get_or_create_wallet(user) if getattr(user, "is_authenticated", False) else None

    unlocked = None
    if getattr(user, "is_authenticated", False):
        unlocked = get_active_unlocked_contact(user, vacancy, now=current_time)

    deadline = policy.paid_window_deadline()
    paid_window_active = bool(deadline and current_time < deadline)

    if policy.contact_unlock_mode == "paid_then_ad":
        current_mode = "paid" if paid_window_active else "ad"
    elif policy.contact_unlock_mode == "paid_forever":
        current_mode = "paid"
    else:
        current_mode = "ad"

    base_price = int(policy.contact_unlock_price_credits or 0)
    effective_price = base_price
    has_seeker_subscription = bool(profile and profile.has_seeker_subscription(current_time))
    discount_percent = int(config.seeker_contact_discount_percent or 0)

    action = current_mode
    ad_required = current_mode == "ad"
    if has_seeker_subscription:
        if current_mode == "paid":
            effective_price = max(1, ceil(base_price * max(0, 100 - discount_percent) / 100.0)) if base_price > 0 else 0
        else:
            action = "subscription_free"
            ad_required = False
            effective_price = 0

    return {
        "vacancy_id": vacancy.id,
        "is_unlocked": bool(unlocked),
        "unlocked_until": getattr(unlocked, "expires_at", None),
        "unlock_source": getattr(unlocked, "unlock_source", "") if unlocked else "",
        "contact_access_duration_minutes": int(
            getattr(config, "contact_access_duration_minutes", CONTACT_ACCESS_DURATION_MINUTES_DEFAULT)
            or CONTACT_ACCESS_DURATION_MINUTES_DEFAULT
        ),
        "mode": policy.contact_unlock_mode,
        "current_action": "already_unlocked" if unlocked else action,
        "base_price_credits": base_price,
        "effective_price_credits": 0 if unlocked else effective_price,
        "contact_unlock_timer_hours": policy.contact_unlock_timer_hours,
        "paid_window_deadline": deadline,
        "paid_window_is_active": paid_window_active,
        "can_use_ad": bool(not unlocked and (action == "ad" or action == "subscription_free")),
        "ad_required": bool(not unlocked and ad_required),
        "has_seeker_subscription": has_seeker_subscription,
        "wallet_total_credits": wallet.total_credits if wallet else 0,
        "can_afford": bool(wallet and wallet.total_credits >= effective_price),
    }
