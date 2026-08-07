# Tasks: Plan a day, compile it, travel it offline

**Feature**: `specs/002-plan-compile-offline` · **Date**: 2026-08-07 · **Branch base**: `main`

**Input**: [spec.md](./spec.md) · [plan.md](./plan.md) · [data-model.md](./data-model.md) · [research.md](./research.md) · [contracts/](./contracts/) · [quickstart.md](./quickstart.md)

**Design authority**: `docs/data/itinerary.md` · `route-leg.md` · `bundle-manifest.md` · `tile-source.md` · `poi-site.md` (**schema ground truth — the card wins**), `docs/design/tech-design.md` §1.3/§1.4/§5.2/§5.3/§5.5, `docs/design/test-strategy.md`, ADR-0002/0003/0004/0008 (reused) and **ADR-0020…0025** (new, `proposed`).

**Delivery mapping**: **DU-04** (plan + HITL) → **DU-04.5** (narration) → **DU-05** (compile) → **DU-06** (offline render, *M1 done*) of `docs/design/delivery-plan.md`.

## Conventions

- **Tests are REQUIRED.** Constitution Article II makes deterministic evals merge-blocking; `test-strategy.md` defines the tiers.
- **`[P]`** = parallelizable: touches files no other in-flight task touches, and its dependencies are complete.
- **`[USn]`** = the user story served. Setup/Foundational/Polish carry no story label.
- One branch per task-group (`agent/<ticket>-<slug>`), PR to `main`, CI 1–7 green (ADR-0005).
- **Geo-API pins** (AGENTS.md): shapely `unary_union`/`.geom_type`, h3 `latlng_to_cell`/`grid_disk`, OSMnx 2.x, GeoPandas 1.x. **CRS: EPSG:4326 (lon, lat).**
- **The LLM ranks and orders. It never emits a coordinate, distance, duration, or time.** Valhalla, shapely/PostGIS and `opening-hours-py` do. This is FR-004 and it is merge-blocking.
- **Ask-gated, never unattended**: Alembic migrations (T009), `.github/workflows/**` edits (T060), any push / PR create / merge.

---

## Phase 1: Setup

**Purpose**: dependencies, the two new local services, fixtures. No product behaviour.

- [ ] T001 Add slice-002 runtime dependencies to `pyproject.toml`, resolved-then-pinned per ADR-0007 — **`opening-hours-py`** (ADR-0022), `mwparserfromhell` (ADR-0024), and a deterministic IANA-timezone-from-point resolver for T008 — then `uv lock` and commit `uv.lock`. Run the slopsquatting check (publisher + registration date + hashes) per Constitution Article V.
  - ⚠️ **Named supply-chain hazard, one hyphen wide.** `pip install opening-hours` resolves to an **abandoned v0.1.1 with an UNKNOWN license** (`anthill/Python_OpeningHours`) — *not* the Rust bindings. The correct distribution is **`opening-hours-py`** (metadata name `opening_hours_py`), which imports as `opening_hours`; verified 2.1.4, 2026-07-07, MIT OR Apache-2.0. Confirm the publisher before locking. This is precisely the case job 6's slopsquatting gate exists for.
- [ ] T002 [P] Add a `valhalla` service to `docker-compose.yml` (official GHCR image, pedestrian costing, `:8002`), mirroring the CI shape, with a comment recording the 1–5 min first-build cost (ADR-0020).
- [ ] T003 [P] Add a `gcs` service (`fake-gcs-server`) to `docker-compose.yml` for bundle artifacts, mirroring GCS.
- [ ] T004 [P] Add `@playwright/test` to `web/devDependencies` (resolved-then-pinned) and a `test:e2e` script; `pnpm exec playwright install chromium`. **Chromium only** — ADR-0002 makes WebKit a flagged future ADR.
- [ ] T005 [P] Commit a recorded Valhalla response fixture (`tests/fixtures/valhalla_rhodes_route.json` + `..._matrix.json`) so Tier 1 never needs the container, plus `tests/fixtures/README.md` rows recording how each was captured.
- [ ] T006 [P] Commit a Wikivoyage/Wikipedia MediaWiki API fixture (`tests/fixtures/wikivoyage_rhodes.json`) including ≥1 article with listing templates, ≥1 place with **no** article, and the `revid` field ADR-0024 attribution depends on.

**Checkpoint**: `uv sync` succeeds; `docker compose up -d` yields reachable PostGIS + Valhalla + fake-gcs; `pnpm -C web exec playwright --version` answers.

---

## Phase 2: Foundational (BLOCKING — every user story depends on this)

**Purpose**: the schema amendments, the models, persistence, and the two deterministic engines that exist precisely so the model cannot do that arithmetic.

### Schema amendments (ADR-0025) — do these FIRST; everything below transcribes them

- [ ] T007 Apply the ADR-0025 card amendments to `docs/data/bundle-manifest.md`, `itinerary.md`, `route-leg.md`, `tile-source.md`, `poi-site.md`, **`area.md`** and `DATA-LICENSES.md` — the frozen-itinerary manifest slot, per-artifact hashes incl. `attribution.sha256`, **`textLicense`**, the `manifest_sha256` canonicalization rule, `withheld[]` with its closed `reason` enum, `ItineraryV1.date`, `area.timezone`/`area.country_code`, `stop_order`/`leg_id` timeline addressing, `Story.observed_at`, the structural derive-don't-read rule (`Story` **and** `RouteLegV1`/`ResolvedArea`/`AreaCandidate`), the `opening_hours.js` → `opening-hours-py` swap. *(Largely applied already — verify rather than reapply.)*
- [ ] T007a **Propagate the ADR-0025 amendments up to `docs/design/tech-design.md` §1.3 (`ItineraryV1`) and §1.4 (`BundleManifestV1`)** — either the same edits or an explicit pointer to ADR-0025. tech-design is the *upstream authority* the cards were derived from; leaving it unamended reintroduces one level up exactly the contradiction ADR-0025 closes. **Required follow-up, not optional tidy** (ADR-0025 Consequences).
- [ ] T007b **Reconcile the `user_plan` shape across all FOUR documents before writing the migration.** `data-model.md` §6 specifies a single `feasibility jsonb` column with `CHECK (status <> 'approved' OR feasibility->>'ok' = 'true')`; ADR-0025 ruling 3 specifies `user_plan.feasible` + `user_plan.violations`; ADR-0023 defines a **seven-state** `status` enum; and **`contracts/plans.md` returns a three-value `approval.state`** — the fourth document, which carries no flag of its own. Reconcile all four **before** T009 writes `0005_user_plan`. If the contract is missed, T024 implements `GET /plans/{id}` against a three-value enum and a `compiling` plan renders as an unknown state in the UI.
  - Also reconcile `superseded_by`: `contracts/plans.md` requires it on `GET /plans/{id}` and in the `409` body, but ADR-0023's column table has none — implemented literally, a superseded row has no link to its successor and the field cannot be filled.

### The data spine

- [ ] T008 Add `timezone` (IANA) + `country_code` to `area` in `commons/db.py`, derived **deterministically from the polygon at resolve time** in `planner/nodes/resolve_area.py` — never guessed, never model-supplied. This is the calendar/locale frame every planned time and every PH lookup depends on (ADR-0025 ruling 2).
  - **The derivation rule, pinned** (it was under-specified, and SC-009 genericity would have found it): resolve to the timezone whose zone polygon has the **largest area of intersection** with the resolved polygon; ties break on the **lexicographically smallest IANA id**. Same rule against country polygons, ties on the smallest ISO 3166-1 alpha-2 code. `docs/data/area.md` is the authority and states this; **ADR-0025 A2's withdrawn first version said `representative_point()` — do not implement that.** A polygon 80% in one country whose sample point lands in the other 20% would resolve public holidays against the wrong national calendar and every real closure would evaluate as open.
  - *Permitted optimization:* where the polygon intersects exactly one zone (the common case for a walkable day) a single point-in-polygon lookup is equivalent; if used, take `representative_point()` and **never the centroid**, which can fall outside a concave or multi-part polygon. The resolved value is **stored**, so later reads never re-derive and never drift. It must never prompt.
  - **Pin the offline dataset**, which no ADR currently does: the lookup needs a bundled timezone/country boundary polygon set (resolved-then-pinned per ADR-0007), and it must work with **no network** so area resolution stays reproducible in CI.
  - **Register it in `DATA-LICENSES.md`.** A timezone-boundary dataset built from OSM is **ODbL** and carries an attribution obligation — a new data dependency entering the product must appear in the registry like every other one (Constitution Article V). Currently unregistered.
- [ ] T009 Generate the Alembic migration in `alembic/versions/` creating `user_plan` (per the reconciled shape from T007b: `user_id` row-scope key, `status`, `revision`, `approved_at`, `approved_by`, `itinerary_hash`, and the feasibility columns) and adding the two `area` columns. *(**ask-gated** — Ben approves.)*
  - ⚠️ **The obvious `CHECK` is wrong.** `CHECK ((status='approved') = (approved_at IS NOT NULL))` **rejects `compiling` and `compiled` rows** — a plan that is approved and then starts compiling would violate its own constraint the moment ADR-0023's state machine advances it. Write the constraint over the **post-approval set**: `approved_at IS NOT NULL` for every state at or beyond `approved` (`approved`, `compiling`, `compiled`), and `NULL` before it. Same trap for the unapprovable-when-infeasible `CHECK` — it must permit the post-approval states too.
- [ ] T010 Implement `ItineraryV1`, `Stop`, `Timeline`, `TimelineEntry`, `Budgets` and `RouteLegV1` in `commons/models.py`, **subclassing the existing `StampedModel`** so frozen/`extra="forbid"`/quarantine guarantees are inherited, not re-implemented. `meals`/`variants`/`RouteLegV1.variant` exist and stay empty (M2+).
- [ ] T011 Implement `BundleManifestV1` in `commons/models.py` per the amended card — per-artifact hashes, `withheld[]`, embedded `TileSourceV1`, **`textLicense`**, and the `integrity.manifest_sha256` canonicalization (sorted-key UTF-8 JSON with `integrity` omitted). `textLicense` is not optional polish: attribution discharges CC BY, the declaration discharges **SA**, and a manifest that is `extra="forbid"` cannot hold it if it is omitted here.
- [ ] T012 [P] Unit-test the new models in `tests/test_models_itinerary.py` — construction, `schema_ver` literals, **naive area-local `time` vs tz-aware UTC `datetime` never interchangeable**, unstamped refusal inherited, `meals`/`variants` empty.
- [ ] T013 [P] Extend `Story` in `commons/models.py` with `observed_at`, and document in `commons/AGENTS.md` (or the module docstring) that `Story` is **not** a `SourcedValue` — quarantine must derive bundleability via `commons/licenses.py::bundleable`, never read a field that isn't there (ADR-0025 gap 8).

### The deterministic engines (the model does none of this)

- [ ] T014 Implement `commons/opening_hours.py` wrapping `opening-hours-py` (ADR-0022) — evaluate an OSM `opening_hours` string at an area-local instant given the area's timezone + country. **Fails closed**: unparseable or `SH`-bearing expressions yield `hours_unknown` with the raw string retained; never defaults to "open".
- [ ] T015 [P] Unit-test `tests/test_opening_hours.py` — a table of real OSM strings from the Rhodes fixture at **fixed instants under a frozen clock**, no network; plus an explicit **rejection table** asserting `SH`/unparseable strings surface as `hours_unknown` rather than open. This is the test that stops a silent wrong answer.
- [ ] T016 Implement `commons/routing.py` — the `RoutingProvider` protocol plus a Valhalla client (`/route`, `/sources_to_targets`, pedestrian costing) and a fixture provider selected by `SIYUR_ROUTING_PROVIDER` (ADR-0020). Legs are stamped with the produced-work-from-OSM `SourceRef` (`kind:"osm"`, `id:"valhalla:pedestrian"`, ODbL, "© OpenStreetMap contributors"), `bundleable` **derived**, never author-set.
- [ ] T017 [P] Unit-test `tests/test_routing.py` against the T005 fixtures — leg geometry is a valid EPSG:4326 `LineString` with **≥3 vertices** (a 2-point line is a straight line pretending to be a route), distance/duration units, ODbL stamping, and provider selection.

**Checkpoint**: models round-trip, both engines answer deterministically offline, `user_plan` exists. **No user story may start before this.**

---

## Phase 3: US1 — Propose a day and approve it (DU-04, Priority P1)

- [ ] T018 [US1] Implement `planner/feasibility.py` — total walking vs `budgets.walking_m`, total elapsed vs `budgets.hours`, and every stop inside its site's opening window in **area-local** time. Returns a verdict with **named violations** (FR-005). All arithmetic here; none in the model.
- [ ] T019 [P] [US1] Unit-test `tests/test_feasibility.py` — each budget violated independently, an opening-window violation, an `hours_unknown` stop blocking rather than silently passing, and a feasible plan passing.
- [ ] T020 [US1] Implement `planner/nodes/propose_itinerary.py` on the **Opus tier** via the `ModelRouter` seam — selects and orders `site_id`s from commons records **only**, and returns nothing else. Legs, distances, durations and times are filled by `commons/routing.py` + `feasibility.py` afterwards. A model-emitted coordinate or duration is a hard failure, not a fallback.
- [ ] T021 [P] [US1] Unit-test `tests/test_planner_propose.py` with a **mocked model, no API key** — schema-valid `ItineraryV1`, a stop referencing a non-existent site is rejected, an unroutable stop is excluded rather than straight-lined, and any model-asserted numeric is refused.
- [ ] T022 [US1] Implement the HITL state machine in `commons/repository.py` per ADR-0023 — `proposing → proposed → approved | superseded`, approval by **compare-and-set on `itinerary_hash`** (idempotent), compile flipping `approved → compiling` **in the same transaction**. The gate is a database constraint, not an application check.
- [ ] T023 [US1] Wire `planner/pipeline.py` — a `run_plan` generator emitting the contract's frames (`status` → `itinerary` → `feasibility` → `done`), mirroring `run_research`'s injected-persistence shape so `planner/` stays storage-agnostic.
- [ ] T024 [US1] Implement `POST /plans` (SSE), `GET /plans/{id}` and `POST /plans/{id}/approve` in `api/plans.py` per `contracts/plans.md` — `401`; another user's plan **`404`, never `403`**; `409` infeasible with violations and **no override**; `409` superseded naming the current revision; idempotent re-approve.
- [ ] T025 [P] [US1] Contract-test `tests/test_api_plans.py` over real PostGIS — the full status-code matrix, the privacy boundary (a second user gets `404`), and that an approved plan is never visible in `GET /sites`.
- [ ] T026 [P] [US1] Integration-test `tests/test_hitl_gate.py` (`-m integration`) — **an approval survives a process restart** (SC-003 = 100%), concurrent double-approve yields exactly one approval, editing an approved plan returns it to `proposing` and re-runs feasibility. These are transaction guarantees, so they run against real Postgres.
- [ ] T027 [P] [US1] Implement `web/src/plan/` — request form (time, walking, interests, date), itinerary panel with per-value provenance chips, named feasibility violations, and an approve affordance **disabled while infeasible**.
- [ ] T028 [P] [US1] Test the plan UI in `web/test/plan.test.ts` — renders from a mock response, a value lacking a source is never rendered, approve is unavailable on an infeasible plan.

### Stand the airplane-mode harness up NOW, not at DU-06

- [ ] T029 [US3-early] Create `web/test/e2e/airplane.spec.ts` against the **existing DU-00 empty map**: load online → wait for the service worker to reach `activated` → install a **context-level** `route('**/*')` recorder with `serviceWorkers: 'allow'` → `setOffline(true)` → reload → assert the recorded request list is **empty** and the map canvas is present. Filter by scheme (`data:`/`blob:` are not network); **allowlist nothing**.
- [ ] T030 [P] [US3-early] Add the **negative-control** spec asserting the harness *catches* a deliberately-requested remote asset. A gate that cannot fail is exactly the stub being replaced (research R6).

**Checkpoint (DU-04 demo)**: "half-day, art + coffee" → itinerary with provenance chips → approve. An infeasible ask cannot be approved. The e2e harness runs real assertions.

---

## Phase 4: US4 — Places tell their story (DU-04.5, Priority P4)

*The designated drop-candidate: the plan/compile/travel spine and the airplane-mode gate all hold without it.*

- [ ] T031 [US4] Implement `commons/sources/wikivoyage.py` — MediaWiki Action API over Wikivoyage + Wikipedia, `mwparserfromhell` for listing templates, **stamping at the boundary** like every other adapter (ADR-0024). Captures article title, canonical URL and **`revid`** into the `SourceRef`.
- [ ] T032 [US4] Implement `planner/nodes/narrate.py` — adapts fetched article prose into a `Story`. The model may **only** adapt text present in the fetched article; a place with no article gets **no story and nothing invented** (FR-023).
- [ ] T033 [P] [US4] Test `tests/test_sources_wikivoyage.py` against the T006 fixtures — per-article + per-revision attribution captured, CC BY-SA license stamped, the no-article place yields `stories: []`, and an unstamped story is refused.
- [ ] T034 [P] [US4] Author `prompts/narration.md` v1 with Article VII front-matter (version, pinned dated model snapshot, date, eval link).

---

## Phase 5: US2 — Compile the day (DU-05, Priority P2)

*One module per ordered stage of tech-design §5.3, so the pipeline reads as its own spec.*

- [ ] T035 [US2] Implement `compiler/tiles.py` — `pmtiles extract` over the itinerary bbox + 1 km buffer (2 km minimum span), z0→maxzoom, build URL **resolved at run time** (never hotlinked), emitting an embedded `TileSourceV1` (ADR-0021).
- [ ] T035a [US2] **Select glyph ranges from the area's own label scripts** — `scripts/fetch-basemap.sh` prunes glyphs to U+0000–U+04FF, which is fine for the Latin/Greek demo area and **fails silently on a Hebrew, Arabic or CJK area: labels render as nothing**. Since SC-009 proves genericity against a second area of different character, a hardcoded range would make the genericity eval pass while the map is unreadable. Derive the ranges from the bundled sites' name scripts.
- [ ] T035b [US2] **Hash the glyph/sprite artifacts.** `TileSourceV1.glyphs` currently carries `{path, license}` while `style` carries `{path, sha256}` — so now that the manifest claims one hash per artifact, glyphs are a bundled artifact with no integrity hash. Same shape of gap `attribution.sha256` just closed; close it here rather than after a corrupted glyph set renders a blank map offline.
- [ ] T036 [US2] Implement `compiler/routes.py` — Valhalla legs plus the pruned walking network for offline recovery. The network **must be topologically noded**; un-noded input silently yields disconnected islands.
- [ ] T037 [P] [US2] Add the walk-graph tripwire `tests/test_walk_graph.py` — assert nodedness/connectivity. This is a **pre-armed guardrail**, not a reaction to a failure (plan Risk 3).
- [ ] T038 [US2] Implement `compiler/quarantine.py` — drop every value where `licenses.bundleable(kind, license)` is false, refuse unstamped input, and record every removal into `withheld[]` with a reason (FR-011/FR-012/FR-021).
  - **Derive bundleability structurally, not per-type.** `Story` is *not* the only structure carrying a bare `SourceRef` with no `bundleable` field — **`RouteLegV1`, `ResolvedArea` and `AreaCandidate` are the same shape**. The rule is: **only a `SourcedValue` has a `bundleable` field to read; everything else derives.** A filter written as `derive if isinstance(v, Story) else v.bundleable` reaches `routing.legs`, finds no attribute, and either raises mid-compile or — with the likelier `getattr(..., False)` repair — **drops every walking leg from every bundle**. The bundle still compiles, hashes and passes every path check, and the traveller's day has no routes. Silent at every gate.
  - **`withheld[].reason` is a closed enum, never free text** — exactly `license_forbids_redistribution` and `source_unavailable`. `withheld` ships inside an artifact the user downloads; a free-text reason is a channel for anything a future withholding rule touches — including the private side of the PRD §13 #4 boundary — to leak out in a downloadable file. **Do not source it from `licenses.quarantine_reason`**, which returns prose naming the internal allowlist.
  - **`unstamped` is not a member.** FR-012 *refuses* unstamped input — the compile fails — so a value is withheld **or** refused, never both. Implementing `unstamped → record and continue` makes the compile succeed while `evals/test_structural.py`'s FR-012 assertion (merge-blocking) fails, or worse ships the placeholder and silently retires the refusal invariant.
- [ ] T039 [US2] Implement `compiler/attribution.py` — regenerate `ATTRIBUTION.md` per bundle: ODbL for every OSM-derived artifact (tiles, legs, walk graph) plus an **individual CC BY-SA credit per contributing article**, and declare the bundled text license (ADR-0024 share-alike discharge).
- [ ] T040 [US2] Implement `compiler/manifest.py` — SHA-256 **per artifact**, then `integrity.manifest_sha256` over canonical JSON with `integrity` omitted; freeze the itinerary itself into `content.itinerary` (ADR-0025 gap 1).
- [ ] T041 [P] [US2] Implement `compiler/storage.py` — put/read bundle objects against GCS / fake-gcs, range-request friendly for resumable download.
- [ ] T042 [US2] Wire `compiler/pipeline.py` — the ordered §5.3 stages as a generator emitting the contract's stage frames, in-process behind a flag.
- [ ] T043 [US2] Implement `POST /bundles` (SSE), `GET /bundles/{id}/manifest` and artifact fetch in `api/bundles.py` per `contracts/bundles.md` — **`409` when the plan is not approved** (the HITL gate made mechanical), `size_bytes` before download, `206`/`416` range semantics, and a path not in the manifest is `404` by construction.
- [ ] T044 [P] [US2] Test `tests/test_compiler_quarantine.py` — a known `bundleable=false` value appears **nowhere** in the bundle, and its removal is recorded in `withheld[]`.
- [ ] T045 [P] [US2] Test `tests/test_compiler_manifest.py` — per-artifact hashes match their bytes, `manifest_sha256` is stable under key reordering, and a mutated artifact is detected.
- [ ] T046 [P] [US2] Test `tests/test_compiler_attribution.py` — ODbL present for OSM-derived artifacts, every bundled story credited exactly once, text license declared.
- [ ] T047 [P] [US2] Contract-test `tests/test_api_bundles.py` (`-m integration`) — compiling an unapproved plan is `409`; a compiled bundle's every manifest path resolves.

**Checkpoint (DU-05 demo)**: approve → compile stages stream green → manifest with size, hashes and zero quarantined values.

---

## Phase 6: US3 — Travel it offline (DU-06, Priority P3) — **M1 done**

- [ ] T048 [US3] Implement `web/src/bundle/` — the download manager fetching the whole archive into **OPFS** with `navigator.storage.persist()`, resumable by range, verifying `manifest_sha256` at launch.
- [ ] T049 [US3] Implement `web/src/bundle/opfs-worker.ts` — a **module worker** (`worker.format:'es'`, ADR-0003) using `FileSystemSyncAccessHandle` reads, backing a `pmtiles` v4 `FileSource`.
- [ ] T050 [US3] Register the MapLibre custom protocol reading tiles from OPFS, swapping the DU-00 HTTP transport (ADR-0002's transport swap — the read model does not change).
- [ ] T051 [US3] Implement `web/src/travel/` — itinerary, timeline, per-place info and narration rendered **from the bundle alone**, plus the "needs connectivity" affordance for anything in `withheld[]` (never an error, never a blank).
- [ ] T052 [US3] Implement off-route recovery with `geojson-path-finder` over the bundled pruned graph, straight-line as last resort, computed on-device.
- [ ] T053 [US3] Implement the launch-time integrity check — a failed `manifest_sha256` reports an unusable bundle rather than rendering a partial day (FR-020).
- [ ] T054 [P] [US3] Verify the ADR-0003 leak tripwire still holds — the PMTiles archive is runtime-fetched, never `import`ed, never in `public/`, excluded from `workbox.globPatterns`, with `maximumFileSizeToCacheInBytes` small.
- [ ] T055 [P] [US3] Test `web/test/travel.test.ts` and `web/test/bundle.test.ts` (vitest) — manifest verification, withheld-content affordance, recovery route shape.
- [ ] T056 [US3] **Grow `web/test/e2e/airplane.spec.ts` (T029) to the full gate** — after offline reload assert map tiles render from OPFS, itinerary/timeline/narration resolve from the bundle, recovery returns a route, and the recorded request list is still **empty**.
- [ ] T057 [P] [US3] Add an e2e case for a **corrupted/partial bundle** — the app reports it unusable rather than rendering a partial day.

---

## Phase 7: Polish, evals, and closing the slice

- [ ] T058 [P] Extend `evals/test_structural.py` — the quarantine invariant over bundled **narration**, manifest integrity, every-manifest-path-resolves, and **zero stories without attribution** (SC-010).
- [ ] T059 [P] Extend `evals/test_trajectory.py` — superset match now including `propose_itinerary`.
- [ ] T060 Replace the CI job-5 `echo` stub in `.github/workflows/ci.yml` with the real Playwright/Chromium run. **This is the moment the merge gate stops being a stub** — the single highest-value line in the slice. *(**ask-gated**, ADR-0006 — one deliberate human-approved edit.)*
- [ ] T061 [P] Author `prompts/planner.md` v1 with Article VII front-matter (version, pinned **dated** Opus 5 snapshot, date, eval link).
- [ ] T062 [P] Extend `evals/test_genericity.py` — the full plan → compile → travel flow completes for a second area with no place-specific code (SC-009).
- [ ] T063 [P] Add a nightly, threshold-gated LLM-judge eval for narration quality — the first genuinely non-deterministic output in the product, so **non-blocking per Article II tiering**, with the judge model pinned to a dated snapshot.
- [ ] T064 Update `docs/TRY-IT.md` with the Valhalla / fake-gcs / Playwright steps. **TRY-IT owns setup instructions; the quickstart must not restate them** (two runbooks drift).
- [ ] T065 Re-verify `specs/002-plan-compile-offline/quickstart.md` **against the shipped code** and correct it in place, replacing its plan-phase caveat with a verification date (the T068 precedent from slice 001).
- [ ] T066 Reconcile slice 001's contracts, which say "bearer token required" while `api/security.py` has used a signed session cookie since DU-00. Documentation drift found during this slice's design; 001's quickstart is already correct.
- [ ] T067 Amend `docs/design/delivery-plan.md` — M2's "narration generator with per-claim provenance + license quarantine" now reads *generator*, since ingestion + quarantine landed here (Spec 002 Q1 = A).
- [ ] T068 Verify all seven CI gates green, **including a job 5 that now genuinely runs**, and record the run explicitly (the T069 precedent — a gate verification has a shelf life measured in days).
- [ ] T069 Close the slice with `/devlog`, an exhibit-tag candidate per DU (`exhibit/U5-hitl-gate`, `exhibit/U5-compile-moment`, `exhibit/U4-valhalla`, `exhibit/U0-airplane-mode`, `exhibit/U5-offline-bundle`), a `/failure` entry + regression eval for any real failure hit, and the `v0.x` milestone tag. **M1 is done when this closes.**

---

## Dependencies

```
Phase 1 (setup)
   └─▶ Phase 2 (BLOCKING: T007 cards → T008-T017 spine + engines)
          ├─▶ Phase 3 US1 plan + HITL ──┐   (T029/T030 e2e harness stands up HERE)
          ├─▶ Phase 4 US4 narration ────┤   (independent; drop-candidate)
          │                             ▼
          └───────────────────────▶ Phase 5 US2 compile
                                        └─▶ Phase 6 US3 offline ─▶ Phase 7 close
```

**Parallel-safe partitions** (no two agents in one file — CLAUDE.md):
`commons/` · `planner/` · `compiler/` · `api/` · `web/` · `tests/` · `evals/` · `docs/`

## Task-count sanity

69 tasks across four DUs. Phase 2 is the true bottleneck: **T007 (card amendments) gates everything**, because every model below transcribes a card, and a card that still contradicts itself produces two implementations that disagree.
