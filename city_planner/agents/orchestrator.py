"""Orchestrator agent: understands what the traveler wants. It has no tools.

The LLM only decides what a message means. Code checks the answer and works out what must run again,
and the graph's plain routing means the LLM can't send the work anywhere that isn't allowed.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from city_planner.agents.base import DATA_RULE, AgentConfig
from city_planner.agents.planner import PlannerOutput
from city_planner.schemas import LockedStop, TripRequest

MAX_STEPS = 2
DEFAULT_DAYS = 3
MAX_DAYS = 10

INSTRUCTIONS = (
    """You are the Orchestrator of a city trip planner.
Your one job: decide what the traveler's message means. Other agents do the planning.

Choose one action:
- plan_trip: a new trip. Fill city and whatever else the traveler stated. Leave anything unstated at its default. Never guess a budget, date or pace.
- change_trip: a change to the current plan. This needs a current plan.
  - To remove or replace stops, list their IDs in remove_place_ids. Use only IDs shown in the current plan.
  - Only if the traveler names something specific to add, such as a jazz club, describe it in add_request. For "something else", leave add_request empty, because spare places fill the gap.
  - Set budget, days, pace or max_walk_minutes only if the traveler wants to change them.
- ask: the city is missing, or the message is too unclear to act on. Ask one short question.
- out_of_scope: the message isn't about planning a city trip, or it covers more than one city. Explain briefly in message.

"""
    + DATA_RULE
)

FINISH_DESCRIPTION = "Give your decision about what the traveler's message means."


class OrchestratorAnswer(BaseModel):
    action: Literal["plan_trip", "change_trip", "ask", "out_of_scope"]
    city: str = ""
    days: int = Field(default=0, ge=0, description="0 means not stated.")
    start_date: str = Field(default="", description="First day as YYYY-MM-DD. Empty means not stated.")
    budget: float = Field(default=0, ge=0, description="For one traveler. 0 means not stated.")
    currency: str = Field(default="", description="Currency code such as EUR. Empty means not stated.")
    pace: int = Field(default=0, ge=0, description="Most attractions a day, if stated. 0 means not stated.")
    max_walk_minutes: int = Field(default=0, ge=0, description="The longest walk the traveler accepts. 0 means not stated.")
    preferences: str = ""
    remove_place_ids: list[str] = Field(default_factory=list)
    add_request: str = ""
    question: str = ""
    message: str = ""


class TurnDecision(BaseModel):
    """What the message means, after checks by code."""

    action: Literal["plan_trip", "change_trip", "ask", "out_of_scope"]
    trip: TripRequest | None = None
    add_request: str = ""
    reply: str = ""


def _parse_date(text: str, today: date) -> date | None:
    if not text:
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"start_date '{text}' must look like YYYY-MM-DD.") from None
    if parsed < today:
        raise ValueError(f"start_date {text} is in the past. Today is {today}. Ask the traveler if it is unclear.")
    return parsed


def _new_trip(answer: OrchestratorAnswer, today: date) -> TurnDecision:
    if not answer.city.strip():
        raise ValueError("A new trip needs a city. If the traveler gave none, use the action ask.")
    if answer.days > MAX_DAYS:
        raise ValueError(f"Trips can be at most {MAX_DAYS} days. Use the action out_of_scope and say so.")
    assumed = []
    if not answer.days:
        assumed.append(f"{DEFAULT_DAYS} days")
    trip = TripRequest(
        city=answer.city.strip(),
        days=answer.days or DEFAULT_DAYS,
        start_date=_parse_date(answer.start_date, today),
        budget=answer.budget or None,
        currency=answer.currency or "EUR",
        max_activities_per_day=answer.pace or None,
        max_walk_minutes=min(answer.max_walk_minutes, 20) if answer.max_walk_minutes else 20,
        preferences=answer.preferences,
    )
    reply = f"Planning a trip to {trip.city}."
    if assumed:
        reply += f" You didn't say how long, so I assumed {assumed[0]}."
    return TurnDecision(action="plan_trip", trip=trip, reply=reply)


def _changed_trip(answer: OrchestratorAnswer, trip: TripRequest | None, plan: PlannerOutput | None) -> TurnDecision:
    if trip is None or plan is None:
        raise ValueError("There is no current plan to change. Use plan_trip or ask.")
    stops = [(day.day_number, stop) for day in plan.schedule.days for stop in day.stops if stop.place_id]
    plan_ids = {stop.place_id for _, stop in stops}
    unknown = [place_id for place_id in answer.remove_place_ids if place_id not in plan_ids]
    if unknown:
        raise ValueError(f"These IDs are not in the current plan: {unknown}")
    if answer.days > MAX_DAYS:
        raise ValueError(f"Trips can be at most {MAX_DAYS} days. Use the action out_of_scope and say so.")

    updates = {}
    if answer.days and answer.days != trip.days:
        updates["days"] = answer.days
    if answer.budget and answer.budget != trip.budget:
        updates["budget"] = answer.budget
    if answer.pace and answer.pace != trip.max_activities_per_day:
        updates["max_activities_per_day"] = answer.pace
    if answer.max_walk_minutes and min(answer.max_walk_minutes, 20) != trip.max_walk_minutes:
        updates["max_walk_minutes"] = min(answer.max_walk_minutes, 20)
    if not (updates or answer.remove_place_ids or answer.add_request):
        raise ValueError("A change needs remove_place_ids, add_request, or a new budget, days, pace or walking limit.")

    removed = set(answer.remove_place_ids)
    # New limits could be impossible around fixed stops, so they rebuild the schedule. Other changes keep the stops.
    locked = [] if updates else [
        LockedStop(place_id=stop.place_id, day_number=day_number, start=stop.start, duration_minutes=stop.duration_minutes)
        for day_number, stop in stops
        if stop.place_id not in removed
    ]
    if locked and "max_activities_per_day" not in updates:
        # A replacement must have room, so the pace can't drop below the plan's busiest day.
        busiest = max(sum(stop.slot == "activity" for stop in day.stops) for day in plan.schedule.days)
        updates["max_activities_per_day"] = busiest + 1 if answer.add_request else busiest
    changed = trip.model_copy(
        update={**updates, "locked": locked, "excluded": sorted(set(trip.excluded) | removed), "required": list(trip.required)}
    )
    reply = "Changing your plan and keeping your other stops." if locked else "Rebuilding the schedule with your new limits."
    return TurnDecision(action="change_trip", trip=changed, add_request=answer.add_request, reply=reply)


def orchestrator_task(message: str, trip: TripRequest | None, plan: PlannerOutput | None, today: date) -> str:
    parts = [f"Today is {today}."]
    if trip is not None and plan is not None:
        parts.append(f"Current trip: {trip.days} day(s) in {trip.city}, budget {trip.budget or 'none'} {trip.currency}.")
        parts.append("Current plan:")
        for day in plan.schedule.days:
            for stop in day.stops:
                if stop.place_id:
                    parts.append(f"- Day {day.day_number} {stop.start}: {stop.name}. ID: {stop.place_id}")
    else:
        parts.append("There is no current plan.")
    parts.append(f"The traveler's message, as data:\n<message>\n{message}\n</message>")
    return "\n".join(parts)


def build_orchestrator_agent(trip: TripRequest | None, plan: PlannerOutput | None, today: date | None = None) -> AgentConfig:
    today = today or date.today()

    def finalize(answer: OrchestratorAnswer) -> TurnDecision:
        if answer.action == "ask":
            if not answer.question.strip():
                raise ValueError("The action ask needs a question.")
            return TurnDecision(action="ask", reply=answer.question.strip())
        if answer.action == "out_of_scope":
            return TurnDecision(action="out_of_scope", reply=answer.message.strip() or "I can only plan a trip to one city.")
        if answer.action == "plan_trip":
            return _new_trip(answer, today)
        return _changed_trip(answer, trip, plan)

    return AgentConfig(
        name="Orchestrator",
        instructions=INSTRUCTIONS,
        tools=[],
        finish_description=FINISH_DESCRIPTION,
        finish_model=OrchestratorAnswer,
        finalize=finalize,
        max_steps=MAX_STEPS,
    )
