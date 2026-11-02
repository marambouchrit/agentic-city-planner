"""Plan a trip from one sentence and show every decision the agents made.

    python -m city_planner.run "3 days in Paris, 300 euros, I love art"
    python -m city_planner.run "2 days in Paris" --then "Replace the Louvre"
"""

import argparse
import json
import uuid

from city_planner.agents.planner import _travel_summary
from city_planner.graph import handle_message, new_checkpointer
from city_planner.llm import GeminiLLM, LLMError


def show(state: dict) -> None:
    print("Decisions:")
    for event in state.get("trace", []):
        print(f"  [{event.agent} step {event.step}] {event.kind}: {event.tool or ''} {json.dumps(event.args, ensure_ascii=False)}")
        if event.result:
            print(f"      -> {event.result}")

    print(f"\nLLM calls this request: {state.get('llm_calls')}")
    if state.get("reply"):
        print(f"Reply: {state['reply']}")
    if state.get("error"):
        print(f"Stopped: {state['error']}")
    discovery = state.get("discovery")
    if discovery is None:
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

    planner = state.get("final_plan") or state.get("planner") or state.get("previous_plan")
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

    review = state.get("final_review") or state.get("previous_review")
    print(f"\nRevisions: {state.get('send_backs', 0)}")
    if review is None:
        print("This plan was not reviewed.")
        return
    print("Reviewer: approved" if review.verdict == "approve" else f"Reviewer: NOT approved ({review.issue_code}). {review.instructions}")
    for note in state.get("notes", []):
        print(f"Note: {note}")
    print(f"Estimated cost for one traveler: {review.budget.total} {review.budget.currency}. {review.budget.note}")
    if review.budget.budget is not None:
        print(f"Budget: {review.budget.budget}. Over budget: {review.budget.over_budget}")
    for item in review.costs:
        print(f"  {item.name}: {'unknown' if item.amount is None else item.amount} (estimate)")
    for issue in review.check.issues:
        if issue.severity == "error":
            print(f"Rule error: {issue.message}")
    print(f"Agent's opinion, not verified data: {review.summary}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan a city trip from one sentence.")
    parser.add_argument("message")
    parser.add_argument("--then", help="A change to ask for after the first plan")
    args = parser.parse_args()
    trip_id = uuid.uuid4().hex
    checkpointer = new_checkpointer()
    try:
        llm = GeminiLLM()
        for message in filter(None, (args.message, args.then)):
            print(f"\n===== {message}")
            show(handle_message(trip_id, message, llm, checkpointer))
    except LLMError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
