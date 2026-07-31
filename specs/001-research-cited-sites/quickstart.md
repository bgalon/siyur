# Quickstart — validate the research→cited-commons→map slice

**Feature**: `specs/001-research-cited-sites` · **Date**: 2026-07-31

A runnable validation guide proving the slice works end-to-end. It exercises **US1** (research → cited sites on the map), **US2** (reuse + refresh), and **US3** (Greek→Latin display names). Implementation detail lives in `tasks.md` (next, via `/speckit-tasks`) and the code; this is the *acceptance* walk-through. **No live Overture/OSM/Anthropic calls in CI** — committed fixtures + a mocked LLM back every automated check (test-strategy.md).

## Prerequisites

- `uv sync` (Python 3.12 toolchain — already green at DU-00).
- Local stack up (mirrors cloud, tech-design §4): `docker-compose up postgres api web auth` → PostGIS `:5432`, API `:8000`, web `:5173`, Firebase Auth emulator `:9099`.
- Alembic migrations applied (`site`, `site_source`, `site_conflict`, `user_note`).
- A signed-in session via the Auth emulator (no real Google creds needed locally).
- Committed fixtures: a tiny **Overture places parquet** and a small **OSM/Overpass JSON** for the Rhodes bbox, plus one **non-Rhodes** area fixture (genericity).

## Scenario A — Research a delimited area, see cited sites on the map (US1)

1. Sign in (emulator) → obtain a bearer token.
2. `POST /areas` with `{"name":"Rhodes medieval old town"}` (or the demo bbox) → returns `area_id` + resolved `polygon`, `coverage.covered=false` (first time).
3. `POST /areas/{area_id}/research` → consume the SSE stream: `status` events per phase, `site` events for each persisted record, a `summary`.
4. Open the web map (`:5173`) → markers appear across the old town; each marker shows a **source + license attribution chip**; the map shows **"© OpenStreetMap contributors"**.

**Expected / pass criteria**
- **≥20** cited places rendered for the demo area (**SC-001**).
- **100%** of displayed values carry a source + license stamp; zero unstamped values shown (**SC-002 / FR-003**). Try to inject an unstamped value → it is **refused**, never displayed.
- Every `location` traces to Overture/OSM; a model-asserted coordinate is **rejected** (**FR-005**).
- Two sources disagreeing on a field ⇒ a recorded `FieldConflict`, **no source discarded** (**FR-009**).
- An **empty** area fixture ⇒ `summary.sites=0`, "nothing found", **zero fabricated places** (**SC-006**).

## Scenario B — Reuse an already-researched area + refresh (US2)

1. Re-run `POST /areas` for the **same** (or overlapping) area → `coverage.covered=true`, `known_site_count>0`, `refresh_available=true`.
2. Confirm the existing cited data shows (`GET /sites?bbox=…`) **without** a new research pass.
3. `POST /areas/{area_id}/research` with `{"force_refresh":true}` → completes; `summary.reused>0`, **`new` rows do not duplicate** existing places.

**Expected / pass criteria**
- Re-delimiting shows existing data with **no** fresh research pass, refresh always offered (**SC-003 / FR-006**).
- Refresh creates **no duplicate rows** (dedupe-on-write via the merge, ε=25m/τ=0.6) and stamps new `observed_at`; newly-disagreeing values become conflicts (**FR-009**). *(backs `test_commons_reuse_dedupe`, ADR-0008.)*
- A record written by session 1 is readable by a **different** session (shared, not private) — *(backs `test_commons_write_shared`, ADR-0008.)*

## Scenario C — Greek → Latin display names (US3)

1. In the Rhodes results, find a place whose source name is Greek only (e.g. `names.el = "Ρολόι"`).
2. Inspect the record via `GET /sites` → it carries `names.el` (original, attributed) **and** `names.el-Latn` (e.g. `"Roloi"`).
3. The map label shows the readable Latin/English form.

**Expected / pass criteria**
- **≥95%** of non-Latin display names show a Latin rendering; the original-script value + attribution preserved in **every** case (**SC-004 / FR-008**).
- The transliteration is **deterministic** (fixed input → fixed output, snapshot-tested).
- A value whose stored **script mismatches** its declared language (FAIL-001 case) is **normalised/flagged, not trusted** (US3 scenario 2). **Addresses are not transliterated.**

## Scenario D — Genericity (SC-005, standing eval)

1. Run Scenario A against the **non-Rhodes** area fixture (different character).
2. Confirm cited sites render with **no place-specific code changes**.

**Expected / pass criteria**
- The same flow produces cited sites on the map for ≥1 additional area (**SC-005**). *(eval: `test_genericity.py`; the full ≥3-area incl. unrehearsed bar is a milestone gate, not this slice.)*

## Automated gate mapping (CI — test-strategy.md jobs 1–7)

| Check | Tier / job | Proves |
|---|---|---|
| `SourcedValue`/`SiteRecordV1` schema + provenance completeness | T1 unit + deterministic eval | FR-003 / SC-002 |
| `test_no_unbundleable_in_bundle` (quarantine) | structural eval (merge-blocking) | Constitution V |
| geometry validity (Point/EPSG:4326, provenance) | T1 hypothesis + eval | FR-005 |
| `test_merge.py` (no source lost, conflicts, winner) | T1 unit + merge golden eval | FR-009 |
| `test_translit.py` (≥95%, original preserved, FAIL-001 guard) | T1 unit | FR-008 / SC-004 |
| `test_llm_seam.py` (no provider SDK above `commons/llm.py`) | T1 unit | ADR-0004 |
| `POST /areas`, `/research` SSE, `GET /sites` contracts, auth, `ST_Within` | T2 component over PostGIS | FR-001/004/006/007 |
| trajectory `superset` `resolve_area → research → curate` | deterministic eval (mocked LLM) | Constitution II |
| `test_genericity.py` (Rhodes + ≥1 other) | eval | SC-005 |
| caching-regression (`cache_read>0`, realistic prefix) + seam-capability gate | eval (ADR-0004) | ADR-0004 constraints |

All ten map back to the FRs/SCs above; **no scenario depends on connectivity beyond the online research phase** (Constitution I — this slice is Define→Research; `bundleable` stamped correct so the downstream offline gate holds).
