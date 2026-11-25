"""FastAPI analytics router exposing executive SaaS metrics and forecasts."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from .analytics_service import (
    build_executive_dashboard,
    compute_cohort_retention,
    forecast_arr_scenarios,
    score_churn_risk,
)
from .logger import get_logger

router = APIRouter(prefix="/api/analytics", tags=["analytics"])
_log = get_logger("analytics_routes")


@router.get("/overview")
def overview_endpoint(horizon_months: int = Query(default=12, ge=1, le=36)) -> dict[str, object]:
    """Return full executive MRR, retention, churn risk, and forecast overview."""
    _log.info("analytics.overview_requested", horizon_months=horizon_months)
    return build_executive_dashboard(horizon_months=horizon_months)


@router.get("/cohorts")
def cohorts_endpoint(months_back: int = Query(default=6, ge=1, le=24)) -> list[dict[str, object]]:
    """Return monthly signup cohort retention metrics."""
    return compute_cohort_retention(months_back=months_back)


@router.get("/churn-risk")
def churn_risk_endpoint(
    min_score: int = Query(default=40, ge=0, le=100),
) -> list[dict[str, object]]:
    """Return accounts exceeding the minimum churn risk threshold."""
    accounts = score_churn_risk(min_score=min_score)
    return [
        {
            "masked_email": item.masked_email,
            "organization_id": item.organization_id,
            "plan_tier": item.plan_tier,
            "risk_score": item.risk_score,
            "risk_band": item.risk_band,
            "overdue_cents": item.overdue_cents,
            "recommended_action": item.recommended_action,
        }
        for item in accounts
    ]


@router.get("/forecast")
def forecast_endpoint(
    horizon_months: int = Query(default=12, ge=1, le=36),
    scenario: str = Query(default="base"),
) -> list[dict[str, int]]:
    """Return projected monthly MRR and ARR trajectories for the chosen scenario."""
    if scenario not in {"conservative", "base", "bull"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Scenario must be conservative, base, or bull.",
        )
    return forecast_arr_scenarios(horizon_months=horizon_months, scenario=scenario)
