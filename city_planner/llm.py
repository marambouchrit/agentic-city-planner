"""The one small LLM interface. Agents only see this, so the provider can be swapped."""

import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from dotenv import load_dotenv

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-3.5-flash-lite"
TIMEOUT_SECONDS = 60.0
ATTEMPTS = 3
RETRY_WAIT_SECONDS = 20.0

load_dotenv()


class LLMError(Exception):
    """The LLM could not answer. Messages never contain the key."""


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass
class LLMResponse:
    calls: list[ToolCall] = field(default_factory=list)
    text: str = ""
    # The provider's own message, sent back unchanged on the next turn.
    raw: Any = None


class LLM(Protocol):
    def generate(
        self, system: str, messages: list[dict], tools: list[dict], only_tools: list[str] | None = None
    ) -> LLMResponse:
        """messages use the roles user (text), assistant (raw) and tool (results).
        tools are {name, description, parameters}. only_tools narrows the choice."""
        ...


def _to_contents(messages: list[dict]) -> list[dict]:
    contents = []
    for message in messages:
        if message["role"] == "user":
            contents.append({"role": "user", "parts": [{"text": message["text"]}]})
        elif message["role"] == "assistant":
            contents.append(message["raw"])
        else:
            parts = [{"functionResponse": {"name": n, "response": r}} for n, r in message["results"]]
            contents.append({"role": "user", "parts": parts})
    return contents


class GeminiLLM:
    def __init__(self, client: httpx.Client | None = None, retry_wait: float = RETRY_WAIT_SECONDS):
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key and client is None:
            raise LLMError("GEMINI_API_KEY is not set.")
        self.model = os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_MODEL
        self.client = client or httpx.Client(headers={"x-goog-api-key": key}, timeout=TIMEOUT_SECONDS)
        self.retry_wait = retry_wait

    def generate(self, system, messages, tools, only_tools=None) -> LLMResponse:
        # ANY means the LLM must call a tool, but it still chooses which one.
        calling = {"mode": "ANY"}
        if only_tools:
            calling["allowedFunctionNames"] = only_tools
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": _to_contents(messages),
            "tools": [{"functionDeclarations": tools}],
            "toolConfig": {"functionCallingConfig": calling},
            "generationConfig": {"temperature": 0},
        }
        body = self._post(payload)
        candidates = body.get("candidates") or []
        if not candidates:
            raise LLMError(f"No answer from the model: {body.get('promptFeedback')}")
        content = candidates[0].get("content") or {"role": "model", "parts": []}
        content.setdefault("role", "model")
        parts = content.get("parts") or []
        content["parts"] = parts
        return LLMResponse(
            calls=[
                ToolCall(p["functionCall"].get("name", ""), p["functionCall"].get("args") or {})
                for p in parts
                if "functionCall" in p
            ],
            text="".join(p.get("text", "") for p in parts),
            raw=content,
        )

    def _post(self, payload: dict) -> dict:
        """Retries overload and rate limits. The daily quota also returns 429, so retries are few."""
        url = f"{BASE_URL}/models/{self.model}:generateContent"
        failure = "no attempts made"
        for attempt in range(ATTEMPTS):
            if attempt:
                time.sleep(self.retry_wait)
            try:
                response = self.client.post(url, json=payload)
            except httpx.HTTPError as exc:
                failure = type(exc).__name__
                continue
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    raise LLMError("The model returned an unreadable response.") from None
            failure = f"HTTP {response.status_code}: {_error_message(response)}"
            if response.status_code not in (429, 503):
                break
        raise LLMError(failure)


def _error_message(response: httpx.Response) -> str:
    try:
        return response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return "no error details returned"
