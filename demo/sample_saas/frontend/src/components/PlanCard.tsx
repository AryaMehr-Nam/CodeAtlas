import type { JSX } from "react";

import { formatUsdFromCents, type PlanDefinition, type PlanTier } from "../types/billing";

export interface PlanCardProps {
  plan: PlanDefinition;
  isCurrent: boolean;
  onSelect: (tier: PlanTier) => void;
}

export function PlanCard({ plan, isCurrent, onSelect }: PlanCardProps): JSX.Element {
  return (
    <article
      className={isCurrent ? "plan-card plan-card--active" : "plan-card"}
      data-tier={plan.tier}
    >
      <header className="plan-card__header">
        <h3>{plan.label}</h3>
        <span className="plan-card__price">{formatUsdFromCents(plan.monthlyCents)}/mo</span>
      </header>
      <p className="plan-card__seats">Includes up to {plan.includedSeats} seats</p>
      <ul className="plan-card__highlights">
        {plan.highlights.map((feature) => (
          <li key={feature}>{feature}</li>
        ))}
      </ul>
      <button
        type="button"
        disabled={isCurrent}
        onClick={() => onSelect(plan.tier)}
      >
        {isCurrent ? "Current Plan" : `Switch to ${plan.label}`}
      </button>
    </article>
  );
}
