"""Discovery agent: finds attractions and restaurants that fit the request, and verifies them.

The LLM proposes names and chooses the places. It only ever answers with place IDs,
and code turns those IDs back into the real data the tools returned.
"""

from typing import Callable

from pydantic import BaseModel, Field

from city_planner.agents.base import AgentConfig, Tool
from city_planner.schemas import Place, RestaurantResult, VerifyResult
from city_planner.tools.find_restaurants import find_restaurants
from city_planner.tools.verify_place import verify_place

MAX_STEPS = 8
MAX_VERIFY_CALLS = 25
MAX_RESTAURANT_CALLS = 3  # Foursquare's free calls are limited

INSTRUCTIONS = """You are the Discovery agent of a city trip planner.
Your one job: find real places that fit the traveler's request.

You decide what to look for, which tools to use, in what order, and when you have enough.
- Propose places from your own knowledge, then check each one with verify_place. You may check several in one turn.
- If a place is rejected, decide whether to try another name, try another place, or drop it.
- "unavailable" means the check failed, not that the place is fake. Never report it as verified.
- Each check should be a different place. If a well-known place is rejected, try its full official English name once.
- Use find_restaurants only if the traveler asks about food or meals, or asks for a full trip. A request only about museums needs no restaurant search.
- If the traveler has a food preference, pass it in the food argument of find_restaurants.
- Use the categories in the restaurant results to choose places that fit the traveler. Some results are cafes or bakeries. If no category shows that a place meets the preference, do not claim it does. Say so in notes.
- Choose places that match the request. Being verified is not enough.
- Only use place IDs that a tool returned to you. Never invent a place, an ID or a fact.
- Each reason must only use the traveler's request and what the tools returned, such as the description, categories and page views. Do not add facts about a place from your memory, such as its famous dishes or collections.
- When you are done, call finish. If you found nothing suitable, say so in notes."""

FINISH_DESCRIPTION = "Give your final choice of places, using place IDs returned by your tools."


class VerifyArgs(BaseModel):
    name: str = Field(description="The place's name, for example 'Belem Tower'.")
    city: str = Field(description="The city the place is in.")


class RestaurantArgs(BaseModel):
    near_place_id: str = Field(description="ID of a verified attraction to search around.")
    food: str = Field(default="", description="Optional type of food, for example 'vegetarian'.")


class Pick(BaseModel):
    place_id: str
    reason: str = Field(description="Why this fits the request, in one short sentence, using only tool results.")


class DiscoveryAnswer(BaseModel):
    city: str = Field(description="The city of this request.")
    attractions: list[Pick]
    restaurants: list[Pick] = Field(default_factory=list)
    notes: str = Field(default="", description="Anything the traveler should know, such as what you could not find.")


class ChosenPlace(BaseModel):
    place: Place
    reason: str  # the LLM's opinion; all the other data comes from tools


class DiscoveryOutput(BaseModel):
    city: str
    attractions: list[ChosenPlace]
    restaurants: list[ChosenPlace]
    notes: str
    # Written by code, so the LLM can't hide what failed.
    rejected: list[str]
    unavailable: list[str]


def _same_city(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def build_discovery_agent(
    verify: Callable[[str, str], VerifyResult] = verify_place,
    restaurants: Callable[..., RestaurantResult] = find_restaurants,
) -> AgentConfig:
    """Each trip gets a new agent, with its own record of what the tools really returned."""
    known: dict[str, Place] = {}
    place_city: dict[str, str] = {}
    rejected: list[str] = []
    unavailable: list[str] = []
    calls = {"verify": 0, "restaurants": 0}

    def run_verify(args: VerifyArgs) -> dict:
        calls["verify"] += 1
        if calls["verify"] > MAX_VERIFY_CALLS:
            raise ValueError(f"Limit of {MAX_VERIFY_CALLS} checks reached. Finish with what you have.")
        result = verify(args.name, args.city)
        if result.status == "rejected":
            rejected.append(f"{args.name} ({args.city})")
        elif result.status == "unavailable":
            unavailable.append(f"{args.name} ({args.city})")
        answer = {"status": result.status, "reason": result.reason}
        if result.place:
            known[result.place.place_id] = result.place
            place_city[result.place.place_id] = args.city
            answer.update(
                place_id=result.place.place_id,
                name=result.place.name,
                description=result.place.description,
                monthly_page_views=result.place.page_views,
                missing=result.place.missing,
            )
        return answer

    def run_restaurants(args: RestaurantArgs) -> dict:
        # The search point comes from a verified place, so the LLM never types coordinates.
        near = known.get(args.near_place_id)
        if near is None or near.kind != "attraction":
            raise ValueError("near_place_id must be the ID of an attraction you verified.")
        if near.latitude is None or near.longitude is None:
            raise ValueError(f"{near.name} has no coordinates. Search around another place.")
        calls["restaurants"] += 1
        if calls["restaurants"] > MAX_RESTAURANT_CALLS:
            raise ValueError(f"Limit of {MAX_RESTAURANT_CALLS} restaurant searches reached. Finish with what you have.")
        result = restaurants(near.latitude, near.longitude, food=args.food)
        if result.status == "unavailable" and result.note:
            unavailable.append(f"Restaurants: {result.note}")
        for place in result.restaurants:
            known[place.place_id] = place
            place_city[place.place_id] = place_city[near.place_id]
        return {
            "status": result.status,
            "note": result.note,
            "restaurants": [
                {"place_id": p.place_id, "name": p.name, "categories": p.description, "missing": p.missing}
                for p in result.restaurants
            ],
        }

    def resolve(picks: list[Pick], kind: str, city: str) -> list[ChosenPlace]:
        chosen: dict[str, ChosenPlace] = {}
        for pick in picks:
            place = known.get(pick.place_id)
            if place is None:
                raise ValueError(f"{pick.place_id} was not returned by any tool.")
            if place.kind != kind:
                raise ValueError(f"{place.name} is in the wrong list: it is a {place.kind}.")
            if not _same_city(place_city[pick.place_id], city):
                raise ValueError(f"{place.name} was found for {place_city[pick.place_id]}, not {city}.")
            chosen[pick.place_id] = ChosenPlace(place=place, reason=pick.reason)
        return list(chosen.values())

    def finalize(answer: DiscoveryAnswer) -> DiscoveryOutput:
        return DiscoveryOutput(
            city=answer.city,
            attractions=resolve(answer.attractions, "attraction", answer.city),
            restaurants=resolve(answer.restaurants, "restaurant", answer.city),
            notes=answer.notes,
            rejected=list(rejected),
            unavailable=list(unavailable),
        )

    return AgentConfig(
        name="Discovery",
        instructions=INSTRUCTIONS,
        tools=[
            Tool("verify_place", "Check on Wikipedia whether a place really exists in a city.", VerifyArgs, run_verify),
            Tool(
                "find_restaurants",
                "Find real restaurants, cafes and similar places near a verified attraction, with their categories.",
                RestaurantArgs,
                run_restaurants,
            ),
        ],
        finish_description=FINISH_DESCRIPTION,
        finish_model=DiscoveryAnswer,
        finalize=finalize,
        max_steps=MAX_STEPS,
    )
