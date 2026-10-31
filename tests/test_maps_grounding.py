"""Offline tests of the Maps grounding check, using fake HTTP responses."""

import json

import httpx

from spikes import check_maps_grounding


def client_returning(status: int, body: dict, sent: list | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if sent is not None:
            sent.append(json.loads(request.content))
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_request_enables_the_maps_tool():
    sent: list = []
    check_maps_grounding.ask_grounded(client_returning(200, {"candidates": [{}]}, sent), "m", "hi")
    assert sent[0]["tools"] == [{"googleMaps": {}}]


def test_answer_with_maps_sources_counts_as_grounded():
    body = {
        "candidates": [
            {
                "content": {"parts": [{"text": "The Louvre, rated 4.7."}]},
                "groundingMetadata": {"groundingChunks": [{"maps": {"title": "Louvre"}}]},
            }
        ]
    }
    answer = check_maps_grounding.ask_grounded(client_returning(200, body), "m", "hi")
    assert answer["grounded"]
    assert answer["sources"] == ["Louvre"]
    assert answer["text"] == "The Louvre, rated 4.7."


def test_answer_without_maps_sources_is_not_grounded():
    body = {"candidates": [{"content": {"parts": [{"text": "From memory."}]}}]}
    assert not check_maps_grounding.ask_grounded(client_returning(200, body), "m", "hi")["grounded"]


def test_free_tier_rejection_is_reported_clearly():
    body = {"error": {"message": "Maps grounding is not available on the free tier"}}
    answer = check_maps_grounding.ask_grounded(client_returning(429, body), "m", "hi")
    assert not answer["grounded"]
    assert answer["error"] == "HTTP 429: Maps grounding is not available on the free tier"


def test_check_city_asks_about_places_and_transit():
    sent: list = []
    report = check_maps_grounding.check_city(client_returning(200, {"candidates": [{}]}, sent), "m", "Oslo", delay=0)
    assert set(report) == {"city", "places", "transit"}
    assert len(sent) == 2
    assert all("Oslo" in request["contents"][0]["parts"][0]["text"] for request in sent)
