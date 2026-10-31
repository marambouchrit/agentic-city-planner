"""find_restaurants: real restaurants near a point, from Foursquare.

With no key, or when free calls run out, it returns no restaurants and says why.
It never invents any, and the schedule then uses a labelled meal break.
Any screen showing these results must say "Powered by Foursquare".
"""

import os

import httpx
from dotenv import load_dotenv

from city_planner.http import new_client
from city_planner.schemas import Place, RestaurantResult

SEARCH_URL = "https://places-api.foursquare.com/places/search"
API_VERSION = "2025-06-17"
# Asking only for these fields keeps calls in the Pro tier, which has the free allowance.
FIELDS = "fsq_place_id,name,latitude,longitude,categories,location"
MAX_RESULTS = 20

load_dotenv()


def _to_place(item: dict) -> Place:
    categories = [c["name"] for c in item.get("categories") or [] if c.get("name")]
    latitude, longitude = item.get("latitude"), item.get("longitude")
    return Place(
        place_id=f"foursquare:{item['fsq_place_id']}",
        name=item["name"],
        kind="restaurant",
        source="foursquare",
        latitude=latitude,
        longitude=longitude,
        description=", ".join(categories) or None,
        address=(item.get("location") or {}).get("formatted_address") or None,
        missing=[] if latitude is not None and longitude is not None else ["coordinates"],
    )


def find_restaurants(
    latitude: float,
    longitude: float,
    food: str = "",
    radius_meters: int = 1500,
    limit: int = 10,
    client: httpx.Client | None = None,
) -> RestaurantResult:
    """food is optional free text, such as "vegetarian" or "seafood"."""
    key = os.environ.get("FOURSQUARE_API_KEY", "").strip()
    if not key:
        return RestaurantResult(status="unavailable", note="No Foursquare key is set, so restaurants were not searched.")

    client = client or new_client()
    try:
        response = client.get(
            SEARCH_URL,
            headers={"Authorization": f"Bearer {key}", "X-Places-Api-Version": API_VERSION},
            params={
                "ll": f"{latitude},{longitude}",
                "radius": radius_meters,
                "query": f"{food} restaurant".strip(),
                "fields": FIELDS,
                "limit": max(1, min(limit, MAX_RESULTS)),
            },
        )
    except httpx.HTTPError as exc:
        return RestaurantResult(status="unavailable", note=f"Foursquare could not be reached: {type(exc).__name__}")

    if response.status_code != 200:
        return RestaurantResult(status="unavailable", note=f"Foursquare returned HTTP {response.status_code}.")

    try:
        items = response.json().get("results") or []
        # A result without an ID or a name can't be checked later, so it isn't used.
        restaurants = [_to_place(i) for i in items if i.get("fsq_place_id") and i.get("name")]
    except (ValueError, AttributeError, TypeError):
        return RestaurantResult(status="unavailable", note="Foursquare returned an unreadable response.")

    if not restaurants:
        return RestaurantResult(status="no_results", note="Foursquare found no restaurants in this area.")
    return RestaurantResult(status="ok", restaurants=restaurants)
