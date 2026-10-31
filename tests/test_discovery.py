"""Offline tests of Discovery: scripted LLM decisions, with fake tool data."""

from city_planner.agents.base import CallBudget, run_agent
from city_planner.agents.discovery import MAX_RESTAURANT_CALLS, build_discovery_agent
from city_planner.graph import plan_trip
from city_planner.llm import LLMError
from city_planner.schemas import Place, RestaurantResult, VerifyResult
from tests.fakes import FakeLLM, call

TILE = Place(place_id="wikipedia:1", name="National Tile Museum", kind="attraction", source="wikipedia", latitude=38.7, longitude=-9.1, page_views=500)
NO_COORDS = Place(place_id="wikipedia:2", name="No Coords Museum", kind="attraction", source="wikipedia", missing=["coordinates"])
CAFE = Place(place_id="foursquare:9", name="Cafe A", kind="restaurant", source="foursquare", latitude=38.7, longitude=-9.1, description="Café")


def fake_verify(name, city):
    if name == "Spoon Museum":
        return VerifyResult(status="rejected", reason="No article.")
    if name == "Offline Museum":
        return VerifyResult(status="unavailable", reason="Wikipedia could not be reached.")
    if name == "No Coords Museum":
        return VerifyResult(status="verified", place=NO_COORDS, reason="Matched.")
    return VerifyResult(status="verified", place=TILE, reason="Matched.")


def fake_restaurants(latitude, longitude, food=""):
    return RestaurantResult(status="ok", restaurants=[CAFE])


def pick(place_id):
    return {"place_id": place_id, "reason": "Fits."}


def verify(name="National Tile Museum", city="Lisbon"):
    return call("verify_place", name=name, city=city)


def finish(attractions=("wikipedia:1",), restaurants=(), city="Lisbon"):
    return call("finish", city=city, attractions=[pick(p) for p in attractions], restaurants=[pick(p) for p in restaurants])


def run(script):
    llm = FakeLLM(script)
    agent = build_discovery_agent(verify=fake_verify, restaurants=fake_restaurants)
    return run_agent(agent, "Popular museums in Lisbon", llm, CallBudget()), llm


def last_result(llm):
    return llm.requests[-1]["messages"][-1]["results"][0][1]


def test_discovery_has_only_its_own_tools():
    assert [t.name for t in build_discovery_agent().tools] == ["verify_place", "find_restaurants"]


def test_output_contains_the_real_tool_data():
    result, _ = run([[verify()], [finish()]])
    place = result.output.attractions[0].place
    assert place == TILE
    assert result.output.attractions[0].reason == "Fits."


def test_invented_place_is_rejected_by_code():
    result, llm = run([[verify()], [finish(attractions=["wikipedia:999"])], [finish()]])
    assert result.trace[1].kind == "rejected"
    assert "not returned by any tool" in str(llm.requests[2]["messages"][-1])
    assert [c.place.place_id for c in result.output.attractions] == ["wikipedia:1"]


def test_place_proposed_but_never_verified_is_rejected():
    result, _ = run([[finish()], [verify()], [finish()]])
    assert result.trace[0].kind == "rejected"


def test_llm_decides_what_to_do_after_a_rejection():
    result, llm = run([[verify("Spoon Museum")], [verify()], [finish()]])
    assert llm.requests[1]["messages"][-1]["results"][0][1]["status"] == "rejected"
    assert result.output.rejected == ["Spoon Museum (Lisbon)"]


def test_rejected_and_unavailable_are_recorded_by_code():
    result, _ = run([[verify("Spoon Museum"), verify("Offline Museum"), verify()], [finish()]])
    assert result.output.rejected == ["Spoon Museum (Lisbon)"]
    assert result.output.unavailable == ["Offline Museum (Lisbon)"]


def test_place_from_another_city_is_rejected():
    result, llm = run([[verify(city="Paris")], [finish(city="Lisbon")], [verify()], [finish()]])
    assert "was found for Paris" in str(llm.requests[2]["messages"][-1])
    assert result.stop_reason == "finished"


def test_restaurant_search_needs_a_verified_attraction():
    _, llm = run([[call("find_restaurants", near_place_id="wikipedia:1")], [finish(attractions=[])]])
    assert "must be the ID" in llm.requests[1]["messages"][-1]["results"][0][1]["error"]


def test_restaurant_search_uses_tool_coordinates_and_returns_categories():
    seen = []

    def spy(latitude, longitude, food=""):
        seen.append((latitude, longitude, food))
        return fake_restaurants(latitude, longitude)

    llm = FakeLLM([[verify()], [call("find_restaurants", near_place_id="wikipedia:1", food="vegan")], [finish(restaurants=["foursquare:9"])]])
    agent = build_discovery_agent(verify=fake_verify, restaurants=spy)
    result = run_agent(agent, "task", llm, CallBudget())
    assert seen == [(38.7, -9.1, "vegan")]
    assert llm.requests[2]["messages"][-1]["results"][0][1]["restaurants"][0]["categories"] == "Café"
    assert result.output.restaurants[0].place == CAFE


def test_place_without_coordinates_cannot_be_a_search_point():
    _, llm = run([[verify("No Coords Museum")], [call("find_restaurants", near_place_id="wikipedia:2")], [finish(attractions=["wikipedia:2"])]])
    assert "no coordinates" in str(llm.requests[2]["messages"][-1])


def test_restaurant_cannot_be_listed_as_attraction():
    script = [[verify()], [call("find_restaurants", near_place_id="wikipedia:1")], [finish(attractions=["foursquare:9"])], [finish()]]
    result, llm = run(script)
    assert "wrong list" in str(llm.requests[3]["messages"][-1])
    assert result.stop_reason == "finished"


def test_restaurant_searches_are_limited():
    search = call("find_restaurants", near_place_id="wikipedia:1")
    script = [[verify()]] + [[search] for _ in range(MAX_RESTAURANT_CALLS + 1)] + [[finish()]]
    _, llm = run(script)
    assert "Limit of" in str(llm.requests[-1]["messages"][-1])


def test_each_trip_gets_its_own_record_of_places():
    run([[verify()], [finish()]])
    result, _ = run([[finish()], [verify()], [finish()]])
    assert result.trace[0].kind == "rejected"


def test_graph_runs_discovery_and_ends(monkeypatch):
    monkeypatch.setattr("city_planner.graph.build_discovery_agent", lambda: build_discovery_agent(fake_verify, fake_restaurants))
    state = plan_trip("Popular museums in Lisbon", FakeLLM([[verify()], [finish()]]))
    assert state["discovery"].attractions[0].place == TILE
    assert state["llm_calls"] == 2
    assert state["error"] is None
    assert [e.kind for e in state["trace"]] == ["tool", "finish"]


def test_graph_reports_failure_and_invents_nothing(monkeypatch):
    monkeypatch.setattr("city_planner.graph.build_discovery_agent", lambda: build_discovery_agent(fake_verify, fake_restaurants))
    state = plan_trip("Popular museums in Lisbon", FakeLLM([LLMError("HTTP 429: quota")]))
    assert state["discovery"] is None
    assert "429" in state["error"]
