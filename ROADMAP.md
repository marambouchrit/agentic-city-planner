# Roadmap

## Decisions

| Topic | Decision | Evidence or reason |
|---|---|---|
| Orchestration | LangGraph | Required by the project |
| Scheduling | OR-Tools, as the Planner's `optimize_schedule` tool | Required by the project owner. Redesigned from the problem in Phase 3, not taken from the old project. See [SPEC.md](SPEC.md) |
| LLM | `gemini-3.5-flash-lite` | Passed 7/7 tool-calling tests. `gemini-3.8-flash` is capped at 20 requests a day |
| Attractions | Gemini proposes, Wikipedia verifies | Real places were confirmed and a fake museum was rejected in all 3 test cities |
| Popularity | Wikipedia page views | Free, but measures attention, not visitor numbers |
| Metro and bus | Transitous | Real route in Paris, none in Tunis or Luang Prabang in one test each |
| Getting around | Public transport first, with walking offered as a choice when near. Taxi only as a labelled backup when neither exists | Project owner's rule. See [SPEC.md](SPEC.md) section 1. Not built yet: Phase 3 replaces the current code |
| Restaurants | Foursquare, if a free key works without a card; otherwise meal breaks with unverified suggestions, clearly labelled | OpenStreetMap is ruled out by the project owner. Gemini Search grounding returned HTTP 429 on the free key. Foursquare's free key returned places with coordinates in Paris, Tunis and Luang Prabang. Its docs say no card is needed and list 500 free Pro calls. Results include cafés and bakeries, so Discovery must choose using their categories. Screens showing its data must say "Powered by Foursquare" |
| Budget | Labelled estimates from Gemini | No free source of real prices |
| Google Maps and Maps grounding | Not used | Maps needs a card; grounding returned HTTP 429 on the free key |

## The four agents

| Agent | Job | Tools |
|---|---|---|
| Orchestrator | Understands the request, routes the work, finishes | None |
| Discovery | Finds and verifies attractions and restaurants | `verify_place`, `find_restaurants` |
| Planner | Builds the day-by-day schedule | `get_route`, `optimize_schedule` |
| Reviewer | Checks the plan and sends it back with feedback | `calculate_budget`, `check_plan` |

```text
User -> Orchestrator -> Discovery -> Planner -> Reviewer -> final itinerary
                           ^            ^          |
                           +------------+----------+
                              sent back with feedback
```

## Rules

1. Each agent has one job and uses only its own tools.
2. Every stop must come from a tool result. Code rejects anything else.
3. Code does the checks and math. The LLM makes the decisions.
4. Agents return validated structured output.
5. Only the Reviewer can send work back, at most 2 times.
6. Each agent has a step limit, and each trip has a limit of 30 LLM calls.
7. Estimates and missing data are labelled.
8. Keep `/plan` requests under 720 a day and follow the Transitous usage policy.

## Not building

No Postgres, RAG, GraphRAG, login, MCP or extra agents. The project owner dropped MCP on 2026-11-03.

## Phases

| Phase | Build | Done when |
|---|---|---|
| 0. Checks (done) | Test the LLM and data sources | Decisions above are backed by real runs |
| 1. Tools (done) | Test Foursquare first, then build `verify_place`, `find_restaurants`, `get_route`, `calculate_budget`, `check_plan`, `optimize_schedule` | Each tool has tests and handles missing data |
| 2. Agent base and Discovery (done) | One reusable agent loop and the LangGraph skeleton | "Popular museums in Lisbon" returns only verified places |
| 3. Planner (done) | Day-by-day schedule with transit | A 3-day trip works in Paris and Tunis |
| 4. Reviewer (done) | Budget and constraint checks, plus the send-back loop | An over-budget plan is revised automatically |
| 5. Orchestrator and API (done) | Full graph, trip changes and FastAPI | One request produces a full itinerary end to end |
| 6. Frontend (done) | Next.js chat and itinerary view, with the old UI used only as a starting point after reading it | A trip can be planned and changed in the browser |
| 7. Finish | Evaluation on 10 cities, README with results, demo video | Measured results are ready for the CV |

## Known risks

- Gemini's free limits can change, and models can be overloaded. The LLM stays behind one small interface so it can be swapped. Gemini returned HTTP 503 in live runs, and a spike longer than about 40 seconds ends the trip, which must then be started again.
- Wikipedia page views can be misleading. For example, Bardo National Museum showed only 85 views in one month in Phase 0, but 1,685 under its current article title, "Bardo National Museum (Tunis)", in Phase 1. Views must use the real article title.
- Some verified places have no coordinates on Wikipedia, so `verify_place` should try Wikidata next and must not use an OpenStreetMap service such as Nominatim.
- Transitous itself uses OpenStreetMap data, so its required credit stays. No part of this project calls OpenStreetMap directly.
- Ask the Transitous team whether agent requests count as automated use before publishing.
- A 3-day trip revised twice for its budget used 15 to 19 LLM calls and 47 to 77 Transitous requests in 4 live Paris runs, so only about 9 to 14 such trips fit the daily limit. A plan needing no revision used 17 to 28 requests before the Reviewer existed. Gemini's daily limit for a full trip is still unmeasured.
- A verified name can match a related article, such as "Luxembourg Garden" matching Luxembourg Palace.
- Code can check that a place is real, but not every word of an agent's reason. Reasons are labelled as opinion.
- Real places with no matching English Wikipedia article are rejected, such as MAAT in Lisbon, so small cities can get few results.
