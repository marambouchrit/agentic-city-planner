"""Planner agent: decides priorities, pace and visit lengths, and asks the optimizer for schedules.

The LLM only ever answers with the ID of a schedule the tool produced, so it can't change a stop or a time.
"""

from typing import Callable

from pydantic import BaseModel, Field

from city_planner.agents.base import AgentConfig, Tool
from city_planner.agents.discovery import DiscoveryOutput
from city_planner.schemas import Hop, Place, ScheduleResult, TripRequest
from city_planner.tools.get_route import get_route
from city_planner.tools.optimize_schedule import optimize_schedule

MAX_STEPS = 5
MAX_OPTIMIZE_CALLS = 2  # each one can take minutes and uses route requests
MAX_ROUTE_CALLS = 4

INSTRUCTIONS = """You are the Planner agent of a city trip planner.
Your one job: build a day-by-day schedule from the verified places you are given.

You decide the traveler's priorities, the pace, how long each visit needs, and whether to try again.
- Call optimize_schedule with your choices. The tool decides the days, the order, the times and the transport. You don't.
- Give a priority from 0 to 1 only to places that matter for this traveler. Popularity is already counted.
- The default visit is 90 minutes. Always give visit_minutes for large museums, palaces and big sites, which need longer, and for small places such as a gate or a square, which need less.
- Read the result. Try once more only if your choices clearly caused a problem, such as too few places on a day. Each try is slow.
- Use get_route only to check one specific hop you have a question about. The schedule already has its routes.
- Only use place IDs from the list you are given. Never invent a place, a time or a route.
- When you are done, call finish with the schedule_id you choose and a short explanation of your choices.
- The explanation must only use the traveler's request and the tool results. Do not add facts from your memory, such as opening hours or prices."""

FINISH_DESCRIPTION = "Choose the final schedule by its schedule_id, and explain your choices."


class VisitLength(BaseModel):
    place_id: str
    minutes: int = Field(ge=30, le=240)


class Priority(BaseModel):
    place_id: str
    value: float = Field(ge=0, le=1)


class OptimizeArgs(BaseModel):
    max_activities_per_day: int = Field(ge=1, le=6, description="How many attractions a day suits this traveler.")
    priorities: list[Priority] = Field(default_factory=list)
    visit_minutes: list[VisitLength] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list, description="IDs of places to leave out.")
    required: list[str] = Field(default_factory=list, description="IDs of places the traveler must see.")


class RouteArgs(BaseModel):
    from_place_id: str
    to_place_id: str


class PlannerAnswer(BaseModel):
    schedule_id: str = Field(description="The ID optimize_schedule returned for the schedule you choose.")
    explanation: str = Field(description="Why you made these choices, in two short sentences, using only tool results.")


class PlannerOutput(BaseModel):
    schedule: ScheduleResult  # written by code, exactly as the tool produced it
    explanation: str  # the LLM's opinion
    schedule_id: str


def _travel_summary(hop: Hop | None) -> str | None:
    if hop is None:
        return None
    options = []
    if hop.public_transport:
        lines = ", ".join(line.line or "unnamed line" for line in hop.public_transport.lines)
        options.append(f"public transport ({lines}), {hop.public_transport.minutes} min")
    if hop.walk:
        options.append(f"walk, {hop.walk.minutes} min")
    if hop.taxi_backup:
        options.append(f"taxi backup, {hop.taxi_backup.minutes} min")
    return " or ".join(options) or "unknown"


def _summary(schedule_id: str, schedule: ScheduleResult) -> dict:
    return {
        "schedule_id": schedule_id,
        "status": schedule.status,
        "days": [
            {
                "day": day.day_number,
                "stops": [
                    {"start": stop.start, "slot": stop.slot, "name": stop.name, "travel": _travel_summary(stop.hop)}
                    for stop in day.stops
                ],
            }
            for day in schedule.days
        ],
        "notes": schedule.notes,
    }


def planner_task(trip: TripRequest, discovery: DiscoveryOutput) -> str:
    def line(chosen) -> str:
        views = f", {chosen.place.page_views} page views" if chosen.place.page_views is not None else ""
        return f"- {chosen.place.place_id}: {chosen.place.name}{views}. Chosen because: {chosen.reason}"

    parts = [
        f"Plan {trip.days} day(s) in {trip.city}.",
        f"Traveler's preferences: {trip.preferences or 'none given'}.",
        "Attractions:",
        *map(line, discovery.attractions),
        "Restaurants:",
        *(map(line, discovery.restaurants) if discovery.restaurants else ["(none found)"]),
    ]
    if trip.max_activities_per_day:
        parts.insert(2, f"The traveler asked for at most {trip.max_activities_per_day} attractions a day.")
    return "\n".join(parts)


def build_planner_agent(
    trip: TripRequest,
    discovery: DiscoveryOutput,
    optimize: Callable[..., ScheduleResult] = optimize_schedule,
    route: Callable[..., Hop] = get_route,
) -> AgentConfig:
    """Each trip gets a new agent, with its own record of the schedules the tool really produced."""
    places: dict[str, Place] = {c.place.place_id: c.place for c in discovery.attractions + discovery.restaurants}
    schedules: dict[str, ScheduleResult] = {}
    route_calls = {"count": 0}

    def check_ids(place_ids: list[str]) -> None:
        unknown = [place_id for place_id in place_ids if place_id not in places]
        if unknown:
            raise ValueError(f"These IDs are not in your list of places: {unknown}")

    def run_optimize(args: OptimizeArgs) -> dict:
        if len(schedules) >= MAX_OPTIMIZE_CALLS:
            raise ValueError(f"Limit of {MAX_OPTIMIZE_CALLS} schedules reached. Finish with one you have.")
        check_ids([p.place_id for p in args.priorities] + [v.place_id for v in args.visit_minutes] + args.excluded + args.required)
        schedule = optimize(
            [c.place for c in discovery.attractions],
            [c.place for c in discovery.restaurants],
            trip.days,
            # What the traveler asked for always beats the LLM's choice.
            max_activities_per_day=trip.max_activities_per_day or args.max_activities_per_day,
            visit_minutes={v.place_id: v.minutes for v in args.visit_minutes},
            priorities={p.place_id: p.value for p in args.priorities},
            excluded=args.excluded,
            required=args.required,
            start_date=trip.start_date,
            max_walk_minutes=trip.max_walk_minutes,
        )
        schedule_id = f"schedule_{len(schedules) + 1}"
        schedules[schedule_id] = schedule
        return _summary(schedule_id, schedule)

    def run_route(args: RouteArgs) -> dict:
        route_calls["count"] += 1
        if route_calls["count"] > MAX_ROUTE_CALLS:
            raise ValueError(f"Limit of {MAX_ROUTE_CALLS} route checks reached.")
        check_ids([args.from_place_id, args.to_place_id])
        a, b = places[args.from_place_id], places[args.to_place_id]
        hop = route(a.latitude, a.longitude, b.latitude, b.longitude, max_walk_minutes=trip.max_walk_minutes)
        return {"travel": _travel_summary(hop), "note": hop.note}

    def finalize(answer: PlannerAnswer) -> PlannerOutput:
        schedule = schedules.get(answer.schedule_id)
        if schedule is None:
            raise ValueError(f"{answer.schedule_id} was not returned by optimize_schedule.")
        if schedule.status == "failed":
            raise ValueError(f"{answer.schedule_id} failed, so it can't be the final schedule.")
        return PlannerOutput(schedule=schedule, explanation=answer.explanation, schedule_id=answer.schedule_id)

    return AgentConfig(
        name="Planner",
        instructions=INSTRUCTIONS,
        tools=[
            Tool(
                "optimize_schedule",
                "Build the whole schedule with real routes from your choices. It can take a minute or more.",
                OptimizeArgs,
                run_optimize,
            ),
            Tool("get_route", "Check how to travel between two places on your list.", RouteArgs, run_route),
        ],
        finish_description=FINISH_DESCRIPTION,
        finish_model=PlannerAnswer,
        finalize=finalize,
        max_steps=MAX_STEPS,
    )
