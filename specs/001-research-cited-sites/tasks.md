# Tasks: Research an area into a cited commons, rendered on the map

**Feature**: `specs/001-research-cited-sites` · **Date**: 2026-08-01 · **Branch base**: `main`

**Input**: [spec.md](./spec.md) · [plan.md](./plan.md) · [data-model.md](./data-model.md) · [research.md](./research.md) · [contracts/](./contracts/) · [quickstart.md](./quickstart.md)

**Design authority**: `docs/data/poi-site.md` (schema ground truth — the card wins), `docs/design/tech-design.md` §1–§2/§5, `docs/design/test-strategy.md` (tiers + 7-job CI), ADR-0002/0004/0007/0008/0009/0010.

**Delivery mapping**: this file spans **DU-01** (define area) → **DU-02** (research, Overture) → **DU-03** (merge, +OSM) of `docs/design/delivery-plan.md`, plus the map-render work that makes the result visible.

## Conventions

- **Tests are REQUIRED** for this feature. The constitution (Article II) makes deterministic evals merge-blocking and `test-strategy.md` defines the tiers, so test tasks are first-class here, not optional.
- **`[P]`** = parallelizable: touches files no other in-flight task touches, and its dependencies are already complete.
- **`[USn]`** = the user story the task serves. Setup/Foundational/Polish tasks carry no story label.
- One branch per task-group per ADR-0005 (`agent/<ticket>-<slug>`), merged via PR with CI checks 1–7 green.
- **Geo-API pins** (AGENTS.md): shapely `unary_union`/`.geom_type`, h3 `latlng_to_cell`/`grid_disk`, OSMnx 2.x, GeoPandas 1.x. `tests/test_geo_api_pins.py` fails CI on stale calls.
- **CRS**: EPSG:4326 (lon, lat) everywhere. The LLM never emits or computes a coordinate.

---

## Phase 1: Setup (shared infrastructure)

**Purpose**: dependencies, local/CI database parity, and the committed fixtures every later tier reads. No product behaviour.

- [x] T001 Add slice-001 runtime dependencies to `pyproject.toml`, resolved-then-pinned per ADR-0007 (`duckdb~=1.3`, `sqlalchemy~=2.0`, `alembic~=1.14`, `psycopg[binary]~=3.2`, `geoalchemy2`, `httpx`, `pydantic-ai~=2.21`, `litellm~=1.94`, `anthropic`, and the ICU transliteration package chosen in T028), then `uv lock` and commit `uv.lock`
- [x] T002 [P] Create `docker-compose.yml` with a `postgis/postgis:16-3.4` service (db `siyur`, exposed `5432`) so local T2 runs match the CI service container
- [x] T003 [P] Initialise Alembic in `alembic/` (`alembic.ini`, `alembic/env.py` reading `SIYUR_DATABASE_URL` from the environment — never a `.env` file) targeting `commons.db` metadata
- [x] T004 [P] Commit a tiny Overture **places** parquet fixture at `tests/fixtures/overture_places_rhodes.parquet` (≤200 rows, includes ≥1 Greek-only name and ≥1 Foursquare/Apache-2.0-licensed row alongside CDLA rows) plus a `tests/fixtures/README.md` recording how it was extracted
- [x] T005 [P] Commit an Overpass response fixture at `tests/fixtures/overpass_rhodes.json` (includes ≥1 record that spatially matches an Overture fixture row for merge testing, ≥1 Greek `name:el`, and a 504-failure fixture `tests/fixtures/overpass_504.txt`)
- [x] T006 Add a PostGIS service container to the `integration` job (job 3) of `.github/workflows/ci.yml` and export `SIYUR_DATABASE_URL` to it *(workflow edit — per-edit review, ADR-0006)*

**Checkpoint**: `uv sync` succeeds, `docker compose up -d` yields a reachable PostGIS, fixtures are committed.

---

## Phase 2: Foundational (BLOCKING — every user story depends on this)

**Purpose**: the data spine, the persistence layer, and the model seam. **No user story can start until this phase completes.**

### The data spine

- [x] T007 Implement `SourceRef`, `SourcedValue`, `FieldConflict`, and `SiteRecordV1` as pydantic v2 models in `commons/models.py`, **verbatim to `docs/data/poi-site.md`** — `SourceRef.kind` as the card's literal enum, `names` keyed by BCP-47 subtag, `location` an EPSG:4326 Point, `schema_ver` the literal `"SiteRecordV1"`
- [x] T008 Enforce the ingestion refusal rule in `commons/models.py`: a bare/unstamped field value fails validation (never constructible), so an unstamped value can never be persisted or displayed (FR-003 / SC-002)
- [x] T009 [P] Implement the license quarantine allowlist and `bundleable(license, kind)` in `commons/licenses.py`, driven by `DATA-LICENSES.md`; `kind ∈ {open_web, review_provider}` ⇒ always `False`; `bundleable=True` ⟺ license ∈ allowlist
- [x] T010 [P] Add geometry validation helpers in `commons/geo.py` — valid `Point`, lon ∈ [-180,180], lat ∈ [-90,90], shapely 2.x API only (`.geom_type`, `unary_union`)
- [x] T011 Unit-test the spine in `tests/test_models.py` — construction, the unstamped-value refusal (T008), BCP-47 name keys, `schema_ver` literal, empty `stories` valid
- [x] T012 [P] Unit-test quarantine in `tests/test_licenses.py` — allowlist ⟺ `bundleable`, `open_web`/`review_provider` always `False`
- [x] T013 [P] Property-test geometry in `tests/test_geo.py` with `hypothesis` — generated lon/lat round-trip, out-of-range rejected (validation rule 3)

### Persistence

- [x] T014 Define SQLAlchemy models in `commons/db.py` — `site` (`id uuid pk`, `gers_id text`, `geom geometry(Point,4326)` GiST-indexed, `fields jsonb`, `updated_at timestamptz`), append-only `site_source`, `site_conflict`, and row-scoped `user_note`; plus a session factory reading `SIYUR_DATABASE_URL`
- [x] T015 Generate the initial Alembic migration in `alembic/versions/` creating the four tables, the PostGIS extension, and the GiST index on `site.geom`
- [x] T016 Add the T2 database harness in `tests/conftest.py` — a PostGIS fixture (testcontainers locally, CI service container when `SIYUR_DATABASE_URL` is set) that runs migrations and truncates between tests
- [x] T017 Integration-test persistence round-trip in `tests/test_db.py` — write a `SiteRecordV1`, read it back with `fields` intact and `geom` matching `location.value`

### The model seam (ADR-0004)

- [x] T018 [P] Implement the `ModelRouter` seam in `commons/llm.py` — per-task tier routing (Haiku 4.5 = research, Sonnet 5 = curate, Opus 5 = plan) pinned to dated snapshots, over PydanticAI + LiteLLM; **the only module permitted to import a provider SDK**
- [x] T019 [P] Gate per-tier capabilities in `commons/llm.py` behind a `SUPPORTS_ADAPTIVE_EFFORT` set so adaptive-thinking / `output_config.effort` is never sent to Haiku 4.5 (which 400s on it — planner-spike constraint 2)
- [x] T020 Add the seam-purity tripwire `tests/test_llm_seam.py` — AST-scan `commons/`, `planner/`, `api/`, `compiler/` and fail if any module **other than** `commons/llm.py` imports `anthropic`, `openai`, or `litellm`
- [x] T021 [P] Unit-test the capability gate in `tests/test_llm_router.py` — routing table resolves the right pinned snapshot per task, and the adaptive-effort kwarg is stripped for non-supporting tiers

**Checkpoint**: the spine validates, PostGIS round-trips, the seam is pure. **User stories may now start in parallel.**

---

## Phase 3: User Story 1 — Research a delimited area and see cited sites on the map (P1) 🎯 MVP

**Goal**: a signed-in user delimits an area, triggers research, and sees real Overture+OSM places on the map, each with a source + license attribution chip and zero unstamped values.

**Independent test**: sign in → delimit the Rhodes old-town bbox → trigger research → cited markers appear, each with a visible source + license chip, ODbL credit rendered, no unstamped value shown.

### Source adapters (ADR-0009)

- [x] T022 [P] [US1] Define the `SourceAdapter` protocol in `commons/sources/base.py` — `fetch(polygon) -> Iterable[SiteRecordV1]`, every emitted value stamped at ingestion; a candidate arriving without a resolvable source is dropped with a counted reason, never stamped optimistically
- [x] T023 [US1] Implement the Overture adapter in `commons/sources/overture.py` — DuckDB (`spatial` + `httpfs`) over Overture cloud parquet filtered by polygon bbox, mapping `names`/`categories`/`addresses` and reading the **per-record** license (CDLA-Permissive-2.0 vs Apache-2.0 differ *within* the places theme — never assume the theme default)
- [x] T024 [US1] Implement the OSM adapter in `commons/sources/osm.py` — Overpass query over the polygon, mapping tags → `names` (incl. `name:el`) / `categories` / `address` / `opening_hours`, every value stamped `kind="osm"`, `license="ODbL-1.0"`, `attribution="© OpenStreetMap contributors"`
- [x] T025 [US1] Make OSM ingestion degrade gracefully in `commons/sources/osm.py` — bounded timeout + retry, a 504/timeout yields partial results with a `degraded` flag and a reason, never a hang and never a lost partial (FR-012)
- [x] T026 [P] [US1] Test the Overture adapter in `tests/test_sources_overture.py` against the T004 parquet fixture — per-record license honoured, every value stamped, coordinates come from the fixture (never synthesised)
- [x] T027 [P] [US1] Test the OSM adapter in `tests/test_sources_osm.py` against the T005 fixtures — tag mapping, ODbL stamping, and the 504 path yielding `degraded=True` with partial results retained

### Merge (DU-03 core)

- [x] T028 [US1] Implement per-field union-first merge in `commons/merge.py` — join on `gers_id`, else spatial ≤ ε=25m **AND** name-similarity ≥ τ=0.6 (same language, post-transliteration); **distance alone never merges** (validation rule 5)
- [x] T029 [US1] Record disagreements as `FieldConflict`s in `commons/merge.py` — every distinct source ref stays reachable as either the winning `SourcedValue` or a conflict candidate; no source is ever discarded (FR-009 / validation rule 4)
- [x] T030 [US1] Implement the commons upsert in `commons/repository.py` — write `site`, append `site_source` provenance rows, write `site_conflict`; refuse any `source.kind="user"` value at the commons boundary (FR-010 / validation rule 7)
- [x] T031 [P] [US1] Test merge in `tests/test_merge.py` — no source lost, conflict creation on disagreement, winner policy, ε/τ boundary cases, and that distance-alone does **not** merge two differently-named neighbours

### Pipeline (planner nodes)

- [x] T032 [US1] Implement `planner/nodes/resolve_area.py` — name/bbox/polygon → resolved polygon via Overture divisions, Nominatim only as a disambiguation fallback; returns candidates when ambiguous
- [x] T033 [US1] Implement `planner/nodes/research.py` — fan out to the source adapters over the polygon, collect stamped records, report per-source counts and degradation (Haiku tier via the seam)
- [x] T034 [US1] Implement `planner/nodes/curate.py` — rank/dedupe candidates and drive `commons/merge.py` (Sonnet tier); the model ranks and never emits or computes a coordinate (FR-005)
- [x] T035 [US1] Wire `planner/pipeline.py` — `resolve_area → research → curate → persist`, emitting per-phase progress events consumable by SSE
- [x] T036 [P] [US1] Test the pipeline in `tests/test_pipeline.py` with a mocked model (no API key) — schema-valid output, phase sequence emitted, model-asserted values without a source are rejected

### API

- [x] T037 [US1] Implement `POST /areas` in `api/areas.py` per `contracts/areas.md` — resolve polygon, run the `ST_Within` coverage query, return `area_id`/`polygon`/`coverage`; `401` unauthenticated, `422` on missing/invalid geometry, `404` with candidates when a name is unresolvable
- [x] T038 [US1] Implement `POST /areas/{area_id}/research` in `api/areas.py` per `contracts/research.md` — drive the pipeline, stream `status`/`site`/`summary`/`done` SSE events, `409` when a pass is already running for the area
- [x] T039 [US1] Implement `GET /sites?bbox=…` in `api/sites.py` per `contracts/sites.md` — PostGIS bbox filter, return stamped records plus the union `attribution[]`; reject unstamped rows at the DB boundary; `401`/`422` per contract
- [x] T040 [P] [US1] Contract-test areas + sites in `tests/test_api_areas.py` and `tests/test_api_sites.py` over real PostGIS — auth `401`s, `422`s, `ST_Within` coverage count matches the fixture, every returned value carries a non-null source, ODbL string present for OSM-derived sites
- [x] T041 [P] [US1] Contract-test the research SSE stream in `tests/test_api_research.py` — event sequence, all-values-stamped invariant, degraded-source reporting, and `summary.sites=0` with zero fabricated places on an empty area (SC-006)

### Web (map render)

- [x] T042 [P] [US1] Implement `web/src/map/sites.ts` — fetch `GET /sites` for the viewport bbox and render a marker per site; display name prefers `en` → `<lang>-Latn` → source-script
- [x] T043 [P] [US1] Implement the attribution chip in `web/src/map/attribution-chip.ts` — per displayed value, render source kind + license **only from the value's stamp**; the client never invents attribution
- [x] T044 [US1] Extend `web/src/map/attribution.ts` so the ODbL control renders "© OpenStreetMap contributors" whenever any returned value is ODbL, driven by the response `attribution[]`
- [x] T045 [P] [US1] Test the web layer in `web/test/sites.test.ts` and `web/test/attribution-chip.test.ts` (vitest) — marker rendering from a mock response, chip text derived only from the stamp, a value lacking a source is never rendered, ODbL control appears for an OSM-derived fixture

### Story evals (merge-blocking, Article II)

- [x] T046 [P] [US1] Add provenance-completeness + quarantine evals in `evals/test_structural.py` — 100% of values on a researched fixture area carry a source (SC-002) and `test_no_unbundleable_in_bundle` holds
- [x] T047 [P] [US1] Add the geometry-provenance eval in `evals/test_structural.py` — every `location` traces to an `overture`/`osm` source ref; a record whose location is absent from source geodata is never synthesised (FR-005)
- [x] T048 [P] [US1] Add the trajectory eval in `evals/test_trajectory.py` — emitted phases are a `superset` of `resolve_area → research → curate` (mocked model, offline)

**Checkpoint**: US1 is independently demoable — research the Rhodes bbox, see ≥20 cited markers with chips and ODbL credit (SC-001, SC-002).

---

## Phase 4: User Story 2 — Reuse already-researched areas and offer a refresh (P2)

**Goal**: re-delimiting a covered area shows existing cited data with no new research pass, and always offers a refresh.

**Independent test**: research the demo area once; delimit the same/overlapping area again → existing data appears with no research pass, and a refresh option is present.

- [x] T049 [US2] Implement the coverage/reuse decision in `commons/repository.py` — `ST_Within` count, `stalest_observed_at` (min `observed_at`), and `refresh_available` whenever covered (FR-006)
- [x] T050 [US2] Honour `force_refresh` in `api/areas.py` — `false` over a covered area is a no-op returning a reuse hint; `true` re-runs and merges into existing records
- [x] T051 [US2] Make refresh non-destructive in `commons/merge.py` — a refresh enriches with new `observed_at` values and new `FieldConflict`s and never overwrites a prior source
- [x] T052 [P] [US2] Add the reuse surface to `web/src/map/sites.ts` — when `coverage.covered`, show existing sites plus an explicit refresh affordance instead of auto-researching
- [x] T053 [P] [US2] Component-test reuse in `tests/test_api_areas.py` — re-`POST /areas` after research yields `covered=true` + `refresh_available=true` (backs `test_commons_reuse_dedupe`, ADR-0008)
- [x] T054 [P] [US2] Test dedupe-on-refresh in `tests/test_repository.py` — `force_refresh=true` over a covered area creates **zero** duplicate `site` rows and loses no source ref
- [x] T055 [P] [US2] Test cross-session sharing in `tests/test_repository.py` — a record written by one user is readable by a different signed-in user (backs `test_commons_write_shared`, ADR-0008)

**Checkpoint**: US2 works on top of US1 without modifying US1 behaviour.

---

## Phase 5: User Story 3 — Non-Latin place names are readable (P3)

**Goal**: Greek-only source names get an automatic Latin display rendering; the original script and its attribution are preserved in every case.

**Independent test**: research the demo area → places with Greek source names show a readable Latin form, original + attribution preserved.

- [x] T056 [US3] Implement the script-validation guard in `commons/translit.py` — assert a value's script matches its declared language before deriving; a mismatch (Cyrillic in an `el`/`he` field — FAIL-001) is flagged and never trusted
- [x] T057 [US3] Implement deterministic Greek→Latin transliteration in `commons/translit.py` using the ICU `Greek-Latin` transform (offline, rule-based, **never the LLM**); pin the resolved ICU package in `pyproject.toml` per T001
- [x] T058 [US3] Emit the derived name under the `el-Latn` BCP-47 key with its `SourceRef` **inherited** from the source-script value (same license, attribution, and `bundleable` — produced-work chain), `confidence` = transliteration certainty, `observed_at` = derivation date (ADR-0010)
- [x] T059 [US3] Wire transliteration into `planner/nodes/curate.py` so derivation runs at curate time on display names **only** — addresses are never transliterated (FAIL-001)
- [x] T060 [P] [US3] Test transliteration in `tests/test_translit.py` — ≥95% of Greek fixture names get a Latin rendering (SC-004), the original `el` value + attribution survive in **every** case, provenance is inherited not invented, and the FAIL-001 mismatch case is flagged
- [x] T061 [P] [US3] Add the FAIL-001 regression eval in `evals/test_structural.py` — a fixture record whose stored script contradicts its language tag is caught by the guard (Article IV: every failure earns a regression eval)
- [x] T062 [P] [US3] Test display-name preference in `web/test/sites.test.ts` — `en` → `<lang>-Latn` → source-script fallback order renders correctly for a Greek-only fixture

**Checkpoint**: all three user stories work independently and together.

---

## Phase 6: Polish & cross-cutting concerns

- [x] T063 [P] Add the genericity eval in `evals/test_genericity.py` — run the flow on Rhodes **and** ≥1 area of different character with no place-specific code change, and AST-scan `commons`/`planner`/`api` for place literals or hardcoded bboxes (SC-005 / validation rule 8)
- [x] T064 [P] Author `prompts/research.md` v1 with Article VII front-matter (version, pinned dated model snapshot, date, eval link) covering the research + curate prompts
- [x] T065 [P] Add the caching-regression eval in `evals/test_caching.py` — re-aimed at the **`curate`** (Sonnet) tier, the only node that crosses the model seam and the only caller passing `cache_prefix=True`, because **`research` makes no model call** (ADR-0014); assert the breakpoint covers the stable ranking prefix (not the per-request record list), that a repeated pass re-presents it byte-identically, and that it is **realistically sized** against the tier's published minimum (1,024 tok Sonnet 5 / 512 tok Opus 5 / 4,096 tok Haiku 4.5 — the "2,048 tok Sonnet" originally written here is the Opus 4.7 / Haiku 3.5 figure) so a `cache_read=0` false-pass is impossible (planner-spike constraint 1)
- [x] T066 [P] Add Overture + OSM rows to `DATA-LICENSES.md` recording per-record license variance (CDLA-Permissive-2.0 / Apache-2.0 / ODbL-1.0) and each one's `bundleable` disposition
- [x] T067 [P] Update `docs/data/poi-site.md` and the schema cards touched by this slice **only if** implementation revealed a genuine schema gap — otherwise record explicitly that the card stood unchanged
- [x] T068 Update `specs/001-research-cited-sites/quickstart.md` so its runnable validation matches the shipped endpoints and commands
- [x] T069 Verify all seven CI gates green on the integration branch and confirm the DU-00 airplane-mode e2e is not regressed (Constitution Article I)
- [ ] T070 Close the slice with `/devlog`, an exhibit-tag candidate per DU (`exhibit/U4-area-resolution`, `exhibit/U4-duckdb-overture`, `exhibit/U3-grounding`, `exhibit/U3-merge-provenance`), and a `/failure` entry + regression eval for any real failure hit along the way

---

## Dependencies

```
Phase 1 (Setup: T001–T006)
        │
        ▼
Phase 2 (Foundational: T007–T021)   ◄── BLOCKS EVERYTHING
        │
        ├──────────────┬──────────────┐
        ▼              ▼              ▼
   US1 (T022–T048)  US3 (T056–T062)  Web T042–T045
   🎯 MVP            (needs T007 only  (needs contracts only —
        │             for the name      startable during Phase 2)
        ▼             model)
   US2 (T049–T055) ◄── needs US1's research+persist path
        │
        ▼
Phase 6 (Polish: T063–T070)
```

**Story independence**:
- **US1** depends only on Phase 2. It is the MVP and ships alone.
- **US2** depends on US1 (there must be researched data to reuse).
- **US3** depends on Phase 2's `commons/models.py` and integrates at `curate` (T059); its transliteration engine (T056–T058) is fully parallel with US1.

**Within-phase blockers**:
- T007 blocks T008–T013, T014, T022–T030, T056–T058.
- T014/T015 block T016, T017, T030, T037–T041, T049.
- T022 blocks T023, T024.
- T023/T024 block T028; T028 blocks T029; T029 blocks T030.
- T032–T035 block T036, T038.
- T018 blocks T019, T021; T020 is independent of both.
- T057 blocks T058; T058 blocks T059.

---

## Parallel execution opportunities

**Phase 1** — T002, T003, T004, T005 all run together (T001 first; T006 is a workflow edit under per-edit review).

**Phase 2** — two independent tracks after T007 lands:
- Track A (spine): T009, T010 → T011, T012, T013
- Track B (seam): T018 → T019, T021; T020 anytime
- Track C (persistence): T014 → T015 → T016 → T017

**Phase 3 (US1)** — the widest fan-out, five tracks:
- Adapters: T022 → T023 ∥ T024 → T025, tested by T026 ∥ T027
- Merge: T028 → T029 → T030, tested by T031
- Pipeline: T032 ∥ T033 ∥ T034 → T035 → T036
- API: T037 ∥ T038 ∥ T039 → T040 ∥ T041
- Web: T042 ∥ T043 → T044 → T045 *(needs only `contracts/sites.md`)*
- Evals: T046, T047, T048 all `[P]`

**Phase 5 (US3)** — T056 → T057 → T058 runs fully parallel to all of Phase 3; only T059 joins the pipeline.

**Phase 6** — T063, T064, T065, T066, T067 all `[P]`.

---

## Implementation strategy

**MVP = Phase 1 + Phase 2 + Phase 3 (US1).** That alone satisfies SC-001, SC-002, SC-005 and SC-006, and is the first thing a stakeholder can *see*: a map full of real, cited, attributable places.

**Incremental delivery**:
1. Setup + Foundational → the spine validates and PostGIS round-trips (nothing visible yet).
2. **US1** → 🎯 demo: research Rhodes, cited markers with chips + ODbL. **Ship/tag here.**
3. **US2** → the commons starts compounding (reuse + refresh).
4. **US3** → Greek names become readable to an English-first user.
5. Polish → genericity proof, prompt lifecycle, license registry, devlog + exhibit tags.

**Definition of Done per DU** (`delivery-plan.md`): EARS criteria verified · named test tiers green · trajectory/structural evals green · an ADR if a decision was made · a devlog entry · an exhibit-tag candidate proposed.

---

## Task summary

| Phase | Tasks | Count | Parallelizable |
|---|---|---|---|
| 1 · Setup | T001–T006 | 6 | 4 |
| 2 · Foundational | T007–T021 | 15 | 6 |
| 3 · US1 (P1) 🎯 | T022–T048 | 27 | 14 |
| 4 · US2 (P2) | T049–T055 | 7 | 4 |
| 5 · US3 (P3) | T056–T062 | 7 | 3 |
| 6 · Polish | T063–T070 | 8 | 5 |
| **Total** | | **70** | **36** |
