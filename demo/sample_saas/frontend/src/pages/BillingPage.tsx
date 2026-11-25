import { useEffect, useState, type JSX } from "react";

import { PlanCard } from "@/components/PlanCard";
import {
  formatUsdFromCents,
  PLAN_OPTIONS,
  type InvoiceItem,
  type PlanTier,
} from "@/types/billing";

import {
  fetchInvoices,
  finalizeSubscriptionInvoices,
  triggerWorkerSync,
  upgradeSubscription,
} from "../api/client";

export const BILLING_ROUTE = "/billing";

export interface BillingPageProps {
  userId: number;
  subscriptionId: number;
  initialTier: PlanTier;
}

export function BillingPage({
  userId,
  subscriptionId,
  initialTier,
}: BillingPageProps): JSX.Element {
  const [currentTier, setCurrentTier] = useState<PlanTier>(initialTier);
  const [invoices, setInvoices] = useState<InvoiceItem[]>([]);
  const [statusNote, setStatusNote] = useState<string>("");

  useEffect(() => {
    fetchInvoices(userId)
      .then(setInvoices)
      .catch((err: unknown) => {
        setStatusNote(err instanceof Error ? err.message : "Unable to load invoices.");
      });
  }, [userId]);

  async function handleTierChange(targetTier: PlanTier): Promise<void> {
    const result = await upgradeSubscription(subscriptionId, targetTier, 5, 15);
    setCurrentTier(result.plan_tier);
    setStatusNote(
      `Upgraded to ${result.plan_tier}; invoice #${result.invoice_id} issued for ${formatUsdFromCents(result.invoice_total_cents)}.`,
    );
    const updatedInvoices = await fetchInvoices(userId);
    setInvoices(updatedInvoices);
  }

  async function handleFinalizeAll(): Promise<void> {
    const summary = await finalizeSubscriptionInvoices(subscriptionId);
    const syncResult = await triggerWorkerSync(`sub-${subscriptionId}`);
    setStatusNote(
      `Settled ${summary.settled_count} invoices; worker synced ${syncResult.processed} records.`,
    );
  }

  return (
    <main className="billing-page">
      <header className="billing-page__header">
        <h1>Subscription &amp; Invoices</h1>
        <button type="button" onClick={() => void handleFinalizeAll()}>
          Finalize Open Invoices
        </button>
      </header>

      {statusNote ? <p className="billing-page__status">{statusNote}</p> : null}

      <section className="billing-page__plans">
        {PLAN_OPTIONS.map((plan) => (
          <PlanCard
            key={plan.tier}
            plan={plan}
            isCurrent={plan.tier === currentTier}
            onSelect={(tier) => void handleTierChange(tier)}
          />
        ))}
      </section>

      <section className="billing-page__ledger">
        <h2>Recent Invoices</h2>
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Subtotal</th>
              <th>Tax</th>
              <th>Total</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {invoices.map((inv) => (
              <tr key={inv.id}>
                <td>#{inv.id}</td>
                <td>{formatUsdFromCents(inv.subtotal_cents)}</td>
                <td>{formatUsdFromCents(inv.tax_cents)}</td>
                <td>{formatUsdFromCents(inv.total_cents)}</td>
                <td>{inv.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </main>
  );
}
