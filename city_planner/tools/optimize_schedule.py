"""optimize_schedule: OR-Tools chooses which place fills each slot of a fixed daily template.

Adapted from the old project's optimizer. Each day is morning activities, lunch, afternoon
activities and dinner. It picks places, not the best route, and travel times are straight-line
estimates. Opening hours and prices are not checked, because we have no real data for them.
"""

import math
import os

from ortools.sat.python import cp_model

from city_planner.schemas import MEAL_BREAK_NAME, Day, Place, ScheduleResult, Stop
from city_planner.tools.distance import estimated_taxi_minutes, estimated_walking_minutes, haversine_km

PLACE_MINUTES = 90
MEAL_MINUTES = 60
MEAL_HOURS = {"lunch": 13, "dinner": 19}
LAST_START_MINUTES = 23 * 60

TOP_K_PER_POOL = 10
MAX_TRANSPORT_MINUTES = 60
DEFAULT_MAX_WALKING_MINUTES = 30

# Filling a slot always beats a better score, and a taxi hop costs some score.
FILL_BONUS = 10_000
SCORE_SCALE = 100
TAXI_PENALTY = 50
FULL_POPULARITY_VIEWS = 10_000_000


def _classify_hop(a: Place, b: Place, max_walking_minutes: int) -> tuple[str, float]:
    km = haversine_km(a.latitude, a.longitude, b.latitude, b.longitude)
    walking = estimated_walking_minutes(km)
    if walking <= max_walking_minutes:
        return "walk_estimate", walking
    taxi = estimated_taxi_minutes(km)
    return ("taxi_estimate" if taxi <= MAX_TRANSPORT_MINUTES else "infeasible"), taxi


def _slot_template(stops_per_day: int) -> list[str]:
    morning = (stops_per_day + 1) // 2
    return ["activity"] * morning + ["lunch"] + ["activity"] * (stops_per_day - morning) + ["dinner"]


def _score(place: Place, priorities: dict[str, float]) -> float:
    """Popularity from page views (0 to 1) plus the agent's priority for this place (0 to 1)."""
    popularity = min(1.0, math.log10((place.page_views or 0) + 1) / math.log10(FULL_POPULARITY_VIEWS))
    return popularity + max(0.0, min(1.0, priorities.get(place.place_id, 0.0)))


def _solve_day(pools: list[list[Place]], scores: dict[str, float], max_walking_minutes: int) -> list[Place | None] | None:
    """Returns the place chosen for each slot, or None if the solver found no answer."""
    model = cp_model.CpModel()
    x = [[model.NewBoolVar(f"x_{i}_{j}") for j in range(len(pool))] for i, pool in enumerate(pools)]

    uses: dict[str, list] = {}
    for i, pool in enumerate(pools):
        model.Add(sum(x[i]) <= 1)
        for j, place in enumerate(pool):
            uses.setdefault(place.place_id, []).append(x[i][j])
    for variables in uses.values():
        model.Add(sum(variables) <= 1)

    penalties = []
    for i in range(len(pools)):
        for k in range(i + 1, len(pools)):
            for a, place_a in enumerate(pools[i]):
                for b, place_b in enumerate(pools[k]):
                    mode, _ = _classify_hop(place_a, place_b, max_walking_minutes)
                    if mode == "infeasible":
                        model.Add(x[i][a] + x[k][b] <= 1)
                    # Only nearby slots, which keeps the model small enough to solve quickly.
                    elif mode == "taxi_estimate" and k - i <= 2:
                        both = model.NewBoolVar(f"taxi_{i}_{a}_{k}_{b}")
                        model.AddBoolAnd([x[i][a], x[k][b]]).OnlyEnforceIf(both)
                        model.AddBoolOr([x[i][a].Not(), x[k][b].Not()]).OnlyEnforceIf(both.Not())
                        penalties.append(both)

    rewards = [
        (FILL_BONUS + int(scores[place.place_id] * SCORE_SCALE)) * x[i][j]
        for i, pool in enumerate(pools)
        for j, place in enumerate(pool)
    ]
    model.Maximize(sum(rewards) - sum(TAXI_PENALTY * p for p in penalties))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 8.0
    solver.parameters.num_workers = min(8, max(1, os.cpu_count() or 4))
    if solver.Solve(model) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    return [
        next((place for j, place in enumerate(pool) if solver.Value(x[i][j])), None)
        for i, pool in enumerate(pools)
    ]


def _clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _build_stops(slots: list[str], chosen: list[Place | None], start_hour: int, max_walking_minutes: int) -> tuple[list[Stop], list[str]]:
    """Sets the times in order, so a stop never starts before the previous one ended."""
    stops: list[Stop] = []
    notes: list[str] = []
    now = start_hour * 60
    previous: Place | None = None

    for slot, place in zip(slots, chosen):
        travel_mode, travel = None, None
        if place is not None and previous is not None:
            travel_mode, minutes = _classify_hop(previous, place, max_walking_minutes)
            travel = round(minutes, 1)

        start = now + math.ceil(travel or 0)
        if slot in MEAL_HOURS:
            start = max(start, MEAL_HOURS[slot] * 60)
        if start > LAST_START_MINUTES:
            notes.append(f"The day ran out of time before the {slot} slot, so it was left out.")
            break

        if place is None and slot == "activity":
            notes.append("An activity slot was left empty: no suitable verified place remained.")
            continue
        if place is None:
            notes.append(f"No verified restaurant for {slot}, so a meal break was added instead.")
            stops.append(Stop(place_id=None, name=MEAL_BREAK_NAME, slot=slot, start=_clock(start), duration_minutes=MEAL_MINUTES))
            now = start + MEAL_MINUTES
            continue

        duration = MEAL_MINUTES if slot in MEAL_HOURS else PLACE_MINUTES
        stops.append(
            Stop(
                place_id=place.place_id,
                name=place.name,
                slot=slot,
                start=_clock(start),
                duration_minutes=duration,
                latitude=place.latitude,
                longitude=place.longitude,
                travel_minutes=travel,
                travel_mode=travel_mode,
            )
        )
        now = start + duration
        previous = place
    return stops, notes


def optimize_schedule(
    places: list[Place],
    days: int,
    stops_per_day: int = 3,
    start_hour: int = 9,
    max_walking_minutes: int | None = None,
    priorities: dict[str, float] | None = None,
) -> ScheduleResult:
    """priorities is optional: place_id to a value from 0 to 1, so an agent can favour some places."""
    if days < 1 or stops_per_day < 1 or not 0 <= start_hour <= 23:
        return ScheduleResult(status="failed", notes=["days and stops_per_day must be at least 1, and start_hour 0 to 23."])

    usable = [p for p in places if p.latitude is not None and p.longitude is not None]
    notes = [f"{p.name} has no coordinates, so it can't be scheduled." for p in places if p not in usable]
    if not any(p.kind == "attraction" for p in usable):
        return ScheduleResult(status="failed", notes=notes + ["No verified attraction with coordinates was given."])

    priorities = priorities or {}
    walking_limit = DEFAULT_MAX_WALKING_MINUTES if max_walking_minutes is None else max_walking_minutes
    scores = {p.place_id: _score(p, priorities) for p in usable}
    ranked = sorted(usable, key=lambda p: scores[p.place_id], reverse=True)
    slots = _slot_template(stops_per_day)

    used: set[str] = set()
    result_days: list[Day] = []
    complete = True
    for day_number in range(1, days + 1):
        remaining = [p for p in ranked if p.place_id not in used]
        activities = [p for p in remaining if p.kind == "attraction"][:TOP_K_PER_POOL]
        meals = [p for p in remaining if p.kind == "restaurant"][:TOP_K_PER_POOL]
        pools = [activities if slot == "activity" else meals for slot in slots]

        chosen = _solve_day(pools, scores, walking_limit)
        if chosen is None:
            complete = False
            notes.append(f"Day {day_number}: the solver found no schedule.")
            result_days.append(Day(day_number=day_number, stops=[]))
            continue

        used.update(p.place_id for p in chosen if p is not None)
        stops, day_notes = _build_stops(slots, chosen, start_hour, walking_limit)
        notes.extend(f"Day {day_number}: {note}" for note in day_notes)
        complete = complete and any(s.place_id for s in stops) and not any(
            "activity slot" in note or "ran out of time" in note for note in day_notes
        )
        result_days.append(Day(day_number=day_number, stops=stops))

    notes.append("Travel times and visit lengths are estimates. Opening hours were not checked.")
    return ScheduleResult(status="ok" if complete else "partial", days=result_days, notes=notes)
