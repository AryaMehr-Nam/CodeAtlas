"""SQLAlchemy ORM models for SaaS subscriptions and billing invoices."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .user_model import User


class PlanTier(StrEnum):
    """Commercial subscription tiers supported by SampleSaaS."""

    STARTER = "starter"
    GROWTH = "growth"
    ENTERPRISE = "enterprise"


class SubscriptionStatus(StrEnum):
    """Lifecycle states for a customer subscription."""

    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"


class InvoiceStatus(StrEnum):
    """Settlement states for issued billing invoices."""

    DRAFT = "draft"
    OPEN = "open"
    PAID = "paid"
    VOID = "void"


PLAN_MONTHLY_CENTS: dict[PlanTier, int] = {
    PlanTier.STARTER: 2900,
    PlanTier.GROWTH: 9900,
    PlanTier.ENTERPRISE: 39900,
}


class Subscription(Base):
    """Customer subscription agreement tied to an organization user."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    plan_tier: Mapped[str] = mapped_column(String(32), default=PlanTier.STARTER.value)
    status: Mapped[str] = mapped_column(String(32), default=SubscriptionStatus.ACTIVE.value)
    seats: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    monthly_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    billing_cycle_day: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship("User")

    def effective_mrr_cents(self) -> int:
        """Compute active recurring revenue contribution in cents."""
        if self.status in {SubscriptionStatus.ACTIVE.value, SubscriptionStatus.PAST_DUE.value}:
            return self.monthly_amount_cents
        return 0


class Invoice(Base):
    """Ledger invoice generated from subscription billing activity."""

    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscriptions.id"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    subtotal_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default=InvoiceStatus.DRAFT.value)
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC) + timedelta(days=14),
        nullable=False,
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    subscription: Mapped[Subscription] = relationship("Subscription")

    def is_overdue(self, reference_time: datetime | None = None) -> bool:
        """Return True if the invoice remains open past its due timestamp."""
        if self.status != InvoiceStatus.OPEN.value:
            return False
        now = reference_time or datetime.now(UTC)
        return self.due_at < now
