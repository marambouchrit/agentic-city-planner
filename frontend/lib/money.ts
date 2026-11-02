import type { Budget } from "@/types/trip";

export function budgetSummary(budget: Budget): string {
  const total = `Estimated cost for one traveler: ${budget.total} ${budget.currency}`;
  if (budget.budget === null) return `${total}.`;
  const compared = `Your budget: ${budget.budget} ${budget.currency}`;
  if (budget.over_budget === true) return `${total}. ${compared}. Over budget.`;
  if (budget.over_budget === false) return `${total}. ${compared}. Within budget.`;
  // A missing cost could change the answer, so we don't claim either way.
  return `${total}. ${compared}. Some costs are unknown, so this can't be confirmed.`;
}
