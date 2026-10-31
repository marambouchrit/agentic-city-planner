"""Offline tests of get_route, using fake HTTP responses."""

import json
from datetime import datetime, timezone

import httpx
import pytest

from city_planner.tools import distance, get_route as module
from city_planner.tools.get_route import get_route

METRO = {
    "itineraries": [
        {
            "duration": 1200,
            "transfers": 1,
            "legs": [
                {"mode": "WALK"},
                {"mode": "SUBWAY", "displayName": "M1", "from": {"name": "X"}, "to": {"name": "Y"}},
            ],
        }
    ]
}
NEAR = (48.860, 2.337, 48.862, 2.340)  # about 300 m
FAR = (48.860, 2.337, 48.900, 2.500)  # about 13 km


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "USAGE_FILE", tmp_path / "usage.json")
    module._cache.clear()


def fake(status=200, body=None, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json={} if body is None else body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_haversine_paris_to_lyon_is_about_392_km():
    assert distance.haversine_km(48.8566, 2.3522, 45.7640, 4.8357) == pytest.approx(392, abs=2)


def test_transit_route_keeps_real_lines_and_is_not_an_estimate():
    route = get_route(*FAR, client=fake(body=METRO))
    assert route.mode == "transit" and route.source == "transitous"
    assert route.minutes == 20
    assert route.transfers == 1
    assert route.lines[0].line == "M1"


def test_walk_only_answer_from_transitous_is_a_real_walk():
    body = {"itineraries": [{"duration": 300, "legs": [{"mode": "WALK"}]}]}
    route = get_route(*NEAR, client=fake(body=body))
    assert route.mode == "walk" and route.source == "transitous"


def test_no_route_nearby_gives_labelled_walking_estimate():
    route = get_route(*NEAR, client=fake(body={"itineraries": []}))
    assert route.mode == "walk_estimate" and route.source == "estimate"
    assert route.lines == []
    assert "no route" in route.note and "not a real route" in route.note


def test_no_route_far_away_gives_labelled_taxi_estimate():
    route = get_route(*FAR, client=fake(body={"itineraries": []}))
    assert route.mode == "taxi_estimate" and route.source == "estimate"


def test_server_error_gives_estimate_and_is_not_cached():
    assert get_route(*FAR, client=fake(status=503)).source == "estimate"
    assert get_route(*FAR, client=fake(body=METRO)).source == "transitous"


def test_missing_coordinates_give_no_route_and_no_request():
    seen = []
    route = get_route(None, 2.3, 48.9, 2.5, client=fake(seen=seen))
    assert route.mode is None and route.minutes is None and seen == []


def test_repeat_request_uses_the_cache():
    seen = []
    client = fake(body=METRO, seen=seen)
    get_route(*FAR, client=client)
    get_route(*FAR, client=client)
    assert len(seen) == 1


def test_departure_time_is_sent():
    seen = []
    get_route(*FAR, departure_utc="2026-11-04T09:00:00Z", client=fake(body=METRO, seen=seen))
    assert seen[0].url.params["time"] == "2026-11-04T09:00:00Z"


def test_daily_limit_stops_requests_and_survives_restarts():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    module.USAGE_FILE.write_text(json.dumps({"date": today, "count": module.DAILY_PLAN_LIMIT}))
    seen = []
    route = get_route(*FAR, client=fake(body=METRO, seen=seen))
    assert seen == []
    assert route.source == "estimate" and "limit" in route.note


def test_daily_count_resets_on_a_new_day():
    module.USAGE_FILE.write_text(json.dumps({"date": "2000-01-01", "count": module.DAILY_PLAN_LIMIT}))
    assert get_route(*FAR, client=fake(body=METRO)).source == "transitous"
    assert json.loads(module.USAGE_FILE.read_text())["count"] == 1
