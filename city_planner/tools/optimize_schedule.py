"""optimize_schedule: OR-Tools plans the whole trip at once: which places, on which day, in what order.

The solver starts from a neutral travel estimate, because asking Transitous about every pair of
places would use too many requests. It then gets real routes for the hops it chose, and solves again
if they differ. Final times always come from real routes. Opening hours are not checked: no data.
"""

import math
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Callable

from ortools.sat.python import cp_model

from city_planner.schemas import MEAL_BREAK_NAME, Day, Hop, LockedStop, Place, ScheduleResult, Stop
from city_planner.tools.distance import estimated_walking_minutes, haversine_km
from city_planner.tools.get_route import MAX_WALK_OFFER_MINUTES, WALK_DETOUR_FACTOR, get_route

DEFAULT_VISIT_MINUTES = 90
MIN_VISIT_MINUTES = 30
MAX_VISIT_MINUTES = 240
MEAL_MINUTES = 60
MEAL_WINDOWS = {"lunch": (12 * 60, 14 * 60 + 30), "dinner": (18 * 60 + 30, 21 * 60)}
FULL_POPULARITY_VIEWS = 10_000_000

# Fitted to 30 real routes in Paris, Berlin and Lisbon. The average error was 4.4 minutes.
PUBLIC_TRANSPORT_WAIT_MINUTES = 7.0
PUBLIC_TRANSPORT_KMH = 14.3
# A meal break has no location, so allow for travel to the next stop.
MEAL_BREAK_TRAVEL_MINUTES = 20

# Scores. One more attraction beats everything except a required place. A value of 1.0 is worth 100 minutes of travel.
REQUIRED_BONUS = 1_000_000
# A meal comes before any attraction, but kept stops can leave no room for one, which must not make the plan impossible.
MISSING_MEAL_PENALTY = 100_000
VISIT_BONUS = 10_000
VALUE_SCALE = 1_000
MEAL_BREAK_PENALTY = 5_000
UNEVEN_DAY_PENALTY = 1_500
TRAVEL_PENALTY_PER_MINUTE = 10
# Start the day on time and finish it early. Only shortening the day would throw away the morning.
DAY_TIME_PENALTY_PER_MINUTE = 1

MAX_CANDIDATES = 30
SHORT_TRIP_DAYS = 3
SHORT_TRIP_SECONDS = 20.0
LONG_TRIP_SECONDS = 60.0
MAX_ROUNDS = 3
MAX_ROUTE_REQUESTS = 40
# Real travel this close to the estimate isn't worth solving again.
RESOLVE_TOLERANCE_MINUTES = 5
FREE_TIME_NOTE_MINUTES = 60


@dataclass(frozen=True)
class _Node:
    key: str
    place: Place | None  # None for a meal break
    slot: str
    duration: int
    earliest: int
    latest: int  # the latest start


@dataclass
class _Problem:
    days: int
    day_start: int
    day_end: int
    max_per_day: int
    attractions: list[Place]
    restaurants: list[Place]
    visit: dict[str, int]
    value: dict[str, int]
    locked: dict[str, tuple[int, int]]  # place_id to (day_number, start)
    required: set[str]
    start_point: tuple[float, float] | None
    # Pairs of places whose real travel is too long to have only a meal break between them.
    no_break_between: set[tuple[str, str]] = field(default_factory=set)


Route =list[tuple[_Node, int]]  # the nodes of one day, in order, with their start times


def _minutes(clock: str) -> int:
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


def _clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _estimate_minutes(from_lat: float, from_lon: float, to_lat: float, to_lon: float) -> int:
    """One neutral number for a hop. It names no mode, because the solver doesn't choose one."""
    km = haversine_km(from_lat, from_lon, to_lat, to_lon)
    walking = estimated_walking_minutes(km) * WALK_DETOUR_FACTOR
    return math.ceil(min(walking, PUBLIC_TRANSPORT_WAIT_MINUTES + km / PUBLIC_TRANSPORT_KMH * 60))


def _value(place: Place, priorities: dict[str, float]) -> int:
    """Popularity from page views (0 to 1) plus the Planner's priority (0 to 1)."""
    popularity = min(1.0, math.log10((place.page_views or 0) + 1) / math.log10(FULL_POPULARITY_VIEWS))
    return int((popularity + max(0.0, min(1.0, priorities.get(place.place_id, 0.0)))) * VALUE_SCALE)


def _meal_slots(problem: _Problem) -> dict[str, tuple[int, int]]:
    """The meal windows that fit inside the day."""
    slots = {}
    for slot, (earliest, latest) in MEAL_WINDOWS.items():
        earliest, latest = max(earliest, problem.day_start), min(latest, problem.day_end - MEAL_MINUTES)
        if earliest <= latest:
            slots[slot] = (earliest, latest)
    return slots


def _attraction_node(place: Place, problem: _Problem) -> _Node:
    duration = problem.visit[place.place_id]
    return _Node(place.place_id, place, "activity", duration, problem.day_start, problem.day_end - duration)


def _meal_node(place: Place | None, slot: str, window: tuple[int, int]) -> _Node:
    key = f"{place.place_id if place else 'break'}|{slot}"
    return _Node(key, place, slot, MEAL_MINUTES, *window)


def _locked_meal_slot(start: int) -> str:
    return "lunch" if start < 16 * 60 else "dinner"


def _day_nodes(problem: _Problem, day_number: int) -> list[_Node]:
    """Every stop that could happen on this day. A locked stop only exists on its day, at its time."""
    nodes = []
    for place in problem.attractions:
        node = _attraction_node(place, problem)
        if place.place_id in problem.locked:
            locked_day, start = problem.locked[place.place_id]
            if locked_day != day_number:
                continue
            node = _Node(node.key, place, "activity", node.duration, start, start)
        nodes.append(node)

    locked_slots = set()
    for place in problem.restaurants:
        if place.place_id in problem.locked and problem.locked[place.place_id][0] == day_number:
            start = problem.locked[place.place_id][1]
            locked_slots.add(_locked_meal_slot(start))
            nodes.append(_meal_node(place, _locked_meal_slot(start), (start, start)))

    for slot, window in _meal_slots(problem).items():
        if slot in locked_slots:
            continue
        nodes.extend(_meal_node(p, slot, window) for p in problem.restaurants if p.place_id not in problem.locked)
        nodes.append(_meal_node(None, slot, window))
    return nodes


def _arc_minutes(a: _Node, b: _Node, matrix: dict[tuple[str, str], int]) -> int:
    if b.place is None:
        return 0
    if a.place is None:
        return MEAL_BREAK_TRAVEL_MINUTES
    return matrix[a.place.place_id, b.place.place_id]


def _start_minutes(node: _Node, problem: _Problem, matrix: dict[tuple[str, str], int]) -> int:
    if problem.start_point is None or node.place is None:
        return 0
    return matrix["start", node.place.place_id]


def _solve(
    problem: _Problem, matrix: dict[tuple[str, str], int], seconds: float, hint: list[Route] | None
) -> tuple[list[Route] | None, bool]:
    """Returns the route of each day, and whether the answer is proven best. No answer gives None."""
    model = cp_model.CpModel()
    objective = []
    uses: dict[str, list] = {}
    counts = []
    day_values = []
    hinted = _hint_lookup(hint)
    variables = []

    for day_index in range(problem.days):
        day_number = day_index + 1
        nodes = _day_nodes(problem, day_number)
        arcs = []
        arc_by_keys = {}
        visits = [model.NewBoolVar(f"visit_{day_number}_{n.key}") for n in nodes]
        starts = [model.NewIntVar(n.earliest, n.latest, f"start_{day_number}_{n.key}") for n in nodes]
        begin = model.NewIntVar(problem.day_start, 24 * 60, f"begin_{day_number}")
        end = model.NewIntVar(problem.day_start, 24 * 60, f"end_{day_number}")
        variables.append((nodes, visits, starts))

        for i, node in enumerate(nodes):
            arcs.append((i + 1, i + 1, visits[i].Not()))
            first = model.NewBoolVar("")
            last = model.NewBoolVar("")
            arcs.append((0, i + 1, first))
            arcs.append((i + 1, 0, last))
            depart = _start_minutes(node, problem, matrix)
            model.Add(starts[i] >= problem.day_start + depart).OnlyEnforceIf(first)
            if depart:
                objective.append(-TRAVEL_PENALTY_PER_MINUTE * depart * first)
            model.Add(begin == starts[i]).OnlyEnforceIf(first)
            model.Add(end >= starts[i] + node.duration).OnlyEnforceIf(visits[i])
            if hinted is not None:
                model.AddHint(visits[i], int((day_index, node.key) in hinted["visits"]))
                model.AddHint(starts[i], hinted["visits"].get((day_index, node.key), node.earliest))
                model.AddHint(first, int(hinted["first"].get(day_index) == node.key))
                model.AddHint(last, int(hinted["last"].get(day_index) == node.key))

            for k, other in enumerate(nodes):
                if i == k or (node.place and other.place and node.place.place_id == other.place.place_id):
                    continue
                # Dinner ends the day, because places are usually closed after it. A stop the traveler kept may still be there.
                if node.slot == "dinner" and other.slot == "activity" and other.key not in problem.locked:
                    continue
                minutes = _arc_minutes(node, other, matrix)
                # A move that can't arrive in time, whatever the start, is left out to keep the model small.
                if node.earliest + node.duration + minutes > other.latest:
                    continue
                arc = model.NewBoolVar("")
                arcs.append((i + 1, k + 1, arc))
                arc_by_keys[node.key, other.key] = arc
                model.Add(starts[k] >= starts[i] + node.duration + minutes).OnlyEnforceIf(arc)
                if minutes:
                    objective.append(-TRAVEL_PENALTY_PER_MINUTE * minutes * arc)
                if hinted is not None:
                    model.AddHint(arc, int((day_index, node.key, other.key) in hinted["arcs"]))
        model.AddCircuit(arcs)

        for a, b in problem.no_break_between:
            for slot in ("lunch", "dinner"):
                into = arc_by_keys.get((a, f"break|{slot}"))
                out = arc_by_keys.get((f"break|{slot}", b))
                if into is not None and out is not None:
                    model.AddBoolOr([into.Not(), out.Not()])

        for slot in ("lunch", "dinner"):
            meals = [visits[i] for i, n in enumerate(nodes) if n.slot == slot]
            if meals:
                model.AddAtMostOne(meals)
                objective.append(MISSING_MEAL_PENALTY * sum(meals))

        count = sum(visits[i] for i, n in enumerate(nodes) if n.slot == "activity")
        model.Add(count <= problem.max_per_day)
        counts.append(count)
        day_values.append(sum(problem.value[n.key] * visits[i] for i, n in enumerate(nodes) if n.slot == "activity"))

        for i, node in enumerate(nodes):
            if node.place is None:
                objective.append(-MEAL_BREAK_PENALTY * visits[i])
            else:
                uses.setdefault(node.place.place_id, []).append(visits[i])
            if node.slot == "activity":
                bonus = REQUIRED_BONUS if node.key in problem.required else 0
                objective.append((bonus + VISIT_BONUS + problem.value[node.key]) * visits[i])
        objective.append(-DAY_TIME_PENALTY_PER_MINUTE * (begin - problem.day_start + end - problem.day_start))

    for place_id, place_visits in uses.items():
        if place_id in problem.locked:
            model.AddExactlyOne(place_visits)
        else:
            model.AddAtMostOne(place_visits)

    most = model.NewIntVar(0, problem.max_per_day, "most")
    fewest = model.NewIntVar(0, problem.max_per_day, "fewest")
    for count in counts:
        model.Add(most >= count)
        model.Add(fewest <= count)
    objective.append(-UNEVEN_DAY_PENALTY * (most - fewest))

    # Swapping two whole days gives the same plan, so only one order of the days is searched.
    if not problem.locked:
        for a, b in zip(day_values, day_values[1:]):
            model.Add(a >= b)

    model.Maximize(sum(objective))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = seconds
    solver.parameters.num_workers = min(8, max(1, os.cpu_count() or 4))
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, False

    routes = []
    for nodes, visits, starts in variables:
        chosen = [(node, solver.Value(start)) for node, visit, start in zip(nodes, visits, starts) if solver.Value(visit)]
        routes.append(sorted(chosen, key=lambda item: item[1]))
    return routes, status == cp_model.OPTIMAL


def _hint_lookup(hint: list[Route] | None) -> dict | None:
    if hint is None:
        return None
    lookup: dict = {"visits": {}, "arcs": set(), "first": {}, "last": {}}
    for day_index, route in enumerate(hint):
        for node, start in route:
            lookup["visits"][day_index, node.key] = start
        for (a, _), (b, _) in zip(route, route[1:]):
            lookup["arcs"].add((day_index, a.key, b.key))
        if route:
            lookup["first"][day_index] = route[0][0].key
            lookup["last"][day_index] = route[-1][0].key
    return lookup


def _quick_plan(problem: _Problem, matrix: dict[tuple[str, str], int]) -> list[Route]:
    """A simple plan: the solver's starting point, and the safety net if it finds nothing in time."""
    free_attractions = sorted(
        (p for p in problem.attractions if p.place_id not in problem.locked),
        key=lambda p: (p.place_id not in problem.required, -problem.value[p.place_id]),
    )
    free_restaurants = [p for p in problem.restaurants if p.place_id not in problem.locked]
    by_id = {p.place_id: p for p in problem.attractions + problem.restaurants}
    meal_windows = _meal_slots(problem)
    routes: list[Route] = []

    for day_index in range(problem.days):
        day_number = day_index + 1
        fixed = sorted(
            (
                (_attraction_node(by_id[pid], problem), start)
                if by_id[pid].kind == "attraction"
                else (_meal_node(by_id[pid], _locked_meal_slot(start), (start, start)), start)
                for pid, (locked_day, start) in problem.locked.items()
                if locked_day == day_number
            ),
            key=lambda item: item[1],
        )
        locked_slots = {node.slot for node, _ in fixed}
        meals = [slot for slot in meal_windows if slot not in locked_slots]
        target = math.ceil(len(free_attractions) / (problem.days - day_index))
        activities = sum(1 for node, _ in fixed if node.slot == "activity")
        route: Route = []
        now = problem.day_start
        previous: _Node | None = None

        def arrive(node: _Node) -> int:
            if previous is None:
                return now + _start_minutes(node, problem, matrix)
            return now + _arc_minutes(previous, node, matrix)

        while True:
            next_fixed = fixed[0][1] if fixed else None
            next_meal_latest = meal_windows[meals[0]][1] if meals else None
            limits = [limit for limit in (next_fixed, next_meal_latest) if limit is not None]
            limit = min(limits) if limits else problem.day_end

            options = []
            dinner_done = any(placed.slot == "dinner" for placed, _ in route)
            if activities < min(target, problem.max_per_day) and not dinner_done:
                for place in free_attractions:
                    node = _attraction_node(place, problem)
                    start = arrive(node)
                    if start + node.duration <= limit and start <= node.latest:
                        options.append((start - now, -problem.value[place.place_id], place.place_id, node, start))
            if options:
                # Travel is 0 before the first stop, so the first choice is simply the most valuable.
                _, _, place_id, node, start = min(options, key=lambda option: option[:3])
                free_attractions = [p for p in free_attractions if p.place_id != place_id]
                activities += 1
            elif fixed and (not meals or fixed[0][1] <= meal_windows[meals[0]][0]):
                node, start = fixed.pop(0)
            elif meals:
                slot = meals.pop(0)
                window = meal_windows[slot]
                candidates = [_meal_node(p, slot, window) for p in free_restaurants] or [_meal_node(None, slot, window)]
                node = min(candidates, key=arrive)
                start = max(arrive(node), window[0])
                if start > window[1] or (fixed and start + node.duration > fixed[0][1]):
                    continue
                free_restaurants = [p for p in free_restaurants if node.place is None or p.place_id != node.place.place_id]
            elif fixed:
                node, start = fixed.pop(0)
            else:
                break
            route.append((node, start))
            now = start + node.duration
            previous = node
        routes.append(route)

    if not problem.locked:
        routes.sort(key=lambda route: -sum(problem.value[n.key] for n, _ in route if n.slot == "activity"))
    return routes


def _legs(route: Route, problem: _Problem) -> list[tuple[str, str, tuple, tuple]]:
    """The real hops of a day. Travel goes from the last real place, skipping any meal break."""
    legs = []
    previous = None
    if problem.start_point:
        previous = ("start", problem.start_point)
    for node, _ in route:
        if node.place is None:
            continue
        here = (node.place.latitude, node.place.longitude)
        if previous:
            legs.append((previous[0], node.place.place_id, previous[1], here))
        previous = (node.place.place_id, here)
    return legs


def _timed_day(
    route: Route, day_number: int, problem: _Problem, hops: dict[tuple[str, str], Hop], matrix: dict[tuple[str, str], int]
) -> tuple[list[Stop], list[str]]:
    """Sets the final times with real travel. A stop that no longer fits is left out and reported."""
    stops: list[Stop] = []
    notes: list[str] = []
    now = problem.day_start
    previous_id = "start" if problem.start_point else None

    for node, planned in route:
        hop = None
        travel = 0
        if node.place is not None and previous_id is not None:
            pair = (previous_id, node.place.place_id)
            hop = hops.get(pair)
            reserved = hop.reserved_minutes if hop else None
            travel = math.ceil(reserved) if reserved is not None else matrix[pair]
        # As soon as the traveler can arrive, so free time is left together rather than scattered.
        start = max(node.earliest, now + travel)
        locked = node.place is not None and node.place.place_id in problem.locked
        if start > node.latest and not locked:
            label = node.place.name if node.place else f"The {node.slot} break"
            notes.append(f"{label} was left out, because real travel times left no room for it.")
            continue
        if locked and start > planned:
            notes.append(f"{node.place.name} is locked at {_clock(planned)}, but real travel arrives at {_clock(start)}.")
            start = planned
        stops.append(
            Stop(
                place_id=node.place.place_id if node.place else None,
                name=node.place.name if node.place else MEAL_BREAK_NAME,
                slot=node.slot,
                start=_clock(start),
                duration_minutes=node.duration,
                latitude=node.place.latitude if node.place else None,
                longitude=node.place.longitude if node.place else None,
                hop=hop,
            )
        )
        if stops[:-1]:
            gap = start - travel - now
            if gap >= FREE_TIME_NOTE_MINUTES:
                notes.append(f"Free time of about {gap} minutes before {stops[-1].name}.")
        now = start + node.duration
        if node.place is not None:
            previous_id = node.place.place_id
    return stops, notes


def optimize_schedule(
    attractions: list[Place],
    restaurants: list[Place],
    days: int,
    day_start: str = "09:00",
    day_end: str = "22:00",
    max_activities_per_day: int = 4,
    visit_minutes: dict[str, int] | None = None,
    priorities: dict[str, float] | None = None,
    locked: list[LockedStop] | None = None,
    excluded: list[str] | None = None,
    required: list[str] | None = None,
    start_point: tuple[float, float] | None = None,
    start_date: date | None = None,
    max_walk_minutes: int = MAX_WALK_OFFER_MINUTES,
    travel: Callable[..., Hop] = get_route,
    solver_seconds: float | None = None,
) -> ScheduleResult:
    """visit_minutes and priorities are the Planner's choices, by place ID. Priorities go from 0 to 1."""
    if days < 1 or max_activities_per_day < 1 or _minutes(day_start) >= _minutes(day_end):
        return ScheduleResult(status="failed", notes=["days and max_activities_per_day must be at least 1, and the day must end after it starts."])

    notes: list[str] = []
    excluded_ids = set(excluded or [])
    locked = locked or []
    locked_ids = {stop.place_id for stop in locked}
    required_ids = set(required or [])

    def usable(places: list[Place]) -> list[Place]:
        kept = []
        for place in places:
            if place.place_id in excluded_ids:
                continue
            if place.latitude is None or place.longitude is None:
                notes.append(f"{place.name} has no coordinates, so it can't be scheduled.")
                continue
            kept.append(place)
        return kept

    usable_attractions = usable(attractions)
    usable_restaurants = usable(restaurants)
    if not usable_attractions:
        return ScheduleResult(status="failed", notes=notes + ["No verified attraction with coordinates was given."])

    priorities = priorities or {}
    value = {p.place_id: _value(p, priorities) for p in usable_attractions}
    kept_lengths = {stop.place_id: stop.duration_minutes for stop in locked if stop.duration_minutes}
    ranked = sorted(usable_attractions, key=lambda p: (p.place_id not in locked_ids | required_ids, -value[p.place_id]))
    if len(ranked) > MAX_CANDIDATES:
        notes.append(f"Only the top {MAX_CANDIDATES} of {len(ranked)} attractions were considered.")

    # A pace below the stops the traveler kept could never be met, so it can't apply to those days.
    attraction_ids = {p.place_id for p in usable_attractions}
    kept_per_day = [sum(s.day_number == n and s.place_id in attraction_ids for s in locked) for n in range(1, days + 1)]

    problem = _Problem(
        days=days,
        day_start=_minutes(day_start),
        day_end=_minutes(day_end),
        max_per_day=max(max_activities_per_day, *kept_per_day),
        attractions=ranked[:MAX_CANDIDATES],
        restaurants=usable_restaurants[:MAX_CANDIDATES],
        visit={
            # A stop the traveler kept also keeps its length.
            p.place_id: kept_lengths.get(p.place_id)
            or max(MIN_VISIT_MINUTES, min(MAX_VISIT_MINUTES, (visit_minutes or {}).get(p.place_id, DEFAULT_VISIT_MINUTES)))
            for p in usable_attractions
        },
        value=value,
        locked={stop.place_id: (stop.day_number, _minutes(stop.start)) for stop in locked},
        required=required_ids,
        start_point=start_point,
    )

    places = problem.attractions + problem.restaurants
    matrix = {
        (a.place_id, b.place_id): _estimate_minutes(a.latitude, a.longitude, b.latitude, b.longitude)
        for a in places
        for b in places
        if a.place_id != b.place_id
    }
    if start_point:
        matrix.update({("start", p.place_id): _estimate_minutes(*start_point, p.latitude, p.longitude) for p in places})

    # Stops the traveler kept had real routes when they were timed, so an estimate can't call their gap impossible.
    kept_gaps: dict[tuple[str, str], int] = {}
    in_order = sorted(locked, key=lambda stop: (stop.day_number, _minutes(stop.start)))
    for a, b in zip(in_order, in_order[1:]):
        if a.day_number == b.day_number and (a.place_id, b.place_id) in matrix:
            length = problem.visit.get(a.place_id, a.duration_minutes or MEAL_MINUTES)
            kept_gaps[a.place_id, b.place_id] = max(0, _minutes(b.start) - _minutes(a.start) - length)
    for pair, gap in kept_gaps.items():
        matrix[pair] = min(matrix[pair], gap)

    if solver_seconds is None:
        solver_seconds = SHORT_TRIP_SECONDS if days <= SHORT_TRIP_DAYS else LONG_TRIP_SECONDS

    hops: dict[tuple[str, str], Hop] = {}

    def fetch(day_index: int, route: Route) -> bool:
        """Gets real routes for a day, and returns True if any differs enough from what the solver assumed."""
        changed = False
        starts = {n.place.place_id: start for n, start in route if n.place}
        for from_id, to_id, origin, destination in _legs(route, problem):
            pair = (from_id, to_id)
            if pair not in hops:
                departure = None
                if start_date:
                    clock = max(0, starts[to_id] - matrix[pair])
                    departure = datetime.combine(start_date + timedelta(days=day_index), time(clock // 60, clock % 60))
                hops[pair] = travel(*origin, *destination, departure_local=departure, max_walk_minutes=max_walk_minutes)
            reserved = hops[pair].reserved_minutes
            if reserved is not None:
                real = math.ceil(reserved)
                real = min(real, kept_gaps.get(pair, real))
                changed = changed or abs(real - matrix[pair]) > RESOLVE_TOLERANCE_MINUTES
                matrix[pair] = real
                # The way back is usually similar, and is a better guess than the estimate until it is checked.
                if (to_id, from_id) in matrix and (to_id, from_id) not in hops:
                    matrix[to_id, from_id] = real
        for (a, _), (between, _), (b, _) in zip(route, route[1:], route[2:]):
            if between.place is None and a.place and b.place:
                pair = (a.place.place_id, b.place.place_id)
                if matrix[pair] > MEAL_BREAK_TRAVEL_MINUTES + RESOLVE_TOLERANCE_MINUTES and pair not in problem.no_break_between:
                    problem.no_break_between.update({pair, pair[::-1]})
                    changed = True
        return changed

    def real_rank(candidate: list[Route]) -> tuple[int, int, int]:
        """Lower is better, judged on real routes: fewest stops lost, most attractions, least travel."""
        timed = [_timed_day(route, i + 1, problem, hops, matrix) for i, route in enumerate(candidate)]
        lost = sum("left out" in note for _, day_notes in timed for note in day_notes)
        attractions_kept = sum(stop.slot == "activity" for stops, _ in timed for stop in stops)
        travelled = sum(matrix[from_id, to_id] for route in candidate for from_id, to_id, _, _ in _legs(route, problem))
        return lost, -attractions_kept, travelled

    latest = _quick_plan(problem, matrix)
    routes: list[Route] | None = None
    proven = False
    for _ in range(MAX_ROUNDS):
        new_routes, new_proven = _solve(problem, matrix, solver_seconds, latest)
        if new_routes is None:
            break
        new_pairs = {pair[:2] for route in new_routes for pair in _legs(route, problem)} - hops.keys()
        # The first plan always gets its routes. A later one must fit the request limit.
        if routes is not None and len(hops) + len(new_pairs) > MAX_ROUTE_REQUESTS:
            notes.append("The route request limit for this trip was reached, so the plan wasn't improved further.")
            break
        unchanged = routes is not None and [[n.key for n, _ in r] for r in new_routes] == [[n.key for n, _ in r] for r in latest]
        latest = new_routes
        changed = [fetch(i, route) for i, route in enumerate(latest)]
        # A new plan can rely on hops not checked yet, so it only wins if it is better on real routes.
        if routes is None or real_rank(latest) < real_rank(routes):
            routes, proven = latest, new_proven
        # A lost stop always gets another try, because the real times are in the matrix now.
        if unchanged or not (any(changed) or real_rank(latest)[0]):
            break

    if routes is None:
        notes.append("The solver found no plan in time, so a simpler plan is shown.")
        routes = latest
        for i, route in enumerate(routes):
            fetch(i, route)
    elif not proven:
        notes.append("This is a good plan, but it wasn't proven to be the best one in the time allowed.")

    result_days = []
    left_out = False
    for i, route in enumerate(routes):
        stops, day_notes = _timed_day(route, i + 1, problem, hops, matrix)
        # Without a lost stop, the stop after it has a new hop, which needs a real route too.
        kept_stops = {(stop.place_id, stop.slot) for stop in stops}
        kept_route = [item for item in route if (item[0].place.place_id if item[0].place else None, item[0].slot) in kept_stops]
        if len(kept_route) < len(route):
            fetch(i, kept_route)
            stops, _ = _timed_day(kept_route, i + 1, problem, hops, matrix)
        for slot in _meal_slots(problem):
            if not any(stop.slot == slot for stop in stops):
                left_out = True
                notes.append(f"Day {i + 1}: there was no room for {slot}.")
        left_out = left_out or any("left out" in note for note in day_notes)
        notes.extend(f"Day {i + 1}: {note}" for note in day_notes)
        result_days.append(Day(day_number=i + 1, stops=stops))
        if not any(stop.place_id and stop.slot == "activity" for stop in stops):
            left_out = True
            notes.append(f"Day {i + 1}: no attraction could be scheduled.")
        if any(stop.place_id is None for stop in stops):
            notes.append(f"Day {i + 1}: no verified restaurant for every meal, so a labelled meal break was added.")

    scheduled = {stop.place_id for day in result_days for stop in day.stops}
    missing_required = required_ids - scheduled
    for place_id in sorted(missing_required):
        notes.append(f"{place_id} was required, but it could not fit in the trip.")

    notes.append("Visit lengths are estimates. Opening hours were not checked.")
    return ScheduleResult(status="partial" if left_out or missing_required else "ok", days=result_days, notes=notes)
