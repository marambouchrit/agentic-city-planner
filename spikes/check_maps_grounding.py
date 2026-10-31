"""Does Gemini's Google Maps grounding work on a free key, and how useful is its data?

Uses 2 requests per city, so 6 in total by default.

    python -m spikes.check_maps_grounding
    python -m spikes.check_maps_grounding --city "Oslo, Norway"
"""

import argparse
import os
import time

import httpx

from spikes.common import error_message, post_with_retry, require_env, save_report

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT_SECONDS = 90.0
DEFAULT_MODEL = "gemini-3.8-flash"

# A big metro system, a city with thin transit data, and a small town.
DEFAULT_CITIES = ["Paris, France", "Tunis, Tunisia", "Luang Prabang, Laos"]

PLACES_PROMPT = (
    "List the 5 most popular tourist attractions in {city}. For each one give its rating, "
    "number of reviews, price level and opening hours. Write 'unknown' for anything you do not have."
)
TRANSIT_PROMPT = (
    "In {city}, how do I travel by public transport between its two most popular attractions? "
    "Name the exact lines, boarding stop, arrival stop, duration and fare. "
    "Write 'unknown' for anything you do not have."
)


def ask_grounded(client: httpx.Client, model: str, prompt: str) -> dict:
    started = time.monotonic()
    response = post_with_retry(
        client,
        f"{BASE_URL}/models/{model}:generateContent",
        {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "tools": [{"googleMaps": {}}]},
    )
    seconds = round(time.monotonic() - started, 2)
    if isinstance(response, str):
        return {"grounded": False, "error": response, "seconds": seconds}
    if response.status_code != 200:
        return {"grounded": False, "error": f"HTTP {response.status_code}: {error_message(response)}", "seconds": seconds}

    candidates = response.json().get("candidates") or []
    if not candidates:
        return {"grounded": False, "error": "no candidates returned", "seconds": seconds}

    candidate = candidates[0]
    chunks = (candidate.get("groundingMetadata") or {}).get("groundingChunks") or []
    sources = [c["maps"].get("title") for c in chunks if "maps" in c]
    return {
        # Without Maps sources the text may come from the model's own memory, so it can't be trusted as data.
        "grounded": bool(sources),
        "sources": sources,
        "text": "".join(p.get("text", "") for p in (candidate.get("content") or {}).get("parts", [])),
        "seconds": seconds,
    }


def check_city(client: httpx.Client, model: str, city: str, delay: float) -> dict:
    places = ask_grounded(client, model, PLACES_PROMPT.format(city=city))
    time.sleep(delay)
    transit = ask_grounded(client, model, TRANSIT_PROMPT.format(city=city))
    return {"city": city, "places": places, "transit": transit}


def print_answer(label: str, answer: dict) -> None:
    if "error" in answer:
        print(f"  {label}: ERROR {answer['error']}")
        return
    print(f"  {label}: grounded={answer['grounded']}  Maps sources={len(answer['sources'])}  {answer['seconds']}s")
    print(f"{answer['text']}\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", action="append", dest="cities")
    parser.add_argument("--delay", type=float, default=7.0, help="seconds between requests, to respect per-minute limits")
    args = parser.parse_args()

    key = require_env("GEMINI_API_KEY")
    model = os.environ.get("GEMINI_GROUNDING_MODEL", "").strip() or DEFAULT_MODEL

    reports = []
    with httpx.Client(headers={"x-goog-api-key": key}, timeout=TIMEOUT_SECONDS) as client:
        for index, city in enumerate(args.cities or DEFAULT_CITIES):
            if index:
                time.sleep(args.delay)
            report = check_city(client, model, city, args.delay)
            reports.append(report)
            print(f"\n=== {city} ===")
            print_answer("Places", report["places"])
            print_answer("Transit", report["transit"])

    path = save_report("maps_grounding", {"model": model, "cities": reports})
    print(f"\nModel: {model}\nSaved to {path}")


if __name__ == "__main__":
    main()
