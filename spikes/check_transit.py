"""Does Transitous return real lines and stops in very different cities?

No key needed. Uses 1 route request per city, against a shared volunteer service,
so keep runs small. Usage policy: https://transitous.org/api/

    python -m spikes.check_transit
"""

from datetime import datetime, timedelta, timezone

import httpx

from spikes.common import save_report

PLAN_URL = "https://api.transitous.org/api/v5/plan"
USER_AGENT = "agentic-city-planner/0.1 (+https://github.com/marambouchrit)"
TIMEOUT_SECONDS = 60.0

# (city, UTC offset in hours, origin, destination): big metro, thin transit data, small town.
TRIPS = [
    ("Paris", 1, ("Louvre", 48.8606, 2.3376), ("Eiffel Tower", 48.8584, 2.2945)),
    ("Tunis", 1, ("Bardo Museum", 36.8092, 10.1344), ("Medina of Tunis", 36.7975, 10.1714)),
    ("Luang Prabang", 7, ("Royal Palace", 19.8910, 102.1340), ("Wat Xieng Thong", 19.8975, 102.1425)),
]


def next_morning_utc(utc_offset_hours: int, now: datetime | None = None) -> str:
    """10:00 local time, two days ahead, so the route isn't judged on a night-time timetable."""
    offset = timezone(timedelta(hours=utc_offset_hours))
    local = (now or datetime.now(timezone.utc)).astimezone(offset) + timedelta(days=2)
    departure = local.replace(hour=10, minute=0, second=0, microsecond=0)
    return departure.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def summarize_plan(body: dict) -> dict:
    itineraries = body.get("itineraries") or []
    if not itineraries:
        return {"found": False, "lines": []}

    best = itineraries[0]
    lines = [
        {
            "mode": leg.get("mode"),
            "line": leg.get("displayName") or leg.get("routeShortName"),
            "from": (leg.get("from") or {}).get("name"),
            "to": (leg.get("to") or {}).get("name"),
        }
        for leg in best.get("legs", [])
        if leg.get("mode") != "WALK"
    ]
    return {
        "found": True,
        "transit_used": bool(lines),
        "minutes": round(best.get("duration", 0) / 60),
        "transfers": best.get("transfers"),
        "lines": lines,
    }


def check_trip(client: httpx.Client, trip: tuple) -> dict:
    city, utc_offset, origin, destination = trip
    report = {"city": city, "from": origin[0], "to": destination[0]}
    try:
        response = client.get(
            PLAN_URL,
            params={
                "fromPlace": f"{origin[1]},{origin[2]}",
                "toPlace": f"{destination[1]},{destination[2]}",
                "time": next_morning_utc(utc_offset),
            },
        )
        response.raise_for_status()
        report.update(summarize_plan(response.json()))
    except (httpx.HTTPError, ValueError) as exc:
        report["error"] = str(exc)
    return report


def main() -> None:
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS) as client:
        reports = [check_trip(client, trip) for trip in TRIPS]

    for r in reports:
        print(f"\n{r['city']}: {r['from']} -> {r['to']}")
        if "error" in r:
            print(f"  ERROR: {r['error']}")
            continue
        print(f"  found={r['found']}  transit_used={r.get('transit_used', False)}  minutes={r.get('minutes')}")
        for line in r["lines"]:
            print(f"  {line['mode']} {line['line']}: {line['from']} -> {line['to']}")

    print(f"\nSaved to {save_report('transit', {'trips': reports})}")


if __name__ == "__main__":
    main()
