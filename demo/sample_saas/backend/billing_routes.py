"""FastAPI billing router for subscription management and invoice settlement."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from .auth_service import resolve_current_user
from .billing_service import cancel_subscription, create_subscription, upgrade_subscription
from .invoice_service import finalize_open_invoices, list_account_invoices
from .logger import get_logger
from .subscription_model import PlanTier

router = APIRouter(prefix="/api/billing", tags=["billing"])
_log = get_logger("billing_routes")


class CreateSubscriptionRequest(BaseModel):
    """Payload to start a new customer subscription."""

    plan_tier: PlanTier = PlanTier.STARTER
    seats: int = Field(default=1, ge=1, le=500)


class UpgradeSubscriptionRequest(BaseModel):
    """Payload to upgrade plan tier or seat allocation."""

    target_tier: PlanTier
    seats: int = Field(default=1, ge=1, le=500)
    remaining_days: int = Field(default=15, ge=1, le=31)


@router.post("/subscriptions", status_code=status.HTTP_201_CREATED)
def create_subscription_endpoint(
    payload: CreateSubscriptionRequest,
    authorization: str = Header(default=""),
) -> dict[str, object]:
    """Create a subscription for the authenticated account."""
    user = resolve_current_user(authorization.removeprefix("Bearer ").strip())
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized.")

    try:
        sub = create_subscription(user_id=user.id, tier=payload.plan_tier, seats=payload.seats)
    except ValueError as exc:
        _log.warning("billing.create_failed", user_id=user.id, reason=str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {
        "id": sub.id,
        "plan_tier": sub.plan_tier,
        "status": sub.status,
        "seats": sub.seats,
        "monthly_amount_cents": sub.monthly_amount_cents,
    }


@router.post("/subscriptions/{subscription_id}/upgrade")
def upgrade_subscription_endpoint(
    subscription_id: int,
    payload: UpgradeSubscriptionRequest,
) -> dict[str, object]:
    """Upgrade a subscription and return the resulting prorated invoice summary."""
    try:
        sub, invoice = upgrade_subscription(
            subscription_id=subscription_id,
            target_tier=payload.target_tier,
            seats=payload.seats,
            remaining_days=payload.remaining_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {
        "subscription_id": sub.id,
        "plan_tier": sub.plan_tier,
        "monthly_amount_cents": sub.monthly_amount_cents,
        "invoice_id": invoice.id,
        "invoice_total_cents": invoice.total_cents,
    }


@router.post("/subscriptions/{subscription_id}/cancel")
def cancel_subscription_endpoint(
    subscription_id: int,
    immediate: bool = Query(default=False),
) -> dict[str, object]:
    """Cancel an existing subscription immediately or at period end."""
    try:
        sub = cancel_subscription(subscription_id=subscription_id, immediate=immediate)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return {"subscription_id": sub.id, "status": sub.status}


@router.get("/invoices")
def list_invoices_endpoint(
    user_id: int = Query(..., ge=1),
    include_void: bool = Query(default=False),
) -> list[dict[str, object]]:
    """List invoices belonging to a customer account."""
    invoices = list_account_invoices(user_id=user_id, include_void=include_void)
    return [
        {
            "id": inv.id,
            "subscription_id": inv.subscription_id,
            "subtotal_cents": inv.subtotal_cents,
            "tax_cents": inv.tax_cents,
            "total_cents": inv.total_cents,
            "status": inv.status,
        }
        for inv in invoices
    ]


@router.post("/subscriptions/{subscription_id}/finalize")
def finalize_invoices_endpoint(subscription_id: int) -> dict[str, object]:
    """Finalize and settle all open invoices for a subscription."""
    try:
        settled = finalize_open_invoices(subscription_id=subscription_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    _log.info("billing.invoices_finalized", subscription_id=subscription_id, count=len(settled))
    return {"subscription_id": subscription_id, "settled_count": len(settled)}
