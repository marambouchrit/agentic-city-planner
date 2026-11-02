# Agentic City Planner

A multi-agent trip planner for any city, with popular places, metro stops and budget estimates.
Four agents: Orchestrator, Discovery, Planner and Reviewer.

See [ROADMAP.md](ROADMAP.md) for decisions, rules and phases, and [SPEC.md](SPEC.md) for the detailed plan.
Phases 0 to 6 are built: the tools, the agent loop, all four agents, the API and the website.

## Plan a trip

```bash
python -m city_planner.run "3 days in Paris with 300 euros, I love art"
python -m city_planner.run "2 days in Paris" --then "Replace the Louvre"
```

This uses your Gemini, Foursquare and Transitous quotas, and it prints every decision the agents made.
A 3-day Paris trip revised twice for its budget used 15 to 19 LLM calls and 47 to 77 Transitous requests in live runs. Reasons are the agents' opinion, not verified data.

The Reviewer sends a plan back at most 2 times. Prices are estimates for one traveler, and a plan that still has problems is shown as not approved.

Public transport comes first. A walk of up to 20 minutes is offered as a choice, and a taxi only appears as a labelled backup
when there is neither. Opening hours are not checked, and visit lengths are estimates.

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

Trips are kept in memory, so a restart loses them. The API allows 20 planning requests a day, and only `FRONTEND_ORIGIN` can call it from a browser.

## Run the website

```bash
cd frontend
npm install
copy .env.example .env.local
npm run dev
```

Start the API first, then open http://localhost:3000.

Browser tests use the real API code with a scripted LLM, so they use no network or quota. They run on desktop and on an emulated phone:

```bash
cd frontend
npx playwright install chromium
npm test
```

## Run the tests and checks

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # then add your Gemini key

pytest
python -m spikes.check_gemini --list-models
python -m spikes.check_gemini
python -m spikes.check_maps_grounding
python -m spikes.check_transit
python -m spikes.check_foursquare
```

`pytest` makes no live calls. `check_gemini` uses about 12 requests, `check_maps_grounding` uses 6,
`check_transit` uses 3 and `check_foursquare` uses 3. Results are saved in `spikes/results/`.

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
