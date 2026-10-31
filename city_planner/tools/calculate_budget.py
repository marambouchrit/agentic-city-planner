"""calculate_budget: code does the math, so the LLM never adds up costs itself."""

from city_planner.schemas import BudgetResult, CostItem


def calculate_budget(items: list[CostItem], budget: float | None = None, currency: str = "EUR") -> BudgetResult:
    if budget is not None and budget < 0:
        raise ValueError("budget can't be negative")

    total = round(sum(item.amount for item in items if item.amount is not None), 2)
    missing = [item.name for item in items if item.amount is None]
    includes_estimates = any(item.is_estimate and item.amount is not None for item in items)

    over_budget = None
    difference = None
    if budget is not None:
        difference = round(budget - total, 2)
        if total > budget:
            over_budget = True
        elif not missing:
            over_budget = False

    notes = []
    if includes_estimates:
        notes.append("Includes estimated prices, not real ones.")
    if missing:
        notes.append(f"The total leaves out {len(missing)} item(s) with no known cost.")
    if budget is None:
        notes.append("No budget was given.")
    return BudgetResult(
        total=total,
        currency=currency,
        budget=budget,
        over_budget=over_budget,
        difference=difference,
        missing_costs=missing,
        includes_estimates=includes_estimates,
        note=" ".join(notes) or "All costs are known.",
    )
