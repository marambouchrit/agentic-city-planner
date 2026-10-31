"""Shared helpers for the Phase 0 risk checks."""

import json
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = ROOT / "spikes" / "results"

load_dotenv(ROOT / ".env")


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is not set. Copy .env.example to .env and fill it in.")
    return value


def error_message(response) -> str:
    """Google error bodies only, so a key in a request header can't reach the output."""
    try:
        return response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return "no error details returned"


def save_report(name: str, report: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{name}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def post_with_retry(client: httpx.Client, url: str, payload: dict, attempts: int = 3, wait: float = 20.0) -> httpx.Response | str:
    """Retries overload and timeout failures. Returns an error message if every attempt fails."""
    failure = "no attempts made"
    for attempt in range(attempts):
        if attempt:
            time.sleep(wait)
        try:
            response = client.post(url, json=payload)
        except httpx.HTTPError as exc:
            failure = f"{type(exc).__name__} after {attempt + 1} attempt(s)"
            continue
        if response.status_code != 503:
            return response
        failure = f"HTTP 503: {error_message(response)}"
    return failure
