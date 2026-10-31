"""Offline tests of the check scripts' logic, using fake HTTP responses."""

import json

import httpx

from spikes import check_gemini
from spikes.common import post_with_retry


def gemini_client(replies: list[dict]) -> tuple[httpx.Client, list[dict]]:
    sent: list[dict] = []
    remaining = iter(replies)

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=next(remaining))

    return httpx.Client(transport=httpx.MockTransport(handler)), sent


def model_reply(*parts: dict) -> dict:
    return {"candidates": [{"content": {"role": "model", "parts": list(parts)}}], "usageMetadata": {"totalTokenCount": 100}}


def call(name: str, **args) -> dict:
    return {"functionCall": {"name": name, "args": args}, "thoughtSignature": "sig"}


def test_single_case_passes_with_expected_tool_and_argument():
    client, _ = gemini_client([model_reply(call("search_places", query="museum", city="Lisbon"))])
    outcome = check_gemini.run_single_case(client, "m", ("Museums in Lisbon", "search_places", "lisbon"))
    assert outcome["passed"]


def test_single_case_fails_on_wrong_tool():
    client, _ = gemini_client([model_reply(call("calculate_budget", costs=[1], budget=2))])
    outcome = check_gemini.run_single_case(client, "m", ("Museums in Lisbon", "search_places", "lisbon"))
    assert not outcome["passed"]


def test_no_tool_case_fails_when_a_tool_is_called():
    client, _ = gemini_client([model_reply(call("search_places", query="x", city="y"))])
    assert not check_gemini.run_single_case(client, "m", ("Thanks!", None, ""))["passed"]


def test_loop_passes_and_sends_thought_signature_back():
    client, sent = gemini_client(
        [
            model_reply(call("search_places", query="museum", city="Rome"), call("search_places", query="park", city="Rome")),
            model_reply(call("get_transit_route", origin="Capitoline Museums", destination="Villa Borghese")),
            model_reply({"text": "Take metro line A."}),
        ]
    )
    outcome = check_gemini.run_loop_case(client, "m", delay=0)
    assert outcome["passed"]
    assert outcome["tokens"] == 300
    assert sent[1]["contents"][1]["parts"][0]["thoughtSignature"] == "sig"


def test_loop_fails_when_route_comes_before_search():
    client, _ = gemini_client(
        [
            model_reply(call("get_transit_route", origin="a", destination="b")),
            model_reply(call("search_places", query="museum", city="Rome")),
            model_reply(call("search_places", query="park", city="Rome")),
            model_reply({"text": "Done."}),
        ]
    )
    assert not check_gemini.run_loop_case(client, "m", delay=0)["passed"]


def test_http_error_counts_as_failure_with_a_clear_message():
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(429, json={"error": {"message": "quota exceeded"}}))
    )
    outcome = check_gemini.run_single_case(client, "m", ("x", None, ""))
    assert not outcome["passed"]
    assert outcome["error"] == "HTTP 429: quota exceeded"




def test_retry_recovers_after_overload_and_timeout():
    replies = iter(["timeout", 503, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        reply = next(replies)
        if reply == "timeout":
            raise httpx.ReadTimeout("slow")
        return httpx.Response(reply, json={"error": {"message": "busy"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert post_with_retry(client, "https://x.test", {}, wait=0).status_code == 200


def test_retry_returns_message_instead_of_crashing():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert post_with_retry(client, "https://x.test", {}, attempts=2, wait=0) == "ReadTimeout after 2 attempt(s)"
