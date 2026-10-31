"""check_plan: code checks the plan, including that every stop came from a tool result."""

from city_planner.schemas import MEAL_BREAK_NAME, Day, Place, PlanCheck, PlanIssue, Stop

MINUTES_PER_DAY = 24 * 60


def _minutes(clock: str) -> int:
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


def _check_source(stop: Stop, known: dict[str, Place]) -> str | None:
    """Returns the problem if this stop doesn't match a tool result exactly."""
    if stop.place_id is None:
        if stop.slot == "activity" or stop.name != MEAL_BREAK_NAME or stop.latitude is not None or stop.longitude is not None:
            return f"'{stop.name}' has no place ID, and only a plain meal break may have none."
        return None
    place = known.get(stop.place_id)
    if place is None:
        return f"'{stop.name}' did not come from a tool result."
    if (stop.name, stop.latitude, stop.longitude) != (place.name, place.latitude, place.longitude):
        return f"'{stop.name}' does not match the data for {stop.place_id}."
    if (stop.slot == "activity") != (place.kind == "attraction"):
        return f"'{stop.name}' is a {place.kind} but is in a {stop.slot} slot."
    return None


def check_plan(
    days: list[Day],
    known_places: list[Place],
    expected_days: int,
    max_walking_minutes: int | None = None,
) -> PlanCheck:
    """known_places must be the places that tools returned during this trip."""
    known = {place.place_id: place for place in known_places}
    issues: list[PlanIssue] = []
    seen_ids: set[str] = set()

    def add(severity: str, code: str, message: str, day_number: int | None = None) -> None:
        issues.append(PlanIssue(severity=severity, code=code, message=message, day_number=day_number))

    if [d.day_number for d in days] != list(range(1, expected_days + 1)):
        add("error", "wrong_days", f"The plan must have days 1 to {expected_days}, in order.")

    for day in days:
        if not any(stop.place_id for stop in day.stops):
            add("error", "empty_day", "This day has no real stops.", day.day_number)

        previous_end = None
        for stop in day.stops:
            problem = _check_source(stop, known)
            if problem:
                add("error", "unknown_place", problem, day.day_number)

            if stop.place_id:
                if stop.place_id in seen_ids:
                    add("error", "duplicate_place", f"'{stop.name}' appears more than once.", day.day_number)
                seen_ids.add(stop.place_id)

            start = _minutes(stop.start)
            if previous_end is not None and start < previous_end + (stop.travel_minutes or 0):
                add("error", "time_overlap", f"'{stop.name}' starts before the traveler can arrive.", day.day_number)
            if start + stop.duration_minutes > MINUTES_PER_DAY:
                add("error", "ends_after_midnight", f"'{stop.name}' ends after midnight.", day.day_number)
            previous_end = start + stop.duration_minutes

            if (
                max_walking_minutes is not None
                and stop.travel_mode in ("walk", "walk_estimate")
                and (stop.travel_minutes or 0) > max_walking_minutes
            ):
                add("error", "walk_too_long", f"The walk to '{stop.name}' is over {max_walking_minutes} minutes.", day.day_number)

            if stop.place_id is None:
                add("warning", "meal_break", f"The {stop.slot} has no verified restaurant.", day.day_number)
            elif stop.latitude is None or stop.longitude is None:
                add("warning", "missing_coordinates", f"'{stop.name}' has no coordinates.", day.day_number)
            if stop.travel_mode in ("walk_estimate", "taxi_estimate"):
                add("warning", "estimated_travel", f"Travel to '{stop.name}' is an estimate.", day.day_number)

        real_stops = [stop for stop in day.stops if stop.place_id]
        for stop in real_stops[1:]:
            if stop.travel_minutes is None or stop.travel_mode is None:
                add("warning", "missing_travel", f"Travel time to '{stop.name}' is unknown.", day.day_number)

    return PlanCheck(passed=not any(issue.severity == "error" for issue in issues), issues=issues)
