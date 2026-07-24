# Test Strategy — Siyur

*v1.0 — 2026-07-24. Companion to `tech-design.md` and `delivery-plan.md`. Defines the three product-test tiers, how evals overlay them, and the CI gates. **Every deliverable unit's DoD names which tiers apply.** Extends `methods-ramp-up-standards.md` §3/§5 (which mandate the eval harness and SAST CI) — this doc adds the product-test pyramid.*

## The split

Three product-test tiers prove the software **works**; **evals are a separate axis** that proves quality **hasn't regressed**. They live in distinct CI jobs so a non-deterministic judge flake can never block a hotfix.

- **Tier 1 · Unit** — logic, pure, no I/O.
- **Tier 2 · Integration & Component** — wiring, against real dependencies.
- **Tier 3 · E2E** — the product promise (offline maps) in a real browser.
- **Evals (overlay)** — deterministic structural/trajectory evals (PR-gating) + LLM-judge quality evals (nightly/release-gating).

## Tier 1 · Unit (pure, <1s each)

Runners: `pytest` + `pytest-asyncio` + `hypothesis` (geometry property tests) + `syrupy` (snapshots); `vitest` (web). What belongs here:

- **Geospatial:** shapely predicates/ops, h3 cell↔boundary round-trips (v4 API — `latlng_to_cell`/`cell_to_latlng`/`grid_disk`), CRS/EPSG:4326 handling, bbox/buffer tolerances. Property-test invariants (area ≥ 0, `unary_union` idempotence). Assert on WKT/coords with fixed inputs + rounding tolerance.
- **Schemas:** `SourcedValue`/`SiteRecordV1`/`ItineraryV1`/`BundleManifestV1` validation, hash fields, serialization round-trips, rejection of bad payloads.
- **The quarantine invariant:** `bundleable` is true only for allowed licenses; a bundle assembled with any `bundleable=false` value fails. (Also enforced as a structural eval — see overlay.)
- **Merge logic:** per-field winner policy, conflict creation, "no source ref lost on merge" — fed in-memory records, no DB.
- **Agent nodes (deterministic parts):** each tool fn tested directly with a **mocked model** (`FakeMessagesListChatModel`/stub client); structured-output validation (planner emits a schema-valid `ItineraryV1`); conditional-edge/routing logic. **No real-model calls.** HTTP boundaries stubbed with `respx`/`vcr`.
- **`tests/test_geo_api_pins.py`** — the stale-API tripwire: imports/exercises every geo entrypoint used, so any 1.x/v3 idiom fails CI immediately.

## Tier 2 · Integration & Component (real deps, ephemeral)

Runner: `pytest` + `testcontainers-python` locally; **GitHub Actions service containers** in CI (pre-pulled, no docker-in-docker).

- **Component** = one service in isolation over its real stores, collaborators stubbed. E.g. the FastAPI app (`httpx.ASGITransport`/`TestClient`) over real PostGIS + DuckDB, with Valhalla and the LLM mocked — verifies the SSE stream, endpoint contracts, auth-dep JWT verification, DB queries (incl. the `ST_Within` coverage query and row-level user scoping).
- **Integration** = two+ real components together: a LangGraph graph run with a **SQLite/`InMemorySaver`** checkpointer (real PostgresSaver exercised nightly), real Valhalla container for legs, real PostGIS for spatial queries, `fake-gcs-server` for bundle objects. DuckDB reads a **small committed Overture fixture parquet**.
- **Compiler contract test:** build a bundle, re-open it, verify every `BundleManifestV1` hash matches its artifact and the quarantine filter dropped all `bundleable=false` values.

Service containers for the standard CI matrix; reserve `docker-compose` for the local full stack.

## Tier 3 · E2E (Playwright — the release gate)

Build the PWA, serve a compiled bundle, drive real MapLibre + PMTiles.

- **Airplane-mode test = the merge gate** (implements tech-design §5.5 step 7): load online → wait for the service worker + OPFS whole-archive download to complete → `context.setOffline(true)` → reload → assert map tiles render **from OPFS**, itinerary/timeline/narration/off-route-recovery resolve from the bundle, and a request listener sees **zero network calls**. Offline is set *after* first load (WebKit/Firefox error otherwise); assert on the rendered canvas/tiles, not just `navigator.onLine`.

## Evals (separate axis, overlays all tiers)

`DeepEval` (pytest-native: `deepeval test run`) + `agentevals` (trajectory match). Two classes:

- **Deterministic evals — PR-gating:** schema/structural checks and trajectory (tool-sequence `superset` match on `resolve_area → research → curate → propose_itinerary → compile`) run on **fixed traces with a mocked LLM** — fast, no API keys, no flake.
- **LLM-judge evals — non-blocking on PR, blocking nightly/pre-release:** plan quality, story quality, (later) style vision-check + translation adequacy, with a **pinned judge model + prompt**. Scores appended to `evals/history.csv`. Gate on statistical significance vs. baseline (agent-ops D4), not raw deltas.

Product tests answer "did it break"; evals answer "did quality regress." Distinct jobs, distinct failure semantics.

## CI gating (GitHub Actions)

| # | Job | Deps in CI | Required check? |
|---|---|---|---|
| 1 | lint + typecheck (ruff, mypy, tsc) | — | ✅ |
| 2 | unit (py + web, sharded via `pytest-xdist`) | — | ✅ |
| 3 | integration & component | service containers: postgis, valhalla, fake-gcs | ✅ |
| 4 | deterministic-evals (mocked LLM) | — | ✅ |
| 5 | **e2e-airplane (Playwright, Chromium)** | built PWA + bundle | ✅ **merge gate** |
| 6 | security (Semgrep + gitleaks + pip-audit + slopsquatting gate) | — | ✅ |
| 7 | diff-guard (>500 lines w/o `size-override`) | — | ✅ |
| 8 | llm-judge-evals | real model | ⚠️ non-blocking PR / blocking on `main` |
| N | nightly-full | real PostgresSaver, multi-browser, full Overture | scheduled |

Required merge checks: **1–7.** Jobs 6–7 come from methods §5 + agent-ops D4. Speed: build artifacts once and reuse across shards (`upload-artifact`); cache Playwright browsers, `uv`, and Vite/pnpm; pre-pull service images; `if: always()` container teardown.

## Test data & flake control

- Commit **tiny deterministic fixtures**: one small Overture parquet, one PMTiles sample, one canned `ItineraryV1` — **never hit live Overture/OSM/Anthropic in CI**.
- Pin all seeds, H3 resolution, and `temperature=0`; record model traces; snapshot geometry with a rounding tolerance to absorb float noise.
- Agentic flake: gate on **trajectory/structure**, never free-text equality; `agentevals` for tool-sequence.
- Per-test DB isolation (transaction rollback or fresh schema per worker); quarantine newly-flaky tests and fail the build weekly on any that stay flaky.

## Per-DU obligation

`delivery-plan.md` gives each Deliverable Unit a DoD line naming its tiers. Rule of thumb: every DU adds **Tier 1** for its new logic; DUs that touch a service or the DB add **Tier 2**; DUs on the plan→compile→travel path extend the **Tier 3** airplane-mode flow; DUs touching an agent node add or update a **trajectory eval**. DU-00 (walking skeleton) stands all jobs up green with stubs so the gates exist before the features do.
