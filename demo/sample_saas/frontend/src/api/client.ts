import type {
  AnalyticsOverview,
  InvoiceItem,
  PlanTier,
  SubscriptionSummary,
} from "@/types/billing";

export class ApiClientError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiClientError";
    this.status = status;
  }
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new ApiClientError(response.status, detail || response.statusText);
  }
  return (await response.json()) as T;
}

export function fetchAnalyticsOverview(horizonMonths = 12): Promise<AnalyticsOverview> {
  return requestJson<AnalyticsOverview>(`/api/analytics/overview?horizon_months=${horizonMonths}`);
}

export function fetchInvoices(userId: number, includeVoid = false): Promise<InvoiceItem[]> {
  return requestJson<InvoiceItem[]>(
    `/api/billing/invoices?user_id=${userId}&include_void=${String(includeVoid)}`,
  );
}

export function createSubscription(
  token: string,
  planTier: PlanTier,
  seats: number,
): Promise<SubscriptionSummary> {
  return requestJson<SubscriptionSummary>("/api/billing/subscriptions", {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: JSON.stringify({ plan_tier: planTier, seats }),
  });
}

export function upgradeSubscription(
  subscriptionId: number,
  targetTier: PlanTier,
  seats: number,
  remainingDays: number,
): Promise<{
  subscription_id: number;
  plan_tier: PlanTier;
  monthly_amount_cents: number;
  invoice_id: number;
  invoice_total_cents: number;
}> {
  return requestJson(`/api/billing/subscriptions/${subscriptionId}/upgrade`, {
    method: "POST",
    body: JSON.stringify({
      target_tier: targetTier,
      seats,
      remaining_days: remainingDays,
    }),
  });
}

export function finalizeSubscriptionInvoices(
  subscriptionId: number,
): Promise<{ subscription_id: number; settled_count: number }> {
  return requestJson(`/api/billing/subscriptions/${subscriptionId}/finalize`, {
    method: "POST",
  });
}

export function triggerWorkerSync(batchId: string): Promise<{ processed: number; failed: number }> {
  return requestJson("/worker/sync", {
    method: "POST",
    body: JSON.stringify({ batch_id: batchId }),
  });
}
