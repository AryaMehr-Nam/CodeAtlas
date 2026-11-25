import { useEffect, useState, type JSX } from "react";

import { fetchAnalyticsOverview } from "@/api/client";
import {
  formatUsdFromCents,
  PLAN_OPTIONS,
  type AnalyticsOverview,
  type PlanTier,
} from "@/types/billing";

import { PlanCard } from "../components/PlanCard";
import { BILLING_ROUTE } from "./BillingPage";

export function DashboardPage(): JSX.Element {
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);
  const [selectedTier, setSelectedTier] = useState<PlanTier>("growth");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchAnalyticsOverview(12)
      .then((data) => {
        if (active) {
          setOverview(data);
        }
      })
      .catch((err: unknown) => {
        if (active) {
          setErrorMessage(err instanceof Error ? err.message : "Failed to load overview");
        }
      });
    return () => {
      active = false;
    };
  }, []);

  if (errorMessage) {
    return <section className="dashboard-error">{errorMessage}</section>;
  }

  if (!overview) {
    return <section className="dashboard-loading">Loading SaaS metrics...</section>;
  }

  return (
    <main className="dashboard-page">
      <header className="dashboard-page__header">
        <div>
          <h1>Revenue &amp; Retention Overview</h1>
          <p>Collection efficiency: {overview.collection_ratio_pct}%</p>
        </div>
        <a href={BILLING_ROUTE} className="dashboard-page__billing-link">
          Manage Billing
        </a>
      </header>

      <section className="kpi-grid">
        <div className="kpi-card">
          <span>MRR</span>
          <strong>{formatUsdFromCents(overview.mrr_cents)}</strong>
        </div>
        <div className="kpi-card">
          <span>ARR</span>
          <strong>{formatUsdFromCents(overview.arr_cents)}</strong>
        </div>
        <div className="kpi-card">
          <span>Active Subscriptions</span>
          <strong>{overview.active_subscriptions}</strong>
        </div>
        <div className="kpi-card">
          <span>At-Risk Accounts</span>
          <strong>{overview.high_risk_accounts.length}</strong>
        </div>
      </section>

      <section className="plan-preview-grid">
        {PLAN_OPTIONS.map((plan) => (
          <PlanCard
            key={plan.tier}
            plan={plan}
            isCurrent={plan.tier === selectedTier}
            onSelect={setSelectedTier}
          />
        ))}
      </section>
    </main>
  );
}
