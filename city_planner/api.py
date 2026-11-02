"""The API the frontend uses. Trips are kept in memory, so a restart loses them.

    uvicorn city_planner.api:app --port 8000
"""

import asyncio
import json
import logging
import os
import threading
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from city_planner.graph import handle_message, new_checkpointer
from city_planner.llm import LLM, GeminiLLM

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 2000
# A budget trip used up to 77 Transitous requests in live runs, and we stop at 700 a day.
MAX_REQUESTS_PER_DAY = 20
MAX_RUNNING = 2  # each request can take minutes and uses the shared quotas
MAX_STORED_TRIPS = 100
EVENT_POLL_SECONDS = 0.5
USAGE_FILE = Path(__file__).resolve().parents[1] / ".cache" / "api_usage.json"
DEFAULT_FRONTEND_ORIGIN = "http://localhost:3000"

CREDITS = [
    "Attractions are verified with Wikipedia and Wikidata.",
    "Restaurants are powered by Foursquare.",
    "Public transport routes come from Transitous, using data from OpenStreetMap contributors.",
]
LABELS = [
    "Prices are estimates from the AI's memory, for one traveler. They are not real prices.",
    "Visit lengths are estimates. Opening hours were not checked.",
    "Reasons and explanations are the AI's opinion, not verified data.",
]
PROGRESS_TEXT = {
    "interpret": "Understanding your request",
    "discovery": "Finding and verifying places",
    "planner": "Building the schedule with real routes",
    "reviewer": "Checking the plan and the budget",
}


class MessageBody(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)


@dataclass
class TripRecord:
    status: str = "running"  # running, done or failed
    events: list[dict] = field(default_factory=list)
    state: dict = field(default_factory=dict)
    public_error: str | None = None


def _take_daily_request(usage_file: Path) -> bool:
    """Counts requests in a file, so the limit also holds across restarts."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        usage = json.loads(usage_file.read_text(encoding="utf-8"))
        count = usage["count"] if usage["date"] == today else 0
    except (OSError, ValueError, KeyError, TypeError):
        count = 0
    if count >= MAX_REQUESTS_PER_DAY:
        return False
    usage_file.parent.mkdir(parents=True, exist_ok=True)
    usage_file.write_text(json.dumps({"date": today, "count": count + 1}), encoding="utf-8")
    return True


def trip_view(trip_id: str, record: TripRecord) -> dict:
    """What the frontend sees. It never includes internal errors or the agents' trace."""
    state = record.state
    plan = state.get("final_plan")
    review = state.get("final_review")
    is_previous = False
    if plan is None and state.get("previous_plan") is not None:
        plan, review = state["previous_plan"], state.get("previous_review")
        is_previous = True
    if plan is None:
        plan = state.get("planner")

    request = state.get("previous_trip") if is_previous else state.get("trip")
    if review is None:
        review_status = "not_reviewed"
    else:
        review_status = "approved" if review.verdict == "approve" else "not_approved"
    return {
        "trip_id": trip_id,
        "status": record.status,
        "reply": state.get("reply"),
        "error": record.public_error,
        "showing_previous_plan": is_previous,
        "request": request.model_dump(mode="json") if request else None,
        "schedule": plan.schedule.model_dump(mode="json") if plan else None,
        "explanation": plan.explanation if plan else None,
        "review_status": review_status if plan else None,
        "budget": review.budget.model_dump(mode="json") if review else None,
        "costs": [item.model_dump(mode="json") for item in review.costs] if review else [],
        "problems": ([review.instructions] if review.verdict != "approve" else []) if review else [],
        # Notes describe the attempt that failed, not the previous plan on show.
        "notes": [] if is_previous else state.get("notes", []),
        "labels": LABELS,
        "credits": CREDITS,
    }


def create_app(
    llm_factory: Callable[[], LLM] = GeminiLLM,
    run_inline: bool = False,
    usage_file: Path = USAGE_FILE,
    frontend_origin: str | None = None,
) -> FastAPI:
    """run_inline runs each request before answering, which keeps tests simple."""
    app = FastAPI(title="Agentic City Planner")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[frontend_origin or os.getenv("FRONTEND_ORIGIN", DEFAULT_FRONTEND_ORIGIN)],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Last-Event-ID"],
    )
    trips: OrderedDict[str, TripRecord] = OrderedDict()
    checkpointer = new_checkpointer()
    executor = ThreadPoolExecutor(max_workers=MAX_RUNNING)
    lock = threading.Lock()

    def execute(trip_id: str, message: str, record: TripRecord) -> None:
        def progress(node: str) -> None:
            record.events.append({"type": "progress", "step": node, "text": PROGRESS_TEXT.get(node, node)})

        try:
            state = handle_message(trip_id, message, llm_factory(), checkpointer, progress)
            record.state = state
            if state.get("error"):
                logger.error("Trip %s stopped: %s", trip_id, state["error"])
                kept = " Your previous plan is unchanged." if state.get("previous_plan") else ""
                record.public_error = f"The plan could not be finished right now.{kept}"
                record.status = "failed"
            else:
                record.status = "done"
        except Exception:
            # The details go to the log. They could include internal paths or service answers.
            logger.exception("Trip %s failed", trip_id)
            record.public_error = "Something went wrong while planning. Please try again."
            record.status = "failed"
        record.events.append({"type": record.status})

    def start(trip_id: str, message: str, record: TripRecord) -> None:
        if run_inline:
            execute(trip_id, message, record)
        else:
            executor.submit(execute, trip_id, message, record)

    def admit() -> None:
        with lock:
            running = sum(record.status == "running" for record in trips.values())
            if running >= MAX_RUNNING:
                raise HTTPException(429, "The planner is busy. Please try again in a few minutes.")
            if not _take_daily_request(usage_file):
                raise HTTPException(429, "The daily planning limit was reached. Please try again tomorrow.")

    def get_record(trip_id: str) -> TripRecord:
        record = trips.get(trip_id)
        if record is None:
            raise HTTPException(404, "This trip doesn't exist. Trips are lost when the server restarts.")
        return record

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/trips", status_code=202)
    def create_trip(body: MessageBody) -> dict:
        admit()
        trip_id = uuid.uuid4().hex
        record = TripRecord()
        with lock:
            trips[trip_id] = record
            while len(trips) > MAX_STORED_TRIPS:
                oldest_id, oldest = next(iter(trips.items()))
                if oldest.status == "running":
                    break
                del trips[oldest_id]
                checkpointer.delete_thread(oldest_id)
        start(trip_id, body.message, record)
        return {"trip_id": trip_id}

    @app.post("/trips/{trip_id}/messages", status_code=202)
    def send_message(trip_id: str, body: MessageBody) -> dict:
        previous = get_record(trip_id)
        if previous.status == "running":
            raise HTTPException(409, "This trip is still being planned.")
        admit()
        # Keep showing the current state while the new request runs.
        record = TripRecord(state=previous.state)
        trips[trip_id] = record
        start(trip_id, body.message, record)
        return {"trip_id": trip_id}

    @app.get("/trips/{trip_id}")
    def read_trip(trip_id: str) -> dict:
        return trip_view(trip_id, get_record(trip_id))

    @app.get("/trips/{trip_id}/events")
    async def events(trip_id: str, last_event_id: str | None = Header(default=None)) -> StreamingResponse:
        record = get_record(trip_id)
        first = int(last_event_id) + 1 if last_event_id and last_event_id.isdigit() else 0

        async def stream():
            index = first
            while True:
                while index < len(record.events):
                    event = record.events[index]
                    yield f"id: {index}\nevent: {event['type']}\ndata: {json.dumps(event)}\n\n"
                    index += 1
                    if event["type"] in ("done", "failed"):
                        return
                await asyncio.sleep(EVENT_POLL_SECONDS)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


app = create_app()
