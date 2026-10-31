"""Offline tests of find_restaurants, using fake HTTP responses."""

import httpx
import pytest

from city_planner.tools.find_restaurants import find_restaurants

ITEM = {
    "fsq_place_id": "abc",
    "name": "Dar El Jeld",
    "latitude": 36.79,
    "longitude": 10.17,
    "categories": [{"name": "Tunisian Restaurant"}],
    "location": {"formatted_address": "5 Rue Dar El Jeld"},
}


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("FOURSQUARE_API_KEY", "test-key")


def search(status=200, body=None, seen=None, **kwargs):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json={"results": []} if body is None else body)

    return find_restaurants(36.8, 10.1, client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)


def test_results_become_places_from_foursquare():
    result = search(body={"results": [ITEM]})
    assert result.status == "ok"
    place = result.restaurants[0]
    assert place.place_id == "foursquare:abc"
    assert place.kind == "restaurant"
    assert place.source == "foursquare"
    assert place.description == "Tunisian Restaurant"
    assert place.address == "5 Rue Dar El Jeld"


def test_request_uses_the_current_api_and_pro_fields_only():
    seen = []
    search(seen=seen, food="seafood")
    request = seen[0]
    assert request.headers["Authorization"] == "Bearer test-key"
    assert request.headers["X-Places-Api-Version"] == "2025-06-17"
    assert request.url.params["ll"] == "36.8,10.1"
    assert request.url.params["query"] == "seafood restaurant"
    for paid_field in ("rating", "price", "hours", "tips", "photos"):
        assert paid_field not in request.url.params["fields"]


def test_no_key_is_labelled_and_makes_no_request(monkeypatch):
    monkeypatch.delenv("FOURSQUARE_API_KEY")
    seen = []
    result = search(seen=seen)
    assert result.status == "unavailable" and result.restaurants == [] and seen == []


def test_used_up_credits_are_unavailable():
    result = search(status=429)
    assert result.status == "unavailable" and "429" in result.note


def test_error_note_never_contains_the_key():
    assert "test-key" not in search(status=401).note


def test_no_results_is_different_from_unavailable():
    assert search().status == "no_results"


def test_result_without_id_is_dropped():
    assert search(body={"results": [{"name": "Mystery Cafe"}]}).status == "no_results"


def test_missing_coordinates_are_labelled():
    item = {"fsq_place_id": "abc", "name": "X"}
    assert search(body={"results": [item]}).restaurants[0].missing == ["coordinates"]


def test_empty_address_counts_as_no_address():
    # Seen in a live test in Luang Prabang.
    item = {"fsq_place_id": "abc", "name": "X", "location": {"formatted_address": ""}}
    assert search(body={"results": [item]}).restaurants[0].address is None


def test_bad_json_is_unavailable():
    def handler(request):
        return httpx.Response(200, text="not json")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert find_restaurants(1, 2, client=client).status == "unavailable"
