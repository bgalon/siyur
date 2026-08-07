# Implementation Plan: Plan a day, compile it, travel it offline

**Branch**: `002-plan-compile-offline` (worktree branch `agent/spec-002-plan-compile-offline`) | **Date**: 2026-08-07 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/002-plan-compile-offline/spec.md`

**Design authority**: `docs/design/tech-design.md` (§1.3 ItineraryV1, §1.4 BundleManifestV1, §5.2 planner graph, §5.3 compile pipeline, §5.5 the airplane-mode e2e trace), the schema cards `docs/data/itinerary.md` · `route-leg.md` · `bundle-manifest.md` · `tile-source.md` (**field-level ground truth — the card wins**), `docs/design/test-strategy.md` (tiers + 7-job CI), `docs/design/delivery-plan.md` (DU-04→DU-06), `docs/planning/methods-stack-reference.md` §1/§2/§5 (component choices, already researched), and ADR-0002/0003/0004/0008. **This plan composes existing design decisions; it does not invent them.** Five forced choices this slice introduces are drafted as ADR-0020…0024 (below), all `proposed`, awaiting Ben.

## Summary

Slice 002 is the second end-to-end vertical and **the one that makes Constitution Article I real**. A signed-in user states a day's shape (time available, walking limit, interests) over an area already researched by slice 001; the planner proposes an **`ItineraryV1`** — ordered stops drawn *only* from cited commons records, real walking legs, a timeline, and budgets — on the **Opus tier of the `ModelRouter` seam** (ADR-0004). Feasibility (distance, duration, opening windows) is computed by **deterministic machinery, never asserted by the model**. The plan then stops at an **explicit persisted HITL pause**; nothing compiles until the user approves.

On approval the **`compiler/` package** (empty since DU-00) runs the tech-design §5.3 pipeline: `pmtiles extract` over the itinerary bbox + buffer → Valhalla per-area build → legs + pruned walk graph → **quarantine filter** → freeze content → regenerate `ATTRIBUTION.md` → SHA-256 every artifact → write `BundleManifestV1`. The PWA downloads the whole archive into **OPFS** and, with the network off, renders map, itinerary, timeline, narration and off-route recovery **from the bundle alone**.

The slice also lands **narration ingestion** (Spec 002 Q1 = A): Wikivoyage/Wikipedia CC BY-SA prose adapted onto `SiteRecordV1.stories` with **per-article attribution**, passing the same quarantine filter as every other value.

**The deliverable that matters most is not code**: CI job 5 (`e2e-airplane`, "THE MERGE GATE") is today a `echo`-only green stub. This slice replaces it with a real Playwright/Chromium run asserting **zero network requests**. Until that lands, every milestone in this project has been gated by a stub.

## Technical Context

**Language/Version**: Python 3.12 (uv-managed) for `commons`/`planner`/`compiler`/`api`; TypeScript + Vite for `web`.

**Primary Dependencies** (existing pins unchanged; new ones resolved-then-pinned per ADR-0007):
- Planner seam: `pydantic-ai~=2.21` + `litellm~=1.94` behind `commons/llm.py` — **Opus 5 tier** for `propose_itinerary` (routing table already exists; this slice is its first Opus caller).
- **Routing (NEW — ADR-0020)**: Valhalla via official GHCR image, per-area tile build at compile, `pedestrian` costing; `/route` for leg geometry, `/sources_to_targets` for the feasibility time matrix. Reached over HTTP behind a `RoutingProvider` protocol so the OpenRouteService free key stays a zero-infra dev fallback and Tier 1 uses a recorded fixture.
- **Tiles (NEW — ADR-0021)**: `pmtiles extract` (go-pmtiles CLI) against the Protomaps daily build, `--bbox` from the itinerary extent + buffer, z0→maxzoom pyramid (clustered-source requirement).
- **Opening hours (NEW — ADR-0022)**: a deterministic evaluator for OSM `opening_hours` syntax, callable from Python. `opening_hours.js` is the canonical implementation but is JS + LGPL-3.0; the Python-callable options are resolved in [research.md](./research.md) §3. **Never the LLM.**
- **Narration (NEW — ADR-0024)**: MediaWiki Action API over Wikivoyage/Wikipedia, CC BY-SA 4.0, per-article attribution captured into the `SourceRef` at ingestion.
- Offline runtime: `pmtiles` v4 `FileSource` over an **OPFS** file handle inside a **module worker** (`worker.format:'es'`), MapLibre `5.19.x` custom protocol; `geojson-path-finder` v2 (ISC) for on-device recovery over the pruned walk graph.
- Storage: `fake-gcs-server` locally mirroring GCS for bundle objects.
- E2E: **Playwright + Chromium** — new to this repo; job 5 currently has no browser at all.

**Storage**: Cloud SQL Postgres + PostGIS (one instance). This slice adds **`user_plan`** (holds `ItineraryV1`, **row-scoped to the auth subject** — the PRD §13 #4 privacy boundary) and the planner's approval-pause state. Bundles are objects (GCS / fake-gcs), never rows. Alembic migration required — **`ask`-gated, Ben approves** (CLAUDE.md).

**Testing**: T1 `pytest` (planner node with mocked model, feasibility maths, quarantine filter, manifest hashing) · T2 `testcontainers`/CI service containers (PostGIS, Valhalla, fake-gcs) for the compile contract test · **T3 Playwright airplane-mode e2e — the release gate** · `vitest` for web units · `deepeval`/`agentevals` for the eval overlay (trajectory superset extended to `propose_itinerary`).

**Target Platform**: Cloud Run (FastAPI + SSE); static PWA on Cloud Storage + CDN; **Chromium-first** for the offline runtime (ADR-0002; iPhone/WebKit is an explicitly flagged future ADR). Local dev via `docker-compose` — this slice adds `valhalla` and `gcs` services to the existing `postgis`.

**Project Type**: Web — backend Python packages (`commons`, `planner`, `compiler`, `api`) + frontend (`web`). This slice is the first to give `compiler/` any content.

**Performance Goals**: Demo-day bundle well under the ≤200 MB budget (PRD §5; a compact old-town day is expected in single-digit MB). Valhalla per-area graph build 1–5 min, folded into compile. Feasibility re-check after any edit is one `sources_to_targets` call — cheap enough to re-run on every change.

**Constraints**: All geometry **EPSG:4326 (lon,lat)**. The LLM **ranks and orders; it never emits a coordinate, a distance, a duration, or a time** (FR-004 — Valhalla, shapely/PostGIS and the opening-hours evaluator do). **100%** of bundled values stamped; **zero** `bundleable=false` values in any bundle (FR-011/FR-012, merge-blocking). **Zero network requests** in the offline flow (FR-018 — the gate). Itineraries are **private, never written to the commons** (FR-007). Seam purity holds: no provider SDK above `commons/llm.py`. English-first, no RTL. Base plan only — no Plan B/C.

**Scale/Scope**: One user, one area, one day, one bundle per compile. IN: plan + HITL + compile + offline render + narration ingestion. OUT: Plan B/C variants, schematic map, rich dynamic timeline, meal anchors, per-claim narration provenance, multi-language/RTL, transit/driving, Cloud Run Jobs compilation, iOS.

## Constitution Check

*GATE: evaluated before Phase 0 and re-checked after Phase 1 design.*

| Article | Gate for this slice | Status |
|---|---|---|
| **I — Airplane-mode is the product** | This is the slice that *discharges* Article I rather than deferring it. The airplane-mode e2e stops being a stub and becomes a real Playwright run asserting zero network requests (FR-018 / SC-005). **Note the finding:** job 5 has been a green `echo` since DU-00, so no prior milestone was actually gated on it — this slice closes that gap, and until it lands the gate should not be described as passing. | PASS *(with finding)* |
| **II — Deterministic evals gate merges** | Adds deterministic, offline, merge-blocking evals: itinerary schema validity, **feasibility** (budgets + opening windows), bundle-manifest integrity (per-artifact + manifest hashes), the quarantine invariant extended to bundled narration, and the trajectory `superset` extended to `resolve_area → research → curate → propose_itinerary`. LLM-judge narration quality is threshold-gated **nightly, non-blocking** (Article II tiering) — narration prose is the first genuinely non-deterministic output in the product. | PASS |
| **III — Every decision is an ADR** | Five forced choices → **ADR-0020** routing engine (Valhalla + provider seam), **ADR-0021** tile source (Protomaps + `pmtiles extract`), **ADR-0022** opening-hours evaluator, **ADR-0023** HITL persisted-pause mechanism, **ADR-0024** narration source + CC BY-SA share-alike handling. All drafted `proposed`, `approved-by` blank — Ben ratifies. ADR-0002/0003/0004/0008 are reused, not re-litigated. | PASS |
| **IV — Every failure earns a regression eval** | No open FAIL entry is discharged by this slice. Two failure modes this slice is *likely* to produce are pre-armed with guardrails rather than waiting for the incident: an **un-noded walk graph** (geojson-path-finder's documented top field bug — silently disconnected islands) gets a connectivity assertion, and **SW/precache leakage of the PMTiles archive** gets the `maximumFileSizeToCacheInBytes` tripwire from ADR-0003. Any real failure still earns its FAIL-NNN + eval. | PASS |
| **V — Provenance is mechanical** | The compile quarantine filter is the mechanical enforcement point: `bundleable=false` values are **removed**, not flagged (FR-011), and unstamped input is refused (FR-012). `ATTRIBUTION.md` is regenerated per bundle with ODbL for OSM-derived data (incl. Valhalla-derived legs — routing over OSM is a Produced Work) and **per-article CC BY-SA credit** for every bundled story. Itineraries are personal data: private, row-scoped, never bundled as personal data and never published to the commons. | PASS |
| **VI — Instructions improve themselves** | Two new mechanical tripwires rather than review vigilance: the walk-graph connectivity assertion and the bundle-leak precache guard (above). The `RoutingProvider` seam mirrors the existing `ModelRouter` seam pattern so "don't reach a vendor directly" stays structural. | PASS |
| **VII — Prompts & models have a governed lifecycle** | `prompts/planner.md` v1 with Article VII front-matter (version, **dated** Opus 5 snapshot, date, eval link); `prompts/narration.md` v1 likewise. No floating aliases. Narration is the first prompt whose output is judged rather than schema-checked, so it is also the first to need the judge-model pinning discipline. | PASS |

**Post-Phase-1 re-check**: to be completed when `data-model.md`, `contracts/` and `quickstart.md` are written — no new violations anticipated; the data model is the three schema cards verbatim.

## Project Structure

### Documentation (this feature)

```text
specs/002-plan-compile-offline/
├── plan.md              # This file
├── research.md          # Phase 0 — resolved unknowns (routing, tiles, opening hours, HITL, narration, e2e)
├── data-model.md        # Phase 1 — ItineraryV1 / RouteLegV1 / BundleManifestV1 as realized here
├── quickstart.md        # Phase 1 — runnable validation of plan → compile → offline
├── contracts/           # Phase 1 — API contracts
│   ├── plans.md         #   propose (SSE), read, approve
│   ├── bundles.md       #   compile (SSE), manifest, artifact fetch
│   └── narration.md     #   story ingestion shape + attribution rules
└── checklists/
    └── requirements.md  # (present; all items pass, Q1 resolved)
```

### Source Code (repository root)

```text
commons/
├── models.py            #   EXTEND — ItineraryV1, Stop, Timeline, RouteLegV1, BundleManifestV1
├── db.py                #   EXTEND — user_plan (row-scoped), plan-approval state
├── opening_hours.py     #   NEW — deterministic opening-window evaluation (ADR-0022); never the LLM
├── routing.py           #   NEW — RoutingProvider protocol + Valhalla client + ORS dev fallback (ADR-0020)
└── sources/
    └── wikivoyage.py    #   NEW — MediaWiki API → Story with per-article CC BY-SA SourceRef (ADR-0024)

planner/
├── nodes/
│   ├── propose_itinerary.py  # NEW — Opus tier; ranks + orders ONLY. No coordinates, no arithmetic.
│   └── narrate.py            # NEW — adapts CC BY-SA article prose onto stories (per-article attribution)
├── feasibility.py            # NEW — budgets + opening windows, deterministic; the approval blocker
└── pipeline.py               # EXTEND — … → curate → propose_itinerary → [HITL pause] → compile

compiler/                # NEW CONTENT — empty package since DU-00
├── tiles.py             #   pmtiles extract over itinerary bbox + buffer (ADR-0021)
├── routes.py            #   Valhalla legs + pruned, NODED walk graph for offline recovery
├── quarantine.py        #   drop every bundleable=false value; refuse unstamped (the invariant)
├── attribution.py       #   regenerate ATTRIBUTION.md — ODbL + per-article CC BY-SA
├── manifest.py          #   SHA-256 per artifact + over the manifest; BundleManifestV1
├── storage.py           #   object store (GCS / fake-gcs) put + signed read
└── pipeline.py          #   the ordered §5.3 compile pipeline, in-process behind a flag

api/
├── plans.py             #   NEW — POST /plans (propose, SSE), GET /plans/{id}, POST /plans/{id}/approve
└── bundles.py           #   NEW — POST /bundles (compile, SSE), GET /bundles/{id}/manifest, artifacts

web/src/
├── plan/                #   NEW — itinerary panel, feasibility flags, provenance chips, approve affordance
├── bundle/              #   NEW — download manager → OPFS whole-archive + navigator.storage.persist()
│   └── opfs-worker.ts   #     module worker (worker.format:'es') — sync-access-handle reads
└── travel/              #   NEW — offline map/itinerary/timeline/narration + geojson-path-finder recovery

tests/
├── test_feasibility.py      #   budgets, opening windows, the infeasible-blocks-approval rule
├── test_hitl_gate.py        #   pause persists across restart; no compile without approval
├── test_compiler_*.py       #   quarantine, manifest hashes, attribution completeness
├── test_walk_graph.py       #   NODED-ness / connectivity tripwire (pre-armed guardrail)
└── e2e/                     #   NEW — Playwright airplane-mode gate (replaces the CI job-5 stub)

evals/
├── test_structural.py   #   EXTEND — quarantine over bundled narration; manifest integrity
└── test_trajectory.py   #   EXTEND — superset incl. propose_itinerary

prompts/
├── planner.md           #   NEW — v1, pinned dated Opus 5 snapshot (Article VII)
└── narration.md         #   NEW — v1, CC BY-SA adaptation prompt
```

**Structure Decision**: Web-application layout over the five already-scaffolded packages. `commons` gains the itinerary/bundle models plus two deterministic engines (`opening_hours`, `routing`) that exist specifically so the model cannot do that arithmetic; `planner` gains the Opus proposal node, the narration node, and the feasibility checker that gates approval; **`compiler` gets its first real content**, one module per ordered stage of tech-design §5.3 so the pipeline reads as its own spec; `api` gains plans + bundles; `web` gains three new areas (plan review, bundle download, offline travel). No new top-level package.

## Delivery mapping

| DU | User stories | Lands |
|---|---|---|
| **DU-04** Plan | US1 | models, `user_plan`, feasibility, `propose_itinerary`, HITL pause, `/plans`, plan UI, `prompts/planner.md`, trajectory eval, **ADR-0022/0023** |
| **DU-04.5** Narration | US4 | `sources/wikivoyage.py`, `narrate.py`, `prompts/narration.md`, per-article attribution, **ADR-0024** |
| **DU-05** Compile | US2 | all of `compiler/`, `/bundles`, docker-compose `valhalla` + `gcs`, **ADR-0020/0021** |
| **DU-06** Offline | US3 | OPFS transport swap, travel UI, recovery, **the real CI job-5 gate** — *M1 done* |

US4 (narration) is sequenced as **DU-04.5** because its output must exist in the commons before compile can freeze it, but it is independent of the plan/compile/travel spine and is the designated drop-candidate if the slice needs to shed scope.

## Risks

1. **Job 5 has never been a real gate.** Everything downstream of "CI green" in this repo's history rests on a stub for the airplane-mode check. Highest-value item in the slice; sequenced last by dependency, which is uncomfortable. Mitigation: stand the Playwright harness up *early* against the DU-00 empty map, so only the assertions grow.
2. **Valhalla in CI is heavy** (per-area graph build, minutes, disk). Mitigation: the `RoutingProvider` seam — Tier 1 runs a recorded fixture, Tier 2 runs the real container behind the `integration` marker, and CI job 3 already has the service-container pattern.
3. **Un-noded walk graph** silently yields disconnected islands (the documented top field bug for geojson-path-finder). Pre-armed with a connectivity tripwire rather than discovered in the field.
4. **CC BY-SA share-alike is contagious.** Bundled Wikivoyage prose drags share-alike onto derived narration text. ADR-0024 must state the obligation explicitly; `ATTRIBUTION.md` per-article credit is the discharge.
5. **CI workflow edits are `ask`-gated** (ADR-0006) and job 5's rewrite is security-adjacent (a browser in CI). Ben approves per edit; not batchable.

## Complexity Tracking

*No Constitution violations — table intentionally empty.*
