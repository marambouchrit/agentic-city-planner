"""The LangGraph graph. Agents share data through the typed TripState.

The Orchestrator agent decides what a message means, in the interpret node. Routing between the
other agents is plain code, so the LLM can't send the work anywhere that isn't allowed.
"""

import math
from datetime import date
import operator
from typing import Annotated, Callable, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph

from city_planner.agents import discovery as discovery_agent
from city_planner.agents import planner as planner_agent
from city_planner.agents import reviewer as reviewer_agent
from city_planner.agents.base import CallBudget, TraceEvent, run_agent
from city_planner.agents.discovery import DiscoveryOutput, build_discovery_agent
from city_planner.agents.orchestrator import TurnDecision, build_orchestrator_agent, orchestrator_task
from city_planner.agents.planner import PlannerOutput, build_planner_agent, planner_task
from city_planner.agents.reviewer import ReviewOutput, build_reviewer_agent, feedback_for_planner, reviewer_task
from city_planner.llm import LLM
from city_planner.schemas import TripRequest

DEFAULT_ACTIVITIES_PER_DAY = 3
EXTRA_PLACES_FACTOR = 1.5  # some spare places, so the Planner has real choices
RESTAURANTS_PER_DAY = 3  # 2 meals, plus spares so a meal can stay near the day's stops
MAX_SEND_BACKS = 2
RECURSION_LIMIT = 50  # enough for every round of Planner and Reviewer, with the routing between them
ROUTES_FROM_ORCHESTRATOR = ("discovery", "planner", "reviewer")
SAVED_TYPES = [
    ("city_planner.agents.base", "TraceEvent"),
    ("city_planner.agents.discovery", "DiscoveryOutput"),
    ("city_planner.agents.planner", "PlannerOutput"),
    ("city_planner.agents.reviewer", "ReviewOutput"),
    ("city_planner.schemas", "TripRequest"),
]


def new_checkpointer() -> MemorySaver:
    """Keeps trips in memory, so a restart loses them. LangGraph only reloads the types named here."""
    return MemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=SAVED_TYPES))


class TripState(TypedDict, total=False):
    message: str | None
    reply: str | None
    stop: bool  # the message needs no planning, such as a question back to the traveler
    search_request: str  # something to add to the places already found
    trip: TripRequest
    discovery: DiscoveryOutput | None
    planner: PlannerOutput | None
    review: ReviewOutput | None
    # The best plan so far, with its review. A revision that gets worse doesn't replace it.
    final_plan: PlannerOutput | None
    final_review: ReviewOutput | None
    # The plan from before this message. It stays available if a change fails.
    previous_plan: PlannerOutput | None
    previous_review: ReviewOutput | None
    previous_trip: TripRequest | None
    earlier_discovery: DiscoveryOutput | None
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


def search_task(trip: TripRequest, request: str) -> str:
    return "\n".join(
        [
            f"The traveler has a plan for {trip.city} and wants to add: {request}",
            "Find only this, 1 to 3 places. To search for food, first verify a landmark in the right area, then search near it.",
        ]
    )


def _merge(earlier: DiscoveryOutput | None, new: DiscoveryOutput) -> DiscoveryOutput:
    """Adds what a later search found to the places already known."""
    if earlier is None:
        return new

    def combine(old: list, added: list) -> list:
        return list({chosen.place.place_id: chosen for chosen in old + added}.values())

    return new.model_copy(
        update={
            "attractions": combine(earlier.attractions, new.attractions),
            "restaurants": combine(earlier.restaurants, new.restaurants),
            "notes": " ".join(filter(None, (earlier.notes, new.notes))),
            "rejected": earlier.rejected + new.rejected,
            "unavailable": earlier.unavailable + new.unavailable,
        }
    )


def _current_trip(state: TripState) -> TripRequest | None:
    """The request behind the plan on show. After a failed change, that is the one from before the change."""
    if state.get("final_plan") is None and state.get("previous_trip") is not None:
        return state["previous_trip"]
    return state.get("trip")


def _turn_update(state: TripState, decision: TurnDecision) -> dict:
    """What this message changes in the shared state. Only a new or changed trip clears the plan."""
    update: dict = {"reply": decision.reply}
    if decision.action in ("ask", "out_of_scope"):
        return {**update, "stop": True}

    update.update(
        trip=decision.trip,
        planner=None,
        review=None,
        final_plan=None,
        final_review=None,
        previous_plan=state.get("final_plan") or state.get("previous_plan"),
        previous_review=state.get("final_review") or state.get("previous_review"),
        previous_trip=_current_trip(state),
        send_backs=0,
        feedback="",
        notes=[],
    )
    if decision.action == "plan_trip":
        return {**update, "discovery": None, "earlier_discovery": None, "prices": {}, "previous_plan": None, "previous_review": None, "previous_trip": None}
    return {**update, "search_request": decision.add_request}


def _attraction_count(plan: PlannerOutput) -> int:
    return sum(stop.slot == "activity" for day in plan.schedule.days for stop in day.stops)


def _rank(plan: PlannerOutput, review: ReviewOutput) -> tuple:
    """Lower is better: fewest rule errors, least over budget, approved, most attractions."""
    errors = sum(issue.severity == "error" for issue in review.check.issues)
    overage = -review.budget.difference if review.budget.over_budget else 0
    return errors, overage, review.verdict != "approve", -_attraction_count(plan)


def _last_choices(plan: PlannerOutput | None) -> str:
    return plan.choices if plan else ""


def build_graph(llm: LLM, budget: CallBudget, checkpointer=None, on_progress: Callable[[str], None] | None = None):
    def starting(step: str) -> None:
        if on_progress:
            on_progress(step)

    def interpret_node(state: TripState) -> dict:
        message = state.get("message")
        if not message:
            return {}
        starting("interpret")
        plan = state.get("final_plan") or state.get("previous_plan")
        trip = _current_trip(state)
        agent = build_orchestrator_agent(trip, plan)
        run = run_agent(agent, orchestrator_task(message, trip, plan, date.today()), llm, budget)
        update: dict = {
            "message": None,
            "stop": False,
            "search_request": "",
            "reply": None,
            "trace": run.trace,
            "llm_calls": budget.used,
            "error": run.error,
        }
        if run.output is not None:
            update.update(_turn_update(state, run.output))
        return update

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
            update.update(discovery=None, earlier_discovery=state.get("discovery"))
        return update

    def route(state: TripState) -> str:
        if state.get("error") or state.get("stop"):
            return END
        if state.get("search_request"):
            return "discovery"
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
        starting("discovery")
        trip, search = state["trip"], state.get("search_request")
        task = search_task(trip, search) if search else discovery_task(trip, state.get("feedback", ""))
        run = run_agent(build_discovery_agent(), task, llm, budget)
        update: dict = {"search_request": "", "trace": run.trace, "llm_calls": budget.used, "error": run.error}
        if run.output is None:
            return update if search else {**update, "discovery": None}
        earlier = state.get("discovery") if search else state.get("earlier_discovery")
        update["discovery"] = _merge(earlier, run.output)
        if search:
            # What the traveler asked for must be in the plan if it fits.
            found = [chosen.place.place_id for chosen in run.output.attractions]
            update["trip"] = trip.model_copy(update={"required": sorted(set(trip.required) | set(found))})
        return update

    def planner_node(state: TripState) -> dict:
        starting("planner")
        trip, discovery = state["trip"], state["discovery"]
        run = run_agent(build_planner_agent(trip, discovery), planner_task(trip, discovery, state.get("feedback", "")), llm, budget)
        update: dict = {"planner": run.output, "trace": run.trace, "llm_calls": budget.used, "error": run.error}
        previous = state.get("previous_plan")
        if run.output is not None and trip.locked and previous is not None:
            if _attraction_count(run.output) < _attraction_count(previous):
                note = "No other verified place could fill the gap. Ask for something specific to add it."
                update["notes"] = list(dict.fromkeys(state.get("notes", []) + [note]))
        return update

    def reviewer_node(state: TripState) -> dict:
        starting("reviewer")
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
    graph.add_node("interpret", interpret_node)
    graph.add_node("orchestrator", orchestrator)
    graph.add_node("discovery", discovery_node)
    graph.add_node("planner", planner_node)
    graph.add_node("reviewer", reviewer_node)
    graph.add_edge(START, "interpret")
    graph.add_edge("interpret", "orchestrator")
    graph.add_conditional_edges("orchestrator", route, {**{node: node for node in ROUTES_FROM_ORCHESTRATOR}, END: END})
    for node in ROUTES_FROM_ORCHESTRATOR:
        graph.add_edge(node, "orchestrator")
    return graph.compile(checkpointer=checkpointer)


def plan_trip(trip: TripRequest, llm: LLM) -> TripState:
    """Plans a request that is already understood, with no Orchestrator call."""
    return build_graph(llm, CallBudget()).invoke({"trip": trip, "trace": [], "llm_calls": 0}, {"recursion_limit": RECURSION_LIMIT})


def handle_message(
    thread_id: str, message: str, llm: LLM, checkpointer, on_progress: Callable[[str], None] | None = None
) -> TripState:
    """Runs one traveler message. The checkpointer remembers the trip, and each message gets its own LLM call limit."""
    graph = build_graph(llm, CallBudget(), checkpointer, on_progress)
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}
    return graph.invoke({"message": message}, config)
