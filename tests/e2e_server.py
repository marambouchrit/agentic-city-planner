"""The real API with a scripted LLM and fake tools, for the browser tests. It uses no network or quota.

    python -m tests.e2e_server

The message decides what happens: "Tunis" has no public transport data, "cheap" stays over budget,
"crash" fails the first trip, and "Break" fails a change.
"""

import os
import re
import tempfile
import time
from functools import partial
from pathlib import Path

import uvicorn

from city_planner import graph
from city_planner.agents.discovery import build_discovery_agent
from city_planner.agents.planner import build_planner_agent
from city_planner.api import create_app
from city_planner.llm import LLMError, LLMResponse, ToolCall
from city_planner.schemas import Hop, Place, PublicTransportOption, RestaurantResult, TaxiBackup, TransitLine, VerifyResult, WalkOption
from city_planner.tools.distance import haversine_km
from city_planner.tools.optimize_schedule import optimize_schedule

STEP_SECONDS = 0.6  # slow enough for a test to see each progress message


def attraction(number, name, lat, lon):
    return Place(place_id=f"wikipedia:{number}", name=name, kind="attraction", source="wikipedia", latitude=lat, longitude=lon, page_views=100_000 - number)


def restaurant(number, name, lat, lon):
    return Place(place_id=f"foursquare:{number}", name=name, kind="restaurant", source="foursquare", latitude=lat, longitude=lon)


PLACES = {
    "Paris": [
        attraction(1, "Louvre", 48.8606, 2.3376),
        attraction(2, "Musee d'Orsay", 48.8600, 2.3266),
        attraction(3, "Sainte-Chapelle", 48.8554, 2.3450),
        attraction(4, "Pantheon", 48.8462, 2.3464),
        attraction(5, "Eiffel Tower", 48.8584, 2.2945),
    ],
    "Tunis": [
        attraction(11, "Bardo Museum", 36.8095, 10.1341),
        attraction(12, "Medina of Tunis", 36.7992, 10.1680),
        attraction(13, "Carthage", 36.8531, 10.3230),
        attraction(14, "Sidi Bou Said", 36.8687, 10.3414),
    ],
}
RESTAURANTS = {
    "Paris": [restaurant(i, f"Bistro {i}", 48.857 + i * 0.001, 2.33) for i in range(1, 5)],
    "Tunis": [restaurant(i, f"Cafe {i}", 36.80 + i * 0.015, 10.17 + i * 0.03) for i in range(11, 15)],
}
ALL_PLACES = [p for places in PLACES.values() for p in places]


def fake_verify(name, city):
    return VerifyResult(status="verified", place=next(p for p in ALL_PLACES if p.name == name), reason="Matched.")


def fake_restaurants(latitude, longitude, food=""):
    return RestaurantResult(status="ok", restaurants=RESTAURANTS["Paris" if latitude > 40 else "Tunis"])


def fake_route(from_lat, from_lon, to_lat, to_lon, departure_local=None, max_walk_minutes=20):
    km = haversine_km(from_lat, from_lon, to_lat, to_lon)
    walking = round(km * 17, 1)
    walk = WalkOption(minutes=walking, is_estimate=False) if walking <= max_walk_minutes else None
    if from_lat < 40:
        taxi = None if walk else TaxiBackup(minutes=round(km * 2.4, 1), reason="No public transport route in our data, and it is too far to walk. Taxi time is an estimate.")
        return Hop(walk=walk, taxi_backup=taxi, distance_km=round(km, 2))
    line = TransitLine(mode="SUBWAY", line="1", from_stop="Louvre - Rivoli", to_stop="Chatelet")
    note = "No date was given, so a normal weekday was checked."
    return Hop(public_transport=PublicTransportOption(minutes=round(8 + km * 3, 1), transfers=0, lines=[line]), walk=walk, distance_km=round(km, 2), note=note)


def _call(tool_name, /, **args):
    return ToolCall(tool_name, args)


class ScriptedLLM:
    """Plays each agent's decisions from what it can read, the way a real LLM would."""

    def __init__(self):
        self.break_change = False

    def generate(self, system, messages, tools, only_tools=None):
        time.sleep(STEP_SECONDS)
        step = sum(message.get("role") == "tool" for message in messages)
        task = messages[0]["text"]
        if system.startswith("You are the Orchestrator"):
            calls = self._orchestrator(task)
        elif system.startswith("You are the Discovery"):
            calls = self._discovery(task, step)
        elif system.startswith("You are the Planner"):
            calls = self._planner(step)
        else:
            calls = self._reviewer(task, step, messages)
        return LLMResponse(calls=calls, raw={"calls": [call.name for call in calls]})

    def _orchestrator(self, task):
        message = re.search(r"<message>\n(.*)\n</message>", task, re.S).group(1)
        if "crash" in message:
            raise LLMError("HTTP 503 secret internal detail")
        if "Replace" in message:
            return [_call("finish", action="change_trip", remove_place_ids=["wikipedia:1"])]
        if "Break" in message:
            self.break_change = True
            return [_call("finish", action="change_trip", remove_place_ids=["wikipedia:1"])]
        for city in PLACES:
            if city in message:
                return [_call("finish", action="plan_trip", city=city, days=2, budget=1 if "cheap" in message else 500)]
        return [_call("finish", action="ask", question="Which city would you like to visit?")]

    def _discovery(self, task, step):
        city = next(city for city in PLACES if city in task)
        if step == 0:
            return [_call("verify_place", name=place.name, city=city) for place in PLACES[city]]
        if step == 1:
            return [_call("find_restaurants", near_place_id=PLACES[city][0].place_id)]
        pick = lambda place: {"place_id": place.place_id, "reason": "Fits."}
        return [_call("finish", city=city, attractions=[pick(p) for p in PLACES[city]], restaurants=[pick(p) for p in RESTAURANTS[city]])]

    def _planner(self, step):
        if self.break_change:
            self.break_change = False
            raise LLMError("HTTP 503 secret internal detail")
        if step == 0:
            return [_call("optimize_schedule", max_activities_per_day=2)]
        return [_call("finish", schedule_id="schedule_1", explanation="A calm pace near the main sights.")]

    def _reviewer(self, task, step, messages):
        if step == 0:
            needed = re.findall(r"needs a price per .*?item_id: (\S+)", task)
            return [_call("calculate_budget", prices=[{"item_id": item, "amount": 10} for item in needed]), _call("check_plan")]
        budget = next(result for name, result in messages[-1]["results"] if name == "calculate_budget")
        if "error" in budget:
            # A price above the whole budget gets a currency warning. These prices are right, so they are sent again.
            needed = re.findall(r"needs a price per .*?item_id: (\S+)", task)
            return [_call("calculate_budget", prices=[{"item_id": item, "amount": 10} for item in needed])]
        if budget["over_budget"]:
            return [_call("finish", verdict="send_back", issue_code="over_budget", instructions="The plan is over budget.", summary="Over budget.")]
        return [_call("finish", verdict="approve", summary="Fine.")]


def build_app():
    fast_optimize = partial(optimize_schedule, travel=fake_route, solver_seconds=1)
    graph.build_discovery_agent = lambda: build_discovery_agent(fake_verify, fake_restaurants)
    graph.build_planner_agent = lambda trip, discovery: build_planner_agent(trip, discovery, optimize=fast_optimize, route=fake_route)
    llm = ScriptedLLM()
    # The tests make more requests than the real daily limit, so each server run counts in its own file.
    usage_file = Path(tempfile.mkdtemp()) / "usage.json"
    import city_planner.api as api

    api.MAX_REQUESTS_PER_DAY = 1000
    return create_app(llm_factory=lambda: llm, usage_file=usage_file, frontend_origin=os.environ["E2E_FRONTEND_ORIGIN"])


if __name__ == "__main__":
    uvicorn.run(build_app(), port=int(os.environ["E2E_API_PORT"]), log_level="warning")
