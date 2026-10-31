# Agentic City Planner

A multi-agent trip planner for any city, with popular places, metro stops and budget estimates.
Four agents: Orchestrator, Discovery, Planner and Reviewer.

See [ROADMAP.md](ROADMAP.md) for decisions, rules and phases. Phase 0 is done; Phase 1 is next.

## Run the Phase 0 checks

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
```

`check_gemini` uses about 12 requests, `check_maps_grounding` uses 6 and `check_transit` uses 3.
Results are saved in `spikes/results/`.

## Data sources

Public transit routes come from [Transitous](https://transitous.org/), using these
[data sources](https://transitous.org/sources/) and data from
[OpenStreetMap](https://www.openstreetmap.org/copyright) contributors.

## License

[MIT](LICENSE)
