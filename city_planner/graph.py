"""The LangGraph skeleton. Agents share data through the typed TripState.

The Orchestrator here is a temporary plain router, not yet an agent. It becomes an LLM
agent in Phase 5, and the Planner and Reviewer nodes join in Phases 3 and 4.
"""

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from city_planner.agents.base import CallBudget, TraceEvent, run_agent
from city_planner.agents.discovery import DiscoveryOutput, build_discovery_agent
from city_planner.llm import LLM


class TripState(TypedDict, total=False):
    request: str
    discovery: DiscoveryOutput | None
    trace: Annotated[list[TraceEvent], operator.add]
    llm_calls: int
    error: str | None


def _orchestrator(state: TripState) -> dict:
    return {}


def _route(state: TripState) -> str:
    if state.get("error") or state.get("discovery"):
        return END
    return "discovery"


def build_graph(llm: LLM, budget: CallBudget):
    def discovery_node(state: TripState) -> dict:
        run = run_agent(build_discovery_agent(), state["request"], llm, budget)
        return {"discovery": run.output, "trace": run.trace, "llm_calls": budget.used, "error": run.error}

    graph = StateGraph(TripState)
    graph.add_node("orchestrator", _orchestrator)
    graph.add_node("discovery", discovery_node)
    graph.add_edge(START, "orchestrator")
    graph.add_conditional_edges("orchestrator", _route, {"discovery": "discovery", END: END})
    graph.add_edge("discovery", "orchestrator")
    return graph.compile()


def plan_trip(request: str, llm: LLM) -> TripState:
    return build_graph(llm, CallBudget()).invoke({"request": request, "trace": [], "llm_calls": 0})
