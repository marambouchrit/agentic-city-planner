"""Data types shared by the tools."""

from typing import Literal

from pydantic import BaseModel, Field

# Modes ending in "_estimate" are calculated from straight-line distance, not from a real route.
TravelMode = Literal["transit", "walk", "walk_estimate", "taxi_estimate"]
ToolStatus = Literal["ok", "no_results", "unavailable"]

MEAL_BREAK_NAME = "Meal break (no verified restaurant)"


class Place(BaseModel):
    """A real place returned by a data source. Only tools create these."""

    place_id: str
    name: str
    kind: Literal["attraction", "restaurant"]
    source: Literal["wikipedia", "foursquare"]
    source_url: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    description: str | None = None
    address: str | None = None
    # Wikipedia views last month: measures attention, not visitor numbers.
    page_views: int | None = None
    missing: list[str] = Field(default_factory=list)


class VerifyResult(BaseModel):
    # "unavailable" means the check failed. It does not mean the place is fake.
    status: Literal["verified", "rejected", "unavailable"]
    place: Place | None = None
    reason: str


class RestaurantResult(BaseModel):
    status: ToolStatus
    restaurants: list[Place] = Field(default_factory=list)
    note: str | None = None


class TransitLine(BaseModel):
    mode: str | None
    line: str | None
    from_stop: str | None
    to_stop: str | None


class Route(BaseModel):
    mode: TravelMode | None
    minutes: float | None
    source: Literal["transitous", "estimate", "none"]
    transfers: int | None = None
    lines: list[TransitLine] = Field(default_factory=list)
    distance_km: float | None = None
    walking_estimate_minutes: float | None = None
    note: str | None = None


class CostItem(BaseModel):
    name: str
    # None means the cost is unknown. It is never counted as zero.
    amount: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    is_estimate: bool = True


class BudgetResult(BaseModel):
    total: float
    currency: str
    budget: float | None
    # None means we can't tell: no budget was given, or a missing cost could change the answer.
    over_budget: bool | None
    difference: float | None  # budget minus total; negative means over budget
    missing_costs: list[str]
    includes_estimates: bool
    note: str


class Stop(BaseModel):
    # None only for a meal break, which is the one stop that is not a real place.
    place_id: str | None
    name: str
    slot: Literal["activity", "lunch", "dinner"]
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    duration_minutes: int = Field(gt=0)
    duration_is_estimate: bool = True
    latitude: float | None = None
    longitude: float | None = None
    # Both None for the first stop of a day.
    travel_minutes: float | None = None
    travel_mode: TravelMode | None = None


class Day(BaseModel):
    day_number: int
    stops: list[Stop]


class ScheduleResult(BaseModel):
    status: Literal["ok", "partial", "failed"]
    days: list[Day] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class PlanIssue(BaseModel):
    severity: Literal["error", "warning"]
    code: str
    message: str
    day_number: int | None = None


class PlanCheck(BaseModel):
    passed: bool  # True when there are no errors; warnings are allowed
    issues: list[PlanIssue]
