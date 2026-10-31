"""Offline tests of the Gemini client, using fake HTTP responses."""

import json

import httpx
import pytest

from city_planner.llm import GeminiLLM, LLMError

ANSWER = {
    "candidates": [
        {"content": {"role": "model", "parts": [{"functionCall": {"name": "verify_place", "args": {"name": "Louvre"}}, "thoughtSignature": "sig"}]}}
    ]
}
MESSAGES = [{"role": "user", "text": "hi"}]


def llm_with(*responses, seen=None):
    queue = list(responses)

    def handler(request):
        if seen is not None:
            seen.append(json.loads(request.content))
        return queue.pop(0)

    return GeminiLLM(client=httpx.Client(transport=httpx.MockTransport(handler)), retry_wait=0)


def test_tool_call_is_parsed_and_raw_message_is_kept_whole():
    response = llm_with(httpx.Response(200, json=ANSWER)).generate("s", MESSAGES, [])
    assert response.calls[0].name == "verify_place"
    assert response.calls[0].args == {"name": "Louvre"}
    assert response.raw["parts"][0]["thoughtSignature"] == "sig"


def test_llm_must_call_a_tool_but_all_tools_stay_available():
    seen = []
    llm_with(httpx.Response(200, json=ANSWER), seen=seen).generate("s", MESSAGES, [{"name": "a"}])
    assert seen[0]["toolConfig"]["functionCallingConfig"] == {"mode": "ANY"}


def test_only_tools_narrows_the_choice():
    seen = []
    llm_with(httpx.Response(200, json=ANSWER), seen=seen).generate("s", MESSAGES, [], ["finish"])
    assert seen[0]["toolConfig"]["functionCallingConfig"]["allowedFunctionNames"] == ["finish"]


def test_messages_are_converted_for_gemini():
    seen = []
    raw = {"role": "model", "parts": [{"text": "x"}]}
    messages = MESSAGES + [{"role": "assistant", "raw": raw}, {"role": "tool", "results": [("a", {"ok": True})]}]
    llm_with(httpx.Response(200, json=ANSWER), seen=seen).generate("s", messages, [])
    assert seen[0]["contents"][1] == raw
    assert seen[0]["contents"][2]["parts"][0]["functionResponse"] == {"name": "a", "response": {"ok": True}}


def test_overload_is_retried():
    llm = llm_with(httpx.Response(503), httpx.Response(200, json=ANSWER))
    assert llm.generate("s", MESSAGES, []).calls


def test_gives_up_after_three_attempts():
    llm = llm_with(*[httpx.Response(429, json={"error": {"message": "quota"}}) for _ in range(3)])
    with pytest.raises(LLMError, match="429: quota"):
        llm.generate("s", MESSAGES, [])


def test_bad_request_is_not_retried():
    seen = []
    llm = llm_with(httpx.Response(400, json={"error": {"message": "bad"}}), seen=seen)
    with pytest.raises(LLMError):
        llm.generate("s", MESSAGES, [])
    assert len(seen) == 1


def test_no_candidates_is_an_error():
    with pytest.raises(LLMError):
        llm_with(httpx.Response(200, json={})).generate("s", MESSAGES, [])


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(LLMError, match="not set"):
        GeminiLLM()
