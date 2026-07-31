# Implementation Plan: Research an area into a cited commons, rendered on the map

**Branch**: `001-research-cited-sites` (worktree branch `agent/du01-plan-001-research-cited-sites`) | **Date**: 2026-07-31 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-research-cited-sites/spec.md`

**Design authority**: `docs/design/tech-design.md` (§1 data spine, §2 storage, §5 M1 slice), `docs/data/poi-site.md` (SiteRecordV1 — field-level ground truth), `docs/design/test-strategy.md` (tiers + 7-job CI), `docs/design/delivery-plan.md` (DU-01→DU-03), and ADR-0002/0004/0008. **This plan composes existing design decisions; it does not invent them.** Two forced choices this slice introduces are drafted as ADR-0009 (source-adapter ingestion) and ADR-0010 (transliteration sliver), both `proposed`, awaiting Ben.

## Summary

Slice 001 is the first end-to-end vertical: a signed-in user delimits an area, the system **researches it into `SiteRecordV1` records drawn from Overture + OpenStreetMap**, merges/dedupes them into the **shared global commons** with every value provenance-stamped (`SourcedValue`: source + license + `bundleable`), and the PWA renders those cited places on the offline-capable MapLibre map, each with a source + license attribution chip and the ODbL credit. Re-delimiting a covered area **reuses** existing records (no re-research) and offers a **refresh**; non-Latin (Greek) source display-names get an automatic **Latin transliteration** on the display-name field only, with the original script + attribution preserved. Stories/narration (slice 002) and reviews (M2+) are OUT.

This plan spans **DU-01 (Define area)**, **DU-02 (Research, one source: Overture)**, and **DU-03 (Merge, add OSM)** of `delivery-plan.md`, plus the map-render + attribution-chip web work that makes the result visible. The planner `research`/`curate` nodes run over the **`ModelRouter` seam** (ADR-0004, PydanticAI + LiteLLM, **no langgraph**); the LLM curates/ranks and never emits or computes coordinates — DuckDB/PostGIS/shapely do.

## Technical Context

**Language/Version**: Python 3.12 (uv-managed) for `commons`/`planner`/`api`; TypeScript + Vite for `web`.

**Primary Dependencies** (all already pinned in `pyproject.toml` per ADR-0007 unless noted):
- Data spine / geo: `pydantic~=2.11`, `shapely~=2.1` (`unary_union`, `.geom_type`), `geopandas~=1.1`, `pyproj~=3.7`, `duckdb~=1.3`, `h3~=4.5` (`latlng_to_cell`/`grid_disk` — only if area tiling is needed).
- Ingestion: DuckDB `spatial` + `httpfs` extensions reading Overture cloud parquet; Overpass HTTP for OSM long-tail.
- Planner seam: `pydantic-ai~=2.21` + `litellm~=1.94` behind `commons/llm.py` (ADR-0004); Anthropic-native adapter (`anthropic` SDK added at DU-02, **below the seam only**).
- Service: `fastapi~=0.118`, `uvicorn~=0.34`, SSE for research progress.
- Persistence: `sqlalchemy~=2.0`, `alembic~=1.14`, `psycopg[binary]~=3.2` over Cloud SQL Postgres + PostGIS.
- Web: MapLibre GL JS `5.19.x` + `pmtiles` v4 protocol (basemap already stood up at DU-00).
- **Transliteration (NEW — ADR-0010)**: deterministic rule-based Greek→Latin (ICU `Greek-Latin` transform); exact package resolved-then-pinned at implementation per ADR-0007 discipline. **Not** the LLM.

**Storage**: Cloud SQL for PostgreSQL + PostGIS (one instance). M1 tables (tech-design §2): `site` (`id uuid`, `gers_id text`, `geom geometry(Point,4326)` GiST-indexed, `fields jsonb` = the `SourcedValue` map, `updated_at`), append-only `site_source` (provenance audit rows), `site_conflict`. Alembic migrations, identical local/cloud. Overture is read (not stored) via DuckDB over cloud parquet; a tiny committed Overture parquet fixture backs CI (test-strategy).

**Testing**: `pytest` + `pytest-asyncio` + `hypothesis` (geometry props) + `syrupy` (snapshots) for T1; `testcontainers` locally / GitHub Actions service containers (PostGIS) for T2; `deepeval` + `agentevals` for the eval overlay. Web: `vitest`. E2e (Playwright) is not extended by this slice beyond the DU-00 empty-map offline gate (this is the online phase).

**Target Platform**: Cloud Run (FastAPI + SSE); static PWA on Cloud Storage + CDN; PostGIS on Cloud SQL. Browser render Chromium-first. Local dev via `docker-compose` (postgis, api, web, auth emulator) mirroring cloud.

**Project Type**: Web — backend Python packages (`commons`, `planner`, `api`) + frontend (`web`); this slice touches all four (see Project Structure).

**Performance Goals**: For the Rhodes demo area, a single research action populates the map with **≥20 cited places** (SC-001); research degrades gracefully to partial results on a slow/failed source (FR-012). Prompt-cache hit on repeated same-area research (ADR-0004 lever), asserted against a realistically-sized prefix.

**Constraints**: All geometry **EPSG:4326 (lon,lat)**; the LLM **never** emits or computes coordinates (FR-005). **100%** of displayed values provenance-stamped, zero unstamped shown (SC-002 / FR-003 — the quarantine invariant). **Nothing hardcoded to Rhodes** (FR-001 / SC-005 genericity eval). **Seam purity**: no provider SDK above `commons/llm.py`. Records write **directly** to the shared commons, auth-gated, deduped by the merge rules (ADR-0008 / FR-007). English-first, no RTL.

**Scale/Scope**: One area per research pass; commons is global + shared and accumulates across users/sessions. Slice bounds: POI **locate + cite** only from Overture + OSM; reuse + refresh; Greek→Latin **display-name** transliteration sliver. OUT: stories/narration (002), reviews (M2+), planning/compile/travel, address transliteration (source scripts untrustworthy — FAIL-001).

## Constitution Check

*GATE: evaluated before Phase 0 and re-checked after Phase 1 design. All seven articles pass; no violations → Complexity Tracking left empty.*

| Article | Gate for this slice | Status |
|---|---|---|
| **I — Airplane-mode is the product** | This slice is the **online** Define→Research phase; nothing new reaches the traveller. The offline guarantee is upheld structurally by stamping `bundleable` **correctly at ingestion** so the downstream bundle filter (DU-05) holds. The DU-00 empty-map offline gate is not regressed. | PASS |
| **II — Deterministic evals gate merges** | Slice adds deterministic, offline, merge-blocking evals: schema validity incl. per-field provenance; `test_no_unbundleable_in_bundle` quarantine invariant; geometry validity (Point/EPSG:4326); "no source ref lost on merge"; genericity (Rhodes + ≥1 other area); trajectory `superset` on `resolve_area → research → curate`. LLM-judge quality evals are non-blocking here (no narration yet). | PASS |
| **III — Every decision is an ADR** | Reuses ADR-0002/0004/0008 (not re-litigated). Two new forced choices → **ADR-0009** (source-adapter ingestion Overture+OSM) and **ADR-0010** (transliteration sliver), both drafted `proposed`, `approved-by` blank — Ben ratifies. | PASS |
| **IV — Every failure earns a regression eval** | **FAIL-001** (Hebrew address stored in Cyrillic — untrustworthy source script) is discharged by the script-validation/transliteration guard (US3 scenario 2) shipped as an eval in this slice; address transliteration is deliberately excluded for the same reason. | PASS |
| **V — Provenance is mechanical** | The spine of the slice: `SourcedValue` stamps `source+license+bundleable` at ingestion; unstamped input refused and never displayed; ODbL "© OpenStreetMap contributors" renders on every map; `DATA-LICENSES.md` is the registry. User notes stay private, never auto-published (FR-010). | PASS |
| **VI — Instructions improve themselves** | The seam-purity tripwire (`tests/test_llm_seam.py`, lands DU-02) and the geo-API pins tripwire are mechanical hooks, not vigilance. Per-package `AGENTS.md` invariants already in place. | PASS |
| **VII — Prompts & models have a governed lifecycle** | `prompts/research.md` v1 with front-matter (version/model/date/eval link); app model pinned to dated snapshots via the routing table (Haiku=research, Sonnet=curate); no floating aliases. | PASS |

**Post-Phase-1 re-check**: design artifacts (data-model.md, contracts/, quickstart.md) introduce no new violations — the data model is `SiteRecordV1` verbatim from the schema card; contracts expose only auth-gated area/research/site reads; no provider SDK leaks above the seam. **PASS.**

## Project Structure

### Documentation (this feature)

```text
specs/001-research-cited-sites/
├── plan.md              # This file
├── research.md          # Phase 0 — resolved unknowns (ingestion, transliteration, reuse, dedupe)
├── data-model.md        # Phase 1 — SiteRecordV1/SourcedValue as realized in this slice
├── quickstart.md        # Phase 1 — runnable validation of the research→map slice
├── contracts/           # Phase 1 — API contracts (areas, research SSE, sites)
│   ├── areas.md
│   ├── research.md
│   └── sites.md
└── checklists/
    └── requirements.md   # (already present; all items pass)
```

### Source Code (repository root — packages already scaffolded at DU-00)

```text
commons/                 # the data spine + shared invariants (commons/AGENTS.md governs)
├── models.py            #   SourcedValue, SourceRef, SiteRecordV1, FieldConflict (pydantic; from poi-site.md)
├── licenses.py          #   the quarantine allowlist + bundleable(license) — DATA-LICENSES.md machinery
├── db.py                #   SQLAlchemy models + session for site / site_source / site_conflict
├── merge.py             #   per-field union-first merge (ε=25m, τ=0.6 same-language, no source lost)
├── translit.py          #   Greek→Latin display-name sliver (ADR-0010; deterministic, offline)
├── llm.py               #   the ModelRouter seam (ADR-0004) — the ONLY place a provider SDK may import
└── sources/             #   the source-adapter pattern (ADR-0009)
    ├── base.py          #     SourceAdapter protocol → yields stamped SourcedValues
    ├── overture.py      #     DuckDB over Overture cloud parquet (places theme)
    └── osm.py           #     Overpass long-tail → OSM tags

planner/                 # typed pipeline over the seam (planner/AGENTS.md governs)
├── nodes/
│   ├── resolve_area.py  #   name/box → polygon (Overture divisions; Nominatim disambig fallback)
│   ├── research.py      #   fan out to source adapters → stamped SiteRecordV1s (Haiku tier)
│   └── curate.py        #   rank/dedupe candidates, drive merge (Sonnet tier); NEVER emits coords
└── pipeline.py          #   resolve_area → research → curate → (persist to commons)

api/                     # FastAPI service (api/AGENTS.md governs — auth is security-critical)
├── auth.py              #   Google-OIDC JWT-verify dependency → user_id (Firebase emulator locally)
├── areas.py             #   POST /areas (resolve + ST_Within coverage), POST /areas/{id}/research (SSE)
└── sites.py             #   GET /sites?bbox=… → cited SiteRecordV1s for the map

web/                     # PWA (Vite + MapLibre; web scaffold from DU-00)
├── src/map/sites.ts     #   fetch /sites, render markers, source+license attribution chip per value
└── src/map/attribution.ts #  ODbL "© OpenStreetMap contributors" control (renders whenever OSM shown)

tests/                   # T1/T2 product tests (test-strategy.md)
├── test_llm_seam.py     #   NEW at DU-02 — no anthropic/openai/litellm import above commons/llm.py
├── test_merge.py        #   no source lost, conflict creation, winner policy
├── test_translit.py     #   Greek→Latin ≥95%, original preserved, script-validation (FAIL-001)
└── ... (unit + component)

evals/                   # deterministic (PR-gating) + structural
├── test_structural.py   #   test_no_unbundleable_in_bundle (quarantine), schema+provenance completeness
├── test_trajectory.py   #   superset match resolve_area → research → curate
└── test_genericity.py   #   Rhodes + ≥1 other area, no place-specific code

prompts/
└── research.md          #   v1 research/curate prompt, pinned model, front-matter (Article VII)
```

**Structure Decision**: Web application layout, mapping onto the four already-scaffolded packages plus `tests`/`evals`/`prompts`. `commons` owns the data model, licenses, merge, transliteration, the seam, and the new `sources/` adapters; `planner` owns the typed pipeline nodes over the seam; `api` exposes the auth-gated endpoints; `web` renders sites + attribution. No new top-level package is introduced.

## Complexity Tracking

*No Constitution violations — table intentionally empty.*
