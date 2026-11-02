"""An independent proof for the optimizer: try every possible plan and compare the best score.

This file shares no planning code with the solver. It writes out the rules and the scoring again by hand,
so a mistake in the solver can't hide by also being in the check.
"""

import itertools
import math
import random

import pytest

from city_planner.schemas import Hop, Place, WalkOption
from city_planner.tools.optimize_schedule import optimize_schedule

DAY_START, DAY_END = 9 * 60, 22 * 60
LUNCH, DINNER = (12 * 60, 14 * 60 + 30), (18 * 60 + 30, 21 * 60)
VISIT, MEAL = 90, 60


def minutes_between(a, b):
    """Written out again by hand. Real routes equal the solver's estimate here, so one solve is the final answer."""
    phi1, phi2 = math.radians(a.latitude), math.radians(b.latitude)
    h = math.sin((phi2 - phi1) / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(math.radians(b.longitude - a.longitude) / 2) ** 2
    km = 6371.0 * 2 * math.atan2(math.sqrt(h), math.sqrt(1 - h))
    return math.ceil(min(km / 4.5 * 60 * 1.3, 7.0 + km / 14.3 * 60))


def lookup_for(places):
    by_position = {(p.latitude, p.longitude): p for p in places}

    def travel(from_lat, from_lon, to_lat, to_lon, departure_local=None, max_walk_minutes=20):
        a, b = by_position[from_lat, from_lon], by_position[to_lat, to_lon]
        return Hop(walk=WalkOption(minutes=minutes_between(a, b), is_estimate=False))

    return travel


def value_of(place):
    popularity = min(1.0, math.log10(place.page_views + 1) / 7)
    return int(popularity * 1000)


def best_day(attractions, lunch, dinner):
    """The best (score, stops) of one day: tries every order of these stops. None if none fits."""
    stops = [(p, VISIT, DAY_START, DAY_END - VISIT) for p in attractions]
    if lunch:
        stops.append((lunch, MEAL, *LUNCH))
    if dinner:
        stops.append((dinner, MEAL, *DINNER))

    best = None
    for order in itertools.permutations(stops):
        # Dinner ends the day.
        if dinner and order[-1][0] is not dinner:
            continue
        travel = sum(minutes_between(a[0], b[0]) for a, b in zip(order, order[1:]))

        # Each stop starts as early as it can, which gives both the earliest start and the earliest finish.
        now, first, fits = None, None, True
        for i, (place, duration, opens, closes) in enumerate(order):
            arrive = opens if now is None else max(opens, now + minutes_between(order[i - 1][0], place))
            if arrive > closes:
                fits = False
                break
            first = arrive if first is None else first
            now = arrive + duration
        if not fits:
            continue

        score = -10 * travel - (first - DAY_START) - (now - DAY_START)
        if best is None or score > best:
            best = score
    return best


def trip_score(days, attractions, restaurants, max_per_day):
    """The best total score of all plans. Every meal must have a restaurant in these test cases."""
    best = None
    choices = itertools.product(range(days + 1), repeat=len(attractions))
    meal_orders = list(itertools.permutations(restaurants, 2 * days))
    cache = {}
    for choice in choices:
        groups = [tuple(a for a, d in zip(attractions, choice) if d == day) for day in range(days)]
        counts = [len(g) for g in groups]
        if max(counts) > max_per_day:
            continue
        score = sum(10_000 + value_of(a) for g in groups for a in g) - 1500 * (max(counts) - min(counts))
        for meals in meal_orders:
            total = score
            for day, group in enumerate(groups):
                lunch, dinner = meals[2 * day], meals[2 * day + 1]
                key = (tuple(a.place_id for a in group), lunch.place_id, dinner.place_id)
                if key not in cache:
                    cache[key] = best_day(group, lunch, dinner)
                if cache[key] is None:
                    total = None
                    break
                total += cache[key]
            if total is not None and (best is None or total > best):
                best = total
    return best


def score_of_plan(result, attractions, restaurants):
    """Scores the optimizer's plan with the same hand-written rules."""
    by_id = {p.place_id: p for p in attractions + restaurants}
    total = 0
    counts = []
    for day in result.days:
        activities = [s for s in day.stops if s.slot == "activity"]
        counts.append(len(activities))
        total += sum(10_000 + value_of(by_id[s.place_id]) for s in activities)
        places = [by_id[s.place_id] for s in day.stops]
        total -= 10 * sum(minutes_between(a, b) for a, b in zip(places, places[1:]))
        starts = [int(s.start[:2]) * 60 + int(s.start[3:]) for s in day.stops]
        total -= (starts[0] - DAY_START) + (starts[-1] + day.stops[-1].duration_minutes - DAY_START)
    return total - 1500 * (max(counts) - min(counts))


def make_case(seed, attraction_count, restaurant_count):
    rng = random.Random(seed)
    coordinate = lambda: (round(48.82 + rng.random() * 0.09, 5), round(2.27 + rng.random() * 0.14, 5))
    attractions = [
        Place(place_id=f"wikipedia:{i}", name=f"A{i}", kind="attraction", source="wikipedia", latitude=(c := coordinate())[0], longitude=c[1], page_views=rng.randint(10, 10**6))
        for i in range(attraction_count)
    ]
    restaurants = [
        Place(place_id=f"foursquare:{i}", name=f"R{i}", kind="restaurant", source="foursquare", latitude=(c := coordinate())[0], longitude=c[1])
        for i in range(restaurant_count)
    ]
    return attractions, restaurants


@pytest.mark.parametrize(
    "seed,days,attraction_count,restaurant_count,max_per_day",
    [
        (1, 1, 4, 2, 4),
        (2, 1, 4, 3, 2),
        (3, 1, 3, 3, 4),
        (4, 1, 5, 2, 3),
        (5, 2, 3, 4, 2),
        (6, 2, 4, 4, 2),
        (7, 2, 4, 4, 3),
    ],
)
def test_solver_matches_trying_every_plan(seed, days, attraction_count, restaurant_count, max_per_day):
    attractions, restaurants = make_case(seed, attraction_count, restaurant_count)
    result = optimize_schedule(
        attractions, restaurants, days,
        max_activities_per_day=max_per_day,
        visit_minutes={a.place_id: VISIT for a in attractions},
        travel=lookup_for(attractions + restaurants),
        solver_seconds=30,
    )  # fmt: skip
    assert not any("wasn't proven" in note for note in result.notes)
    assert score_of_plan(result, attractions, restaurants) == trip_score(days, attractions, restaurants, max_per_day)
