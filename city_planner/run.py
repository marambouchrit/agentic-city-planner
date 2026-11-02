"""Plan a trip and show every decision the agents made. The Orchestrator replaces these options in Phase 5.

    python -m city_planner.run Paris --days 3
    python -m city_planner.run Tunis --days 3 --date 2026-12-07 --pace 3 --preferences "history, local food"
"""

import argparse
import json
from datetime import date

from city_planner.agents.planner import _travel_summary
from city_planner.graph import plan_trip
from city_planner.llm import GeminiLLM, LLMError
from city_planner.schemas import TripRequest


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan a city trip.")
    parser.add_argument("city")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--date", type=date.fromisoformat, help="First day of the trip, as 2026-12-07")
    parser.add_argument("--pace", type=int, help="At most this many attractions a day")
    parser.add_argument("--budget", type=float)
    parser.add_argument("--preferences", default="")
    args = parser.parse_args()
    trip = TripRequest(
        city=args.city,
        days=args.days,
        start_date=args.date,
        max_activities_per_day=args.pace,
        budget=args.budget,
        preferences=args.preferences,
    )
    try:
        state = plan_trip(trip, GeminiLLM())
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
    discovery = state.get("discovery")
    if discovery is None:
        print("No result.")
        return

    print(f"\nCity: {discovery.city}")
    for label, chosen in (("Attractions", discovery.attractions), ("Restaurants", discovery.restaurants)):
        print(f"{label}:")
        for item in chosen:
            views = f", {item.place.page_views} Wikipedia views last month" if item.place.page_views is not None else ""
            print(f"  - {item.place.name} ({item.place.source}{views})")
            print(f"      Agent's opinion, not verified data: {item.reason}")
    print(f"Notes: {discovery.notes}")
    print(f"Rejected by tools: {discovery.rejected}")
    print(f"Could not be checked: {discovery.unavailable}")

    planner = state.get("planner")
    if planner is None:
        print("\nNo schedule was produced.")
        return

    print(f"\nSchedule ({planner.schedule.status}):")
    for day in planner.schedule.days:
        print(f"Day {day.day_number}")
        for stop in day.stops:
            travel = _travel_summary(stop.hop)
            print(f"  {stop.start} {stop.slot}: {stop.name}" + (f"  [{travel}]" if travel else ""))
            if stop.hop and stop.hop.taxi_backup:
                print(f"      {stop.hop.taxi_backup.reason}")
    for note in planner.schedule.notes:
        print(f"Note: {note}")
    print(f"\nAgent's opinion, not verified data: {planner.explanation}")


if __name__ == "__main__":
    main()
