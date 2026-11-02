"""Offline tests of get_route. The fixtures are real Transitous responses, trimmed and recorded on 2026-11-02."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from city_planner.schemas import Hop, TaxiBackup, WalkOption
from city_planner.tools import distance, get_route as module
from city_planner.tools.get_route import get_route

FIXTURES = Path(__file__).parent / "fixtures"
LOUVRE = (48.8606, 2.3376)
PONT_NEUF = (48.8580, 2.3470)
EIFFEL = (48.8584, 2.2945)
NOTRE_DAME = (48.8530, 2.3499)
TUNIS_A = (36.8008, 10.1800)
TUNIS_B = (36.8065, 10.1815)
CARTHAGE = (36.8531, 10.3230)


def recorded(name):
    return json.loads((FIXTURES / f"transitous_{name}.json").read_text(encoding="utf-8"))


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


def in_days(days, hour=9, minute=0):
    return (datetime.now() + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)


def test_haversine_paris_to_lyon_is_about_392_km():
    assert distance.haversine_km(48.8566, 2.3522, 45.7640, 4.8357) == pytest.approx(392, abs=2)


# The four rows of the transport rules.


def test_public_transport_and_near_walk_offers_both_and_no_taxi():
    hop = get_route(*LOUVRE, *PONT_NEUF, client=fake(body=recorded("paris_both")))
    assert hop.public_transport and hop.public_transport.lines
    assert hop.walk and hop.walk.is_estimate is False
    assert hop.taxi_backup is None


def test_public_transport_and_far_walk_offers_only_public_transport():
    hop = get_route(*LOUVRE, *EIFFEL, client=fake(body=recorded("paris_transit_only")))
    assert hop.public_transport and hop.walk is None and hop.taxi_backup is None


def test_no_public_transport_and_near_walk_offers_only_the_walk():
    hop = get_route(*TUNIS_A, *TUNIS_B, client=fake(body=recorded("tunis_walk_only")))
    assert hop.public_transport is None and hop.taxi_backup is None
    assert hop.walk and hop.walk.is_estimate is False


def test_no_public_transport_and_far_walk_gives_labelled_taxi_backup():
    hop = get_route(*TUNIS_A, *CARTHAGE, client=fake(body=recorded("tunis_nothing")))
    assert hop.public_transport is None and hop.walk is None
    assert "No public transport route in our data" in hop.taxi_backup.reason
    assert "estimate" in hop.taxi_backup.reason


# The walk.


def test_real_walk_over_the_limit_is_not_offered():
    hop = get_route(*LOUVRE, *NOTRE_DAME, client=fake(body=recorded("paris_walk_over_limit")))
    assert hop.public_transport and hop.walk is None


def test_traveler_can_lower_the_walking_limit():
    hop = get_route(*LOUVRE, *PONT_NEUF, max_walk_minutes=5, client=fake(body=recorded("paris_both")))
    assert hop.walk is None and hop.public_transport


def test_traveler_cannot_raise_the_walking_limit():
    hop = get_route(*LOUVRE, *NOTRE_DAME, max_walk_minutes=60, client=fake(body=recorded("paris_walk_over_limit")))
    assert hop.walk is None


def test_walk_with_no_real_data_is_a_labelled_estimate_with_detour():
    hop = get_route(*TUNIS_A, *TUNIS_B, client=fake(body={"itineraries": [], "direct": []}))
    straight = distance.estimated_walking_minutes(hop.distance_km)
    assert hop.walk.is_estimate is True
    assert hop.walk.minutes == pytest.approx(straight * 1.3, abs=0.2)


# Choosing the public transport.


def itinerary(minutes, transfers=0, mode="BUS", line="1"):
    return {
        "duration": minutes * 60,
        "transfers": transfers,
        "legs": [
            {"mode": "WALK", "duration": 120},
            {"mode": mode, "displayName": line, "from": {"name": "X"}, "to": {"name": "Y"}},
        ],
    }


def test_fastest_route_is_chosen_not_the_first():
    body = {"itineraries": [itinerary(27, line="slow"), itinerary(26, line="fast")]}
    assert get_route(*LOUVRE, *EIFFEL, client=fake(body=body)).public_transport.lines[0].line == "fast"


def test_fewer_transfers_win_a_tie():
    body = {"itineraries": [itinerary(26, transfers=1, line="change"), itinerary(26, line="direct")]}
    assert get_route(*LOUVRE, *EIFFEL, client=fake(body=body)).public_transport.lines[0].line == "direct"


def test_walking_inside_the_route_is_reported():
    hop = get_route(*LOUVRE, *EIFFEL, client=fake(body={"itineraries": [itinerary(26)]}))
    assert hop.public_transport.walking_minutes_inside == 2


@pytest.mark.parametrize("mode", ["AIRPLANE", "ODM", "RIDE_SHARING"])
def test_other_modes_do_not_count_as_public_transport(mode):
    hop = get_route(*LOUVRE, *EIFFEL, client=fake(body={"itineraries": [itinerary(26, mode=mode)]}))
    assert hop.public_transport is None and hop.taxi_backup


def test_walk_only_itinerary_counts_as_a_walk():
    body = {"itineraries": [{"duration": 300, "legs": [{"mode": "WALK", "duration": 300}]}]}
    hop = get_route(*LOUVRE, *PONT_NEUF, client=fake(body=body))
    assert hop.public_transport is None and hop.walk.minutes == 5 and hop.walk.is_estimate is False


# The rules on Hop itself.


def test_taxi_next_to_another_option_is_refused():
    with pytest.raises(ValidationError):
        Hop(walk=WalkOption(minutes=5, is_estimate=False), taxi_backup=TaxiBackup(minutes=5, reason="x"))


def test_schedule_reserves_the_longer_option():
    hop = get_route(*LOUVRE, *PONT_NEUF, client=fake(body=recorded("paris_both")))
    assert hop.reserved_minutes == max(hop.public_transport.minutes, hop.walk.minutes)


def test_reserved_time_is_saved_with_the_hop():
    hop = get_route(*TUNIS_A, *TUNIS_B, client=fake(body=recorded("tunis_walk_only")))
    assert hop.model_dump()["reserved_minutes"] == hop.walk.minutes


# Failures.


def test_outage_is_reported_as_a_failed_check_not_as_no_transport():
    hop = get_route(*TUNIS_A, *CARTHAGE, client=fake(status=503))
    assert hop.check_failed is True
    assert "could not be checked" in hop.taxi_backup.reason


def test_outage_is_retried_once_and_not_cached():
    seen = []
    get_route(*LOUVRE, *EIFFEL, client=fake(status=503, seen=seen))
    assert len(seen) == 2
    assert get_route(*LOUVRE, *EIFFEL, client=fake(body=recorded("paris_transit_only"))).public_transport


def test_request_error_is_not_retried():
    seen = []
    get_route(*LOUVRE, *EIFFEL, client=fake(status=400, seen=seen))
    assert len(seen) == 1


def test_missing_coordinates_give_no_route_and_no_request():
    seen = []
    hop = get_route(None, 2.3, 48.9, 2.5, client=fake(seen=seen))
    assert hop.reserved_minutes is None and seen == []
    assert hop.taxi_backup is None


# Dates and time zones.


def asked_time(request):
    return datetime.strptime(request.url.params["time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def test_local_time_is_converted_with_the_city_time_zone():
    seen = []
    get_route(*TUNIS_A, *CARTHAGE, departure_local=in_days(3, hour=9), client=fake(body={}, seen=seen))
    assert asked_time(seen[0]).hour == 8  # Tunis is UTC+1 all year


def test_time_is_rounded_to_the_half_hour_and_shares_the_cache():
    seen = []
    client = fake(body=recorded("paris_transit_only"), seen=seen)
    get_route(*LOUVRE, *EIFFEL, departure_local=in_days(3, minute=5), client=client)
    get_route(*LOUVRE, *EIFFEL, departure_local=in_days(3, minute=25), client=client)
    assert len(seen) == 1
    assert asked_time(seen[0]).minute == 0


def test_far_date_checks_the_same_weekday_soon_and_says_so():
    seen = []
    far = in_days(90)
    hop = get_route(*LOUVRE, *EIFFEL, departure_local=far, client=fake(body=recorded("paris_transit_only"), seen=seen))
    asked = asked_time(seen[0])
    assert asked.weekday() == far.weekday()
    assert 6 <= (asked - datetime.now(timezone.utc)).days <= 14
    assert "same weekday" in hop.note


def test_near_date_is_used_as_it_is():
    seen = []
    near = in_days(3)
    hop = get_route(*LOUVRE, *EIFFEL, departure_local=near, client=fake(body=recorded("paris_transit_only"), seen=seen))
    assert asked_time(seen[0]).date() == near.date()
    assert hop.note is None


def test_no_date_checks_a_weekday_morning_and_says_so():
    seen = []
    hop = get_route(*LOUVRE, *EIFFEL, client=fake(body=recorded("paris_transit_only"), seen=seen))
    assert asked_time(seen[0]).weekday() == 1
    assert "normal weekday" in hop.note


# Limits and the cache.


def test_repeat_request_uses_the_cache():
    seen = []
    client = fake(body=recorded("paris_both"), seen=seen)
    get_route(*LOUVRE, *PONT_NEUF, client=client)
    get_route(*LOUVRE, *PONT_NEUF, client=client)
    assert len(seen) == 1


def test_cached_answer_still_follows_the_walking_limit():
    client = fake(body=recorded("paris_both"))
    assert get_route(*LOUVRE, *PONT_NEUF, client=client).walk
    assert get_route(*LOUVRE, *PONT_NEUF, max_walk_minutes=5, client=client).walk is None


def test_daily_limit_stops_requests_and_survives_restarts():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    module.USAGE_FILE.write_text(json.dumps({"date": today, "count": module.DAILY_PLAN_LIMIT}))
    seen = []
    hop = get_route(*LOUVRE, *EIFFEL, client=fake(body=recorded("paris_transit_only"), seen=seen))
    assert seen == []
    assert hop.check_failed and "limit" in hop.note


def test_daily_count_resets_on_a_new_day():
    module.USAGE_FILE.write_text(json.dumps({"date": "2000-01-01", "count": module.DAILY_PLAN_LIMIT}))
    assert get_route(*LOUVRE, *EIFFEL, client=fake(body=recorded("paris_transit_only"))).public_transport
    assert json.loads(module.USAGE_FILE.read_text())["count"] == 1
