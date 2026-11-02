"""Tests of optimize_schedule with the real OR-Tools solver. No network is used."""

import inspect
import math
import random
from datetime import date

from city_planner.schemas import MEAL_BREAK_NAME, Hop, LockedStop, Place, WalkOption
from city_planner.tools import optimize_schedule as module
from city_planner.tools.check_plan import check_plan
from city_planner.tools.distance import haversine_km
from city_planner.tools.optimize_schedule import optimize_schedule

SECONDS = 5


def place(number, lat=48.86, lon=2.34, kind="attraction", views=1000):
    prefix = "wikipedia" if kind == "attraction" else "foursquare"
    return Place(
        place_id=f"{prefix}:{number}",
        name=f"Place {number}",
        kind=kind,
        source=prefix,
        latitude=lat,
        longitude=lon,
        page_views=views if kind == "attraction" else None,
    )


def food(number, lat=48.86, lon=2.34):
    return place(number, lat, lon, kind="restaurant")


def by_distance(from_lat, from_lon, to_lat, to_lon, departure_local=None, max_walk_minutes=20):
    """A stand-in for real routes: minutes follow distance, with no network."""
    return Hop(walk=WalkOption(minutes=round(haversine_km(from_lat, from_lon, to_lat, to_lon) * 10, 1), is_estimate=False))


def plan(attractions, restaurants=(), days=1, **kwargs):
    return optimize_schedule(list(attractions), list(restaurants), days, travel=kwargs.pop("travel", by_distance), solver_seconds=kwargs.pop("solver_seconds", SECONDS), **kwargs)


def city(attractions=4, restaurants=2):
    return (
        [place(i, 48.86 + i * 0.001) for i in range(attractions)],
        [food(100 + i, 48.86 + i * 0.001) for i in range(restaurants)],
    )


def names(result, slot=None):
    return [s.name for d in result.days for s in d.stops if slot is None or s.slot == slot]


def minutes(clock):
    return int(clock[:2]) * 60 + int(clock[3:])


def test_full_trip_passes_check_plan_and_never_repeats_a_place():
    attractions, restaurants = city(8, 4)
    result = plan(attractions, restaurants, days=2)
    assert result.status == "ok"
    assert len(set(names(result))) == len(names(result)) == 12
    assert check_plan(result.days, attractions + restaurants, expected_days=2).passed


def test_meals_are_inside_their_windows():
    day = plan(*city()).days[0]
    lunch = next(s for s in day.stops if s.slot == "lunch")
    dinner = next(s for s in day.stops if s.slot == "dinner")
    assert 12 * 60 <= minutes(lunch.start) <= 14 * 60 + 30
    assert 18 * 60 + 30 <= minutes(dinner.start) <= 21 * 60


def test_two_separate_areas_are_not_mixed_across_days():
    # Two groups about 11 km apart, with the same page views.
    north = [place(i, 48.90 + i * 0.001) for i in range(2)]
    south = [place(10 + i, 48.80 + i * 0.001) for i in range(2)]
    result = plan(north + south, days=2, max_activities_per_day=2)
    days = [{s.place_id for s in d.stops if s.place_id} for d in result.days]
    assert {p.place_id for p in north} in days and {p.place_id for p in south} in days


def test_route_order_avoids_zigzag():
    # Far ends first and last would be a zigzag: the solver must go along the line.
    line = [place(0, 48.860), place(1, 48.880), place(2, 48.870), place(3, 48.890)]
    # This afternoon has no meal, because a meal break has no location and would hide the real distance.
    short_visits = {p.place_id: 30 for p in line}
    result = plan(line, day_start="15:00", day_end="18:00", visit_minutes=short_visits)
    stops = [s.place_id for s in result.days[0].stops if s.slot == "activity"]
    order = [int(p.split(":")[1]) for p in stops]
    assert order in ([0, 2, 1, 3], [3, 1, 2, 0])


def test_nearer_restaurant_beats_a_far_one():
    attractions = [place(i, 48.86 + i * 0.001) for i in range(2)]
    near, far = food(100, 48.861), food(101, 48.90)
    result = plan(attractions, [far, near], max_activities_per_day=2, day_end="17:00")
    assert names(result, "lunch") == ["Place 100"]


def test_most_viewed_places_are_chosen():
    places = [place(i, views=10) for i in range(5)] + [place(50 + i, views=1_000_000) for i in range(2)]
    assert {"Place 50", "Place 51"} <= set(names(plan(places, max_activities_per_day=2), "activity"))


def test_planner_priority_can_beat_popularity():
    places = [place(0, views=1_000_000), place(1, views=10)]
    chosen = plan(places, max_activities_per_day=1, priorities={"wikipedia:1": 1.0})
    assert names(chosen, "activity") == ["Place 1"]


def test_days_are_kept_even():
    result = plan(*city(6, 0), days=2, max_activities_per_day=4)
    assert [len([s for s in d.stops if s.slot == "activity"]) for d in result.days] == [3, 3]


def test_pace_limit_is_respected():
    result = plan(*city(8, 2), max_activities_per_day=2)
    assert len(names(result, "activity")) == 2


def test_visit_length_comes_from_the_planner_within_limits():
    a, b = place(0), place(1, 48.861)
    result = plan([a, b], visit_minutes={a.place_id: 150, b.place_id: 999})
    lengths = {s.place_id: s.duration_minutes for s in result.days[0].stops}
    assert lengths[a.place_id] == 150 and lengths[b.place_id] == 240


def test_excluded_place_never_appears():
    attractions, restaurants = city()
    result = plan(attractions, restaurants, excluded=[attractions[0].place_id, restaurants[0].place_id])
    assert attractions[0].name not in names(result) and restaurants[0].name not in names(result)


def test_required_place_beats_a_more_popular_one():
    popular, wanted = place(0, views=1_000_000), place(1, views=10)
    result = plan([popular, wanted], max_activities_per_day=1, required=[wanted.place_id])
    assert names(result, "activity") == [wanted.name]


def test_required_place_that_cannot_fit_is_reported():
    a, b = place(0), place(1, 48.861)
    result = plan([a, b], max_activities_per_day=1, required=[a.place_id, b.place_id])
    assert result.status == "partial" and any("required" in n for n in result.notes)


def test_locked_stop_keeps_its_day_and_time():
    attractions, restaurants = city(6, 4)
    lock = LockedStop(place_id=attractions[5].place_id, day_number=2, start="10:00")
    result = plan(attractions, restaurants, days=2, locked=[lock])
    stop = next(s for s in result.days[1].stops if s.place_id == lock.place_id)
    assert stop.start == "10:00"
    assert lock.place_id not in {s.place_id for s in result.days[0].stops}


def test_locked_restaurant_keeps_its_meal():
    attractions, restaurants = city(3, 3)
    lock = LockedStop(place_id=restaurants[2].place_id, day_number=1, start="13:30")
    stop = next(s for s in plan(attractions, restaurants, locked=[lock]).days[0].stops if s.slot == "lunch")
    assert stop.place_id == lock.place_id and stop.start == "13:30"


def test_place_that_cannot_fit_is_left_out_with_a_note():
    # Over 100 km apart with real travel of over 1,000 minutes.
    far = [place(0, 48.86), place(1, 50.0)]
    result = plan(far, days=1)
    assert len(names(result, "activity")) == 1


def test_slow_real_route_forces_a_new_solve_and_final_times_use_it():
    a, b, c = place(0, 48.860), place(1, 48.861), place(2, 48.862)
    pair = {(a.latitude, b.latitude), (b.latitude, a.latitude)}

    def slow(from_lat, from_lon, to_lat, to_lon, departure_local=None, max_walk_minutes=20):
        minutes_ = 200 if (from_lat, to_lat) in pair else 5
        return Hop(walk=WalkOption(minutes=minutes_, is_estimate=False))

    result = plan([a, b, c], max_activities_per_day=3, travel=slow)
    order = [s.place_id for s in result.days[0].stops if s.place_id]
    assert all({x, y} != {a.place_id, b.place_id} for x, y in zip(order, order[1:]))
    assert len(order) == 3
    assert len([s for s in result.days[0].stops if s.slot == "lunch"]) == 1
    assert check_plan(result.days, [a, b, c], 1).passed


def test_too_few_places_gives_honest_partial_and_invents_nothing():
    attractions, restaurants = city(1, 1)
    result = plan(attractions, restaurants, days=2)
    assert result.status == "partial"
    real = {p.name for p in attractions + restaurants}
    assert all(n in real or n == MEAL_BREAK_NAME for n in names(result))


def test_no_restaurants_gives_labelled_meal_breaks():
    attractions, _ = city()
    result = plan(attractions)
    meals = [s for s in result.days[0].stops if s.slot != "activity"]
    assert [m.name for m in meals] == [MEAL_BREAK_NAME] * 2
    assert all(m.place_id is None and m.latitude is None for m in meals)
    assert check_plan(result.days, attractions, 1).passed


def test_restaurants_are_used_before_meal_breaks():
    attractions, _ = city()
    meals = plan(attractions, [food(100)]).days[0].stops
    assert [s.name for s in meals if s.slot != "activity"].count(MEAL_BREAK_NAME) == 1


def test_place_without_coordinates_is_reported():
    missing = Place(place_id="wikipedia:9", name="No Coords", kind="attraction", source="wikipedia")
    result = plan([missing, place(1)])
    assert "No Coords" not in names(result) and any("No Coords" in n for n in result.notes)


def test_no_usable_places_fails_clearly():
    assert plan([]).status == "failed"


def test_invalid_input_fails_clearly():
    assert plan(*city(), days=0).status == "failed"
    assert plan(*city(), day_start="20:00", day_end="10:00").status == "failed"


def test_notes_say_opening_hours_are_unchecked():
    assert any("Opening hours were not checked" in n for n in plan(*city()).notes)


def test_short_day_leaves_out_meals_that_cannot_fit():
    result = plan(*city(), day_start="09:00", day_end="15:00")
    assert names(result, "dinner") == [] and len(names(result, "lunch")) == 1


def test_start_point_counts_travel_to_the_first_stop():
    first = plan([place(0, 48.90)], start_point=(48.86, 2.34), max_activities_per_day=1, day_end="12:00")
    stop = first.days[0].stops[0]
    assert stop.hop is not None and minutes(stop.start) >= 9 * 60 + stop.hop.reserved_minutes


def test_trip_date_gives_each_day_its_own_departure():
    seen = []

    def spy(from_lat, from_lon, to_lat, to_lon, departure_local=None, max_walk_minutes=20):
        seen.append(departure_local)
        return by_distance(from_lat, from_lon, to_lat, to_lon)

    attractions, _ = city(4, 0)
    plan(attractions, days=2, max_activities_per_day=2, start_date=date(2026, 12, 7), travel=spy)
    assert {d.date() for d in seen} == {date(2026, 12, 7), date(2026, 12, 8)}


def test_walking_limit_reaches_the_route_lookup():
    seen = []

    def spy(from_lat, from_lon, to_lat, to_lon, departure_local=None, max_walk_minutes=20):
        seen.append(max_walk_minutes)
        return by_distance(from_lat, from_lon, to_lat, to_lon)

    plan(*city(2, 0), max_walk_minutes=7, travel=spy)
    assert set(seen) == {7}


def test_route_requests_stay_under_the_limit_after_the_first_plan():
    calls = []

    def changing(from_lat, from_lon, to_lat, to_lon, departure_local=None, max_walk_minutes=20):
        calls.append(1)
        return Hop(walk=WalkOption(minutes=60, is_estimate=False))

    attractions, restaurants = city(6, 4)
    plan(attractions, restaurants, days=2, travel=changing, solver_seconds=2)
    assert len(calls) <= module.MAX_ROUTE_REQUESTS


def test_no_taxi_logic_in_the_optimizer():
    assert "taxi" not in inspect.getsource(module).lower()


def test_quick_plan_is_a_valid_safety_net(monkeypatch):
    monkeypatch.setattr(module, "_solve", lambda *args: (None, False))
    attractions, restaurants = city(6, 4)
    result = plan(attractions, restaurants, days=2)
    assert any("simpler plan" in n for n in result.notes)
    assert check_plan(result.days, attractions + restaurants, 2).passed
    assert len(set(names(result))) == len(names(result))
    assert all(s.slot == "activity" or s.place_id for d in result.days for s in d.stops)


def test_invariants_on_random_trips():
    for seed in range(200):
        rng = random.Random(seed)
        coordinate = lambda: (48.82 + rng.random() * 0.09, 2.27 + rng.random() * 0.14)
        attractions = [place(i, *coordinate(), views=rng.randint(1, 10**6)) for i in range(rng.randint(1, 7))]
        restaurants = [food(100 + i, *coordinate()) for i in range(rng.randint(0, 3))]
        days = rng.randint(1, 2)
        excluded = [attractions[0].place_id] if len(attractions) > 1 and rng.random() < 0.3 else []
        locked = []
        kept = [p for p in attractions if p.place_id not in excluded]
        if kept and rng.random() < 0.3:
            locked = [LockedStop(place_id=kept[-1].place_id, day_number=rng.randint(1, days), start="10:00")]

        result = plan(attractions, restaurants, days=days, excluded=excluded, locked=locked, max_activities_per_day=rng.randint(1, 4), solver_seconds=0.5)

        context = f"seed {seed}"
        ids = [s.place_id for d in result.days for s in d.stops if s.place_id]
        assert len(ids) == len(set(ids)), context
        assert not set(ids) & set(excluded), context
        for lock in locked:
            kept_stop = [s for s in result.days[lock.day_number - 1].stops if s.place_id == lock.place_id]
            assert kept_stop and kept_stop[0].start == lock.start, context
        for day in result.days:
            end = None
            for stop in day.stops:
                start = minutes(stop.start)
                if end is not None:
                    assert start >= end + math.floor(stop.hop.reserved_minutes if stop.hop else 0), context
                end = start + stop.duration_minutes
                assert end <= 22 * 60 or stop.slot != "activity", context
                window = module.MEAL_WINDOWS.get(stop.slot)
                if window:
                    assert window[0] <= start <= window[1], context
        check = check_plan(result.days, attractions + restaurants, days)
        assert not [i for i in check.issues if i.severity == "error" and i.code != "empty_day"], context
