from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Count, Max, Q, Sum
from django.utils import timezone

from .models import (
    EconomyConfig,
    PurchaseRecord,
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
ZERO_CREDITS = Decimal("0.00")
CREDIT_QUANT = Decimal("0.01")


class InsufficientCreditsError(Exception):
    pass


class EconomyActionRequiredError(Exception):
    def __init__(self, code, state):
        super().__init__(code)
        self.code = code
        self.state = state


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


def is_employer_profile_visible_for_vacancy(vacancy, *, now=None):
    current_time = now or timezone.now()
    policy = getattr(vacancy, "contact_access_policy", None)
    if policy is None:
        return True

    mode_state = _contact_unlock_mode_state(vacancy, policy=policy, now=current_time)
    mode = mode_state["current_mode"]
    if mode != "paid":
        return True
    return False


def _normalize_tx_kind(kind):
    if kind not in TRANSACTION_KINDS:
        return "system_adjustment"
    return kind


def _credit_decimal(value):
    if isinstance(value, Decimal):
        raw = value
    elif value in (None, ""):
        raw = ZERO_CREDITS
    else:
        raw = Decimal(str(value))
    return raw.quantize(CREDIT_QUANT, rounding=ROUND_HALF_UP)


def _credit_json_value(value):
    return float(_credit_decimal(value))


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
    paid_credits = _credit_decimal(paid_credits)
    bonus_credits = _credit_decimal(bonus_credits)
    if paid_credits < ZERO_CREDITS or bonus_credits < ZERO_CREDITS:
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
    paid_credits = _credit_decimal(paid_credits)
    bonus_credits = _credit_decimal(bonus_credits)
    if paid_credits < ZERO_CREDITS or bonus_credits < ZERO_CREDITS:
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
    amount = _credit_decimal(amount)
    if amount <= ZERO_CREDITS:
        raise ValueError("spend amount must be positive")

    wallet = UserWallet.objects.select_for_update().filter(user=user).first()
    if wallet is None:
        wallet = UserWallet.objects.create(user=user)

    if wallet.total_credits < amount:
        raise InsufficientCreditsError("insufficient_credits")

    spend_bonus = min(wallet.bonus_credits, amount)
    spend_paid = _credit_decimal(amount - spend_bonus)
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


@transaction.atomic
def record_wallet_event(
    user,
    *,
    kind,
    note="",
    related_vacancy=None,
    metadata=None,
):
    wallet = UserWallet.objects.select_for_update().filter(user=user).first()
    if wallet is None:
        wallet = UserWallet.objects.create(user=user)

    tx = WalletTransaction.objects.create(
        user=user,
        wallet=wallet,
        kind=_normalize_tx_kind(kind),
        delta_paid_credits=0,
        delta_bonus_credits=0,
        balance_paid_after=wallet.paid_credits,
        balance_bonus_after=wallet.bonus_credits,
        note=(note or "").strip(),
        related_vacancy=related_vacancy,
        metadata=metadata or {},
    )
    return wallet, tx


def _subscription_extension_window(current_until, *, now, duration_days):
    baseline = current_until if current_until and current_until > now else now
    return baseline, baseline + timedelta(days=duration_days)


@transaction.atomic
def apply_store_product_purchase(
    user,
    *,
    product,
    platform,
    external_transaction_id,
    purchase_token="",
    payload=None,
):
    external_transaction_id = (external_transaction_id or "").strip()
    if not external_transaction_id:
        raise ValueError("purchase_transaction_id_required")

    purchase_payload = payload or {}
    purchase_record = (
        PurchaseRecord.objects.select_for_update()
        .filter(external_transaction_id=external_transaction_id)
        .first()
    )
    if purchase_record and purchase_record.user_id != user.id:
        raise ValueError("purchase_transaction_user_mismatch")

    created = False
    if purchase_record is None:
        purchase_record = PurchaseRecord.objects.create(
            user=user,
            product=product,
            platform=platform,
            product_type=product.product_type,
            store_product_id=(product.store_product_id or "").strip(),
            external_transaction_id=external_transaction_id,
            purchase_token=(purchase_token or "").strip(),
            payload=purchase_payload,
        )
        created = True
    else:
        purchase_record.product = product
        purchase_record.platform = platform
        purchase_record.product_type = product.product_type
        purchase_record.store_product_id = (product.store_product_id or "").strip()
        if purchase_token:
            purchase_record.purchase_token = purchase_token.strip()
        if purchase_payload:
            purchase_record.payload = purchase_payload
        purchase_record.save(
            update_fields=[
                "product",
                "platform",
                "product_type",
                "store_product_id",
                "purchase_token",
                "payload",
                "updated_at",
            ]
        )

    if purchase_record.status == "validated":
        return purchase_record, False

    now = timezone.now()
    credits_granted = 0
    entitlement_started_at = None
    entitlement_expires_at = None

    if product.product_type == "credits":
        credits_granted = int(product.credit_amount or 0)
        if credits_granted <= 0:
            raise ValueError("store_credit_amount_invalid")
        grant_credits(
            user,
            paid_credits=credits_granted,
            kind="purchase_credit_pack",
            note=(product.title or "").strip(),
            metadata={
                "store_product_code": product.code,
                "store_product_id": product.store_product_id,
                "platform": platform,
                "purchase_record_id": purchase_record.id,
            },
        )
    elif product.product_type == "employer_subscription":
        duration_days = int(product.duration_days or 0)
        if duration_days <= 0:
            raise ValueError("store_subscription_duration_invalid")
        profile = get_or_create_monetization_profile(user)
        entitlement_started_at, entitlement_expires_at = _subscription_extension_window(
            profile.employer_subscription_until,
            now=now,
            duration_days=duration_days,
        )
        profile.employer_subscription_until = entitlement_expires_at
        profile.save(update_fields=["employer_subscription_until", "updated_at"])
        record_wallet_event(
            user,
            kind="subscription_activation",
            note=(product.title or "").strip(),
            metadata={
                "store_product_code": product.code,
                "store_product_id": product.store_product_id,
                "platform": platform,
                "purchase_record_id": purchase_record.id,
                "subscription_kind": "employer",
                "duration_days": duration_days,
            },
        )
    elif product.product_type == "seeker_subscription":
        duration_days = int(product.duration_days or 0)
        if duration_days <= 0:
            raise ValueError("store_subscription_duration_invalid")
        profile = get_or_create_monetization_profile(user)
        entitlement_started_at, entitlement_expires_at = _subscription_extension_window(
            profile.seeker_subscription_until,
            now=now,
            duration_days=duration_days,
        )
        profile.seeker_subscription_until = entitlement_expires_at
        profile.save(update_fields=["seeker_subscription_until", "updated_at"])
        record_wallet_event(
            user,
            kind="subscription_activation",
            note=(product.title or "").strip(),
            metadata={
                "store_product_code": product.code,
                "store_product_id": product.store_product_id,
                "platform": platform,
                "purchase_record_id": purchase_record.id,
                "subscription_kind": "seeker",
                "duration_days": duration_days,
            },
        )
    else:
        raise ValueError("store_product_type_invalid")

    purchase_record.status = "validated"
    purchase_record.credits_granted = credits_granted
    purchase_record.entitlement_started_at = entitlement_started_at
    purchase_record.entitlement_expires_at = entitlement_expires_at
    purchase_record.validated_at = now
    purchase_record.save(
        update_fields=[
            "status",
            "credits_granted",
            "entitlement_started_at",
            "entitlement_expires_at",
            "validated_at",
            "updated_at",
        ]
    )
    return purchase_record, created


def _valid_unlocked_contact(unlocked, *, now=None):
    if unlocked is None:
        return None
    current_time = now or timezone.now()
    expires_at = getattr(unlocked, "expires_at", None)
    if expires_at and expires_at <= current_time:
        return None
    return unlocked


def _contact_unlock_campaign_started_at(policy):
    if policy is None:
        return None
    return (
        getattr(policy, "paid_window_started_at", None)
        or getattr(policy, "set_at", None)
        or getattr(getattr(policy, "vacancy", None), "approved_at", None)
        or getattr(getattr(policy, "vacancy", None), "published_at", None)
    )


def get_vacancy_contact_unlock_stats(vacancy, *, policy=None):
    policy = policy or getattr(vacancy, "contact_access_policy", None)
    campaign_started_at = _contact_unlock_campaign_started_at(policy)
    tx_qs = WalletTransaction.objects.filter(
        kind="contact_unlock",
        related_vacancy=vacancy,
    )
    if campaign_started_at is not None:
        tx_qs = tx_qs.filter(created_at__gte=campaign_started_at)

    aggregate = tx_qs.aggregate(
        total_unlocks=Count("id"),
        paid_unlocks=Count("id", filter=Q(metadata__method="credits")),
        ad_unlocks=Count("id", filter=Q(metadata__method="ad")),
        subscription_unlocks=Count("id", filter=Q(metadata__method="subscription")),
        unique_users=Count("user", distinct=True),
        paid_spent=Sum("delta_paid_credits", filter=Q(metadata__method="credits")),
        bonus_spent=Sum("delta_bonus_credits", filter=Q(metadata__method="credits")),
        last_opened_at=Max("created_at"),
    )
    paid_spent = _credit_decimal(aggregate.get("paid_spent") or ZERO_CREDITS)
    bonus_spent = _credit_decimal(aggregate.get("bonus_spent") or ZERO_CREDITS)
    return {
        "campaign_started_at": campaign_started_at,
        "total_unlocks": int(aggregate.get("total_unlocks") or 0),
        "paid_unlocks": int(aggregate.get("paid_unlocks") or 0),
        "ad_unlocks": int(aggregate.get("ad_unlocks") or 0),
        "subscription_unlocks": int(aggregate.get("subscription_unlocks") or 0),
        "unique_users": int(aggregate.get("unique_users") or 0),
        "earned_credits": max(ZERO_CREDITS, _credit_decimal(-(paid_spent + bonus_spent))),
        "last_opened_at": aggregate.get("last_opened_at"),
    }


def _contact_unlock_mode_state(vacancy, *, policy=None, now=None):
    current_time = now or timezone.now()
    policy = policy or get_or_create_contact_policy(vacancy)
    stats = get_vacancy_contact_unlock_stats(vacancy, policy=policy)
    deadline = policy.paid_window_deadline()
    paid_window_active = bool(deadline and current_time < deadline)
    paid_click_limit = getattr(policy, "contact_unlock_paid_click_limit", None)
    paid_click_limit = int(paid_click_limit) if paid_click_limit else None
    paid_unlocks_count = int(stats["paid_unlocks"] or 0)
    paid_click_limit_reached = bool(
        paid_click_limit is not None and paid_unlocks_count >= paid_click_limit
    )

    raw_mode = (getattr(policy, "contact_unlock_mode", "") or "ad_forever").strip()
    if raw_mode == "paid_then_ad":
        if paid_click_limit_reached:
            current_mode = "ad"
        elif deadline is not None:
            current_mode = "paid" if paid_window_active else "ad"
        elif paid_click_limit is not None:
            current_mode = "paid"
        else:
            current_mode = "paid"
    elif raw_mode == "paid_forever":
        current_mode = "ad" if paid_click_limit_reached else "paid"
    else:
        current_mode = "ad"

    paid_unlocks_remaining = None
    if paid_click_limit is not None:
        paid_unlocks_remaining = max(0, paid_click_limit - paid_unlocks_count)

    return {
        "current_mode": current_mode,
        "deadline": deadline,
        "paid_window_active": bool(current_mode == "paid" and raw_mode == "paid_then_ad" and deadline and current_time < deadline),
        "paid_click_limit": paid_click_limit,
        "paid_unlocks_count": paid_unlocks_count,
        "paid_unlocks_remaining": paid_unlocks_remaining,
        "paid_click_limit_reached": paid_click_limit_reached,
        "stats": stats,
    }


def get_active_unlocked_contact(user, vacancy, *, now=None):
    unlocked = (
        UnlockedContact.objects.filter(user=user, vacancy=vacancy)
        .order_by("-opened_at", "-id")
        .first()
    )
    return _valid_unlocked_contact(unlocked, now=now)


def _current_employer_daily_submission_usage(profile, *, now):
    if profile.employer_daily_submission_date == now.date():
        return int(profile.employer_daily_submissions_used or 0)
    return 0


def build_vacancy_submission_state(user, *, flow, now=None):
    if flow not in {"create", "edit_resubmit"}:
        raise ValueError("invalid_submission_flow")
    if not getattr(user, "is_authenticated", False):
        raise ValueError("auth_required")

    current_time = now or timezone.now()
    config = get_economy_config()
    profile = get_or_create_monetization_profile(user)
    wallet = get_or_create_wallet(user)

    if flow == "create":
        free_limit = int(config.free_create_ad_submissions_limit or 0)
        free_used = int(profile.free_create_ad_submissions_used or 0)
        base_price = int(config.vacancy_submit_price_credits or 0)
        tx_kind = "vacancy_submit"
    else:
        free_limit = int(config.free_edit_ad_resubmissions_limit or 0)
        free_used = int(profile.free_edit_ad_resubmissions_used or 0)
        base_price = int(config.vacancy_edit_resubmit_price_credits or 0)
        tx_kind = "vacancy_edit_resubmit"

    free_remaining = max(0, free_limit - free_used)
    employer_subscription_active = profile.has_employer_subscription(current_time)
    employer_daily_limit = int(config.employer_daily_free_submissions_limit or 0)
    employer_daily_used = _current_employer_daily_submission_usage(
        profile,
        now=current_time,
    )
    employer_daily_remaining = max(0, employer_daily_limit - employer_daily_used)
    if not employer_subscription_active:
        employer_daily_remaining = 0

    if employer_subscription_active and employer_daily_remaining > 0:
        current_action = "subscription_free"
        effective_price = 0
    elif free_remaining > 0:
        current_action = "ad"
        effective_price = 0
    else:
        current_action = "paid"
        effective_price = base_price

    return {
        "flow": flow,
        "current_action": current_action,
        "expected_method": {
            "subscription_free": "subscription",
            "ad": "ad",
            "paid": "credits",
        }[current_action],
        "base_price_credits": base_price,
        "effective_price_credits": effective_price,
        "free_ad_limit": free_limit,
        "free_ad_used": free_used,
        "free_ad_remaining": free_remaining,
        "employer_subscription_active": employer_subscription_active,
        "employer_daily_free_limit": employer_daily_limit,
        "employer_daily_free_used": employer_daily_used,
        "employer_daily_free_remaining": employer_daily_remaining,
        "wallet_total_credits": wallet.total_credits,
        "can_afford": wallet.total_credits >= effective_price,
        "transaction_kind": tx_kind,
    }


@transaction.atomic
def apply_vacancy_submission_action(
    user,
    *,
    flow,
    method,
    related_vacancy=None,
    now=None,
):
    current_time = now or timezone.now()
    normalized_method = (method or "").strip().lower()
    state = build_vacancy_submission_state(user, flow=flow, now=current_time)

    if normalized_method != state["expected_method"]:
        raise EconomyActionRequiredError("submission_action_required", state)

    profile = UserMonetizationProfile.objects.select_for_update().filter(user=user).first()
    if profile is None:
        profile = UserMonetizationProfile.objects.create(user=user)

    if state["current_action"] == "subscription_free":
        if profile.employer_daily_submission_date != current_time.date():
            profile.employer_daily_submission_date = current_time.date()
            profile.employer_daily_submissions_used = 0
        profile.employer_daily_submissions_used = int(profile.employer_daily_submissions_used or 0) + 1
        profile.save(
            update_fields=[
                "employer_daily_submission_date",
                "employer_daily_submissions_used",
                "updated_at",
            ]
        )
        _, tx = record_wallet_event(
            user,
            kind=state["transaction_kind"],
            note="Employer subscription submission",
            related_vacancy=related_vacancy,
            metadata={"method": "subscription", "flow": flow},
        )
    elif state["current_action"] == "ad":
        if flow == "create":
            profile.free_create_ad_submissions_used = int(
                profile.free_create_ad_submissions_used or 0
            ) + 1
            profile.save(
                update_fields=[
                    "free_create_ad_submissions_used",
                    "updated_at",
                ]
            )
        else:
            profile.free_edit_ad_resubmissions_used = int(
                profile.free_edit_ad_resubmissions_used or 0
            ) + 1
            profile.save(
                update_fields=[
                    "free_edit_ad_resubmissions_used",
                    "updated_at",
                ]
            )
        _, tx = record_wallet_event(
            user,
            kind=state["transaction_kind"],
            note="Rewarded ad submission",
            related_vacancy=related_vacancy,
            metadata={"method": "ad", "flow": flow},
        )
    else:
        _, tx = spend_credits(
            user,
            amount=state["effective_price_credits"],
            kind=state["transaction_kind"],
            note="Paid vacancy submission",
            related_vacancy=related_vacancy,
            metadata={"method": "credits", "flow": flow},
        )

    refreshed_state = build_vacancy_submission_state(user, flow=flow, now=current_time)
    return refreshed_state, tx


def build_contact_access_state(user, vacancy, *, now=None):
    current_time = now or timezone.now()
    policy = get_or_create_contact_policy(vacancy)
    config = get_economy_config()
    mode_state = _contact_unlock_mode_state(vacancy, policy=policy, now=current_time)
    deadline = mode_state["deadline"]
    paid_window_active = bool(mode_state["paid_window_active"])
    paid_click_limit = mode_state["paid_click_limit"]
    paid_unlocks_count = mode_state["paid_unlocks_count"]
    paid_unlocks_remaining = mode_state["paid_unlocks_remaining"]
    paid_click_limit_reached = bool(mode_state["paid_click_limit_reached"])
    if getattr(user, "is_authenticated", False):
        if getattr(user, "is_staff", False) or vacancy.created_by_id == getattr(user, "id", None):
            return {
                "vacancy_id": vacancy.id,
                "is_unlocked": True,
                "unlocked_until": None,
                "unlock_source": "owner_free",
                "contact_access_duration_minutes": int(
                    getattr(config, "contact_access_duration_minutes", CONTACT_ACCESS_DURATION_MINUTES_DEFAULT)
                    or CONTACT_ACCESS_DURATION_MINUTES_DEFAULT
                ),
                "mode": policy.contact_unlock_mode,
                "current_action": "already_unlocked",
                "expected_method": "",
                "base_price_credits": _credit_json_value(policy.contact_unlock_price_credits or ZERO_CREDITS),
                "effective_price_credits": _credit_json_value(ZERO_CREDITS),
                "contact_unlock_timer_hours": policy.contact_unlock_timer_hours,
                "paid_window_deadline": deadline,
                "paid_window_is_active": paid_window_active,
                "paid_unlock_click_limit": paid_click_limit,
                "paid_unlocks_count": paid_unlocks_count,
                "paid_unlocks_remaining": paid_unlocks_remaining,
                "paid_click_limit_reached": paid_click_limit_reached,
                "can_use_ad": False,
                "ad_required": False,
                "has_seeker_subscription": False,
                "wallet_total_credits": _credit_json_value(ZERO_CREDITS),
                "can_afford": True,
            }

    profile = get_or_create_monetization_profile(user) if getattr(user, "is_authenticated", False) else None
    wallet = get_or_create_wallet(user) if getattr(user, "is_authenticated", False) else None

    unlocked = None
    if getattr(user, "is_authenticated", False):
        unlocked = get_active_unlocked_contact(user, vacancy, now=current_time)
    current_mode = mode_state["current_mode"]

    base_price = _credit_decimal(policy.contact_unlock_price_credits or ZERO_CREDITS)
    effective_price = base_price
    has_seeker_subscription = bool(profile and profile.has_seeker_subscription(current_time))
    discount_percent = int(config.seeker_contact_discount_percent or 0)

    action = current_mode
    ad_required = current_mode == "ad"
    if has_seeker_subscription:
        if current_mode == "paid":
            remaining_percent = max(0, 100 - discount_percent)
            effective_price = (
                _credit_decimal(
                    base_price * Decimal(str(remaining_percent)) / Decimal("100")
                )
                if base_price > ZERO_CREDITS
                else ZERO_CREDITS
            )
        else:
            action = "subscription_free"
            ad_required = False
            effective_price = ZERO_CREDITS

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
        "expected_method": (
            ""
            if unlocked
            else {
                "paid": "credits",
                "ad": "ad",
                "subscription_free": "subscription",
            }.get(action, "")
        ),
        "base_price_credits": _credit_json_value(base_price),
        "effective_price_credits": _credit_json_value(ZERO_CREDITS if unlocked else effective_price),
        "contact_unlock_timer_hours": policy.contact_unlock_timer_hours,
        "paid_window_deadline": deadline,
        "paid_window_is_active": paid_window_active,
        "paid_unlock_click_limit": paid_click_limit,
        "paid_unlocks_count": paid_unlocks_count,
        "paid_unlocks_remaining": paid_unlocks_remaining,
        "paid_click_limit_reached": paid_click_limit_reached,
        "can_use_ad": bool(not unlocked and (action == "ad" or action == "subscription_free")),
        "ad_required": bool(not unlocked and ad_required),
        "has_seeker_subscription": has_seeker_subscription,
        "wallet_total_credits": _credit_json_value(wallet.total_credits if wallet else ZERO_CREDITS),
        "can_afford": bool(wallet and wallet.total_credits >= effective_price),
    }


@transaction.atomic
def unlock_vacancy_contacts(
    user,
    vacancy,
    *,
    method,
    now=None,
):
    current_time = now or timezone.now()
    normalized_method = (method or "").strip().lower()
    state = build_contact_access_state(user, vacancy, now=current_time)

    if state["is_unlocked"]:
        return (
            get_active_unlocked_contact(user, vacancy, now=current_time),
            state,
            None,
        )

    expected_method = {
        "paid": "credits",
        "ad": "ad",
        "subscription_free": "subscription",
    }.get(state["current_action"], "")
    if normalized_method != expected_method:
        raise EconomyActionRequiredError("contact_unlock_action_required", state)

    tx = None
    charged_credits = ZERO_CREDITS
    unlock_source = "paid"
    metadata = {"method": normalized_method, "state_action": state["current_action"]}
    should_persist_unlock = True
    expires_at = None

    if state["current_action"] == "paid":
        config = get_economy_config()
        duration_minutes = int(
            getattr(config, "contact_access_duration_minutes", CONTACT_ACCESS_DURATION_MINUTES_DEFAULT)
            or CONTACT_ACCESS_DURATION_MINUTES_DEFAULT
        )
        expires_at = current_time + timedelta(minutes=duration_minutes)
        charged_credits = _credit_decimal(state["effective_price_credits"] or ZERO_CREDITS)
        unlock_source = "paid"
        metadata["charged_credits"] = str(charged_credits)
        _, tx = spend_credits(
            user,
            amount=charged_credits,
            kind="contact_unlock",
            note="Paid contact unlock",
            related_vacancy=vacancy,
            metadata=metadata,
        )
    elif state["current_action"] == "ad":
        should_persist_unlock = False
        unlock_source = "ad"
        _, tx = record_wallet_event(
            user,
            kind="contact_unlock",
            note="Rewarded ad contact unlock",
            related_vacancy=vacancy,
            metadata=metadata,
        )
    else:
        config = get_economy_config()
        duration_minutes = int(
            getattr(config, "contact_access_duration_minutes", CONTACT_ACCESS_DURATION_MINUTES_DEFAULT)
            or CONTACT_ACCESS_DURATION_MINUTES_DEFAULT
        )
        expires_at = current_time + timedelta(minutes=duration_minutes)
        unlock_source = "subscription"
        _, tx = record_wallet_event(
            user,
            kind="contact_unlock",
            note="Subscription contact unlock",
            related_vacancy=vacancy,
            metadata=metadata,
        )

    unlocked = None
    if should_persist_unlock:
        unlocked, created = UnlockedContact.objects.update_or_create(
            user=user,
            vacancy=vacancy,
            defaults={
                "expires_at": expires_at,
                "unlock_source": unlock_source,
                "charged_credits": charged_credits,
                "metadata": metadata,
            },
        )
        if not created:
            unlocked.opened_at = current_time
            unlocked.save(update_fields=["opened_at"])

    refreshed_state = build_contact_access_state(user, vacancy, now=current_time)
    return unlocked, refreshed_state, tx
