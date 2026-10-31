# Agentic City Planner

A multi-agent trip planner for any city, with popular places, metro stops and budget estimates.
Four agents: Orchestrator, Discovery, Planner and Reviewer.

See [ROADMAP.md](ROADMAP.md) for decisions, rules and phases. Phases 0 and 1 are built. The agents start in Phase 2.

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
