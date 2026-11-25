"""Invoice drafting, tax computation, settlement, and aging service."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from .billing_service import calculate_prorated_charge, get_plan_monthly_rate
from .crypto import generate_idempotency_key
from .db import session_scope
from .logger import get_logger
from .subscription_model import Invoice, InvoiceStatus, PlanTier, Subscription

_log = get_logger("invoice_service")


def create_invoice_draft(
    subscription: Subscription,
    extra_charge_cents: int = 0,
    tax_rate_bps: int = 825,
) -> Invoice:
    """Create an open invoice draft with tax calculation and idempotency key."""
    subtotal = max(subscription.monthly_amount_cents + extra_charge_cents, 0)
    tax_cents = (subtotal * tax_rate_bps) // 10_000
    total_cents = subtotal + tax_cents
    key = generate_idempotency_key(
        "inv",
        f"{subscription.id}:{subtotal}:{datetime.now(UTC).strftime('%Y%m%d%H%M')}",
    )

    with session_scope() as session:
        invoice = Invoice(
            subscription_id=subscription.id,
            user_id=subscription.user_id,
            subtotal_cents=subtotal,
            tax_cents=tax_cents,
            total_cents=total_cents,
            status=InvoiceStatus.OPEN.value,
            idempotency_key=key,
        )
        session.add(invoice)
        session.flush()
        session.refresh(invoice)
        _log.info("invoice.drafted", invoice_id=invoice.id, total_cents=total_cents)
        return invoice


def mark_invoice_paid(invoice_id: int) -> Invoice:
    """Transition an open invoice to paid and record settlement timestamp."""
    with session_scope() as session:
        invoice = session.get(Invoice, invoice_id)
        if invoice is None:
            raise ValueError(f"Invoice {invoice_id} was not found.")
        if invoice.status == InvoiceStatus.VOID.value:
            raise ValueError("Cannot pay a voided invoice.")

        invoice.status = InvoiceStatus.PAID.value
        invoice.paid_at = datetime.now(UTC)
        session.add(invoice)
        session.flush()
        session.refresh(invoice)
        _log.info("invoice.paid", invoice_id=invoice.id, total_cents=invoice.total_cents)
        return invoice


def list_account_invoices(user_id: int, include_void: bool = False) -> list[Invoice]:
    """Retrieve ledger invoices belonging to a specific customer user."""
    with session_scope() as session:
        stmt = select(Invoice).where(Invoice.user_id == user_id)
        if not include_void:
            stmt = stmt.where(Invoice.status != InvoiceStatus.VOID.value)
        return list(session.scalars(stmt.order_by(Invoice.issued_at.desc())).all())


def finalize_open_invoices(subscription_id: int) -> list[Invoice]:
    """Settle all open invoices for a subscription using current billing rates."""
    settled: list[Invoice] = []
    with session_scope() as session:
        subscription = session.get(Subscription, subscription_id)
        if subscription is None:
            raise ValueError(f"Subscription {subscription_id} was not found.")
        expected_rate = get_plan_monthly_rate(
            PlanTier(subscription.plan_tier),
            seats=subscription.seats,
        )
        open_invoices = list(
            session.scalars(
                select(Invoice).where(
                    Invoice.subscription_id == subscription_id,
                    Invoice.status == InvoiceStatus.OPEN.value,
                )
            ).all()
        )
        for invoice in open_invoices:
            if invoice.subtotal_cents == 0:
                invoice.subtotal_cents = expected_rate
                invoice.total_cents = expected_rate + invoice.tax_cents
            invoice.status = InvoiceStatus.PAID.value
            invoice.paid_at = datetime.now(UTC)
            session.add(invoice)
            settled.append(invoice)

    _log.info("invoice.batch_finalized", subscription_id=subscription_id, count=len(settled))
    return settled


def recalculate_prorated_invoice(
    subscription: Subscription,
    previous_tier: PlanTier,
    remaining_days: int,
) -> Invoice:
    """Issue a standalone proration adjustment invoice for mid-cycle plan changes."""
    target_tier = PlanTier(subscription.plan_tier)
    prorated_delta = calculate_prorated_charge(
        current_tier=previous_tier,
        target_tier=target_tier,
        seats=subscription.seats,
        remaining_days=remaining_days,
    )
    return create_invoice_draft(subscription, extra_charge_cents=prorated_delta)


def summarize_invoice_aging(invoices: list[Invoice], reference_time: datetime) -> dict[str, int]:
    """Group open and overdue invoice balances into standard aging buckets."""
    buckets = {"current": 0, "overdue_1_30": 0, "overdue_31_plus": 0}
    for invoice in invoices:
        if invoice.status != InvoiceStatus.OPEN.value:
            continue
        if not invoice.is_overdue(reference_time):
            buckets["current"] += invoice.total_cents
            continue
        overdue_days = (reference_time - invoice.due_at).days
        if overdue_days <= 30:
            buckets["overdue_1_30"] += invoice.total_cents
        else:
            buckets["overdue_31_plus"] += invoice.total_cents
    return buckets
