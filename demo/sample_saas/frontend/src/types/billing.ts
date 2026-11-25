export type PlanTier = "starter" | "growth" | "enterprise";

export type SubscriptionStatus = "trialing" | "active" | "past_due" | "canceled";

export type InvoiceStatus = "draft" | "open" | "paid" | "void";

export interface PlanDefinition {
  tier: PlanTier;
  label: string;
  monthlyCents: number;
  includedSeats: number;
  highlights: string[];
}

export interface SubscriptionSummary {
  id: number;
  plan_tier: PlanTier;
  status: SubscriptionStatus;
  seats: number;
  monthly_amount_cents: number;
}

export interface InvoiceItem {
  id: number;
  subscription_id: number;
  subtotal_cents: number;
  tax_cents: number;
  total_cents: number;
  status: InvoiceStatus;
}

export interface CohortRow {
  cohort_month: string;
  total_accounts: number;
  retained_accounts: number;
  retention_pct: number;
  health_tag: "healthy" | "watch" | "critical";
}

export interface ChurnRiskItem {
  masked_email: string;
  organization_id: string;
  plan_tier: PlanTier;
  risk_score: number;
  risk_band: "low" | "medium" | "high";
  overdue_cents: number;
  recommended_action: string;
}

export interface ForecastPoint {
  month: number;
  projected_mrr_cents: number;
  projected_arr_cents: number;
  net_new_mrr_cents: number;
}

export interface AnalyticsOverview {
  database_healthy: boolean;
  mrr_cents: number;
  arr_cents: number;
  active_subscriptions: number;
  past_due_subscriptions: number;
  canceled_subscriptions: number;
  tier_breakdown_cents: Record<PlanTier, number>;
  collection_ratio_pct: number;
  cohorts: CohortRow[];
  high_risk_accounts: ChurnRiskItem[];
  forecast: ForecastPoint[];
}

export const PLAN_OPTIONS: PlanDefinition[] = [
  {
    tier: "starter",
    label: "Starter",
    monthlyCents: 2900,
    includedSeats: 1,
    highlights: ["Core billing ledger", "CSV invoice exports", "Email receipts"],
  },
  {
    tier: "growth",
    label: "Growth",
    monthlyCents: 9900,
    includedSeats: 5,
    highlights: ["Cohort retention tracking", "Automated dunning", "Priority API limits"],
  },
  {
    tier: "enterprise",
    label: "Enterprise",
    monthlyCents: 39900,
    includedSeats: 25,
    highlights: ["ARR scenario forecasting", "Dedicated worker sync", "SSO & audit trails"],
  },
];

export function formatUsdFromCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}
