"""Offline tests of the evaluation's measurements, so a bug can't make the results look better than they are."""

from city_planner.agents.discovery import ChosenPlace, DiscoveryOutput
from city_planner.agents.planner import PlannerOutput
from city_planner.schemas import Day, Hop, Place, PublicTransportOption, ScheduleResult, Stop, TaxiBackup, TripRequest, WalkOption
from evaluation.run_eval import change_metrics, plan_metrics

A = Place(place_id="wikipedia:1", name="A", kind="attraction", source="wikipedia", latitude=48.86, longitude=2.34, page_views=10)
B = Place(place_id="wikipedia:2", name="B", kind="attraction", source="wikipedia", latitude=48.87, longitude=2.34, page_views=99)
C = Place(place_id="foursquare:3", name="C", kind="restaurant", source="foursquare", latitude=48.86, longitude=2.35)
DISCOVERY = DiscoveryOutput(city="Paris", attractions=[ChosenPlace(place=p, reason="") for p in (A, B)], restaurants=[ChosenPlace(place=C, reason="")], notes="", rejected=[], unavailable=[])
BUS = Hop(public_transport=PublicTransportOption(minutes=20, transfers=0, lines=[]))


def stop(place, start, slot="activity", hop=None):
    return Stop(place_id=place.place_id, name=place.name, slot=slot, start=start, duration_minutes=60, latitude=place.latitude, longitude=place.longitude, hop=hop)


def state(stops, days=1):
    plan = PlannerOutput(schedule=ScheduleResult(status="ok", days=[Day(day_number=1, stops=stops)]), explanation="", schedule_id="s", choices="")
    return {"final_plan": plan, "discovery": DISCOVERY, "trip": TripRequest(city="Paris", days=days), "trace": []}


def test_good_plan_has_no_problems_counted():
    metrics = plan_metrics(state([stop(A, "09:00"), stop(C, "12:00", "lunch", BUS), stop(B, "14:00", hop=BUS), stop(C, "19:00", "dinner")]))
    assert metrics["invented_stops"] == 0 and metrics["visits_after_dinner"] == 0
    assert metrics["hops"]["public_transport"] == 2
    assert len(metrics["estimate_minus_real_minutes"]) == 2


def test_invented_stop_is_counted():
    fake = Stop(place_id="wikipedia:999", name="Fake", slot="activity", start="09:00", duration_minutes=60)
    metrics = plan_metrics(state([fake]))
    assert metrics["invented_stops"] == 1 and not metrics["check_passed"]


def test_visit_after_dinner_and_missing_meal_are_counted():
    metrics = plan_metrics(state([stop(A, "09:00"), stop(C, "19:00", "dinner"), stop(B, "21:00", hop=BUS)]))
    assert metrics["visits_after_dinner"] == 1 and metrics["meals_missing"] == 1


def test_taxi_next_to_another_option_counts_as_a_rule_break():
    bad = Hop.model_construct(walk=WalkOption(minutes=5, is_estimate=False), taxi_backup=TaxiBackup(minutes=5, reason="x"), public_transport=None, check_failed=False)
    metrics = plan_metrics(state([stop(A, "09:00"), Stop.model_construct(**{**stop(B, "11:00").model_dump(), "hop": bad})]))
    assert metrics["taxi_rule_breaks"] == 1


def test_estimate_is_compared_with_the_fastest_real_option_only():
    estimated_walk = Hop(walk=WalkOption(minutes=5, is_estimate=True))
    metrics = plan_metrics(state([stop(A, "09:00"), stop(B, "11:00", hop=estimated_walk)]))
    assert metrics["estimate_minus_real_minutes"] == []


def test_no_plan_is_reported_as_no_plan():
    assert plan_metrics({"discovery": DISCOVERY}) == {"has_plan": False}


def test_change_must_keep_every_other_stop_exactly():
    before = state([stop(A, "09:00"), stop(B, "11:00", hop=BUS)])
    same = state([stop(A, "09:00"), stop(C, "12:00", "lunch", BUS)])
    moved = state([stop(A, "10:00"), stop(C, "12:00", "lunch", BUS)])
    assert change_metrics(before, same, B.place_id) | {"approved": False} == {"has_plan": True, "removed": True, "others_unchanged": True, "added": [C.place_id], "approved": False}
    assert change_metrics(before, moved, B.place_id)["others_unchanged"] is False
    assert change_metrics(before, before, B.place_id)["removed"] is False
