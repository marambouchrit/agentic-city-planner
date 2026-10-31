"""Tests of optimize_schedule with the real OR-Tools solver. No network is used."""

import math

from city_planner.schemas import MEAL_BREAK_NAME, Place
from city_planner.tools.check_plan import check_plan
from city_planner.tools.optimize_schedule import optimize_schedule


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


def city_places(attractions=9, restaurants=6):
    """Everything within a short walk."""
    return [place(i, 48.86 + i * 0.001) for i in range(attractions)] + [
        place(100 + i, 48.86 + i * 0.001, kind="restaurant") for i in range(restaurants)
    ]


def names(result):
    return [s.name for d in result.days for s in d.stops]


def test_full_trip_passes_check_plan_and_never_repeats_a_place():
    places = city_places()
    result = optimize_schedule(places, days=3)
    assert result.status == "ok"
    assert [len(d.stops) for d in result.days] == [5, 5, 5]
    assert len(set(names(result))) == 15
    assert check_plan(result.days, places, expected_days=3).passed


def test_day_follows_the_template():
    day = optimize_schedule(city_places(), days=1).days[0]
    assert [s.slot for s in day.stops] == ["activity", "activity", "lunch", "activity", "dinner"]
    assert day.stops[0].start == "09:00"
    assert day.stops[0].travel_mode is None
    assert day.stops[2].start == "13:00"
    assert day.stops[4].start == "19:00"


def test_most_viewed_places_are_chosen_first():
    places = [place(i, views=10) for i in range(5)] + [place(50 + i, views=1_000_000) for i in range(3)]
    chosen = names(optimize_schedule(places, days=1))
    assert {"Place 50", "Place 51", "Place 52"} <= set(chosen)


def test_agent_priority_can_beat_popularity():
    places = [place(0, views=1_000_000), place(1, views=1_000_000), place(2, views=1_000_000), place(3, views=10)]
    chosen = names(optimize_schedule(places, days=1, priorities={"wikipedia:3": 1.0}))
    assert "Place 3" in chosen


def test_late_morning_pushes_lunch_back_instead_of_overlapping():
    # A 12:00 start with 2 stops of 90 minutes can't reach lunch at 13:00.
    places = city_places()
    result = optimize_schedule(places, days=1, start_hour=12)
    lunch = next(s for s in result.days[0].stops if s.slot == "lunch")
    assert lunch.start >= "15:00"
    assert check_plan(result.days, places, expected_days=1).passed


def test_travel_time_is_included_in_the_next_start():
    # Three places fill every activity slot, so the second stop always follows the first.
    # Each hop is about 1.1 km or 2.2 km: 15 or 30 minutes on foot.
    stops = optimize_schedule([place(i, 48.86 + i * 0.01) for i in range(3)], days=1).days[0].stops
    assert stops[1].travel_mode == "walk_estimate"
    assert stops[1].start == f"10:{30 + math.ceil(stops[1].travel_minutes):02d}"


def test_hop_too_far_to_walk_is_labelled_taxi_estimate():
    # Each hop is at least 4.4 km.
    stops = optimize_schedule([place(i, 48.86 + i * 0.04) for i in range(3)], days=1).days[0].stops
    assert stops[1].travel_mode == "taxi_estimate"


def test_user_walking_limit_changes_the_label():
    places = [place(i, 48.86 + i * 0.01) for i in range(3)]
    stops = optimize_schedule(places, days=1, max_walking_minutes=5).days[0].stops
    assert stops[1].travel_mode == "taxi_estimate"


def test_places_too_far_apart_never_share_a_day():
    # Over 100 km apart, which is more than 60 minutes by taxi.
    result = optimize_schedule([place(0, 48.86), place(1, 50.0)], days=1)
    assert len([s for s in result.days[0].stops if s.place_id]) == 1
    assert result.status == "partial"


def test_no_restaurants_gives_labelled_meal_breaks():
    places = city_places(restaurants=0)
    result = optimize_schedule(places, days=1)
    meals = [s for s in result.days[0].stops if s.slot in ("lunch", "dinner")]
    assert [m.name for m in meals] == [MEAL_BREAK_NAME] * 2
    assert all(m.place_id is None and m.latitude is None for m in meals)
    assert any("no verified restaurant" in n.lower() for n in result.notes)
    assert check_plan(result.days, places, expected_days=1).passed


def test_too_few_places_leaves_slots_empty_and_invents_nothing():
    places = city_places(attractions=2, restaurants=1)
    result = optimize_schedule(places, days=2)
    assert result.status == "partial"
    real = {p.name for p in places}
    assert all(n in real or n == MEAL_BREAK_NAME for n in names(result))


def test_place_without_coordinates_is_skipped_with_a_note():
    missing = Place(place_id="wikipedia:9", name="No Coords", kind="attraction", source="wikipedia")
    result = optimize_schedule([missing, place(1)], days=1)
    assert "No Coords" not in names(result)
    assert any("No Coords" in n for n in result.notes)


def test_no_usable_places_fails_clearly():
    assert optimize_schedule([], days=1).status == "failed"


def test_invalid_input_fails_clearly():
    assert optimize_schedule(city_places(), days=0).status == "failed"


def test_notes_say_estimates_and_hours_are_unchecked():
    assert any("Opening hours were not checked" in n for n in optimize_schedule(city_places(), days=1).notes)
