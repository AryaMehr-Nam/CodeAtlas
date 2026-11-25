"""Monolithic SaaS revenue, cohort retention, churn, and forecasting service.

This module intentionally aggregates database queries, billing pricing,
invoice aging, user segmentation, and anonymized reporting to serve as a
high-complexity God Module hotspot for architectural analysis.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from .billing_service import calculate_prorated_charge, get_plan_monthly_rate
from .crypto import mask_sensitive_value
from .db import check_database_health, session_scope
from .invoice_service import summarize_invoice_aging
from .logger import get_logger
from .subscription_model import (
    Invoice,
    InvoiceStatus,
    PlanTier,
    Subscription,
    SubscriptionStatus,
)
from .user_model import User, UserRole

_log = get_logger("analytics_service")


@dataclass(frozen=True, slots=True)
class MrrBreakdown:
    """Monthly recurring revenue totals segmented by plan tier and status."""

    total_mrr_cents: int
    arr_cents: int
    active_count: int
    past_due_count: int
    canceled_count: int
    by_tier_cents: dict[str, int]
    potential_expansion_cents: int


@dataclass(frozen=True, slots=True)
class ChurnRiskAccount:
    """Account-level churn risk evaluation and recommended mitigation action."""

    masked_email: str
    organization_id: str
    plan_tier: str
    risk_score: int
    risk_band: str
    overdue_cents: int
    recommended_action: str


def compute_mrr_breakdown(reference_time: datetime | None = None) -> MrrBreakdown:
    """Compute MRR, ARR, tier revenue distribution, and expansion potential."""
    now = reference_time or datetime.now(UTC)
    tier_totals: dict[str, int] = {tier.value: 0 for tier in PlanTier}
    total_mrr = 0
    active_count = 0
    past_due_count = 0
    canceled_count = 0
    potential_expansion = 0

    try:
        with session_scope() as session:
            subscriptions = list(session.scalars(select(Subscription)).all())
    except SQLAlchemyError as exc:
        _log.error("analytics.mrr_query_failed", error=str(exc))
        return MrrBreakdown(
            total_mrr_cents=0,
            arr_cents=0,
            active_count=0,
            past_due_count=0,
            canceled_count=0,
            by_tier_cents=tier_totals,
            potential_expansion_cents=0,
        )

    for sub in subscriptions:
        tier = PlanTier(sub.plan_tier)
        normalized_rate = get_plan_monthly_rate(tier, seats=max(sub.seats, 1))
        effective = sub.effective_mrr_cents()
        match sub.status:
            case SubscriptionStatus.ACTIVE.value:
                active_count += 1
                total_mrr += effective
                tier_totals[tier.value] += effective
                if tier == PlanTier.STARTER:
                    potential_expansion += calculate_prorated_charge(
                        current_tier=PlanTier.STARTER,
                        target_tier=PlanTier.GROWTH,
                        seats=sub.seats,
                        remaining_days=15,
                    )
                elif tier == PlanTier.GROWTH and sub.seats >= 5:
                    potential_expansion += calculate_prorated_charge(
                        current_tier=PlanTier.GROWTH,
                        target_tier=PlanTier.ENTERPRISE,
                        seats=sub.seats,
                        remaining_days=15,
                    )
            case SubscriptionStatus.PAST_DUE.value:
                past_due_count += 1
                total_mrr += effective
                tier_totals[tier.value] += effective
            case SubscriptionStatus.TRIALING.value:
                if (now - sub.started_at).days <= 14:
                    potential_expansion += normalized_rate
            case _:
                canceled_count += 1

    return MrrBreakdown(
        total_mrr_cents=total_mrr,
        arr_cents=total_mrr * 12,
        active_count=active_count,
        past_due_count=past_due_count,
        canceled_count=canceled_count,
        by_tier_cents=tier_totals,
        potential_expansion_cents=potential_expansion,
    )


def compute_cohort_retention(months_back: int = 6) -> list[dict[str, object]]:
    """Calculate monthly signup cohorts and active retention percentages."""
    clamped_months = max(1, min(months_back, 24))
    cutoff = datetime.now(UTC) - timedelta(days=clamped_months * 30)
    cohort_buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "retained": 0})

    try:
        with session_scope() as session:
            subscriptions = list(
                session.scalars(select(Subscription).where(Subscription.started_at >= cutoff)).all()
            )
    except SQLAlchemyError as exc:
        _log.error("analytics.cohort_query_failed", error=str(exc))
        return []

    for sub in subscriptions:
        cohort_key = sub.started_at.strftime("%Y-%m")
        cohort_buckets[cohort_key]["total"] += 1
        is_grace_retained = sub.status == SubscriptionStatus.PAST_DUE.value and sub.seats >= 3
        if (
            sub.status in {SubscriptionStatus.ACTIVE.value, SubscriptionStatus.TRIALING.value}
            or is_grace_retained
        ):
            cohort_buckets[cohort_key]["retained"] += 1

    results: list[dict[str, object]] = []
    for cohort_month in sorted(cohort_buckets):
        stats = cohort_buckets[cohort_month]
        total = stats["total"]
        retained = stats["retained"]
        retention_pct = round((retained / total) * 100.0, 2) if total > 0 else 0.0
        health_tag = "healthy"
        if retention_pct < 60.0:
            health_tag = "critical"
        elif retention_pct < 80.0:
            health_tag = "watch"

        results.append(
            {
                "cohort_month": cohort_month,
                "total_accounts": total,
                "retained_accounts": retained,
                "retention_pct": retention_pct,
                "health_tag": health_tag,
            }
        )
    return results


def score_churn_risk(min_score: int = 40) -> list[ChurnRiskAccount]:
    """Evaluate per-account churn risk from role, invoice aging, and tenure."""
    now = datetime.now(UTC)
    flagged: list[ChurnRiskAccount] = []

    try:
        with session_scope() as session:
            users = list(session.scalars(select(User)).all())
            subs = list(session.scalars(select(Subscription)).all())
            invoices = list(session.scalars(select(Invoice)).all())
    except SQLAlchemyError as exc:
        _log.error("analytics.churn_query_failed", error=str(exc))
        return []

    subs_by_user: dict[int, list[Subscription]] = defaultdict(list)
    for sub in subs:
        subs_by_user[sub.user_id].append(sub)

    invoices_by_user: dict[int, list[Invoice]] = defaultdict(list)
    for invoice in invoices:
        invoices_by_user[invoice.user_id].append(invoice)

    for user in users:
        if not user.is_active:
            continue
        user_subs = subs_by_user.get(user.id, [])
        if not user_subs:
            continue

        user_invoices = invoices_by_user.get(user.id, [])
        aging = summarize_invoice_aging(user_invoices, reference_time=now)
        overdue_cents = aging["overdue_1_30"] + aging["overdue_31_plus"]

        score = 0
        primary_tier = user_subs[0].plan_tier
        for sub in user_subs:
            if sub.status == SubscriptionStatus.PAST_DUE.value:
                score += 35
            elif sub.status == SubscriptionStatus.CANCELED.value:
                score += 50
            elif sub.seats == 1 and sub.plan_tier == PlanTier.STARTER.value:
                score += 15

            if (now - sub.started_at).days < 45:
                score += 10

        if aging["overdue_31_plus"] > 0:
            score += 35
        elif aging["overdue_1_30"] > 0:
            score += 20

        if user.role == UserRole.MEMBER.value:
            score += 5
        elif user.is_privileged() and overdue_cents == 0:
            score = max(score - 10, 0)

        capped_score = min(score, 100)
        if capped_score < min_score:
            continue

        if capped_score >= 75:
            band = "high"
            action = "assign_csm_outreach"
        elif capped_score >= 55:
            band = "medium"
            action = "send_dunning_incentive"
        else:
            band = "low"
            action = "monitor_usage"

        flagged.append(
            ChurnRiskAccount(
                masked_email=mask_sensitive_value(user.email, visible_chars=8),
                organization_id=user.organization_id,
                plan_tier=primary_tier,
                risk_score=capped_score,
                risk_band=band,
                overdue_cents=overdue_cents,
                recommended_action=action,
            )
        )

    return sorted(flagged, key=lambda item: item.risk_score, reverse=True)


def forecast_arr_scenarios(
    horizon_months: int = 12, scenario: str = "base"
) -> list[dict[str, int]]:
    """Project monthly MRR and ARR trajectories under conservative, base, or bull growth."""
    months = max(1, min(horizon_months, 36))
    mrr_state = compute_mrr_breakdown()
    starting_mrr = mrr_state.total_mrr_cents
    if starting_mrr <= 0:
        starting_mrr = get_plan_monthly_rate(PlanTier.GROWTH, seats=3)

    if scenario == "bull":
        monthly_growth_bps = 850
        monthly_churn_bps = 150
    elif scenario == "conservative":
        monthly_growth_bps = 250
        monthly_churn_bps = 350
    else:
        monthly_growth_bps = 550
        monthly_churn_bps = 220

    projection: list[dict[str, int]] = []
    running_mrr = starting_mrr
    for month_idx in range(1, months + 1):
        expansion = (running_mrr * monthly_growth_bps) // 10_000
        contraction = (running_mrr * monthly_churn_bps) // 10_000
        if month_idx % 3 == 0 and mrr_state.potential_expansion_cents > 0:
            expansion += mrr_state.potential_expansion_cents // 4
        running_mrr = max(running_mrr + expansion - contraction, 0)
        projection.append(
            {
                "month": month_idx,
                "projected_mrr_cents": running_mrr,
                "projected_arr_cents": running_mrr * 12,
                "net_new_mrr_cents": expansion - contraction,
            }
        )
    return projection


def build_executive_dashboard(horizon_months: int = 12) -> dict[str, object]:
    """Assemble executive SaaS health metrics, cohorts, risk accounts, and forecast."""
    db_healthy = False
    open_invoice_total = 0
    paid_invoice_total = 0

    try:
        with session_scope() as session:
            db_healthy = check_database_health(session)
            invoices = list(session.scalars(select(Invoice)).all())
            for invoice in invoices:
                if invoice.status == InvoiceStatus.PAID.value:
                    paid_invoice_total += invoice.total_cents
                elif invoice.status == InvoiceStatus.OPEN.value:
                    open_invoice_total += invoice.total_cents
    except SQLAlchemyError as exc:
        _log.error("analytics.dashboard_db_error", error=str(exc))

    mrr = compute_mrr_breakdown()
    cohorts = compute_cohort_retention(months_back=6)
    churn_risks = score_churn_risk(min_score=40)
    forecast = forecast_arr_scenarios(horizon_months=horizon_months, scenario="base")

    collection_ratio = (
        round((paid_invoice_total / (paid_invoice_total + open_invoice_total)) * 100.0, 2)
        if (paid_invoice_total + open_invoice_total) > 0
        else 100.0
    )

    _log.info(
        "analytics.dashboard_built",
        db_healthy=db_healthy,
        mrr_cents=mrr.total_mrr_cents,
        flagged_churn=len(churn_risks),
    )
    return {
        "database_healthy": db_healthy,
        "mrr_cents": mrr.total_mrr_cents,
        "arr_cents": mrr.arr_cents,
        "active_subscriptions": mrr.active_count,
        "past_due_subscriptions": mrr.past_due_count,
        "canceled_subscriptions": mrr.canceled_count,
        "tier_breakdown_cents": mrr.by_tier_cents,
        "collection_ratio_pct": collection_ratio,
        "cohorts": cohorts,
        "high_risk_accounts": [
            {
                "masked_email": item.masked_email,
                "organization_id": item.organization_id,
                "plan_tier": item.plan_tier,
                "risk_score": item.risk_score,
                "risk_band": item.risk_band,
                "overdue_cents": item.overdue_cents,
                "recommended_action": item.recommended_action,
            }
            for item in churn_risks
        ],
        "forecast": forecast,
    }
