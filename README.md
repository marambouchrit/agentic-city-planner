# Agentic City Planner

A multi-agent trip planner for any city, with popular places, metro stops and budget estimates.
Four agents: Orchestrator, Discovery, Planner and Reviewer.

See [ROADMAP.md](ROADMAP.md) for decisions, rules and phases. Phases 0 to 2 are built: the tools, the agent loop,
the Discovery agent and the LangGraph skeleton. The Planner, Reviewer and Orchestrator agents come next.

## Try the Discovery agent

```bash
python -m city_planner.run "Popular museums in Lisbon"
```

This uses your Gemini quota, and it prints every decision the agent made. Reasons are the agent's opinion, not verified data.

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

## License

[MIT](LICENSE)
