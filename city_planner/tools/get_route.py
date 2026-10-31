"""get_route: a real public transit route from Transitous, or a clearly labelled estimate."""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from city_planner.http import new_client
from city_planner.schemas import Route, TransitLine
from city_planner.tools.distance import estimated_taxi_minutes, estimated_walking_minutes, haversine_km

PLAN_URL = "https://api.transitous.org/api/v5/plan"
# The Transitous policy flags more than 720 /plan requests a day. We stop before that.
DAILY_PLAN_LIMIT = 700
USAGE_FILE = Path(__file__).resolve().parents[2] / ".cache" / "transitous_usage.json"
MAX_WALKING_ESTIMATE_MINUTES = 30

_cache: dict[tuple, Route] = {}


def _take_daily_request() -> bool:
    """Counts requests in a file, so the limit also holds across restarts."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        usage = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
        count = usage["count"] if usage["date"] == today else 0
    except (OSError, ValueError, KeyError, TypeError):
        count = 0
    if count >= DAILY_PLAN_LIMIT:
        return False
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    USAGE_FILE.write_text(json.dumps({"date": today, "count": count + 1}), encoding="utf-8")
    return True


def _estimate(km: float, reason: str) -> Route:
    walking = round(estimated_walking_minutes(km), 1)
    if walking <= MAX_WALKING_ESTIMATE_MINUTES:
        mode, minutes = "walk_estimate", walking
    else:
        mode, minutes = "taxi_estimate", round(estimated_taxi_minutes(km), 1)
    return Route(
        mode=mode,
        minutes=minutes,
        source="estimate",
        distance_km=round(km, 2),
        walking_estimate_minutes=walking,
        note=f"{reason} Estimated from straight-line distance, not a real route.",
    )


def _parse_plan(body: dict, km: float) -> Route | None:
    itineraries = body.get("itineraries") or []
    if not itineraries:
        return None
    best = itineraries[0]
    if best.get("duration") is None:
        return None
    lines = [
        TransitLine(
            mode=leg.get("mode"),
            line=leg.get("displayName") or leg.get("routeShortName"),
            from_stop=(leg.get("from") or {}).get("name"),
            to_stop=(leg.get("to") or {}).get("name"),
        )
        for leg in best.get("legs", [])
        if leg.get("mode") != "WALK"
    ]
    return Route(
        mode="transit" if lines else "walk",
        minutes=round(best["duration"] / 60, 1),
        source="transitous",
        transfers=best.get("transfers"),
        lines=lines,
        distance_km=round(km, 2),
        walking_estimate_minutes=round(estimated_walking_minutes(km), 1),
    )


def get_route(
    from_lat: float | None,
    from_lon: float | None,
    to_lat: float | None,
    to_lon: float | None,
    departure_utc: str | None = None,
    client: httpx.Client | None = None,
) -> Route:
    """departure_utc looks like 2026-11-04T09:00:00Z. Without it, Transitous uses the current time."""
    if None in (from_lat, from_lon, to_lat, to_lon):
        return Route(mode=None, minutes=None, source="none", note="A place has no coordinates, so no route was found.")

    km = haversine_km(from_lat, from_lon, to_lat, to_lon)
    key = (round(from_lat, 4), round(from_lon, 4), round(to_lat, 4), round(to_lon, 4), departure_utc)
    if key in _cache:
        return _cache[key]
    if not _take_daily_request():
        return _estimate(km, "The daily Transitous limit was reached.")

    params = {"fromPlace": f"{from_lat},{from_lon}", "toPlace": f"{to_lat},{to_lon}"}
    if departure_utc:
        params["time"] = departure_utc
    client = client or new_client()
    try:
        response = client.get(PLAN_URL, params=params)
        response.raise_for_status()
        route = _parse_plan(response.json(), km)
    except (httpx.HTTPError, ValueError, AttributeError, TypeError) as exc:
        # Failures aren't cached, so a later call can try again.
        return _estimate(km, f"Transitous could not be used: {type(exc).__name__}.")

    route = route or _estimate(km, "Transitous found no route.")
    _cache[key] = route
    return route
