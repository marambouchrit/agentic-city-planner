"""Can the Gemini model make reliable tool calls, and what does one call cost?

Uses about 12 requests per repeat, which matters if your daily free quota is low.

    python -m spikes.check_gemini --list-models
    python -m spikes.check_gemini
"""

import argparse
import os
import time

import httpx

from spikes.common import error_message, post_with_retry, require_env, save_report

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT_SECONDS = 60.0
MAX_LOOP_STEPS = 6

TOOLS = [
    {
        "functionDeclarations": [
            {
                "name": "search_places",
                "description": "Search for real places in a city.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "What to look for, e.g. 'history museum'"},
                        "city": {"type": "string"},
                    },
                    "required": ["query", "city"],
                },
            },
            {
                "name": "get_transit_route",
                "description": "Get the public transit route between two named places.",
                "parameters": {
                    "type": "object",
                    "properties": {"origin": {"type": "string"}, "destination": {"type": "string"}},
                    "required": ["origin", "destination"],
                },
            },
            {
                "name": "calculate_budget",
                "description": "Add up a list of costs and compare the total with a budget.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "costs": {"type": "array", "items": {"type": "number"}},
                        "budget": {"type": "number"},
                    },
                    "required": ["costs", "budget"],
                },
            },
        ]
    }
]

SYSTEM_PROMPT = (
    "You help plan city trips. Use the tools for facts and calculations. "
    "If no tool is needed, answer directly."
)

# (prompt, expected tool or None for a direct answer, text that must appear in the arguments)
SINGLE_CALL_CASES = [
    ("Find popular history museums in Lisbon.", "search_places", "lisbon"),
    ("I want cheap street food in Bangkok.", "search_places", "bangkok"),
    ("How do I get from the Louvre to the Eiffel Tower by metro?", "get_transit_route", "louvre"),
    ("What is the public transport from Colosseum to Trevi Fountain?", "get_transit_route", "colosseum"),
    ("My costs are 20, 35 and 15. Is that within a budget of 60?", "calculate_budget", "35"),
    ("Thanks, that is all for today!", None, ""),
]

LOOP_PROMPT = (
    "Find a museum in Rome and a park in Rome, then get the transit route between the two "
    "places you found. Then tell me the result in one sentence."
)
FAKE_RESULTS = {
    "search_places": lambda args: {
        "places": [{"name": "Villa Borghese" if "park" in str(args).lower() else "Capitoline Museums"}]
    },
    "get_transit_route": lambda args: {"summary": "Metro line A, 3 stops, 18 minutes"},
    "calculate_budget": lambda args: {"total": sum(args.get("costs", [])), "budget": args.get("budget")},
}


def generate(client: httpx.Client, model: str, contents: list[dict]) -> dict:
    started = time.monotonic()
    response = post_with_retry(
        client,
        f"{BASE_URL}/models/{model}:generateContent",
        {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": contents,
            "tools": TOOLS,
            "generationConfig": {"temperature": 0},
        },
    )
    seconds = round(time.monotonic() - started, 2)
    if isinstance(response, str):
        return {"error": response, "seconds": seconds}
    if response.status_code != 200:
        return {"error": f"HTTP {response.status_code}: {error_message(response)}", "seconds": seconds}

    body = response.json()
    candidates = body.get("candidates") or []
    if not candidates:
        return {"error": f"no candidates: {body.get('promptFeedback')}", "seconds": seconds}
    content = candidates[0].get("content") or {"role": "model", "parts": []}
    return {
        # Kept whole: later turns must send it back as is, including any thought signatures.
        "content": content,
        "calls": [p["functionCall"] for p in content.get("parts", []) if "functionCall" in p],
        "text": "".join(p.get("text", "") for p in content.get("parts", [])),
        "tokens": (body.get("usageMetadata") or {}).get("totalTokenCount", 0),
        "seconds": seconds,
    }


def run_single_case(client: httpx.Client, model: str, case: tuple[str, str | None, str]) -> dict:
    prompt, expected_tool, expected_arg = case
    result = generate(client, model, [{"role": "user", "parts": [{"text": prompt}]}])
    if "error" in result:
        return {"prompt": prompt, "passed": False, "error": result["error"], "seconds": result["seconds"], "tokens": 0}

    called = [c.get("name") for c in result["calls"]]
    if expected_tool is None:
        passed = not called
    else:
        arguments = str([c.get("args") for c in result["calls"]]).lower()
        passed = expected_tool in called and expected_arg in arguments
    return {
        "prompt": prompt,
        "expected_tool": expected_tool,
        "called": called,
        "args": [c.get("args") for c in result["calls"]],
        "passed": passed,
        "seconds": result["seconds"],
        "tokens": result["tokens"],
    }


def run_loop_case(client: httpx.Client, model: str, delay: float) -> dict:
    """Feeds fake tool results back, as a real agent loop will."""
    contents = [{"role": "user", "parts": [{"text": LOOP_PROMPT}]}]
    called: list[str] = []
    tokens = 0
    seconds = 0.0
    final_text = ""
    error = None

    for step in range(MAX_LOOP_STEPS):
        if step:
            time.sleep(delay)
        result = generate(client, model, contents)
        seconds += result["seconds"]
        if "error" in result:
            error = result["error"]
            break
        tokens += result["tokens"]
        if not result["calls"]:
            final_text = result["text"]
            break

        contents.append(result["content"])
        parts = []
        for call in result["calls"]:
            name = call.get("name")
            called.append(name)
            fake = FAKE_RESULTS.get(name, lambda args: {"error": "unknown tool"})
            parts.append({"functionResponse": {"name": name, "response": fake(call.get("args") or {})}})
        contents.append({"role": "user", "parts": parts})

    route_after_search = (
        "get_transit_route" in called
        and "search_places" in called
        and called.index("search_places") < called.index("get_transit_route")
    )
    return {
        "called": called,
        "final_text": final_text,
        "passed": error is None and route_after_search and called.count("search_places") >= 2 and bool(final_text),
        "error": error,
        "tokens": tokens,
        "seconds": round(seconds, 2),
    }


def list_models(client: httpx.Client) -> list[str]:
    response = client.get(f"{BASE_URL}/models", params={"pageSize": 200})
    if response.status_code != 200:
        raise SystemExit(f"Could not list models: HTTP {response.status_code}: {error_message(response)}")
    return [
        m["name"].removeprefix("models/")
        for m in response.json().get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-models", action="store_true")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--delay", type=float, default=7.0, help="seconds between requests, to respect per-minute limits")
    args = parser.parse_args()

    key = require_env("GEMINI_API_KEY")
    model = os.environ.get("GEMINI_MODEL", "").strip() or "gemini-flash-latest"

    with httpx.Client(headers={"x-goog-api-key": key}, timeout=TIMEOUT_SECONDS) as client:
        if args.list_models:
            print("\n".join(list_models(client)))
            return

        singles = []
        for _ in range(args.repeats):
            for case in SINGLE_CALL_CASES:
                outcome = run_single_case(client, model, case)
                singles.append(outcome)
                print(f"{'PASS' if outcome['passed'] else 'FAIL'}  {outcome['prompt']}  {outcome.get('error') or outcome['called']}")
                time.sleep(args.delay)

        loops = []
        for _ in range(args.repeats):
            outcome = run_loop_case(client, model, args.delay)
            loops.append(outcome)
            print(f"{'PASS' if outcome['passed'] else 'FAIL'}  agent loop  {outcome.get('error') or outcome['called']}")

    passed = sum(s["passed"] for s in singles)
    worked = [s for s in singles if s["tokens"]]
    report = {
        "model": model,
        "single_calls_passed": f"{passed}/{len(singles)}",
        "loops_passed": f"{sum(l['passed'] for l in loops)}/{len(loops)}",
        "avg_seconds_per_call": round(sum(s["seconds"] for s in worked) / len(worked), 2) if worked else None,
        "avg_tokens_per_call": round(sum(s["tokens"] for s in worked) / len(worked)) if worked else None,
        "avg_tokens_per_loop": round(sum(l["tokens"] for l in loops) / len(loops)) if loops else None,
        "singles": singles,
        "loops": loops,
    }
    path = save_report("gemini", report)
    print(f"\nModel: {model}")
    print(f"Single calls: {report['single_calls_passed']}  Agent loops: {report['loops_passed']}")
    print(f"Average: {report['avg_seconds_per_call']}s and {report['avg_tokens_per_call']} tokens per call")
    print(f"Saved to {path}")


if __name__ == "__main__":
    main()
