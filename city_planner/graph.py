"""The LangGraph skeleton. Agents share data through the typed TripState.

The Orchestrator here is a temporary plain router, not yet an agent. It becomes an LLM agent in Phase 5.
"""

import math
import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from city_planner.agents import discovery as discovery_agent
from city_planner.agents import planner as planner_agent
from city_planner.agents import reviewer as reviewer_agent
from city_planner.agents.base import CallBudget, TraceEvent, run_agent
from city_planner.agents.discovery import DiscoveryOutput, build_discovery_agent
from city_planner.agents.planner import PlannerOutput, build_planner_agent, planner_task
from city_planner.agents.reviewer import ReviewOutput, build_reviewer_agent, feedback_for_planner, reviewer_task
from city_planner.llm import LLM
from city_planner.schemas import TripRequest

DEFAULT_ACTIVITIES_PER_DAY = 3
EXTRA_PLACES_FACTOR = 1.5  # some spare places, so the Planner has real choices
RESTAURANTS_PER_DAY = 3  # 2 meals, plus spares so a meal can stay near the day's stops
MAX_SEND_BACKS = 2


class TripState(TypedDict, total=False):
    trip: TripRequest
    discovery: DiscoveryOutput | None
    planner: PlannerOutput | None
    review: ReviewOutput | None
    # The best plan so far, with its review. A revision that gets worse doesn't replace it.
    final_plan: PlannerOutput | None
    final_review: ReviewOutput | None
    prices: dict[str, float]
    send_backs: int
    feedback: str
    notes: list[str]
    trace: Annotated[list[TraceEvent], operator.add]
    llm_calls: int
    error: str | None


def discovery_task(trip: TripRequest, feedback: str = "") -> str:
    per_day = trip.max_activities_per_day or DEFAULT_ACTIVITIES_PER_DAY
    attractions = math.ceil(trip.days * per_day * EXTRA_PLACES_FACTOR)
    parts = [
        f"Find places for a full {trip.days}-day trip in {trip.city}.",
        f"Choose about {attractions} attractions, spread across different areas of the city.",
        f"Choose about {RESTAURANTS_PER_DAY * trip.days} restaurants, searching near attractions in different areas and keeping several from each search.",
    ]
    if trip.preferences:
        parts.append(f"Traveler's preferences: {trip.preferences}")
    if feedback:
        parts.append(feedback)
    return "\n".join(parts)


def _rank(plan: PlannerOutput, review: ReviewOutput) -> tuple:
    """Lower is better: fewest rule errors, least over budget, approved, most attractions."""
    errors = sum(issue.severity == "error" for issue in review.check.issues)
    overage = -review.budget.difference if review.budget.over_budget else 0
    attractions = sum(stop.slot == "activity" for day in plan.schedule.days for stop in day.stops)
    return errors, overage, review.verdict != "approve", -attractions


def _last_choices(plan: PlannerOutput | None) -> str:
    return plan.choices if plan else ""


def build_graph(llm: LLM, budget: CallBudget):
    def orchestrator(state: TripState) -> dict:
        """Starts the round the Reviewer asked for, if the send-back limit and the LLM calls allow it."""
        review = state.get("review")
        if state.get("error") or review is None or review.verdict != "send_back":
            return {}
        target = review.target
        needed = planner_agent.MAX_STEPS + reviewer_agent.MAX_STEPS
        if target == "discovery":
            needed += discovery_agent.MAX_STEPS
        if state.get("send_backs", 0) >= MAX_SEND_BACKS:
            return {"notes": state.get("notes", []) + [f"The limit of {MAX_SEND_BACKS} revisions was reached, so the problems below remain."]}
        if budget.remaining < needed:
            return {"notes": state.get("notes", []) + ["The trip's LLM call limit left no room for another revision, so the problems below remain."]}
        update: dict = {
            "planner": None,
            "review": None,
            "send_backs": state.get("send_backs", 0) + 1,
            "feedback": feedback_for_planner(review, _last_choices(state.get("planner"))),
        }
        if target == "discovery":
            update["discovery"] = None
        return update

    def route(state: TripState) -> str:
        if state.get("error"):
            return END
        discovery = state.get("discovery")
        if discovery is None:
            return "discovery"
        # With no verified attraction there is nothing to schedule, and nothing is invented.
        if not discovery.attractions:
            return END
        if state.get("planner") is None:
            return "planner"
        if state.get("review") is None:
            return "reviewer"
        return END

    def discovery_node(state: TripState) -> dict:
        task = discovery_task(state["trip"], state.get("feedback", ""))
        run = run_agent(build_discovery_agent(), task, llm, budget)
        return {"discovery": run.output, "trace": run.trace, "llm_calls": budget.used, "error": run.error}

    def planner_node(state: TripState) -> dict:
        trip, discovery = state["trip"], state["discovery"]
        run = run_agent(build_planner_agent(trip, discovery), planner_task(trip, discovery, state.get("feedback", "")), llm, budget)
        return {"planner": run.output, "trace": run.trace, "llm_calls": budget.used, "error": run.error}

    def reviewer_node(state: TripState) -> dict:
        trip, plan, discovery = state["trip"], state["planner"], state["discovery"]
        known_places = [chosen.place for chosen in discovery.attractions + discovery.restaurants]
        prices = state.get("prices", {})
        agent = build_reviewer_agent(trip, plan.schedule, known_places, prices)
        run = run_agent(agent, reviewer_task(trip, plan.schedule, prices), llm, budget)
        update: dict = {"review": run.output, "trace": run.trace, "llm_calls": budget.used, "error": run.error}
        if run.output is not None:
            update["prices"] = run.output.prices
            best_plan, best_review = state.get("final_plan"), state.get("final_review")
            if best_plan is None or _rank(plan, run.output) <= _rank(best_plan, best_review):
                update.update(final_plan=plan, final_review=run.output)
        return update

    graph = StateGraph(TripState)
    graph.add_node("orchestrator", orchestrator)
    graph.add_node("discovery", discovery_node)
    graph.add_node("planner", planner_node)
    graph.add_node("reviewer", reviewer_node)
    graph.add_edge(START, "orchestrator")
    graph.add_conditional_edges(
        "orchestrator", route, {"discovery": "discovery", "planner": "planner", "reviewer": "reviewer", END: END}
    )
    for node in ("discovery", "planner", "reviewer"):
        graph.add_edge(node, "orchestrator")
    return graph.compile()


def plan_trip(trip: TripRequest, llm: LLM) -> TripState:
    # 2 rounds of Planner and Reviewer visit each node twice more, plus the Orchestrator between them.
    return build_graph(llm, CallBudget()).invoke({"trip": trip, "trace": [], "llm_calls": 0}, {"recursion_limit": 50})
