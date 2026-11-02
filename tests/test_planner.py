"""Offline tests of the Planner: scripted LLM decisions, with the real optimizer and no network."""

from functools import partial

from city_planner.agents.base import CallBudget, run_agent
from city_planner.agents.discovery import ChosenPlace, DiscoveryOutput, build_discovery_agent
from city_planner.agents.planner import MAX_OPTIMIZE_CALLS, build_planner_agent, planner_task
from city_planner.graph import discovery_task, plan_trip
from city_planner.schemas import Day, Hop, Place, RestaurantResult, ScheduleResult, Stop, TaxiBackup, TripRequest, VerifyResult, WalkOption
from city_planner.tools.check_plan import check_plan
from city_planner.tools.optimize_schedule import optimize_schedule
from tests.fakes import FakeLLM, call


def attraction(number, lat):
    return Place(place_id=f"wikipedia:{number}", name=f"Museum {number}", kind="attraction", source="wikipedia", latitude=lat, longitude=2.34, page_views=1000)


def restaurant(number, lat):
    return Place(place_id=f"foursquare:{number}", name=f"Cafe {number}", kind="restaurant", source="foursquare", latitude=lat, longitude=2.34)


ATTRACTIONS = [attraction(i, 48.86 + i * 0.001) for i in range(4)]
RESTAURANTS = [restaurant(i, 48.86 + i * 0.001) for i in range(2)]
TRIP = TripRequest(city="Paris", days=1)
DISCOVERY = DiscoveryOutput(
    city="Paris",
    attractions=[ChosenPlace(place=p, reason="Fits.") for p in ATTRACTIONS],
    restaurants=[ChosenPlace(place=p, reason="Fits.") for p in RESTAURANTS],
    notes="",
    rejected=[],
    unavailable=[],
)


def walk(from_lat, from_lon, to_lat, to_lon, departure_local=None, max_walk_minutes=20):
    return Hop(walk=WalkOption(minutes=5, is_estimate=False))


fast_optimize = partial(optimize_schedule, travel=walk, solver_seconds=2)


def optimize_call(pace=2, **kwargs):
    return call("optimize_schedule", max_activities_per_day=pace, **kwargs)


def finish(schedule_id="schedule_1"):
    return call("finish", schedule_id=schedule_id, explanation="Good pace.")


def run(script, trip=TRIP, optimize=fast_optimize, route=walk):
    llm = FakeLLM(script)
    agent = build_planner_agent(trip, DISCOVERY, optimize=optimize, route=route)
    return run_agent(agent, planner_task(trip, DISCOVERY), llm, CallBudget()), llm


def tool_result(llm, request=-1):
    return llm.requests[request]["messages"][-1]["results"][0][1]


def test_planner_has_only_its_own_tools():
    assert [t.name for t in build_planner_agent(TRIP, DISCOVERY).tools] == ["optimize_schedule", "get_route"]


def test_final_schedule_is_exactly_what_the_tool_produced():
    produced = []

    def spy(*args, **kwargs):
        produced.append(fast_optimize(*args, **kwargs))
        return produced[-1]

    result, _ = run([[optimize_call()], [finish()]], optimize=spy)
    assert result.output.schedule == produced[0]
    assert check_plan(result.output.schedule.days, ATTRACTIONS + RESTAURANTS, 1).passed


def test_invented_schedule_id_is_rejected():
    result, llm = run([[optimize_call()], [finish("schedule_99")], [finish()]])
    assert result.trace[1].kind == "rejected"
    assert "not returned by optimize_schedule" in str(llm.requests[2]["messages"][-1])
    assert result.stop_reason == "finished"


def test_finish_before_any_schedule_is_rejected():
    result, _ = run([[finish()], [optimize_call()], [finish()]])
    assert result.trace[0].kind == "rejected"


def test_failed_schedule_cannot_be_final():
    failed = lambda *args, **kwargs: ScheduleResult(status="failed", notes=["No."])
    result, llm = run([[optimize_call()], [finish()], [finish()], [finish()], [finish()]], optimize=failed)
    assert "can't be the final" in str(llm.requests[2]["messages"][-1])
    assert result.output is None and result.stop_reason == "step_limit"


def test_llm_choices_reach_the_optimizer():
    seen = {}

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return fast_optimize(*args, **kwargs)

    args = dict(
        priorities=[{"place_id": "wikipedia:1", "value": 0.8}],
        visit_minutes=[{"place_id": "wikipedia:2", "minutes": 150}],
        excluded=["wikipedia:3"],
        required=["wikipedia:0"],
    )
    run([[optimize_call(pace=3, **args)], [finish()]], optimize=spy)
    assert seen["max_activities_per_day"] == 3
    assert seen["priorities"] == {"wikipedia:1": 0.8}
    assert seen["visit_minutes"] == {"wikipedia:2": 150}
    assert seen["excluded"] == ["wikipedia:3"] and seen["required"] == ["wikipedia:0"]


def test_traveler_pace_beats_the_llm_and_walking_limit_is_passed():
    seen = {}

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return fast_optimize(*args, **kwargs)

    trip = TripRequest(city="Paris", days=1, max_activities_per_day=1, max_walk_minutes=10)
    run([[optimize_call(pace=5)], [finish()]], trip=trip, optimize=spy)
    assert seen["max_activities_per_day"] == 1 and seen["max_walk_minutes"] == 10


def test_invented_place_id_is_refused_before_the_solver_runs():
    calls = []
    spy = lambda *a, **k: calls.append(1)
    llm = FakeLLM([[optimize_call(excluded=["wikipedia:999"])], [finish()], [finish()], [finish()], [finish()]])
    run_agent(build_planner_agent(TRIP, DISCOVERY, optimize=spy, route=walk), "task", llm, CallBudget())
    assert calls == []
    assert "not in your list" in tool_result(llm, 1)["error"]


def test_out_of_range_choice_is_refused():
    _, llm = run([[optimize_call(pace=50)], [optimize_call()], [finish()]])
    assert "Invalid arguments" in tool_result(llm, 1)["error"]


def test_schedule_tries_are_limited():
    script = [[optimize_call()] for _ in range(MAX_OPTIMIZE_CALLS + 1)] + [[finish()]]
    result, llm = run(script)
    assert "Limit of" in str(llm.requests[-1]["messages"][-1])
    assert result.stop_reason == "finished"


def test_llm_can_choose_between_two_schedules():
    result, _ = run([[optimize_call(pace=1)], [optimize_call(pace=3)], [finish("schedule_2")]])
    assert result.output.schedule_id == "schedule_2"
    assert len([s for s in result.output.schedule.days[0].stops if s.slot == "activity"]) == 3


def test_route_check_uses_coordinates_from_tools_not_the_llm():
    seen = []

    def spy(from_lat, from_lon, to_lat, to_lon, departure_local=None, max_walk_minutes=20):
        seen.append((from_lat, to_lat))
        return walk(from_lat, from_lon, to_lat, to_lon)

    _, llm = run([[call("get_route", from_place_id="wikipedia:0", to_place_id="wikipedia:1")], [optimize_call()], [finish()]], route=spy)
    assert seen == [(ATTRACTIONS[0].latitude, ATTRACTIONS[1].latitude)]
    assert tool_result(llm, 1)["travel"] == "walk, 5.0 min"


def test_route_check_refuses_an_unknown_place():
    _, llm = run([[call("get_route", from_place_id="wikipedia:0", to_place_id="wikipedia:999")], [optimize_call()], [finish()]])
    assert "not in your list" in tool_result(llm, 1)["error"]


def test_task_lists_places_and_preferences():
    trip = TripRequest(city="Paris", days=2, preferences="modern art", max_activities_per_day=2)
    task = planner_task(trip, DISCOVERY)
    assert "wikipedia:0" in task and "foursquare:1" in task and "modern art" in task and "at most 2" in task


def test_discovery_task_states_how_many_places_are_needed():
    task = discovery_task(TripRequest(city="Tunis", days=3, preferences="history"))
    assert "14 attractions" in task and "about 9 restaurants" in task and "history" in task and "Tunis" in task


def test_taxi_next_to_another_option_fails_check_plan():
    # A plan can only get here by skipping the rules on Hop.
    bad = Hop.model_construct(walk=WalkOption(minutes=5, is_estimate=False), taxi_backup=TaxiBackup(minutes=5, reason="x"), public_transport=None, check_failed=False)
    day = Day(day_number=1, stops=[Stop.model_construct(place_id=ATTRACTIONS[0].place_id, name=ATTRACTIONS[0].name, slot="activity", start="09:00", duration_minutes=90, latitude=ATTRACTIONS[0].latitude, longitude=ATTRACTIONS[0].longitude, hop=bad)])
    result = check_plan([day], ATTRACTIONS, 1)
    assert not result.passed and "taxi_not_backup" in [i.code for i in result.issues]


# The whole graph.


def graph_setup(monkeypatch):
    def fake_verify(name, city):
        return VerifyResult(status="verified", place=ATTRACTIONS[int(name)], reason="Matched.")

    def fake_restaurants(latitude, longitude, food=""):
        return RestaurantResult(status="ok", restaurants=RESTAURANTS)

    monkeypatch.setattr("city_planner.graph.build_discovery_agent", lambda: build_discovery_agent(fake_verify, fake_restaurants))
    monkeypatch.setattr(
        "city_planner.graph.build_planner_agent",
        lambda trip, discovery: build_planner_agent(trip, discovery, optimize=fast_optimize, route=walk),
    )


def discovery_script(restaurant_ids=("foursquare:0", "foursquare:1")):
    pick = lambda place_id: {"place_id": place_id, "reason": "Fits."}
    return [
        [call("verify_place", name=str(i), city="Paris") for i in range(4)],
        [call("find_restaurants", near_place_id="wikipedia:0")],
        [call("finish", city="Paris", attractions=[pick(p.place_id) for p in ATTRACTIONS], restaurants=[pick(r) for r in restaurant_ids])],
    ]


def test_graph_runs_discovery_then_planner_and_shares_the_call_budget(monkeypatch):
    graph_setup(monkeypatch)
    state = plan_trip(TRIP, FakeLLM(discovery_script() + [[optimize_call()], [finish()]]))
    assert state["error"] is None
    assert state["planner"].schedule.status == "ok"
    assert state["llm_calls"] == 5
    assert {e.agent for e in state["trace"]} == {"Discovery", "Planner"}


def test_every_stop_traces_to_a_tool_result(monkeypatch):
    graph_setup(monkeypatch)
    state = plan_trip(TRIP, FakeLLM(discovery_script() + [[optimize_call()], [finish()]]))
    known = {c.place.place_id for c in state["discovery"].attractions + state["discovery"].restaurants}
    assert all(s.place_id in known for d in state["planner"].schedule.days for s in d.stops if s.place_id)


def test_graph_skips_the_planner_with_no_verified_attractions(monkeypatch):
    graph_setup(monkeypatch)
    llm = FakeLLM([[call("finish", city="Paris", attractions=[], notes="Nothing found.")]])
    state = plan_trip(TRIP, llm)
    assert state.get("planner") is None and state["llm_calls"] == 1


def test_planner_failure_is_reported_and_keeps_discovery(monkeypatch):
    graph_setup(monkeypatch)
    from city_planner.llm import LLMError

    state = plan_trip(TRIP, FakeLLM(discovery_script() + [LLMError("HTTP 429: quota")]))
    assert state["planner"] is None and "429" in state["error"]
    assert state["discovery"].attractions
