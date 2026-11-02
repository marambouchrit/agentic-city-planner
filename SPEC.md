# Specification for the remaining phases

**Status: approved by the project owner on 2026-11-02. New big decisions still go to the owner first.**

This is the build contract for Phases 3 to 7. [ROADMAP.md](ROADMAP.md) stays the short summary. If the two disagree, this file wins until the disagreement is resolved in both.

A phase is finished only when every item in its "Done when" list has been checked and the result recorded. Offline tests alone never finish a phase that has a live check.

## 1. Traveler-facing transport rules

These come from the project owner and apply everywhere: tools, solver, agents, API and UI.

"Public transport" means metro, bus, tram, train and ferry. It does not mean transport that costs nothing.

| Situation for one hop | What the traveler sees | Taxi mentioned? |
|---|---|---|
| Public transport found, walk within the limit | Take the public transport, or walk if you prefer | No |
| Public transport found, walk too long | Take the public transport | No |
| No public transport found, walk within the limit | Walk | No |
| No public transport found, walk too long | Taxi, as a labelled backup with the reason | Yes, only here |

Rules:

1. **Taxi is a last resort, not a mode to choose.** No score, penalty or solver variable refers to taxis. The word appears in a plan only for a hop in the last row.
2. **Walking is an offer, not a decision.** The default limit for offering a walk is 20 minutes. The traveler can lower it. Nobody is ever told to walk 30 minutes.
3. **The plan works for either choice.** When both options are offered, the schedule reserves the longer of the two times.
4. **"Not found" is not "does not exist".** Thin data is common, so the wording is "no public transport route in our data", not "this city has no buses". An outage says that the check failed.
5. **Real data and estimates stay separate.** Anything calculated from straight-line distance is labelled as an estimate.

## 2. Problems in the current code

Found by reading the code and by one live Transitous test on 2026-11-02, Louvre to Notre-Dame and Louvre to Eiffel Tower. These are the reasons for Phase 3A and 3B.

| # | Where | Problem | Evidence |
|---|---|---|---|
| 1 | `optimize_schedule.py` | Schedules never use public transport. Every hop is `walk_estimate` or `taxi_estimate`, because `get_route` is not called | `_classify_hop` |
| 2 | `optimize_schedule.py` | Anything over about 2.25 km becomes a taxi, even in Paris. Times can be far off | Louvre to Eiffel: about 8 minutes by the taxi estimate, 26 to 29 minutes by real bus or metro |
| 3 | `optimize_schedule.py` | `TAXI_PENALTY` makes taxis part of the scoring | Breaks rule 1 |
| 4 | `optimize_schedule.py` | A 30-minute walk is treated as normal, and walking time costs nothing, so a day can zigzag | `_classify_hop`, objective in `_solve_day` |
| 5 | `optimize_schedule.py` | Places fill fixed slots, so the route order is not optimised | Module docstring |
| 6 | `optimize_schedule.py` | Days are solved one at a time, so day 1 takes the best places and nearby places can end up split across days | Loop in `optimize_schedule` |
| 7 | `optimize_schedule.py` | Restaurants score 0, so one 29 minutes away ties with one next door | `_score` |
| 8 | `optimize_schedule.py` | The default day has an empty gap of about 3.5 hours before dinner | 15:30 to 19:00 from the template |
| 9 | `optimize_schedule.py` | The solver does not reason about time. Stops are dropped afterwards if the day runs out | `_build_stops` |
| 10 | `optimize_schedule.py` | Every place takes 90 minutes, whether it is a large museum or a small monument | `PLACE_MINUTES` |
| 11 | `optimize_schedule.py` | `locked_stops` was removed, but trip changes in Phase 5 need it | Phase 1 change |
| 12 | `get_route.py` | Real walking data is thrown away. Transitous returns walks in `direct`, and only `itineraries` is read | Live: `direct` held a 22-minute walk |
| 13 | `get_route.py` | Straight-line walking estimates can be too low | Same hop: about 16 minutes estimated, 22 minutes real |
| 14 | `get_route.py` | `itineraries[0]` is taken as best, but it is not always the fastest | Live: first was 27 minutes, a later one 26 |
| 15 | `schemas.py` | `Stop` keeps only a mode and minutes, so an itinerary cannot say which line to take | `Stop` fields |
| 16 | `schemas.py` | `Route` holds one mode, so it cannot offer "public transport or walk" | `Route.mode` |
| 17 | `get_route.py` | The docstring asks for UTC, but nothing knows the city's time zone or the trip date | No time zone code exists |
| 18 | `agents/discovery.py` | Discovery does not know how many places a trip needs | No target in its task |

## 3. Phase 3: Planner

Build in this order. Each part must pass before the next starts.

### 3A. Transport layer

Replace `Route` and `TravelMode`:

```python
class PublicTransportOption(BaseModel):
    minutes: float
    transfers: int
    lines: list[TransitLine]
    walking_minutes_inside: float | None  # walking to, between and from stops

class WalkOption(BaseModel):
    minutes: float
    is_estimate: bool

class TaxiBackup(BaseModel):
    minutes: float       # always an estimate
    reason: str          # why no public transport or walk was offered

class Hop(BaseModel):
    public_transport: PublicTransportOption | None = None
    walk: WalkOption | None = None
    taxi_backup: TaxiBackup | None = None
    check_failed: bool = False  # the service failed, as opposed to finding nothing
    reserved_minutes: float | None  # the time the schedule must allow
    distance_km: float | None
```

`Hop` has a validator that rejects a taxi alongside any other option, and `reserved_minutes` follows section 1.

`get_route` changes:

- Read walks from `direct`, and public transport from `itineraries`.
- Choose the fastest itinerary. Break a tie with fewer transfers.
- Use a walking estimate only when Transitous gave no real walk.
- Accept a local departure time and a time zone, then convert to UTC in code.
- Keep the daily request limit. Group cache keys into 30-minute departure windows, so small retiming does not spend more requests.
- Retry a failed request once, and set `check_failed` if it fails again.

Dates and time zones:

- Use `timezonefinder`, which works offline, to find the time zone from coordinates. Its data is built from OpenStreetMap. The owner approved this (section 9), and the README must credit it. No OpenStreetMap service is called.
- With no trip date, plan for a normal weekday and say so.
- Timetables may not cover distant dates, which would look like "no public transport". Before coding, test this live. If it is true, query the same weekday in the near future and label the result.

**Done when:**

- All four rows of section 1 have tests, using response shapes recorded from the real API.
- A test confirms that no plan output contains a taxi unless it is in the last row.
- Live check: Louvre to Pont Neuf offers both options, Louvre to Notre-Dame and Louvre to Eiffel offer public transport only (their walks are over the limit), and Tunis hops give the correct labelled results.
- The checks and tests that use the old modes are rewritten on purpose, not deleted to pass.

### 3B. Optimizer, designed from the problem

The old project is not a reference. Its code and its tests are not trusted, and correctness is proven by the tests below.

**The problem:** a team orienteering problem with time windows. For each day, choose an ordered route that collects the most value within the day's time, with meals in their windows.

**The tool:** OR-Tools CP-SAT, with one `AddCircuit` per day and optional places, solved for the whole trip at once.

| Part | Rule |
|---|---|
| Hard: time | Each stop starts only after the previous stop's visit and travel. Every stop fits within the day |
| Hard: uniqueness | A place appears at most once in the trip |
| Hard: meals | Lunch from 12:00 to 14:30, dinner from 18:30 to 21:00, one each per day |
| Hard: traveler | Locked stops stay in place, excluded places never appear, required places are visited if possible |
| Hard: pace | At most `max_activities_per_day` attractions per day |
| Objective, in order | 1. Most attractions, then their value. 2. Days as even as possible. 3. Least travel time. 4. Start the day on time and finish early. Weighted, not absolute: a value of 1.0 is worth 100 minutes of travel, so a tiny value difference can't cause a long detour |

Value is popularity from page views plus the Planner's priority, from 0 to 1, with its effect on results covered by tests. Restaurants have no popularity value, so travel time decides between them, which fixes problem 7.

Inputs:

```python
optimize_schedule(
    attractions, restaurants, days, day_start, day_end,
    max_activities_per_day, visit_minutes, priorities,
    locked, excluded, required, start_point, travel,
)
```

- `visit_minutes` comes from the Planner, between 30 and 240, default 90, and is labelled an estimate.
- `start_point` is optional. Without it, each day starts at its first stop.
- With no restaurant, the meal is a labelled meal break, a reserved time block with no location. Because it has no location, the solver can't see the real distance across it. A slow real route across a break is caught and avoided, but route order around breaks is not fully optimised.
- Opening hours stay unchecked, because there is no data source. The plan says so.

**Travel times:** asking Transitous about every pair is not affordable, because 15 places would mean 210 requests against a limit of 720 a day. So:

1. The solver starts from one neutral estimate per pair, with no mode or taxi in it: the smaller of the walking estimate and `wait + km / speed`. Fit `wait` and `speed` to at least 20 real hops across at least 3 cities.
2. Fetch real routes only for the hops chosen.
3. Put the real times into the matrix, and solve again if the plan no longer fits or real times changed the best order.
4. Stop after 3 rounds, or sooner if the route stops changing.
5. The final times always come from the real routes in the final plan, with a check that it still fits. Anything dropped is reported in the notes.

Each trip may use at most 40 Transitous requests. Measure the true number, because it sets how many trips a day are possible.

The solver has a time limit: 20 seconds per round for up to 3 days and 60 seconds for longer trips, starting from a quick plan that is also the safety net (approved by the owner on 2026-11-02). Each round can use hops not checked yet, so the plan kept is the best on real routes, not the last. A result that is good but not proven best is labelled in the notes. Tests use small cases that solve to proven optimality, so that they repeat exactly.

**Proof that it is correct:**

- **Independent check:** on small random cases, compare the score with a separate brute-force search that shares no code with the solver. They must match.
- **Invariants on at least 200 seeded random cases:** no overlap, no repeat, meals in their windows, locked stops kept, excluded places absent, and `check_plan` passes.
- **Named cases:**
  - Two clusters of places across two days are not mixed.
  - A nearer restaurant beats a far one.
  - A place that does not fit is left out with a note.
  - Too few places gives an honest partial result.
  - A slow real route forces a re-solve.
  - A place with no coordinates is reported.
  - No taxi logic affects the result.
- **Scale:** first test a 5-day case with 25 attractions and 10 restaurants. If it cannot give a good answer in 20 seconds on this machine, stop and ask the owner. The possible fallback is to group places into days first and then solve each day, which is simpler but can give worse plans.

**Done when:** all of the above passes, problems 1 to 11 in section 2 are gone, and a person has read a Paris plan and a Tunis plan and found them sensible.

### 3C. Planner agent

| The LLM decides | Code enforces |
|---|---|
| Priorities, pace, visit lengths, what to change after feedback, whether to retry | Only known place IDs, value limits, step limit, final plan matches the tool's output exactly |

- **Tools:** `optimize_schedule`, which fetches the routes itself so the LLM does not spend a call per hop, and `get_route`, for checking one specific hop.
- **Final answer:** the Planner returns the ID of a schedule that the tool produced, plus its explanation. Code supplies the schedule, so the LLM cannot alter a stop or a time.
- **Limit:** 5 steps.
- `Stop` carries its full `Hop`. `check_plan` is updated: `walk_too_long` applies to offered walks, and a new error rejects a taxi that breaks section 1.
- **Discovery target:** the task states how many are needed, about 1.5 times `days * max_activities_per_day` attractions and about `3 * days` restaurants, spread across areas, so that a meal can stay near the day's stops. If there are too few, the plan is shorter and says why, and nothing is invented.
- **Temporary input:** a `TripRequest` with city, days, optional date, optional budget, pace and preferences, given through the command line until Phase 5.
- **Graph:** `orchestrator -> discovery -> planner`.

**Done when:** a 3-day trip works live in Paris and Tunis. Paris shows real lines. Tunis shows honest labels. Every stop traces to a tool result. LLM calls and Transitous requests are recorded.

## 4. Phase 4: Reviewer

- **Tools:** `calculate_budget` and `check_plan`.
- **Costs:** the LLM estimates entry prices, meals and a single public transport fare for the city. Everything is labelled an estimate, because no real price source exists and Transitous returned no fare data in the live test.
- **Coverage:** code builds the list of items from the plan, so the LLM only supplies amounts and cannot leave a stop out. Unknown stays unknown.
- **Transport cost:** public transport rides count at the fare, including when a walk is also offered, so the total stays on the safe side. Taxi cost is counted only for backup hops.
- **Verdict:** `approve` or `send_back`, with `target` (Discovery or Planner), an issue code and specific instructions. Code refuses `approve` if `check_plan` has errors or a known budget is exceeded.
- **Send-back limit:** at most 2. The third result is returned as it is, with its remaining problems listed, and is never presented as passing.
- **Best plan:** keep the best plan so far. A revision that gets worse does not replace it.
- **Over-budget changes:** the Planner gets the items and the amount to cut. Expensive places can be dropped or swapped through `excluded`.
- **Fixed prices:** once an item has a price, it stays fixed for the trip, so a plan can't pass on a lower guess. All meal breaks share one price.

**LLM call budget:** the current limits do not fit together. Discovery 8, Planner 5, Reviewer 4 and a 2-call Orchestrator make 19 for one pass. Each send-back to the Planner adds up to 9, for 37 in total against a limit of 30. The Reviewer has 4 steps because 3 left no room to correct a refused approval in a live run. In 4 live Paris runs with 2 revisions each, a trip really used 15 to 19 calls, with no Orchestrator yet. The trip limit stays at 30. When too few calls are left for another round, the Reviewer's result is final, and the plan is returned with its issues. Tested offline.

**Done when:**

- A plan that is over budget is revised automatically and ends within budget, or explains why it cannot.
- Tests cover the limit of 2, a missing price, a false `approve`, and a revision that gets worse.
- Measured call counts replace the worst-case numbers.

## 5. Phase 5: Orchestrator and API

**Orchestrator:** an LLM agent with no tools. It turns the request into a `TripRequest`, routes the work and finishes. Code checks every route it picks against the allowed graph edges.

- If something essential is missing, such as the city, it asks one question. Otherwise it uses defaults and states them.
- Requests covering more than one city are out of scope and say so.

**Trip changes:** these go through the Orchestrator. Stops the traveler kept are `locked`, removed places are `excluded`, and only what is needed runs again. For example, "replace the Louvre" should not re-verify the whole city.

**State:** LangGraph's in-memory checkpointer, keyed by trip ID. No database was agreed, so trips are lost on restart, and the README says so.

**API (FastAPI):**

| Endpoint | Purpose |
|---|---|
| `POST /trips` | Start a trip, returns its ID |
| `GET /trips/{id}/events` | Progress stream (server-sent events) |
| `GET /trips/{id}` | The current itinerary and its labels |
| `POST /trips/{id}/messages` | Ask for a change |
| `GET /health` | Status check |

A trip can take minutes, at about 9 seconds per LLM call, so a single blocking request will not work.

**Safety:**

- Limit request length.
- Set a limit on trips per day that protects the Gemini, Foursquare and Transitous quotas.
- Allow only the known frontend origin.
- Never send keys to the browser.
- Treat text from Wikipedia, Foursquare and the user as data, not instructions, with a test that plants instructions in a tool result.
- Errors must not expose internal details.

**Done when:** one request gives a full itinerary through the API, a change keeps locked stops, a service outage gives a labelled result rather than a crash, and tests confirm the graph can only take allowed routes.

## 6. Phase 6: Frontend

- **Stack:** Next.js chat and itinerary view. The old UI is a starting point only. Each part kept is read and tested first, and old assumptions about the backend are removed.
- **Transport display:** follows section 1 exactly. Example: "Line 72, 7 min. Or walk, about 13 min." The taxi appears only as a backup, with its reason.
- **Labels:** estimates, unchecked opening hours, opinion reasons and meal breaks are visible, not hidden in a tooltip.
- **Credits on screen:** "Powered by Foursquare" where its data appears, plus Transitous and OpenStreetMap contributors.
- **States:** progress while planning, plus clear screens for failures, partial plans and a lost trip after a restart.
- **Map:** none in this version. Google Maps needs a card and OpenStreetMap tiles are ruled out.
- **Quality:** works on a phone and by keyboard.

**Done when:** a trip is planned and changed in a real browser, with end-to-end tests for that path and for a city with no public transport data.

## 7. Phase 7: Finish

**Evaluation:** 10 cities, chosen in advance, including large metros, thin-data cities and small towns such as Paris, Tunis and Luang Prabang. The set and metrics are fixed before running, and results are reported even when poor.

| Metric | How it is measured |
|---|---|
| Invented stops | Must be 0, checked by code |
| Plan validity | Share passing `check_plan` |
| Taxi rule | Violations of section 1, must be 0 |
| Public transport coverage | Share of hops with a real route, per city |
| Accuracy of the solver's estimate | Estimated minutes compared with real minutes |
| Cost per trip | LLM calls, Transitous requests, Foursquare calls, time taken |
| Reviewer value | How often it finds a real problem, and how often revision fixes it |

Budget totals are not reported as accurate, because the prices are estimates.

**Deliverables:** README with measured results and known limits, demo video, and MCP only if time allows.

**Before publishing:** ask the Transitous team whether agent requests count as automated use, confirm the attribution, and check that the repo holds no keys or the private journal.

## 8. Rules for every phase

1. Read the real code and test real API responses before building on an assumption.
2. Fake test responses must match shapes recorded from the real service.
3. Fix the cause. A failing test is never deleted or weakened just to pass.
4. Report live results honestly, including failed runs, in `JOURNAL.md`.
5. Update `JOURNAL.md`, `ROADMAP.md` and this file at the end of each phase. `JOURNAL.md` is never committed.
6. Commit and push only when the owner asks.
7. ROADMAP's rules still apply: one job per agent, every stop from a tool, code does the math, labelled estimates.

## 9. Decisions for the owner

Big decisions are the owner's. Claude presents options and a recommendation, and waits for an answer.

Approved on 2026-11-02:

| Question | Decision |
|---|---|
| Walking offer limit | 20 minutes |
| Time reserved when both public transport and a walk are offered | The longer of the two |
| Optimizer design | Whole-trip route solver with CP-SAT, written fresh. If the scale test fails, stop and ask the owner before using any fallback |
| Time zones | `timezonefinder`, with a README credit |

Also approved with the whole spec on 2026-11-02: trips are lost on restart, no map in the frontend, meal windows and objective order as in 3B, and Phases 4 to 7 as written.
