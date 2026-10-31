"""Run a request and show every decision the agents made.

    python -m city_planner.run "Popular museums in Lisbon"
"""

import json
import sys

from city_planner.graph import plan_trip
from city_planner.llm import GeminiLLM, LLMError


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python -m city_planner.run "Popular museums in Lisbon"')
    try:
        state = plan_trip(sys.argv[1], GeminiLLM())
    except LLMError as exc:
        raise SystemExit(str(exc)) from None

    print("Decisions:")
    for event in state["trace"]:
        print(f"  [{event.agent} step {event.step}] {event.kind}: {event.tool or ''} {json.dumps(event.args, ensure_ascii=False)}")
        if event.result:
            print(f"      -> {event.result}")

    print(f"\nLLM calls: {state['llm_calls']}")
    if state.get("error"):
        print(f"Stopped: {state['error']}")
    output = state.get("discovery")
    if output is None:
        print("No result.")
        return

    print(f"\nCity: {output.city}")
    for label, chosen in (("Attractions", output.attractions), ("Restaurants", output.restaurants)):
        print(f"{label}:")
        for item in chosen:
            views = f", {item.place.page_views} Wikipedia views last month" if item.place.page_views is not None else ""
            print(f"  - {item.place.name} ({item.place.source}{views})")
            print(f"      Agent's opinion, not verified data: {item.reason}")
    print(f"Notes: {output.notes}")
    print(f"Rejected by tools: {output.rejected}")
    print(f"Could not be checked: {output.unavailable}")


if __name__ == "__main__":
    main()
