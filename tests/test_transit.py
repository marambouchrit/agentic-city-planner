"""Offline tests of the Transitous check, using fake HTTP responses."""

from datetime import datetime, timezone

import httpx

from spikes import check_transit

TRIP = ("Paris", 1, ("A", 1.0, 2.0), ("B", 3.0, 4.0))


def test_next_morning_is_ten_local_two_days_ahead():
    now = datetime(2026, 11, 1, 23, 0, tzinfo=timezone.utc)
    # UTC+1: already 00:00 on Nov 2 locally, so Nov 4 at 10:00 local is 09:00 UTC.
    assert check_transit.next_morning_utc(1, now) == "2026-11-04T09:00:00Z"


def test_summary_keeps_transit_legs_and_skips_walking():
    body = {
        "itineraries": [
            {
                "duration": 1200,
                "transfers": 0,
                "legs": [
                    {"mode": "WALK", "from": {"name": "A"}, "to": {"name": "X"}},
                    {"mode": "SUBWAY", "displayName": "M1", "from": {"name": "X"}, "to": {"name": "Y"}},
                ],
            }
        ]
    }
    summary = check_transit.summarize_plan(body)
    assert summary["transit_used"]
    assert summary["minutes"] == 20
    assert summary["lines"] == [{"mode": "SUBWAY", "line": "M1", "from": "X", "to": "Y"}]


def test_walk_only_route_is_found_without_transit():
    body = {"itineraries": [{"duration": 600, "legs": [{"mode": "WALK"}]}]}
    summary = check_transit.summarize_plan(body)
    assert summary["found"] and not summary["transit_used"]


def test_no_itineraries_means_not_found():
    assert check_transit.summarize_plan({"itineraries": []}) == {"found": False, "lines": []}


def test_server_error_is_reported_instead_of_crashing():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    assert "error" in check_transit.check_trip(client, TRIP)


def test_request_uses_coordinates():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params)
        return httpx.Response(200, json={})

    check_transit.check_trip(httpx.Client(transport=httpx.MockTransport(handler)), TRIP)
    assert seen[0]["fromPlace"] == "1.0,2.0"
    assert seen[0]["toPlace"] == "3.0,4.0"
