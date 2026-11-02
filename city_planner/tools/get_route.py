"""get_route: public transport and walking from Transitous, following SPEC.md section 1.

A taxi is only a labelled backup, when there is no public transport and no walk to offer.
"""

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from timezonefinder import TimezoneFinder

from city_planner.http import new_client
from city_planner.schemas import Hop, PublicTransportOption, TaxiBackup, TransitLine, WalkOption
from city_planner.tools.distance import estimated_taxi_minutes, estimated_walking_minutes, haversine_km

PLAN_URL = "https://api.transitous.org/api/v5/plan"
# The Transitous policy flags more than 720 /plan requests a day. We stop before that.
DAILY_PLAN_LIMIT = 700
USAGE_FILE = Path(__file__).resolve().parents[2] / ".cache" / "transitous_usage.json"

MAX_WALK_OFFER_MINUTES = 20
# Real walks in 3 live tests took about 1.3 to 1.4 times the straight-line estimate.
WALK_DETOUR_FACTOR = 1.3
# Live tests had timetables 21 days ahead in 3 cities, but none 30 days ahead in Paris.
TIMETABLE_DAYS_AHEAD = 14
PUBLIC_TRANSPORT_MODES = {
    "BUS", "COACH", "TRAM", "SUBWAY", "METRO", "RAIL", "HIGHSPEED_RAIL", "LONG_DISTANCE", "NIGHT_RAIL",
    "REGIONAL_FAST_RAIL", "REGIONAL_RAIL", "SUBURBAN", "FERRY", "CABLE_CAR", "FUNICULAR", "AREAL_LIFT",
}  # fmt: skip

_cache: dict[tuple, tuple[PublicTransportOption | None, float | None]] = {}
_timezones = TimezoneFinder()


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


def _next_weekday(today: date, weekday: int) -> date:
    """The given weekday, 7 to 13 days from today, which is inside the timetable data."""
    return today + timedelta(days=(weekday - today.weekday()) % 7 + 7)


def _lookup_time(departure_local: datetime | None, zone: ZoneInfo, today: date) -> tuple[datetime, str | None]:
    """Returns the local time to ask about, on a half hour so that nearby times share a cache entry."""
    note = None
    if departure_local is None:
        departure_local = datetime.combine(_next_weekday(today, 1), datetime.min.time()).replace(hour=10)
        note = "No date was given, so a normal weekday was checked."
    elif not 0 <= (departure_local.date() - today).days <= TIMETABLE_DAYS_AHEAD:
        note = f"Timetables for {departure_local.date()} aren't available, so the same weekday was checked instead."
        departure_local = datetime.combine(_next_weekday(today, departure_local.weekday()), departure_local.time())
    rounded = departure_local.replace(minute=departure_local.minute // 30 * 30, second=0, microsecond=0, tzinfo=None)
    return rounded.replace(tzinfo=zone), note


def _public_transport(itinerary: dict) -> PublicTransportOption | None:
    legs = itinerary.get("legs") or []
    rides = [leg for leg in legs if leg.get("mode") != "WALK"]
    if not rides or itinerary.get("duration") is None or any(leg.get("mode") not in PUBLIC_TRANSPORT_MODES for leg in rides):
        return None
    walks = [leg.get("duration") for leg in legs if leg.get("mode") == "WALK"]
    return PublicTransportOption(
        minutes=round(itinerary["duration"] / 60, 1),
        transfers=itinerary.get("transfers") or 0,
        lines=[
            TransitLine(
                mode=leg.get("mode"),
                line=leg.get("displayName") or leg.get("routeShortName"),
                from_stop=(leg.get("from") or {}).get("name"),
                to_stop=(leg.get("to") or {}).get("name"),
            )
            for leg in rides
        ],
        walking_minutes_inside=None if None in walks else round(sum(walks) / 60, 1),
    )


def _parse_plan(body: dict) -> tuple[PublicTransportOption | None, float | None]:
    """Returns the fastest public transport, with the fewest transfers on a tie, and the real walking minutes."""
    options = [option for option in map(_public_transport, body.get("itineraries") or []) if option]
    best = min(options, key=lambda option: (option.minutes, option.transfers), default=None)
    walks = [
        trip["duration"] / 60
        for trip in (body.get("direct") or []) + (body.get("itineraries") or [])
        if trip.get("duration") is not None and all(leg.get("mode") == "WALK" for leg in trip.get("legs") or [{}])
    ]
    return best, round(min(walks), 1) if walks else None


def _fetch(client: httpx.Client, params: dict) -> dict:
    """Tries twice when the service has a temporary failure. Raises if it can't be used."""
    for attempt in (1, 2):
        if not _take_daily_request():
            raise RuntimeError("The daily Transitous limit was reached.")
        try:
            response = client.get(PLAN_URL, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.TransportError:
            if attempt == 2:
                raise
        except httpx.HTTPStatusError as exc:
            if attempt == 2 or exc.response.status_code < 500:
                raise
    raise AssertionError("unreachable")


def _build_hop(
    public_transport: PublicTransportOption | None,
    real_walk: float | None,
    km: float,
    max_walk_minutes: int,
    check_failed: bool,
    notes: list[str],
) -> Hop:
    walk_minutes = real_walk if real_walk is not None else round(estimated_walking_minutes(km) * WALK_DETOUR_FACTOR, 1)
    walk = None
    if walk_minutes <= max_walk_minutes:
        walk = WalkOption(minutes=walk_minutes, is_estimate=real_walk is None)

    taxi = None
    if public_transport is None and walk is None:
        reason = "Public transport could not be checked" if check_failed else "No public transport route in our data"
        taxi = TaxiBackup(
            minutes=round(estimated_taxi_minutes(km), 1),
            reason=f"{reason}, and it is too far to walk. Taxi time is an estimate.",
        )
    return Hop(
        public_transport=public_transport,
        walk=walk,
        taxi_backup=taxi,
        check_failed=check_failed,
        distance_km=round(km, 2),
        note=" ".join(notes) or None,
    )


def get_route(
    from_lat: float | None,
    from_lon: float | None,
    to_lat: float | None,
    to_lon: float | None,
    departure_local: datetime | None = None,
    max_walk_minutes: int = MAX_WALK_OFFER_MINUTES,
    client: httpx.Client | None = None,
) -> Hop:
    """departure_local is the clock time in the city. The traveler can lower the walking limit, not raise it."""
    if None in (from_lat, from_lon, to_lat, to_lon):
        return Hop(note="A place has no coordinates, so no route was found.")

    km = haversine_km(from_lat, from_lon, to_lat, to_lon)
    walk_limit = min(max_walk_minutes, MAX_WALK_OFFER_MINUTES)
    zone_name = _timezones.timezone_at(lat=from_lat, lng=from_lon)
    if zone_name is None:
        return _build_hop(None, None, km, walk_limit, True, ["The city's time zone could not be found."])

    zone = ZoneInfo(zone_name)
    lookup, date_note = _lookup_time(departure_local, zone, datetime.now(zone).date())
    notes = [date_note] if date_note else []
    key = (round(from_lat, 4), round(from_lon, 4), round(to_lat, 4), round(to_lon, 4), lookup)
    if key in _cache:
        return _build_hop(*_cache[key], km, walk_limit, False, notes)

    params = {
        "fromPlace": f"{from_lat},{from_lon}",
        "toPlace": f"{to_lat},{to_lon}",
        "time": lookup.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        found = _parse_plan(_fetch(client or new_client(), params))
    except RuntimeError as exc:
        return _build_hop(None, None, km, walk_limit, True, [*notes, str(exc)])
    except (httpx.HTTPError, ValueError, AttributeError, TypeError) as exc:
        # Failures aren't cached, so a later call can try again.
        return _build_hop(None, None, km, walk_limit, True, [*notes, f"Transitous could not be used: {type(exc).__name__}."])

    _cache[key] = found
    return _build_hop(*found, km, walk_limit, False, notes)
