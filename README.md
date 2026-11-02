# Agentic City Planner

A multi-agent trip planner for any city, with popular places, metro stops and budget estimates.
Four agents: Orchestrator, Discovery, Planner and Reviewer.

See [ROADMAP.md](ROADMAP.md) for decisions, rules and phases, and [SPEC.md](SPEC.md) for the detailed plan.
Phases 0 to 3 are built: the tools, the agent loop, and the Discovery and Planner agents. The Reviewer and Orchestrator come next.

## Plan a trip

```bash
python -m city_planner.run Paris --days 3
python -m city_planner.run Tunis --days 3 --date 2026-12-07 --pace 3 --preferences "history, local food"
```

This uses your Gemini, Foursquare and Transitous quotas, and it prints every decision the agents made.
A 3-day trip used 5 to 6 LLM calls and 17 to 28 Transitous requests in live runs. Reasons are the agents' opinion, not verified data.

Public transport comes first. A walk of up to 20 minutes is offered as a choice, and a taxi only appears as a labelled backup
when there is neither. Opening hours are not checked, and visit lengths are estimates.

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
