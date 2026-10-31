"""Tests of check_plan. No network is used."""

from city_planner.schemas import MEAL_BREAK_NAME, Day, Place, Stop
from city_planner.tools.check_plan import check_plan

MUSEUM = Place(place_id="wikipedia:1", name="Louvre", kind="attraction", source="wikipedia", latitude=48.86, longitude=2.33)
TOWER = Place(place_id="wikipedia:2", name="Eiffel Tower", kind="attraction", source="wikipedia", latitude=48.85, longitude=2.29)
CAFE = Place(place_id="foursquare:3", name="Cafe", kind="restaurant", source="foursquare", latitude=48.86, longitude=2.34)
KNOWN = [MUSEUM, TOWER, CAFE]


def stop(place=MUSEUM, start="09:00", slot="activity", **overrides):
    fields = {
        "place_id": place.place_id,
        "name": place.name,
        "slot": slot,
        "start": start,
        "duration_minutes": 90,
        "latitude": place.latitude,
        "longitude": place.longitude,
    }
    return Stop(**{**fields, **overrides})


def later(place=TOWER, start="11:00", **overrides):
    return stop(place, start, travel_minutes=20, travel_mode="transit", **overrides)


def check(*stops, expected_days=1, **kwargs):
    return check_plan([Day(day_number=1, stops=list(stops))], KNOWN, expected_days, **kwargs)


def codes(result):
    return [issue.code for issue in result.issues]


def test_good_plan_passes_with_no_issues():
    result = check(stop(), later())
    assert result.passed and result.issues == []


def test_invented_place_is_rejected():
    result = check(stop(place_id="wikipedia:999", name="Museum of Invisible Spoons"))
    assert not result.passed and "unknown_place" in codes(result)


def test_changed_name_or_coordinates_are_rejected():
    assert "unknown_place" in codes(check(stop(name="Renamed Louvre")))
    assert "unknown_place" in codes(check(stop(latitude=1.0)))


def test_invented_stop_cannot_pose_as_a_meal_break():
    fake = Stop(place_id=None, name="Fake Bistro", slot="lunch", start="13:00", duration_minutes=60)
    assert "unknown_place" in codes(check(stop(), fake))


def test_activity_cannot_be_a_meal_break():
    fake = Stop(place_id=None, name=MEAL_BREAK_NAME, slot="activity", start="13:00", duration_minutes=60)
    assert "unknown_place" in codes(check(stop(), fake))


def test_labelled_meal_break_is_allowed_with_a_warning():
    meal = Stop(place_id=None, name=MEAL_BREAK_NAME, slot="lunch", start="13:00", duration_minutes=60)
    result = check(stop(), meal)
    assert result.passed and "meal_break" in codes(result)


def test_restaurant_in_activity_slot_is_rejected():
    assert "unknown_place" in codes(check(stop(CAFE)))


def test_duplicate_across_days_is_rejected():
    days = [Day(day_number=1, stops=[stop()]), Day(day_number=2, stops=[stop()])]
    assert "duplicate_place" in codes(check_plan(days, KNOWN, 2))


def test_overlap_counts_travel_time():
    # The first stop ends at 10:30, and 20 minutes of travel means 10:50 at the earliest.
    assert "time_overlap" in codes(check(stop(), later(start="10:40")))
    assert check(stop(), later(start="10:50")).passed


def test_wrong_day_count_is_rejected():
    assert "wrong_days" in codes(check(stop(), expected_days=2))


def test_empty_day_is_rejected():
    assert "empty_day" in codes(check())


def test_day_of_only_meal_breaks_is_rejected():
    meal = Stop(place_id=None, name=MEAL_BREAK_NAME, slot="lunch", start="13:00", duration_minutes=60)
    assert "empty_day" in codes(check(meal))


def test_stop_ending_after_midnight_is_rejected():
    assert "ends_after_midnight" in codes(check(stop(start="23:30")))


def test_long_walk_is_rejected_only_with_a_limit():
    walk = stop(TOWER, "11:00", travel_minutes=45, travel_mode="walk_estimate")
    assert "walk_too_long" in codes(check(stop(), walk, max_walking_minutes=30))
    assert "walk_too_long" not in codes(check(stop(), walk))


def test_estimates_and_missing_travel_are_warnings():
    estimated = check(stop(), stop(TOWER, "11:00", travel_minutes=10, travel_mode="taxi_estimate"))
    assert estimated.passed and "estimated_travel" in codes(estimated)
    unknown = check(stop(), stop(TOWER, "11:00"))
    assert unknown.passed and "missing_travel" in codes(unknown)
