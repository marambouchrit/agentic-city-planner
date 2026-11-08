"""Offline tests of the Reviewer and the send-back loop: scripted LLM decisions, with no network."""

from functools import partial

from city_planner.agents.base import CallBudget, run_agent
from city_planner.agents.discovery import ChosenPlace, DiscoveryOutput, build_discovery_agent
from city_planner.agents.planner import build_planner_agent
from city_planner.agents.reviewer import BACKUP_PRICE_PER_KM, MEAL_BREAK_PRICE, PUBLIC_TRANSPORT_FARE, build_reviewer_agent, price_lines, reviewer_task
from city_planner.graph import plan_trip
from city_planner.llm import LLMError
from city_planner.schemas import Day, Hop, Place, PublicTransportOption, RestaurantResult, ScheduleResult, Stop, TaxiBackup, TripRequest, VerifyResult, WalkOption
from city_planner.tools.optimize_schedule import optimize_schedule
from tests.fakes import FakeLLM, call

MUSEUM = Place(place_id="wikipedia:1", name="Museum", kind="attraction", source="wikipedia", latitude=48.86, longitude=2.34, page_views=1000)
TOWER = Place(place_id="wikipedia:2", name="Tower", kind="attraction", source="wikipedia", latitude=48.861, longitude=2.34, page_views=900)
CAFE = Place(place_id="foursquare:3", name="Cafe", kind="restaurant", source="foursquare", latitude=48.86, longitude=2.34)
BISTRO = Place(place_id="foursquare:4", name="Bistro", kind="restaurant", source="foursquare", latitude=48.861, longitude=2.34)
PLACES = [MUSEUM, TOWER, CAFE, BISTRO]
TRIP = TripRequest(city="Paris", days=1, budget=100)


def stop(place, start, slot="activity", hop=None):
    return Stop(place_id=place.place_id, name=place.name, slot=slot, start=start, duration_minutes=60, latitude=place.latitude, longitude=place.longitude, hop=hop)


BUS = Hop(public_transport=PublicTransportOption(minutes=10, transfers=0, lines=[]), walk=WalkOption(minutes=12, is_estimate=False), distance_km=1)
SCHEDULE = ScheduleResult(status="ok", days=[Day(day_number=1, stops=[stop(MUSEUM, "09:00"), stop(CAFE, "12:00", "lunch", BUS), stop(TOWER, "14:00", hop=BUS)])])
BAD_SCHEDULE = ScheduleResult(status="ok", days=[Day(day_number=1, stops=[stop(MUSEUM, "09:00"), stop(MUSEUM, "12:00")])])

CHEAP = [
    {"item_id": "entry:wikipedia:1", "amount": 10},
    {"item_id": "entry:wikipedia:2", "amount": 10},
    {"item_id": "meal:foursquare:3", "amount": 20},
    {"item_id": PUBLIC_TRANSPORT_FARE, "amount": 2},
]
PRICEY = [{**CHEAP[0], "amount": 90}, *CHEAP[1:]]


def budget_call(prices=CHEAP):
    return call("calculate_budget", prices=prices)


def verdict(kind="approve", **kwargs):
    return call("finish", verdict=kind, summary="Fine.", **kwargs)


def review(script, schedule=SCHEDULE, trip=TRIP, known=None):
    llm = FakeLLM(script)
    agent = build_reviewer_agent(trip, schedule, PLACES, known or {})
    return run_agent(agent, "task", llm, CallBudget()), llm


def result(llm, request=-1, index=0):
    return llm.requests[request]["messages"][-1]["results"][index][1]


def test_reviewer_has_only_its_own_tools():
    assert [t.name for t in build_reviewer_agent(TRIP, SCHEDULE, PLACES, {}).tools] == ["calculate_budget", "check_plan"]


def test_code_lists_every_cost_and_counts_a_ride_even_when_a_walk_is_offered():
    lines = {line.item_id: line for line in price_lines(SCHEDULE)}
    assert set(lines) == {"entry:wikipedia:1", "entry:wikipedia:2", "meal:foursquare:3", PUBLIC_TRANSPORT_FARE}
    assert lines[PUBLIC_TRANSPORT_FARE].quantity == 2


def test_walk_alone_costs_nothing_and_taxi_counts_only_for_backup_hops():
    walk = Hop(walk=WalkOption(minutes=5, is_estimate=False), distance_km=0.3)
    taxi = Hop(taxi_backup=TaxiBackup(minutes=9, reason="None."), distance_km=4)
    schedule = ScheduleResult(status="ok", days=[Day(day_number=1, stops=[stop(MUSEUM, "09:00"), stop(TOWER, "11:00", hop=walk), stop(CAFE, "13:00", "lunch", taxi)])])
    lines = {line.item_id: line for line in price_lines(schedule)}
    assert PUBLIC_TRANSPORT_FARE not in lines
    assert lines[BACKUP_PRICE_PER_KM].quantity == 4


def test_all_meal_breaks_share_one_price():
    lunch = Stop(place_id=None, name="Meal break (no verified restaurant)", slot="lunch", start="12:00", duration_minutes=60)
    dinner = lunch.model_copy(update={"slot": "dinner", "start": "19:00"})
    schedule = ScheduleResult(status="ok", days=[Day(day_number=1, stops=[stop(MUSEUM, "09:00"), lunch, dinner])])
    lines = {line.item_id: line for line in price_lines(schedule)}
    assert lines[MEAL_BREAK_PRICE].quantity == 2


def test_code_does_the_math():
    _, llm = review([[budget_call()], [verdict()]])
    assert result(llm)["total"] == 44
    assert result(llm)["over_budget"] is False
    assert result(llm)["includes_estimates"] is True


def test_approval_gives_the_code_written_results():
    run, _ = review([[budget_call(), call("check_plan")], [verdict()]])
    assert run.output.verdict == "approve"
    assert run.output.budget.total == 44 and run.output.check.passed


def test_approval_over_budget_is_refused():
    run, llm = review([[budget_call(PRICEY)], [verdict()], [verdict("send_back", instructions="Drop the museum.")]])
    assert "over budget" in str(llm.requests[2]["messages"][-1])
    assert run.output.verdict == "send_back"


def test_approval_with_rule_errors_is_refused():
    prices = [{"item_id": "entry:wikipedia:1", "amount": 10}]
    run, llm = review([[budget_call(prices)], [verdict()], [verdict("send_back", instructions="Remove the repeat.")]], schedule=BAD_SCHEDULE)
    assert "check_plan has errors" in str(llm.requests[2]["messages"][-1])
    assert run.output.verdict == "send_back"


def test_finish_without_budget_is_refused():
    _, llm = review([[verdict()], [budget_call()], [verdict()]])
    assert "calculate_budget before" in str(llm.requests[1]["messages"][-1])


def test_send_back_needs_instructions():
    _, llm = review([[budget_call()], [verdict("send_back")], [verdict()]])
    assert "specific instructions" in str(llm.requests[2]["messages"][-1])


def test_missing_price_stays_unknown_and_means_no_clear_answer():
    run, _ = review([[budget_call(CHEAP[:3])], [verdict()]])
    assert run.output.budget.over_budget is None
    assert run.output.budget.missing_costs == ["Public transport: 2 rides"]


def test_invented_item_is_refused():
    _, llm = review([[budget_call([{"item_id": "entry:wikipedia:999", "amount": 1}])], [budget_call()], [verdict()]])
    assert "not in the plan" in result(llm, 1)["error"]
    assert "entry:wikipedia:1" in result(llm, 1)["error"]


def test_task_shows_each_exact_item_id_and_fixed_prices():
    task = reviewer_task(TRIP, SCHEDULE, {"entry:wikipedia:1": 12})
    assert "item_id: entry:wikipedia:2" in task
    assert "fixed price 12 per visit. item_id: entry:wikipedia:1" in task


def test_known_price_cannot_be_lowered_in_a_later_round():
    run, _ = review([[budget_call(PRICEY)], [verdict("send_back", instructions="Too much.")]], known={"entry:wikipedia:1": 95})
    assert run.output.prices["entry:wikipedia:1"] == 95
    run, _ = review([[budget_call([{"item_id": "entry:wikipedia:1", "amount": 1}, *CHEAP[1:]])], [verdict("send_back", instructions="Too much.")]], known={"entry:wikipedia:1": 95})
    assert run.output.budget.over_budget is True


def test_price_in_another_currency_is_refused_before_it_is_fixed():
    yen = [{"item_id": "entry:wikipedia:1", "amount": 2000}, *CHEAP[1:]]
    run, llm = review([[budget_call(yen)], [budget_call()], [verdict()]])
    assert "another currency" in result(llm, 1)["error"]
    assert run.output.prices["entry:wikipedia:1"] == 10


def test_high_price_sent_again_after_the_warning_is_accepted():
    pricey = [{"item_id": "entry:wikipedia:1", "amount": 150}, *CHEAP[1:]]
    run, llm = review([[budget_call(pricey)], [budget_call(pricey)], [verdict("send_back", instructions="Too much.")]])
    assert "another currency" in result(llm, 1)["error"]
    assert run.output.prices["entry:wikipedia:1"] == 150 and run.output.budget.over_budget is True


def test_task_asks_for_the_trip_currency():
    assert "Give every price in EUR" in reviewer_task(TRIP, SCHEDULE, {})


def test_negative_price_is_refused():
    _, llm = review([[budget_call([{"item_id": "entry:wikipedia:1", "amount": -5}])], [budget_call()], [verdict()]])
    assert "Invalid arguments" in result(llm, 1)["error"]


# The whole graph. The real optimizer runs with no network.


def walk(from_lat, from_lon, to_lat, to_lon, departure_local=None, max_walk_minutes=20):
    return Hop(walk=WalkOption(minutes=5, is_estimate=False), distance_km=0.5)


fast_optimize = partial(optimize_schedule, travel=walk, solver_seconds=2)
DISCOVERY = DiscoveryOutput(
    city="Paris",
    attractions=[ChosenPlace(place=p, reason="Fits.") for p in (MUSEUM, TOWER)],
    restaurants=[ChosenPlace(place=p, reason="Fits.") for p in (CAFE, BISTRO)],
    notes="",
    rejected=[],
    unavailable=[],
)


def graph_setup(monkeypatch):
    fake_verify = lambda name, city: VerifyResult(status="verified", place=MUSEUM if name == "1" else TOWER, reason="Matched.")
    fake_restaurants = lambda latitude, longitude, food="": RestaurantResult(status="ok", restaurants=[CAFE, BISTRO])
    monkeypatch.setattr("city_planner.graph.build_discovery_agent", lambda: build_discovery_agent(fake_verify, fake_restaurants))
    monkeypatch.setattr(
        "city_planner.graph.build_planner_agent",
        lambda trip, discovery: build_planner_agent(trip, discovery, optimize=fast_optimize, route=walk),
    )


pick = lambda place_id: {"place_id": place_id, "reason": "Fits."}
DISCOVER = [
    [call("verify_place", name="1", city="Paris"), call("verify_place", name="2", city="Paris")],
    [call("find_restaurants", near_place_id="wikipedia:1")],
    [call("finish", city="Paris", attractions=[pick("wikipedia:1"), pick("wikipedia:2")], restaurants=[pick("foursquare:3"), pick("foursquare:4")])],
]


def planner_turn(schedule_id="schedule_1", **kwargs):
    return [[call("optimize_schedule", max_activities_per_day=2, **kwargs)], [call("finish", schedule_id=schedule_id, explanation="Good.")]]


ALL_PRICES = [
    {"item_id": "entry:wikipedia:1", "amount": 80},
    {"item_id": "entry:wikipedia:2", "amount": 10},
    {"item_id": "meal:foursquare:3", "amount": 20},
    {"item_id": "meal:foursquare:4", "amount": 20},
]


def reviewer_turn(kind="approve", prices=ALL_PRICES, **kwargs):
    if kind == "send_back":
        kwargs.setdefault("instructions", "Drop the museum, it costs the most.")
        kwargs.setdefault("issue_code", "over_budget")
    return [[call("calculate_budget", prices=prices)], [verdict(kind, **kwargs)]]


TRIP_100 = TripRequest(city="Paris", days=1, budget=100)


def test_over_budget_plan_is_revised_automatically_and_ends_within_budget(monkeypatch):
    graph_setup(monkeypatch)
    script = DISCOVER + planner_turn() + reviewer_turn("send_back") + planner_turn(excluded=["wikipedia:1"]) + reviewer_turn("approve", prices=[{"item_id": "entry:wikipedia:2", "amount": 10}])
    llm = FakeLLM(script)
    state = plan_trip(TRIP_100, llm)
    assert state["send_backs"] == 1
    assert state["final_review"].verdict == "approve"
    assert state["final_review"].budget.over_budget is False
    assert "Museum" not in [s.name for d in state["final_plan"].schedule.days for s in d.stops]


def test_planner_receives_specific_feedback_with_costs_from_code(monkeypatch):
    graph_setup(monkeypatch)
    script = DISCOVER + planner_turn() + reviewer_turn("send_back") + planner_turn(excluded=["wikipedia:1"]) + reviewer_turn("approve", prices=[{"item_id": "entry:wikipedia:2", "amount": 10}])
    llm = FakeLLM(script)
    plan_trip(TRIP_100, llm)
    task = next(r["messages"][0]["text"] for r in llm.requests if "feedback" not in r and "sent the last plan back" in r["messages"][0]["text"])
    assert "Drop the museum" in task and "30.0 EUR over budget" in task and "Entry: Museum: 80" in task


def test_send_backs_stop_at_two_and_problems_are_reported_not_hidden(monkeypatch):
    graph_setup(monkeypatch)
    turn = planner_turn() + reviewer_turn("send_back")
    llm = FakeLLM(DISCOVER + turn + turn + turn)
    state = plan_trip(TRIP_100, llm)
    assert state["send_backs"] == 2
    assert llm.script == []
    assert state["final_review"].verdict == "send_back"
    assert state["final_review"].budget.over_budget is True
    assert any("limit of 2" in note for note in state["notes"])


def test_a_worse_revision_does_not_replace_the_best_plan(monkeypatch):
    graph_setup(monkeypatch)
    # The revision has fewer places but still costs more, because its new meal is priced much higher.
    worse = [{"item_id": "entry:wikipedia:2", "amount": 10}, {"item_id": "meal:foursquare:3", "amount": 20}, {"item_id": "meal:foursquare:4", "amount": 99}]
    first = planner_turn() + reviewer_turn("send_back", prices=[*ALL_PRICES[:2], {"item_id": "meal:foursquare:3", "amount": 20}])
    revision = planner_turn(excluded=["wikipedia:1"]) + reviewer_turn("send_back", prices=worse)
    state = plan_trip(TRIP_100, FakeLLM(DISCOVER + first + revision + planner_turn() + reviewer_turn("send_back", prices=worse)))
    assert state["send_backs"] == 2
    assert state["review"].budget.total > 110
    assert state["final_review"].budget.total == 110
    assert "Museum" in [s.name for d in state["final_plan"].schedule.days for s in d.stops]


def test_send_back_to_discovery_runs_discovery_again(monkeypatch):
    graph_setup(monkeypatch)
    script = DISCOVER + planner_turn() + reviewer_turn("send_back", target="discovery", instructions="Find more museums.", issue_code="too_few") + DISCOVER + planner_turn() + reviewer_turn("approve", prices=[{"item_id": "entry:wikipedia:2", "amount": 10}, *ALL_PRICES[2:], {"item_id": "entry:wikipedia:1", "amount": 1}])
    llm = FakeLLM(script)
    state = plan_trip(TripRequest(city="Paris", days=1), llm)
    assert state["send_backs"] == 1
    assert sum(r["tools"][0] == "verify_place" for r in llm.requests) >= 2
    assert any("Find more museums" in r["messages"][0]["text"] for r in llm.requests if r["tools"][0] == "verify_place")


def test_no_new_round_when_too_few_llm_calls_are_left(monkeypatch):
    graph_setup(monkeypatch)
    monkeypatch.setattr("city_planner.graph.CallBudget", lambda: CallBudget(limit=10))
    llm = FakeLLM(DISCOVER + planner_turn() + reviewer_turn("send_back"))
    state = plan_trip(TRIP_100, llm)
    assert state.get("send_backs", 0) == 0
    assert any("LLM call limit" in note for note in state["notes"])
    assert state["final_plan"] is not None


def test_planner_failure_during_a_revision_keeps_the_best_plan(monkeypatch):
    graph_setup(monkeypatch)
    llm = FakeLLM(DISCOVER + planner_turn() + reviewer_turn("send_back") + [LLMError("HTTP 429: quota")])
    state = plan_trip(TRIP_100, llm)
    assert "429" in state["error"]
    assert state["final_plan"] is not None and state["final_review"].verdict == "send_back"


def test_reviewer_failure_leaves_the_plan_unreviewed_not_approved(monkeypatch):
    graph_setup(monkeypatch)
    state = plan_trip(TRIP_100, FakeLLM(DISCOVER + planner_turn() + [LLMError("HTTP 429: quota")]))
    assert state["planner"] is not None
    assert state.get("final_review") is None and "429" in state["error"]


def test_trip_with_no_budget_can_be_approved_and_still_shows_the_total(monkeypatch):
    graph_setup(monkeypatch)
    state = plan_trip(TripRequest(city="Paris", days=1), FakeLLM(DISCOVER + planner_turn() + reviewer_turn("approve")))
    assert state["final_review"].verdict == "approve"
    assert state["final_review"].budget.total == 130
    assert state["final_review"].budget.over_budget is None
