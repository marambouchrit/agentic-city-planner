"""Reviewer agent: estimates prices, reads the checks, then approves the plan or sends it back.

The LLM only supplies price estimates and its judgement. Code builds the list of costs from the plan,
does the math, runs the checks, and refuses an approval that the checks don't support.
"""

from typing import Literal

from pydantic import BaseModel, Field

from city_planner.agents.base import DATA_RULE, AgentConfig, Tool
from city_planner.schemas import BudgetResult, CostItem, Place, PlanCheck, ScheduleResult, TripRequest
from city_planner.tools.calculate_budget import calculate_budget
from city_planner.tools.check_plan import check_plan

MAX_STEPS = 4  # 3 left no room to correct a refused approval in a live run

PUBLIC_TRANSPORT_FARE = "public_transport_fare"
BACKUP_PRICE_PER_KM = "taxi_per_km"
# A price is fixed for the whole trip once set, so one above the whole budget gets a currency warning first.
# The same price sent again after the warning is accepted, because a small budget can be real.
MAX_SHARE_OF_BUDGET = 1.0
MEAL_BREAK_PRICE = "meal_break"  # one price, because every meal break is the same unnamed meal

INSTRUCTIONS = """You are the Reviewer agent of a city trip planner.
Your one job: check a finished plan, then approve it or send it back with specific instructions.

- Call calculate_budget with your estimate of what one traveler typically pays for each item that has no fixed price yet. Use 0 for a free entry. Use exactly the item_id shown for each item. Leave out an item if you really can't estimate it. Your prices are labelled as estimates.
- Call check_plan. It reports rule errors and warnings.
- You may call both tools in one turn, then read both results before you finish.
- Approve if there are no errors and the plan is within budget. Warnings alone, such as a meal break or an estimated hop, are not a reason to send a plan back.
- Otherwise send it back. Choose target "planner" for anything about the schedule or its cost, and "discovery" only if more or different places are needed.
- Instructions must be specific: name the problem and what to change, using the tool results.
- Code refuses an approval when check_plan has errors or the plan is over budget.
- Never claim opening hours, real prices or anything else the tools did not return.
""" + DATA_RULE

FINISH_DESCRIPTION = "Give your verdict on the plan, after reading the results of calculate_budget and check_plan."


class Price(BaseModel):
    item_id: str
    amount: float = Field(ge=0, description="Price for one traveler in the trip's currency. For taxi_per_km, the price per km.")


class BudgetArgs(BaseModel):
    prices: list[Price]


class CheckArgs(BaseModel):
    note: str = Field(default="", description="Optional: what you want to look at. It doesn't change the check.")


class ReviewAnswer(BaseModel):
    verdict: Literal["approve", "send_back"]
    target: Literal["planner", "discovery"] = "planner"
    issue_code: str = Field(default="", description="A short code for the main problem, such as over_budget.")
    instructions: str = Field(default="", description="For send_back: what to change, specifically.")
    summary: str = Field(description="One or two short sentences for the traveler, using only tool results.")


class PriceLine(BaseModel):
    """One thing in the plan that may cost money. Code builds these, so nothing can be left out."""

    item_id: str
    name: str
    quantity: float
    unit: str


class ReviewOutput(BaseModel):
    verdict: Literal["approve", "send_back"]
    target: Literal["planner", "discovery"]
    issue_code: str
    instructions: str
    summary: str  # the LLM's opinion
    # Written by code.
    budget: BudgetResult
    check: PlanCheck
    costs: list[CostItem]
    prices: dict[str, float]


def price_lines(schedule: ScheduleResult) -> list[PriceLine]:
    lines: list[PriceLine] = []
    rides = 0
    backup_km = 0.0
    meal_breaks = 0
    for day in schedule.days:
        for stop in day.stops:
            if stop.place_id and stop.slot == "activity":
                lines.append(PriceLine(item_id=f"entry:{stop.place_id}", name=f"Entry: {stop.name}", quantity=1, unit="visit"))
            elif stop.place_id:
                lines.append(PriceLine(item_id=f"meal:{stop.place_id}", name=f"{stop.slot.capitalize()}: {stop.name}", quantity=1, unit="meal"))
            else:
                meal_breaks += 1
            if stop.hop and stop.hop.public_transport:
                # Counted even when a walk is also offered, so the total stays on the safe side.
                rides += 1
            elif stop.hop and stop.hop.taxi_backup:
                backup_km += stop.hop.distance_km or 0
    if meal_breaks:
        lines.append(PriceLine(item_id=MEAL_BREAK_PRICE, name=f"Meal breaks: {meal_breaks} meals", quantity=meal_breaks, unit="meal"))
    if rides:
        lines.append(PriceLine(item_id=PUBLIC_TRANSPORT_FARE, name=f"Public transport: {rides} rides", quantity=rides, unit="ride"))
    if backup_km:
        lines.append(PriceLine(item_id=BACKUP_PRICE_PER_KM, name=f"Taxi backup: {backup_km:.1f} km", quantity=round(backup_km, 1), unit="km"))
    return lines


def reviewer_task(trip: TripRequest, schedule: ScheduleResult, known_prices: dict[str, float]) -> str:
    def line(item: PriceLine) -> str:
        if item.item_id in known_prices:
            return f"- {item.name}: fixed price {known_prices[item.item_id]} per {item.unit}. item_id: {item.item_id}"
        return f"- {item.name}: needs a price per {item.unit}. item_id: {item.item_id}"

    plan = [
        f"Day {day.day_number}: " + "; ".join(f"{stop.start} {stop.name}" for stop in day.stops) for day in schedule.days
    ]
    budget = f"{trip.budget} {trip.currency} for one traveler" if trip.budget is not None else f"none given (use {trip.currency})"
    return "\n".join(
        [
            f"Review this {trip.days}-day plan for {trip.city}.",
            f"Budget: {budget}. Give every price in {trip.currency}, even if the city uses another currency.",
            "Plan:",
            *plan,
            "Items:",
            *map(line, price_lines(schedule)),
        ]
    )


def build_reviewer_agent(trip: TripRequest, schedule: ScheduleResult, known_places: list[Place], known_prices: dict[str, float]) -> AgentConfig:
    """known_prices keeps a price fixed between rounds, so a plan can't pass just because of a lower guess."""
    lines = price_lines(schedule)
    line_ids = {line.item_id for line in lines}
    state: dict = {"budget": None, "costs": [], "prices": dict(known_prices)}
    warned: dict[str, float] = {}

    def run_budget(args: BudgetArgs) -> dict:
        unknown = [price.item_id for price in args.prices if price.item_id not in line_ids]
        if unknown:
            raise ValueError(f"These items are not in the plan: {unknown}. Valid item IDs: {sorted(line_ids)}")
        if trip.budget:
            too_high = [
                price.item_id
                for price in args.prices
                if price.item_id != BACKUP_PRICE_PER_KM
                and price.amount > trip.budget * MAX_SHARE_OF_BUDGET
                and warned.get(price.item_id) != price.amount
            ]
            if too_high:
                warned.update({price.item_id: price.amount for price in args.prices if price.item_id in too_high})
                raise ValueError(
                    f"These prices are more than the whole budget, which suggests another currency: {too_high}. "
                    f"Give every price in {trip.currency}, for one traveler. If a price really is that high, send it again unchanged."
                )
        prices = dict(known_prices)
        for price in args.prices:
            prices.setdefault(price.item_id, price.amount)
        costs = [
            CostItem(name=line.name, amount=round(prices[line.item_id] * line.quantity, 2) if line.item_id in prices else None)
            for line in lines
        ]
        result = calculate_budget(costs, trip.budget, trip.currency)
        state.update(budget=result, costs=costs, prices=prices)
        return result.model_dump()

    def run_check(args: CheckArgs) -> dict:
        return _check().model_dump()

    def _check() -> PlanCheck:
        return check_plan(schedule.days, known_places, trip.days, trip.max_walk_minutes)

    def finalize(answer: ReviewAnswer) -> ReviewOutput:
        budget = state["budget"]
        if budget is None:
            raise ValueError("Call calculate_budget before you finish.")
        check = _check()
        if answer.verdict == "approve":
            if not check.passed:
                raise ValueError("You can't approve this plan: check_plan has errors. Send it back.")
            if budget.over_budget:
                raise ValueError("You can't approve this plan: it is over budget. Send it back.")
        elif not answer.instructions.strip():
            raise ValueError("A plan sent back needs specific instructions.")
        return ReviewOutput(
            verdict=answer.verdict,
            target=answer.target,
            issue_code=answer.issue_code,
            instructions=answer.instructions,
            summary=answer.summary,
            budget=budget,
            check=check,
            costs=state["costs"],
            prices=state["prices"],
        )

    return AgentConfig(
        name="Reviewer",
        instructions=INSTRUCTIONS,
        tools=[
            Tool("calculate_budget", "Add up the plan's costs from your price estimates, and compare them with the budget.", BudgetArgs, run_budget),
            Tool("check_plan", "Check the plan against the rules. It always checks the current plan.", CheckArgs, run_check),
        ],
        finish_description=FINISH_DESCRIPTION,
        finish_model=ReviewAnswer,
        finalize=finalize,
        max_steps=MAX_STEPS,
    )


def feedback_for_planner(review: ReviewOutput, last_choices: str) -> str:
    """What the Planner needs for a revision. The costs come from code, not from the LLM's memory."""
    parts = [f"The Reviewer sent the last plan back ({review.issue_code or 'no code'}): {review.instructions}"]
    if review.budget.over_budget:
        parts.append(f"The plan is {-review.budget.difference} {review.budget.currency} over budget.")
    priced = sorted((item for item in review.costs if item.amount), key=lambda item: item.amount, reverse=True)
    if priced:
        parts.append("Estimated costs, highest first: " + "; ".join(f"{item.name}: {item.amount}" for item in priced))
    errors = [issue.message for issue in review.check.issues if issue.severity == "error"]
    if errors:
        parts.append("Rule errors: " + " ".join(errors))
    if last_choices:
        parts.append(f"Your last choices were: {last_choices}. Keep what still applies.")
    return "\n".join(parts)
