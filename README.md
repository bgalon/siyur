# Siyur (סיור — "a tour")

Plan a tour day of any city online with an LLM into a shared cited commons → compile
a self-contained offline bundle (PMTiles + MapLibre PWA + itinerary + narrations) →
travel guided with **zero connectivity, zero LLM**. This repo is also the dogfooded
case study for a GeoAI course — how it is built and documented is a first-class
deliverable.

> **Start here:** [`AGENTS.md`](AGENTS.md) (agent instructions + geo API traps),
> [`docs/planning/prd.md`](docs/planning/prd.md) (product contract),
> [`docs/design/`](docs/design/) (architecture, delivery plan, test strategy),
> [`docs/adr/`](docs/adr/) (decision records).

## Status

Governance + design phase, ramping into **DU-00** (the walking skeleton — see
`docs/design/delivery-plan.md`). Product packages appear incrementally during DU-00.

## Layout

| Path | What |
|---|---|
| `commons/` | Data spine: models, PostGIS access, merge, the `ModelRouter` seam (ADR-0004) |
| `planner/` | Typed planning pipeline over the model seam (PydanticAI + LiteLLM) |
| `compiler/` | Offline-bundle pipeline (tiles, routing, quarantine, manifest) |
| `api/` | FastAPI service (auth dependency, SSE endpoints) |
| `web/` | PWA (Vite + MapLibre + PMTiles + OPFS) — *JS app, not a Python package* |
| `evals/` · `tests/` | Three-tier eval harness + tests (`docs/design/test-strategy.md`) |
| `docs/` | Planning, design, ADRs, devlog, failures, data schema cards |

## Develop

```bash
uv sync                 # install (Python 3.12, pinned geo + app stack)
uv run pytest           # tests
uv run ruff check .     # lint
uv run mypy .           # types
```

Pins and traps for the geospatial libraries (Shapely 2.1 / h3 4.5 / OSMnx 2.1 /
GeoPandas 1.1) live in [`AGENTS.md`](AGENTS.md); never emit the pre-major idioms.
