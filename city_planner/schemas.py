"""Data types shared by the tools."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, computed_field, model_validator

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


class PublicTransportOption(BaseModel):
    minutes: float
    transfers: int
    lines: list[TransitLine]
    # Walking to, between and from the stops.
    walking_minutes_inside: float | None = None


class WalkOption(BaseModel):
    minutes: float
    is_estimate: bool


class TaxiBackup(BaseModel):
    minutes: float  # always an estimate
    reason: str


class Hop(BaseModel):
    """How to get from one place to the next, following the transport rules in SPEC.md section 1."""

    public_transport: PublicTransportOption | None = None
    walk: WalkOption | None = None
    # Only when there is neither public transport nor a walk to offer.
    taxi_backup: TaxiBackup | None = None
    # True when the service failed, which is different from finding no route.
    check_failed: bool = False
    distance_km: float | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _taxi_is_only_a_backup(self) -> "Hop":
        if self.taxi_backup and (self.public_transport or self.walk):
            raise ValueError("A taxi can only be offered when there is no public transport and no walk.")
        return self

    @computed_field
    @property
    def reserved_minutes(self) -> float | None:
        """The time the schedule must allow: the longer option, so either choice arrives on time."""
        options = [option.minutes for option in (self.public_transport, self.walk, self.taxi_backup) if option]
        return max(options) if options else None


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
    # None for the first stop of a day, unless the day has a start point.
    hop: Hop | None = None


class Day(BaseModel):
    day_number: int
    stops: list[Stop]


class LockedStop(BaseModel):
    """A stop the traveler kept, which must stay on this day at this time."""

    place_id: str
    day_number: int = Field(ge=1)
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


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


class TripRequest(BaseModel):
    """What the traveler asked for. The command line fills it until the Orchestrator does in Phase 5."""

    city: str
    days: int = Field(ge=1)
    start_date: date | None = None
    budget: float | None = Field(default=None, ge=0)
    # None lets the Planner choose the pace.
    max_activities_per_day: int | None = Field(default=None, ge=1)
    max_walk_minutes: int = Field(default=20, ge=1)
    preferences: str = ""
