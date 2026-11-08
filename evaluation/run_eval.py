"""Evaluation on 10 big cities. The protocol is fixed in SPEC.md section 7, before any result.

    python -m evaluation.run_eval            # runs the cities not done yet, while the Transitous quota allows
    python -m evaluation.run_eval --report   # writes evaluation/RESULTS.md from the saved runs

Each finished city is saved at once, so the run can stop and continue on another day.
It uses real quotas: about 70 Transitous requests and 20 to 40 Gemini calls per city.
"""

import argparse
import json
import statistics
import time
import uuid
from pathlib import Path

from city_planner.graph import handle_message, new_checkpointer
from city_planner.llm import GeminiLLM
from city_planner.tools import get_route
from city_planner.tools.check_plan import check_plan
from city_planner.tools.optimize_schedule import _estimate_minutes

CITIES = ["Paris", "London", "Berlin", "Madrid", "Rome", "Amsterdam", "Vienna", "Barcelona", "New York", "Tokyo"]
REQUEST = "3 days in {city} with 400 euros, I love art and history"
CHANGE = "I don't want the {name}, replace it with something else"
MIN_TRANSITOUS_LEFT = 80
# Outages and lost connections, which say nothing about the planner. Anything else is a real failure.
RETRYABLE = ("HTTP 429", "HTTP 503", "ConnectError", "ReadTimeout", "ConnectTimeout", "RemoteProtocolError")

HERE = Path(__file__).resolve().parent
RUNS_FILE = HERE / "runs.json"
REPORT_FILE = HERE / "RESULTS.md"


def transitous_used() -> int:
    try:
        usage = json.loads(get_route.USAGE_FILE.read_text(encoding="utf-8"))
        return usage["count"] if usage["date"] == time.strftime("%Y-%m-%d", time.gmtime()) else 0
    except (OSError, ValueError, KeyError):
        return 0


def _plan(state: dict):
    return state.get("final_plan") or state.get("planner")


def plan_metrics(state: dict) -> dict:
    """Everything here is measured by code from the state, never by the LLM."""
    plan, review, discovery, trip = _plan(state), state.get("final_review"), state.get("discovery"), state.get("trip")
    if plan is None or discovery is None:
        return {"has_plan": False}
    known = [chosen.place for chosen in discovery.attractions + discovery.restaurants]
    known_ids = {place.place_id for place in known}
    check = check_plan(plan.schedule.days, known, trip.days, trip.max_walk_minutes)

    hops = {"public_transport": 0, "walk_only": 0, "taxi_backup": 0, "check_failed": 0, "unknown": 0}
    estimate_errors = []
    visits_after_dinner = 0
    meals_missing = 0
    for day in plan.schedule.days:
        previous = None
        slots = [stop.slot for stop in day.stops]
        meals_missing += ("lunch" not in slots) + ("dinner" not in slots)
        if "dinner" in slots:
            visits_after_dinner += slots[slots.index("dinner") + 1 :].count("activity")
        for stop in day.stops:
            hop = stop.hop
            if hop is not None:
                if hop.check_failed:
                    hops["check_failed"] += 1
                elif hop.public_transport:
                    hops["public_transport"] += 1
                elif hop.walk:
                    hops["walk_only"] += 1
                elif hop.taxi_backup:
                    hops["taxi_backup"] += 1
                else:
                    hops["unknown"] += 1
                # The solver's estimate against the fastest real option.
                real = [option for option in (hop.public_transport and hop.public_transport.minutes, hop.walk and not hop.walk.is_estimate and hop.walk.minutes) if option]
                if real and previous is not None:
                    estimate = _estimate_minutes(previous.latitude, previous.longitude, stop.latitude, stop.longitude)
                    estimate_errors.append(estimate - min(real))
            if stop.latitude is not None:
                previous = stop

    trace = state.get("trace", [])
    return {
        "has_plan": True,
        "schedule_status": plan.schedule.status,
        "attractions": sum(stop.slot == "activity" for day in plan.schedule.days for stop in day.stops),
        "invented_stops": sum(1 for day in plan.schedule.days for stop in day.stops if stop.place_id and stop.place_id not in known_ids),
        "check_passed": check.passed,
        "check_errors": [issue.code for issue in check.issues if issue.severity == "error"],
        "taxi_rule_breaks": sum(issue.code == "taxi_not_backup" for issue in check.issues),
        "hops": hops,
        "estimate_minus_real_minutes": estimate_errors,
        "visits_after_dinner": visits_after_dinner,
        "meals_missing": meals_missing,
        "approved": review is not None and review.verdict == "approve",
        "reviewed": review is not None,
        "send_backs": state.get("send_backs", 0),
        "estimated_cost": review.budget.total if review else None,
        "over_budget": review.budget.over_budget if review else None,
        "verified_attractions": len(discovery.attractions),
        "rejected_names": len(discovery.rejected),
        "foursquare_calls": sum(event.tool == "find_restaurants" and event.kind == "tool" for event in trace),
        # Kept so that a strange total can be traced afterwards.
        "costs": [[item.name, item.amount] for item in review.costs] if review else [],
    }


def change_metrics(before: dict, after: dict, removed_id: str) -> dict:
    """Whether the change removed the place, kept every other stop, and filled the gap."""
    def stops(state):
        plan = _plan(state)
        if plan is None:
            return {}
        return {stop.place_id: (day.day_number, stop.start) for day in plan.schedule.days for stop in day.stops if stop.place_id}

    old, new = stops(before), stops(after)
    kept = {place_id: slot for place_id, slot in old.items() if place_id != removed_id}
    return {
        "has_plan": bool(new),
        "removed": removed_id not in new,
        "others_unchanged": bool(new) and all(new.get(place_id) == slot for place_id, slot in kept.items()),
        "added": [place_id for place_id in new if place_id not in old],
        "approved": after.get("final_review") is not None and after["final_review"].verdict == "approve",
    }


def _run_message(thread_id: str, message: str, llm, checkpointer) -> tuple[dict, float, int]:
    start, requests = time.time(), transitous_used()
    state = handle_message(thread_id, message, llm, checkpointer)
    return state, round(time.time() - start), transitous_used() - requests


def run_city(city: str, llm) -> dict:
    checkpointer, thread_id = new_checkpointer(), uuid.uuid4().hex
    result: dict = {"city": city, "date": time.strftime("%Y-%m-%d"), "retries": 0}

    for attempt in range(2):
        state, seconds, requests = _run_message(thread_id, REQUEST.format(city=city), llm, checkpointer)
        error = state.get("error") or ""
        if not error or not any(code in error for code in RETRYABLE) or attempt == 1:
            break
        result["retries"] += 1
        checkpointer, thread_id = new_checkpointer(), uuid.uuid4().hex
    result["trip"] = {"error": error or None, "seconds": seconds, "transitous_requests": requests, "llm_calls": state.get("llm_calls"), **plan_metrics(state)}

    plan = _plan(state)
    if plan is None or error:
        return result
    places = {chosen.place.place_id: chosen.place for chosen in state["discovery"].attractions}
    visited = [stop.place_id for day in plan.schedule.days for stop in day.stops if stop.place_id in places]
    target = max(visited, key=lambda place_id: places[place_id].page_views or 0)
    after, seconds, requests = _run_message(thread_id, CHANGE.format(name=places[target].name), llm, checkpointer)
    result["change"] = {
        "removed_name": places[target].name,
        "error": after.get("error"),
        "seconds": seconds,
        "transitous_requests": requests,
        "llm_calls": after.get("llm_calls"),
        **change_metrics(state, after, target),
    }
    return result


def load_runs() -> list[dict]:
    return json.loads(RUNS_FILE.read_text(encoding="utf-8")) if RUNS_FILE.exists() else []


def run() -> None:
    runs = load_runs()
    done = {r["city"] for r in runs}
    llm = GeminiLLM()
    for city in CITIES:
        if city in done:
            continue
        left = get_route.DAILY_PLAN_LIMIT - transitous_used()
        if left < MIN_TRANSITOUS_LEFT:
            print(f"Stopping: only {left} Transitous requests are left today. Run again tomorrow.")
            return
        print(f"{city}...", flush=True)
        result = run_city(city, llm)
        runs.append(result)
        RUNS_FILE.write_text(json.dumps(runs, indent=1, ensure_ascii=False), encoding="utf-8")
        trip = result["trip"]
        print(f"  trip: plan={trip.get('has_plan')} approved={trip.get('approved')} error={trip['error']} {trip['seconds']}s", flush=True)
        if "change" in result:
            change = result["change"]
            print(f"  change: removed={change['removed']} others_unchanged={change['others_unchanged']} approved={change['approved']}", flush=True)
    print("All cities are done.")


def _share(values: list[bool]) -> str:
    return f"{sum(values)}/{len(values)}" if values else "n/a"


def report() -> None:
    runs = load_runs()
    trips = [r["trip"] for r in runs]
    planned = [t for t in trips if t.get("has_plan")]
    changes = [r["change"] for r in runs if "change" in r]
    hop_totals = {key: sum(t["hops"][key] for t in planned) for key in planned[0]["hops"]} if planned else {}
    all_hops = sum(hop_totals.values())
    errors = [e for t in planned for e in t["estimate_minus_real_minutes"]]

    lines = [
        "# Evaluation results",
        "",
        f"{len(runs)} of 10 cities, one run each. Protocol: SPEC.md section 7. Generated by `python -m evaluation.run_eval --report`.",
        "",
        "## Summary",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| Trips with a plan | {_share([t.get('has_plan', False) for t in trips])} |",
        f"| Invented stops | {sum(t['invented_stops'] for t in planned)} |",
        f"| Taxi rule breaks | {sum(t['taxi_rule_breaks'] for t in planned)} |",
        f"| Plans passing `check_plan` | {_share([t['check_passed'] for t in planned])} |",
        f"| Plans approved by the Reviewer | {_share([t['approved'] for t in planned])} |",
        f"| Plans sent back at least once | {_share([t['send_backs'] > 0 for t in planned])} |",
        f"| Visits after dinner | {sum(t['visits_after_dinner'] for t in planned)} |",
        f"| Meals missing | {sum(t['meals_missing'] for t in planned)} |",
        f"| Hops with real public transport | {hop_totals.get('public_transport', 0)}/{all_hops} |" if all_hops else "| Hops | n/a |",
        f"| Hops with a taxi backup | {hop_totals.get('taxi_backup', 0)}/{all_hops} |" if all_hops else "| Hops | n/a |",
        f"| Hops where the route check failed | {hop_totals.get('check_failed', 0)}/{all_hops} |" if all_hops else "| Hops | n/a |",
        f"| Solver estimate minus real time, median | {statistics.median(errors):+.1f} min |" if errors else "| Estimate error | n/a |",
        f"| Solver estimate, mean absolute error | {statistics.mean(abs(e) for e in errors):.1f} min over {len(errors)} hops |" if errors else "| Estimate error | n/a |",
        f"| Changes that removed the place | {_share([c['removed'] for c in changes if c['has_plan']])} |",
        f"| Changes that kept every other stop | {_share([c['others_unchanged'] for c in changes if c['has_plan']])} |",
        f"| Changes that filled the gap | {_share([bool(c['added']) for c in changes if c['has_plan']])} |",
        f"| Changed plans approved | {_share([c['approved'] for c in changes if c['has_plan']])} |",
        f"| Retries after an outage | {sum(r['retries'] for r in runs)} |",
        "",
        "## Per city",
        "",
        "| City | Plan | Approved | Revisions | Attractions | Public transport hops | Estimated cost (EUR) | LLM calls | Transitous requests | Time (s) | Change kept others | Notes |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in runs:
        t, c = r["trip"], r.get("change", {})
        hops = sum(t["hops"].values()) if t.get("has_plan") else 0
        notes = "; ".join(filter(None, [t.get("error") and "trip failed", c.get("error") and "change failed", r["retries"] and f"{r['retries']} retry"]))
        lines.append(
            f"| {r['city']} | {'yes' if t.get('has_plan') else 'no'} | {'yes' if t.get('approved') else 'no'} | {t.get('send_backs', '')} | "
            f"{t.get('attractions', '')} | {t['hops']['public_transport'] if hops else 0}/{hops} | {t.get('estimated_cost', '')} | "
            f"{t.get('llm_calls')} | {t['transitous_requests']} | {t['seconds']} | {'yes' if c.get('others_unchanged') else 'no' if c else 'n/a'} | {notes} |"
        )
    lines += [
        "",
        "Costs are the Reviewer's estimates from the LLM's memory, not real prices. Opening hours were not checked.",
        "One run per city is one result, not a success rate for that city.",
    ]
    REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {REPORT_FILE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", action="store_true")
    report() if parser.parse_args().report else run()
