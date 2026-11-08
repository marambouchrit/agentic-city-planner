# Agentic City Planner

A multi-agent trip planner for any city. Type one sentence, such as "3 days in Paris with 400 euros, I love art",
and get a day-by-day itinerary with verified places, real public transport, estimated costs and a review.
You can then change it, for example "replace the Louvre", and the other stops keep their day and time.

## How it works

Four LLM agents, connected with LangGraph. Each agent makes decisions, and plain code does the facts, math and checks.

| Agent | Decides | Tools |
|---|---|---|
| Orchestrator | What a message means: a new trip, a change, a question back, or out of scope | None |
| Discovery | Which places to look for, and which ones fit | `verify_place` (Wikipedia, Wikidata), `find_restaurants` (Foursquare) |
| Planner | Priorities, pace and visit lengths | `optimize_schedule` (OR-Tools), `get_route` (Transitous) |
| Reviewer | Whether the plan is good enough, and what to send back | `calculate_budget`, `check_plan` |

```text
Message -> Orchestrator -> Discovery -> Planner -> Reviewer -> itinerary
                              ^            ^          |
                              +------------+----------+
                                 sent back, at most 2 times
```

Rules enforced by code, not by asking the LLM:

- **No invented places.** Agents answer with IDs, and code swaps in the real data the tools returned. An ID no tool returned is refused.
- **Transport.** Public transport comes first. A walk of up to 20 minutes is offered as a choice. A taxi only appears as a labelled backup when there is neither.
- **Schedule.** OR-Tools CP-SAT plans the whole trip at once: which places, on which day, in what order, with meals in their windows and as little travel as possible. Its answers matched a separate brute-force search on small cases. Final times come from real routes.
- **Budget.** The LLM estimates prices, code lists every cost and does the math, and a plan over budget can't be approved.
- **Honest labels.** Estimates, unchecked opening hours and the agents' opinions are labelled. A plan with problems is shown as not approved.

## Results

Measured on 10 big cities on 2026-11-03, one run each, with the same request:
"3 days in {city} with 400 euros, I love art and history", then one change replacing the most viewed stop.
The protocol was fixed before running ([SPEC.md](SPEC.md) section 7). Full tables: [evaluation/RESULTS.md](evaluation/RESULTS.md).

| Measure | Result |
|---|---|
| Trips with a plan | 10/10 |
| Invented stops | 0 |
| Taxi rule breaks | 0 |
| Plans passing the rule checks | 10/10 |
| Plans approved by the Reviewer | 8/10 |
| Visits after dinner | 0 |
| Meals missing | 1, in Berlin |
| Hops with real public transport | 61/137. The other 76 were short walks with no route found. None needed a taxi |
| Solver travel estimate against real routes | 2.3 minutes mean absolute error, over 137 hops |
| Changes that removed the place and kept every other stop | 10/10 |
| Changes that filled the gap | 9/10 |
| Changed plans approved | 7/10 |
| Time per trip | 85 to 298 seconds |
| LLM calls per trip | 8 to 20 |
| Transitous requests per trip | 15 to 65 |

What the results showed:

- **Tokyo got only 2 attractions at an estimated 3,185 EUR.** A separate diagnostic run gave a normal plan: 12 museums, 292 EUR, approved. The likely cause is prices given in yen. Prices stay fixed for a trip once set, so one wrong price got locked in, and the Planner kept cutting places. A price above the whole budget now gets a currency warning, and is only accepted if the Reviewer sends it again. The evaluation wasn't rerun, so the Tokyo result above is the original one.
- **Madrid had no public transport hops.** A direct check found only commuter trains and night buses, so Transitous's Madrid data seems to lack the metro. The planner grouped nearby places to walk instead.
- **New York and Tokyo stayed over budget** after 2 revisions, and were shown as not approved.
- **One run per city is one result, not a success rate.** Plans differ between runs, and the full trips weren't proven optimal in the time allowed.

## Known limits

- Prices are estimates from the LLM's memory, for one traveler. They are not real prices.
- Opening hours aren't checked, because there is no free data source. Visit lengths are estimates. Dinner ends the day, so no visit comes after it.
- Places need an English Wikipedia article to be verified, so small cities get fewer places.
- Transitous coverage varies by city. "No route" means none in its data, not that none exists.
- Gemini's free tier can be overloaded. A spike longer than about 40 seconds ends a trip.
- Trips are kept in memory, so a server restart loses them.

## Plan a trip

```bash
python -m city_planner.run "3 days in Paris with 300 euros, I love art"
python -m city_planner.run "2 days in Paris" --then "Replace the Louvre"
```

This uses your Gemini, Foursquare and Transitous quotas, and it prints every decision the agents made.

## Run the API

```bash
uvicorn city_planner.api:app --port 8000
```

| Endpoint | Purpose |
|---|---|
| `POST /trips` | Start a trip from one sentence. Returns its ID |
| `GET /trips/{id}/events` | Live progress, as server-sent events |
| `GET /trips/{id}` | The itinerary, costs, labels and credits |
| `POST /trips/{id}/messages` | Ask for a change. Kept stops stay at the same time |
| `GET /health` | Status check |

The API allows 20 planning requests a day, and only `FRONTEND_ORIGIN` can call it from a browser.

## Run the website

```bash
cd frontend
npm install
copy .env.example .env.local
npm run dev
```

Start the API first, then open http://localhost:3000.

## Tests

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # then add your Gemini key

pytest
```

`pytest` makes no live calls. It includes a brute-force check of the optimizer and 200 random trips.

Browser tests use the real API code with a scripted LLM, so they use no network or quota. They run on desktop and on an emulated phone:

```bash
cd frontend
npx playwright install chromium
npm test
```

These use real services and quotas:

```bash
cd frontend
npx playwright test --config e2e-live/live.config.ts   # a 3-day Paris trip and a change, in a browser
cd ..
python -m evaluation.run_eval                          # the 10-city evaluation, then --report
python -m spikes.check_gemini                          # the Phase 0 checks, in spikes/
```

See [ROADMAP.md](ROADMAP.md) for decisions and phases, and [SPEC.md](SPEC.md) for the detailed plan.

## Data sources

Attractions are verified with [Wikipedia](https://www.wikipedia.org/) and [Wikidata](https://www.wikidata.org/).
Popularity is Wikipedia page views, which measure attention, not visitor numbers.

Restaurants are powered by [Foursquare](https://foursquare.com/) when a key is set.

Public transit routes come from [Transitous](https://transitous.org/), using these
[data sources](https://transitous.org/sources/) and data from
[OpenStreetMap](https://www.openstreetmap.org/copyright) contributors.

Time zones are looked up offline with [timezonefinder](https://github.com/jannikmi/timezonefinder), whose data is
built from OpenStreetMap. No OpenStreetMap service is called.

## License

[MIT](LICENSE)
