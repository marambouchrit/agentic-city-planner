"""The LangGraph skeleton. Agents share data through the typed TripState.

The Orchestrator here is a temporary plain router, not yet an agent. It becomes an LLM
agent in Phase 5, and the Reviewer node joins in Phase 4.
"""

import math
import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from city_planner.agents.base import CallBudget, TraceEvent, run_agent
from city_planner.agents.discovery import DiscoveryOutput, build_discovery_agent
from city_planner.agents.planner import PlannerOutput, build_planner_agent, planner_task
from city_planner.llm import LLM
from city_planner.schemas import TripRequest

DEFAULT_ACTIVITIES_PER_DAY = 3
EXTRA_PLACES_FACTOR = 1.5  # some spare places, so the Planner has real choices
RESTAURANTS_PER_DAY = 3  # 2 meals, plus spares so a meal can stay near the day's stops


class TripState(TypedDict, total=False):
    trip: TripRequest
    discovery: DiscoveryOutput | None
    planner: PlannerOutput | None
    trace: Annotated[list[TraceEvent], operator.add]
    llm_calls: int
    error: str | None


def discovery_task(trip: TripRequest) -> str:
    per_day = trip.max_activities_per_day or DEFAULT_ACTIVITIES_PER_DAY
    attractions = math.ceil(trip.days * per_day * EXTRA_PLACES_FACTOR)
    parts = [
        f"Find places for a full {trip.days}-day trip in {trip.city}.",
        f"Choose about {attractions} attractions, spread across different areas of the city.",
        f"Choose about {RESTAURANTS_PER_DAY * trip.days} restaurants, searching near attractions in different areas and keeping several from each search.",
    ]
    if trip.preferences:
        parts.append(f"Traveler's preferences: {trip.preferences}")
    return "\n".join(parts)


def _orchestrator(state: TripState) -> dict:
    return {}


def _route(state: TripState) -> str:
    if state.get("error") or state.get("planner"):
        return END
    discovery = state.get("discovery")
    if discovery is None:
        return "discovery"
    # With no verified attraction there is nothing to schedule, and nothing is invented.
    return "planner" if discovery.attractions else END


def build_graph(llm: LLM, budget: CallBudget):
    def discovery_node(state: TripState) -> dict:
        run = run_agent(build_discovery_agent(), discovery_task(state["trip"]), llm, budget)
        return {"discovery": run.output, "trace": run.trace, "llm_calls": budget.used, "error": run.error}

    def planner_node(state: TripState) -> dict:
        trip, discovery = state["trip"], state["discovery"]
        run = run_agent(build_planner_agent(trip, discovery), planner_task(trip, discovery), llm, budget)
        return {"planner": run.output, "trace": run.trace, "llm_calls": budget.used, "error": run.error}

    graph = StateGraph(TripState)
    graph.add_node("orchestrator", _orchestrator)
    graph.add_node("discovery", discovery_node)
    graph.add_node("planner", planner_node)
    graph.add_edge(START, "orchestrator")
    graph.add_conditional_edges("orchestrator", _route, {"discovery": "discovery", "planner": "planner", END: END})
    graph.add_edge("discovery", "orchestrator")
    graph.add_edge("planner", "orchestrator")
    return graph.compile()


def plan_trip(trip: TripRequest, llm: LLM) -> TripState:
    return build_graph(llm, CallBudget()).invoke({"trip": trip, "trace": [], "llm_calls": 0})
