"""Subscription pricing, upgrades, and billing orchestration service."""

from __future__ import annotations

from datetime import UTC, datetime

from .db import session_scope
from .invoice_service import create_invoice_draft, mark_invoice_paid
from .logger import get_logger
from .subscription_model import (
    PLAN_MONTHLY_CENTS,
    Invoice,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from .user_model import User

_log = get_logger("billing_service")

VOLUME_DISCOUNT_THRESHOLD = 10
VOLUME_DISCOUNT_BPS = 1000


def get_plan_monthly_rate(tier: PlanTier, seats: int = 1) -> int:
    """Compute monthly recurring charge in cents with seat volume discounts."""
    if seats < 1:
        raise ValueError("Subscription seat count must be at least 1.")
    base_cents = PLAN_MONTHLY_CENTS[tier]
    gross_cents = base_cents * seats
    if seats >= VOLUME_DISCOUNT_THRESHOLD:
        discount = (gross_cents * VOLUME_DISCOUNT_BPS) // 10_000
        return gross_cents - discount
    return gross_cents


def calculate_prorated_charge(
    current_tier: PlanTier,
    target_tier: PlanTier,
    seats: int,
    remaining_days: int,
    cycle_days: int = 30,
) -> int:
    """Calculate prorated upgrade delta in cents for the remainder of a cycle."""
    if remaining_days <= 0 or cycle_days <= 0:
        return 0
    clamped_days = min(remaining_days, cycle_days)
    current_rate = get_plan_monthly_rate(current_tier, seats=seats)
    target_rate = get_plan_monthly_rate(target_tier, seats=seats)
    delta = target_rate - current_rate
    if delta <= 0:
        return 0
    return (delta * clamped_days) // cycle_days


def create_subscription(user_id: int, tier: PlanTier, seats: int = 1) -> Subscription:
    """Provision a new subscription and generate its initial settled invoice."""
    monthly_amount = get_plan_monthly_rate(tier, seats=seats)
    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None or not user.is_active:
            raise ValueError(f"Active user {user_id} was not found.")

        subscription = Subscription(
            user_id=user.id,
            plan_tier=tier.value,
            status=SubscriptionStatus.ACTIVE.value,
            seats=seats,
            monthly_amount_cents=monthly_amount,
            billing_cycle_day=datetime.now(UTC).day,
        )
        session.add(subscription)
        session.flush()
        session.refresh(subscription)

    initial_invoice = create_invoice_draft(subscription)
    mark_invoice_paid(initial_invoice.id)
    _log.info(
        "subscription.created",
        subscription_id=subscription.id,
        tier=tier.value,
        seats=seats,
    )
    return subscription


def upgrade_subscription(
    subscription_id: int,
    target_tier: PlanTier,
    seats: int,
    remaining_days: int,
) -> tuple[Subscription, Invoice]:
    """Upgrade an active subscription and issue a prorated adjustment invoice."""
    with session_scope() as session:
        subscription = session.get(Subscription, subscription_id)
        if subscription is None:
            raise ValueError(f"Subscription {subscription_id} does not exist.")
        if subscription.status == SubscriptionStatus.CANCELED.value:
            raise ValueError("Cannot upgrade a canceled subscription.")

        current_tier = PlanTier(subscription.plan_tier)
        proration_cents = calculate_prorated_charge(
            current_tier=current_tier,
            target_tier=target_tier,
            seats=seats,
            remaining_days=remaining_days,
        )
        subscription.plan_tier = target_tier.value
        subscription.seats = seats
        subscription.monthly_amount_cents = get_plan_monthly_rate(target_tier, seats=seats)
        session.add(subscription)
        session.flush()
        session.refresh(subscription)

    invoice = create_invoice_draft(subscription, extra_charge_cents=proration_cents)
    _log.info(
        "subscription.upgraded",
        subscription_id=subscription.id,
        target_tier=target_tier.value,
        proration_cents=proration_cents,
    )
    return subscription, invoice


def cancel_subscription(subscription_id: int, immediate: bool = False) -> Subscription:
    """Cancel an active subscription and optionally mark it past-due/canceled."""
    with session_scope() as session:
        subscription = session.get(Subscription, subscription_id)
        if subscription is None:
            raise ValueError(f"Subscription {subscription_id} does not exist.")
        subscription.status = (
            SubscriptionStatus.CANCELED.value if immediate else SubscriptionStatus.PAST_DUE.value
        )
        subscription.canceled_at = datetime.now(UTC)
        session.add(subscription)
        session.flush()
        session.refresh(subscription)
        _log.info("subscription.canceled", subscription_id=subscription.id, immediate=immediate)
        return subscription
