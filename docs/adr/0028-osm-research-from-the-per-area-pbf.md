# 0028 — Source OSM research from the per-area PBF, not Overpass

- Status: **proposed — deliberately deferred until M1 lands** (see Scheduling)
- Decision Maker(s): Ben
- drafted-by: claude-code (Opus 5) · approved-by: _pending_ · Date: 2026-08-07
- Would supersede: **ADR-0009**'s Overpass ingestion mechanism · Builds on **ADR-0020** (Valhalla per-area PBF) · Makes **ADR-0027** obsolete

## Context and Problem Statement

ADR-0027 fixed the *symptoms* of depending on a public Overpass instance: a better mirror, a retry policy that reads `Retry-After`, exponential backoff. It says so in its own title. What it cannot fix is the shape of the dependency — a free, shared, volunteer-run service, queried live, in the middle of a user-facing research pass, with a fair-use quota we do not control.

The observation that reframes it: **ADR-0020 already commits us to downloading a per-area OSM PBF extract at compile time**, to build the Valhalla routing graph. We will have the raw OSM data on disk regardless.

So the question stops being *"which Overpass instance?"* and becomes: **why are we fetching OSM twice, over two transports, with two failure modes, when one of them is a file we already have?**

Three things make this more than tidiness:

1. **Reproducibility.** A research pass over the same area today can return 781 records or 400, depending on someone else's load. SC-009 (genericity: the flow completes for a second area of different character) and the eval suite would both prefer a source that answers identically every time. Today the only reason CI is stable is that it never touches Overpass at all — it reads committed fixtures, so **the tests and production do not share a data path**.
2. **DU-04 raises the stakes.** Feasibility is built on `opening_hours`, which comes overwhelmingly from OSM. Under ADR-0022's fail-closed rule a missing value is `hours_unknown`, which blocks approval. A source that sheds records under load becomes a source that silently makes days unplannable.
3. **Coverage, not just reliability.** Overpass returns what our query asked for; a PBF contains the whole extract. Tag coverage stops being a function of query construction.

## Considered Options

- **A — Read OSM research data from the per-area PBF** (osmium/DuckDB clip by bbox), sharing the artifact ADR-0020 already fetches for Valhalla.
- **B — Keep Overpass** (ADR-0027's state) and accept live-service variance as the cost of a targeted query.
- **C — Both:** PBF as the source of record, Overpass as a fallback when a PBF is unavailable for an area. Two code paths, two failure modes, and the fallback would be exercised rarely enough to rot — the worst property a fallback can have.

## Decision Outcome

*Proposed:* **A**, scheduled after M1.

The research node would read from the same per-area PBF the compiler builds Valhalla tiles from, clipped to the area polygon. Overpass leaves the runtime path entirely; `commons/sources/osm.py` keeps its stamping, tag-mapping and BCP-47 handling — **the adapter's contract does not change**, only where its bytes come from. Every value stays `kind="osm"`, ODbL, with the OSMF-required attribution: a PBF extract is the same ODbL data by a different transport, and carries the same Produced-Work obligations already discharged for Valhalla's output.

### Scheduling — and why this is not being done now

**This ADR is written and then deliberately not acted on.** Spec 002 is already planned at 72 tasks across four DUs, and M1's critical path is the airplane-mode gate that has been a stub since DU-00. Pulling a new ingestion path into that slice trades a scheduled, bounded risk for an unscheduled one.

The judgement is that ADR-0027 buys enough headroom to get DU-04→06 built, and that this decision is better made when the PBF pipeline actually exists (DU-05) than speculatively against a pipeline that is still a task list. **Recorded now because the reasoning is fresh and the alternative — rediscovering it during DU-04 debugging — is how architecture decisions get made badly.**

Revisit when **any** of these is true: DU-05 lands and the PBF pipeline is real; a research pass loses records after ADR-0027's fix; or DU-04 feasibility starts failing on `hours_unknown` traceable to a degraded pass.

### Consequences (were it adopted)

- Good: **no rate limit, no 429, no third-party availability in the research path.** The failure that produced ADR-0027 becomes structurally impossible.
- Good: **research becomes reproducible offline** — the same PBF yields the same records, so tests and production could share a data path instead of tests reading fixtures while production reads a live API.
- Good: one OSM dependency instead of two, and the PBF download amortises across research *and* routing.
- Bad / accepted: **the PBF download moves earlier**, from compile to research — so the first research pass on an area pays a download the user does not currently wait for. Needs measuring, not assuming.
- Bad / accepted: **Geofabrik extracts are regional, not per-city.** A Rhodes day means fetching Greece (~200 MB) and clipping. Fine for a server, and it changes the cost model for a rarely-researched area in an unpopular region.
- Bad / accepted: a new parsing path (osmium/DuckDB over PBF) with its own dependency, its own pins, and its own failure modes — replacing an HTTP client that, whatever its faults, is well understood.
- Neutral: no licence change. ODbL either way, same attribution, same stamping.

### Confirmation (were it adopted)

- `tests/test_sources_osm.py` re-pointed at a **committed tiny PBF fixture**, asserting the same tag mapping, ODbL stamping and Greek `name:el` preservation the Overpass path asserts today — the adapter contract proven unchanged across the transport swap.
- A differential run over the Rhodes demo area — PBF vs Overpass — recorded **as evidence in this ADR**, quantifying what Overpass was actually missing. That number is the real justification and it does not exist yet.
- `evals/test_genericity.py` gains the property this makes possible: a research pass over a fixture area returns **byte-identical** records on repeat, which the live-service path cannot promise.
