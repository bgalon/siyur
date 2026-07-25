# Technical Design — Siyur

*v1.0 — 2026-07-24. Companion to `docs/planning/prd.md` v2.0. Engineering-track design; feeds the ramp-up schema cards (step 11) and the Spec 001 interview. Every hard choice here becomes an ADR at build time.*

**Scope: M1-slice-deep.** The M1 vertical slice (sign in → define area → research a few cited sites → plan → compile → offline render) is designed to buildable depth; M2–M4 subsystems are sketched. Legend: **[M1]** in the first slice · **[M2+]** sketched/deferred · **❓** decision deferred to an ADR (often pending the discovery spike, §7).

**Reading order:** §1 data spine (everything hangs off it) · §2 commons storage · §3 GCP topology · §4 local dev · §5 the M1 slice architecture · §6 decisions (lock-now vs defer) · §7 discovery spike.

---

## 1. The data spine — three schemas + one primitive

Research writes `SiteRecordV1`, planning composes `ItineraryV1` from it, compile freezes both into a bundle described by `BundleManifestV1`. The **airplane-mode guarantee** and the **license quarantine** are enforced at these boundaries. Schemas are versioned (`…V1`); M1 populates a subset of each — later fields exist in the schema but may be empty in M1.

### 1.0 The primitive: `SourcedValue` [M1] (provenance + license stamp)

Every fact Siyur shows is *stamped*, not bare — the mechanical enforcement of PRD §7 ("the bundle step refuses unstamped input").

```
SourcedValue<T>:
  value:       T                     # the fact itself
  source:      SourceRef             # where it came from
  bundleable:  bool                  # may this be baked into an offline bundle?
  confidence:  float [0..1]          # curation/merge confidence
  observed_at: date                  # when we fetched/derived it (staleness)

SourceRef:
  kind:     "overture" | "osm" | "wikivoyage" | "wikipedia" | "wikidata"
            | "commons" | "opening_hours_js" | "review_provider" | "open_web" | "user"
  id:       str            # GERS id / OSM type+id / QID / article title / URL
  url:      str | null
  license:  SPDX str | "proprietary" | "user-owned"
  attribution: str | null  # rendered string if the license requires it
```

**Quarantine rule (invariant, merge-blocking test `test_structural.py::test_no_unbundleable_in_bundle`):** a value may be `bundleable=true` **only if** `source.license` ∈ {ODbL, CDLA-Permissive-2.0, CC0, CC-BY-4.0, CC-BY-SA-4.0, PD, OFL, LGPL-as-dependency}. `open_web` and `review_provider` are **always** `bundleable=false`. No bundle may contain a `bundleable=false` value.

### 1.1 `SiteRecordV1` — the commons record (the v2.0 heart)

One record per real-world place, **globally shared**, assembled by merging many sources.

```
SiteRecordV1:
  id:            UUID                          # [M1] our stable id
  gers_id:       str | null                    # [M1] Overture GERS — cross-source join key when present
  names:         { lang: SourcedValue<str> }   # [M1] en (+ he at M3); canonical + translations
  location:      SourcedValue<Point>           # [M1] EPSG:4326 (lon,lat); PostGIS geometry
  categories:    [SourcedValue<str>]           # [M1] Overture basic_category (post-Sept-2026 field) + OSM tags
  address:       SourcedValue<str> | null      # [M1]
  opening_hours: SourcedValue<str> | null      # [M1] opening_hours.js syntax + parsed windows
  stories:       [Story]                        # [M1] ≥1 adapted CC-BY-SA story with attribution
  notes:         [SourcedValue<str>]           # [M1] free text; user notes are source.kind="user"
  phone:         SourcedValue<str> | null      # [M2+]
  price:         SourcedValue<str> | null      # [M2+] tickets/fees
  accessibility: SourcedValue<str> | null      # [M2+]
  website:       SourcedValue<str> | null      # [M2+] official / booking
  links:         [SourcedValue<str>]           # [M2+] tourism-site links (bundleable if just URLs)
  reviews:       ReviewSummary | null          # [M2+] link-and-summarize; bundleable=false (PRD §13 #2)
  conflicts:     [FieldConflict]               # [M1] unresolved disagreements between sources
  updated_at:    timestamptz
  schema_ver:    "SiteRecordV1"

Story:
  text_by_lang:  { lang: str }                 # en canonical (+ translations at M3)
  source:        SourceRef                      # CC-BY-SA article; attribution required
  claims:        [ {span, SourceRef} ]         # [M2+] per-claim provenance for factual sentences

ReviewSummary:            # [M2+] bundleable=false, live-online-only until PRD §13 #2
  ratings:  [ { provider, stars: float, count: int|null, url: str } ]
  fetched_at: timestamptz

FieldConflict:
  field: str
  candidates: [SourcedValue]                   # the disagreeing values, each still sourced
  resolution: "unresolved" | "picked:<source.id>" | "user-override"
```

**M1 must populate:** `id`, `location`, `names.en`, `categories`, and — where the source has it — `address`, `opening_hours`, and ≥1 `story`; every populated value carries a real `SourceRef` and `bundleable` stamp. Empty M2+ fields are valid in M1.

**i18n findings (discovery spike §7) baked into the schema:**
- `names` keys are **BCP-47 subtags**, not bare language codes — the spike found `ja-Hira` (hiragana) and `ja-Latn` (romaji) alongside `ja`.
- **Local-script names are sparse in sources** (Overture `names.common` mostly null; Greek/Japanese names came mainly from OSM `name:xx`) → **transliteration/translation of names & addresses is an M1-relevant capability, not an M3 afterthought** (at least for the record's display name). **Accepted by Ben 2026-07-24:** a name/address transliteration sliver moves into M1; the exact extent is pinned in the Spec 001 interview and formalized as an ADR at ramp-up.
- **Source scripts are untrustworthy** — the spike found a Hebrew Jaffa address stored in Cyrillic (`Сгула` for `סגולה`). Never trust a value's script from its source; normalize/validate.
- The `bundleable` stamp reads the **per-source** license: Overture places mix CDLA-Permissive-2.0 (Meta) and **Apache-2.0** (Foursquare) *within one theme*.

### 1.2 Merge model [M1; thresholds set by the discovery spike §7]

- **Join key:** `gers_id` when present; else **fuzzy spatial+name** — PostGIS distance ≤ **ε = 25 m** AND same-language name similarity ≥ **τ = 0.6** (values from the spike). In practice Overture places (Meta/Foursquare-sourced) and OSM share **no id**, so nearly all joins are fuzzy.
- **Distance alone never merges** — a name signal is required (spike: median name-sim among <20 m pairs ≈ 0.1; dense old towns pack many *different* POIs together). Compare names **within a language after transliteration** — raw cross-script comparison (Latin `primary` vs `name:he`/`name:ja`) scores ~0.
- **Union-first:** the sources are ~27–40 % overlapping (mostly complementary POIs), so merge **enriches coverage** more than it reconciles; prefer keeping two records over a wrong collapse.
- **Per-field, not per-record:** merging never discards a source. Each field keeps the winning `SourcedValue`; a losing *different* value becomes a `FieldConflict` (tested: "no source ref lost on merge", PRD §8). Spike-verified: name/category/address conflicts are captured correctly.
- **Winner policy (default, ADR-able):** highest `confidence`, tie-broken by source-trust order (Overture/Wikidata > Wikivoyage > OSM tags > open_web) then most recent `observed_at`.
- **Staleness:** per-value `observed_at` drives refresh-on-reuse (PRD §5): a stale record offers re-research, doesn't block.
- **User edits:** a user's note/override is `source.kind="user"`, stored **private** (not auto-published) per PRD §13 #4; source-derived cited data auto-publishes to the commons.

### 1.3 `ItineraryV1` — the planned day (composed, per-user)

References `SiteRecordV1` by id; single source of truth for planner output *and* bundle.

```
ItineraryV1:
  id, user_id, area_id
  lang:      str                    # [M1] presentation language (en at M1)
  stops:     [Stop]                 # [M1] ordered; each -> site_id + planned window + dwell
  legs:      [RouteLeg]             # [M1] walking legs between stops (precomputed, Valhalla)
  timeline:  Timeline               # [M1] simple ordered times/durations (rich dynamic timeline = M2)
  budgets:   { walking_m, hours }   # [M1] feasibility limits (must hold)
  meals:     [Anchor]               # [M2+]
  variants:  { "B":PlanVariant, "C":PlanVariant }  # [M2+] Plan B/C contingencies
  schema_ver: "ItineraryV1"

PlanVariant:  # [M2+] a divergence from the base plan
  trigger:   "site_closed" | "rain" | "behind_pace"
  changes:   [StopEdit];  legs: [RouteLeg]
```

Feasibility (EARS §5, tested): base (and each variant, at M2) satisfies `budgets` + opening windows, else flagged before approval.

### 1.4 `BundleManifestV1` — the frozen offline artifact

Compile freezes the above into a hashed bundle in GCS, downloaded to OPFS.

```
BundleManifestV1:
  bundle_id, itinerary_id, created_at, size_bytes, schema_ver
  tiles:       { pmtiles: {path, sha256, bbox, maxzoom} }   # [M1]
  routing:     { walk_graph, legs, sha256 }                 # [M1] incl. B/C branches at M2
  content:     { sites, narrations, sha256 }                # [M1] only bundleable=true values
  attribution: { path }                                     # [M1] ATTRIBUTION.md regenerated per bundle
  integrity:   { manifest_sha256 }                          # [M1] launch-time check (iOS eviction guard)
  schematic:   { style_json, sha256 } | null                # [M2+] illustrated-map render
```

**Airplane-mode invariant (release gate):** everything the travel UI reads resolves to a path in the manifest; no `bundleable=false` value is present; review links (M2) render as "needs connectivity," never errors.

### 1.5 How they relate

```
  many SourceRef ─stamp─► SourcedValue ─merge─► SiteRecordV1   (commons, shared, PostGIS)
                                                     │ referenced by id
                                                     ▼
                                    ItineraryV1 (+ Plan B/C at M2)   (per-user, Postgres)
                                                     │ compile (freeze + hash + quarantine filter)
                                                     ▼
                                    BundleManifestV1 → GCS → download → OPFS  (offline travel)
```

## 2. Commons storage (PostGIS)

- **Cloud SQL for PostgreSQL + PostGIS.** M1 tables:
  - `site` — `id uuid pk`, `gers_id text`, `geom geometry(Point,4326)`, `fields jsonb` (the `SourcedValue` map), `updated_at`. **GiST index on `geom`**; the Phase-A coverage query is `SELECT … WHERE ST_Within(geom, :area_polygon)`.
  - `site_source` — **append-only** provenance rows: `(id, site_id fk, field, source jsonb, value jsonb, observed_at)`. The audit trail that lets a merge re-run with a better policy without data loss, and lets the UI show *why* a value is what it is.
  - `story` — `(id, site_id fk, text_by_lang jsonb, source jsonb)`.
  - `site_conflict` — `(id, site_id fk, field, candidates jsonb, resolution)`.
  - Per-user (private): `user_plan` (holds `ItineraryV1`), `user_note`, `user_pref` — each with `user_id` and **row-level scoping** to the auth subject. This is the PRD §13 #4 privacy boundary expressed in the schema.
- **Migrations: Alembic**, identical local and Cloud SQL. The planner's Postgres checkpoint (ADR-0004) lives in the same instance (separate tables) for M1.
- **❓ Moderation/write-trust:** auto-publish source-derived cited data (traceable + reversible via `site_source`) for MVP; revisit if abuse appears → ADR (PRD §13 #4).

## 3. GCP topology

Open-source *application* stack; GCP is substrate; Google SSO the one sanctioned hosted identity dependency. **[M1] minimum is marked; everything else is [M2+].**

| Concern | Service | M1? |
|---|---|---|
| Static PWA / shell | Cloud Storage + Cloud CDN (or Firebase Hosting) | [M1] (CDN tuning [M2+]) |
| API + planner (interactive) | **Cloud Run** (FastAPI + SSE) | [M1] |
| Compiler | Cloud Run **Jobs** | [M2+] — M1 compiles **in-process** behind a flag |
| Planner state + commons | **Cloud SQL Postgres + PostGIS** (one instance) | [M1] |
| Bundles, tiles, media, glyphs | **GCS** | [M1] |
| Identity | **Identity Platform** → Google OIDC | [M1] |
| Secrets | **Secret Manager** (Anthropic key, ORS dev key, DB creds) | [M1] |
| LLM | Anthropic API (egress); cache research + translations in commons | [M1] |
| Routing | Valhalla container (per-area build at compile) | [M1] (ORS dev fallback) |
| Observability | Cloud Logging/Monitoring; self-host Phoenix for LLM traces | [M2+] (see agent-ops D4) |

Cost posture: the commons **is** the cost mitigation — research/translations are cached and shared, so per-user marginal LLM cost falls as coverage grows; per-user quotas guard the tail. ❓ region/multi-region deferred, not M1-blocking.

## 4. Local dev environment (required, PRD §6)

`docker-compose` mirroring cloud so nothing "works only in GCP":

| Service | Image / tool | Mirrors | Port (default) |
|---|---|---|---|
| `postgres` | postgis/postgis:16 | Cloud SQL + PostGIS | 5432 |
| `valhalla` | ghcr.io/…/valhalla (per-area build) | routing engine | 8002 |
| `gcs` | `fake-gcs-server` | GCS | 4443 |
| `auth` | Firebase Auth emulator | Identity Platform | 9099 |
| `api` | uv Python 3.12 (`langgraph dev` + FastAPI) | Cloud Run | 8000 |
| `web` | Vite dev server | Cloud CDN static | 5173 |

Geo stack pinned exactly as prod (`shapely~=2.1`, `h3~=4.5`, `osmnx~=2.1`, `geopandas~=1.1`). **Parity is the point:** the airplane-mode e2e and structural evals must pass identically local and in CI (see `test-strategy.md`).

## 5. M1 slice architecture

### 5.1 Repo layout (packages appear during ramp-up)

```
commons/    data model (SourcedValue, SiteRecordV1, …) + PostGIS access + merge
planner/    typed pipeline + tool nodes (research/curate) + prompts (PydanticAI+LiteLLM over the model seam — ADR-0004)
compiler/   bundle pipeline (tiles, routing, quarantine, manifest)
api/        FastAPI app (auth dep, SSE endpoints)
web/        PWA (MapLibre + PMTiles + OPFS)
evals/  tests/   per test-strategy.md
```
Root `AGENTS.md` (exists); per-package `AGENTS.md` added as packages appear.

### 5.2 Planner graph [M1]

> **Framework superseded by ADR-0004:** the planner is **PydanticAI + LiteLLM over the `ModelRouter` seam** with an owned Postgres checkpoint — not LangGraph. The node sequence, HITL gate, and determinism discipline below are unchanged in *shape*; read "graph / checkpointer / `interrupt()`" as their owned-pipeline equivalents (explicit persisted pause; one `UPSERT` per step over `user_plan`). Per-task model routing: Haiku=research, Sonnet=curate, Opus=plan.

Nodes: `resolve_area → research → curate/merge → propose_itinerary → [HITL: approve] → compile`.
- **Checkpointer:** Postgres saver (Cloud SQL); `InMemorySaver` in unit tests, SQLite in local integration (test-strategy Tier 2).
- **HITL:** `interrupt()` at itinerary approval (style/compile approval is a second gate at M2).
- **Trajectory eval target (agentevals superset):** the node sequence above — merge-blocking (PRD §8).
- **Determinism discipline:** the LLM ranks/curates and writes prose; it never emits coordinates or does spatial/temporal arithmetic — PostGIS/DuckDB/shapely and opening_hours.js do. Tool nodes are typed and independently unit-tested with a mocked model.

### 5.3 Compile pipeline (ordered) [M1]

`pmtiles extract` (tight itinerary bbox + buffer from a Protomaps daily build; resolve URL at run time) → base MapLibre style (no customization/schematic at M1) → Valhalla per-area build → route legs + pruned walk graph → **quarantine filter** (drop every `bundleable=false` value — the §1.0 invariant applied) → freeze `content` (sites + narrations) → assemble `ATTRIBUTION.md` (ODbL + CC-BY-SA credits) → SHA-256 each artifact → write `BundleManifestV1` → upload to GCS → client downloads whole archive to OPFS + `navigator.storage.persist()`. M1 runs this in-process behind a flag; M2 moves it to a Cloud Run Job.

### 5.4 Auth flow [M1]

Google OIDC via Identity Platform → the PWA obtains an ID token → sends it as `Authorization: Bearer` → a FastAPI dependency verifies the JWT (issuer/audience + Google public keys) → resolves `user_id` → scopes every `user_*` query to that subject; the commons (`site*`) is world-readable to any signed-in user. Locally, the Firebase Auth emulator issues tokens so the flow runs without real Google creds. Auth is security-critical → agent autonomy restricted here (agent-ops D4); covered by SAST + component tests.

### 5.5 Airplane-mode e2e trace (the M1 release gate)

1. **Sign in** — Identity Platform (emulator locally) → JWT.
2. **Define area** — `POST /areas` → resolve polygon (Overture divisions; Nominatim fallback for disambiguation) → `ST_Within` coverage check.
3. **Research** — planner `research` node → DuckDB over Overture (+ Overpass/Wikivoyage/Wikidata) → `SiteRecordV1`s stamped + merged + persisted to the commons.
4. **Plan** — `propose_itinerary` streams over SSE → user approves at the `interrupt()` gate → `ItineraryV1` saved to `user_plan`.
5. **Compile** — §5.3 pipeline → `BundleManifestV1` in GCS.
6. **Download** — PWA fetches the whole archive → OPFS, persisted.
7. **Network off** — Playwright `context.setOffline(true)` after load → reload → MapLibre renders tiles from OPFS PMTiles; itinerary, timeline, narration, and off-route recovery (geojson-path-finder over the pruned graph) all resolve from the bundle; assert **zero network requests** and tiles present. This is CI required check #5 (`test-strategy.md`).

## 6. Decisions — lock-now vs defer

| Lock now for M1 (→ ADR at ramp-up) | Defer to ADR (needs spike/data or a PRD open decision) |
|---|---|
| Python 3.12/uv; geo pins (shapely~=2.1, h3~=4.5, osmnx~=2.1, geopandas~=1.1) | Merge winner-policy tuning beyond the default |
| **Merge thresholds ε = 25 m, τ = 0.6 same-language** (spike §7); union-first; require a name signal | Cross-script name matching depth (transliteration engine choice) |
| Postgres+PostGIS on Cloud SQL; Alembic migrations | Full translation architecture (compile-freeze vs on-read) — but name/address transliteration lands at M1 |
| ~~LangGraph planner + PostgresSaver~~ → **PydanticAI + LiteLLM over the model seam (ADR-0004)**; FastAPI + SSE on Cloud Run | Translation architecture (compile-freeze vs on-read) |
| MapLibre 5.19.x + PMTiles v3; whole-archive → OPFS | Schematic-map rendering approach |
| Identity Platform (Google OIDC); JWT-verify FastAPI dep | Plan B/C bundle-size strategy vs ≤200 MB |
| M1 compiles in-process; Cloud Run Jobs at M2 | Review-provider integration (PRD §13 #2) |
| License-quarantine as a merge-blocking structural test | Commons moderation / write-trust (PRD §13 #4) |

**Amended after v1.0 (design review 2026-07-25):** the LangGraph-planner lock is superseded by **ADR-0004** — a layered planner (**PydanticAI + LiteLLM** over a `ModelRouter` seam + owned Postgres checkpoint), Anthropic-native in M1 with per-task model routing, cross-provider deferred. Offline sequencing is set by **ADR-0002** — online-first delivery on the bundle read model (the client reads the compiled bundle over HTTP in M1; OPFS is a later transport swap, Chromium-first). Frontend build tool pinned to Vite by **ADR-0003**.

## 7. Discovery spike (throwaway, pre-ramp-up) — the spec

Disposable exploration; **not** in the product packages (`spike/`, gitignored, never merged). Its *learnings* feed §1 (schema) and §6 (ε/τ lock), plus a devlog/FAIL exhibit.

- **Targets:** a dense metro, a small town, and a **non-Latin-script-named** place (disambiguation + RTL/name stress) — chosen at run time, not hardcoded.
- **Exercise:** for a small bbox each — Overture places/divisions via DuckDB (+ Overpass long tail, Wikivoyage listings, Wikidata) → build `SiteRecordV1`s with real `SourcedValue` stamps → run the per-field merge → emit conflict + coverage statistics.
- **Outputs:** sample `SiteRecordV1` JSON per place; a merge/coverage report (what's thin, where GERS-join works vs. the spatial+name fallback, conflict rate); and a **findings note** recommending concrete ε/τ, any schema changes, the Spec 001 demo area, and i18n/RTL gotchas.
- **Then:** fold findings into §1 + §6; the recommended demo area seeds Spec 001.

### 7.1 Result (2026-07-24) — DONE

Ran against **Rhodes/Ρόδος** (Greek), **Jaffa/יפו** (Hebrew+Arabic, RTL), **Takayama/高山** (CJK) — three non-Latin scripts. Full write-up in `spike/out/FINDINGS.md` (gitignored throwaway); the durable learnings are folded into §1.1, §1.2, and §6 above. Headlines:

- **Overture places are commercial-POI-sourced** (Meta/Foursquare, per-record licenses incl. Apache-2.0), **not OSM** → no shared id; joins are fuzzy. Datasets are ~27–40 % overlapping (complementary) → merge is enrichment-first.
- **ε = 25 m, τ = 0.6 same-language, name-signal-required** — derived, not guessed (named-pair coordinate offset p90 ≈ 12–20 m; name-sim among <20 m pairs ≈ 0.1).
- **i18n is harder and earlier than assumed:** local-script names are sparse in sources and need transliteration/translation; BCP-47 subtags (`ja-Hira`/`ja-Latn`); source scripts untrustworthy (Cyrillic-rendered Hebrew address). → a name/address transliteration sliver moves into M1 (**accepted by Ben 2026-07-24**; exact scope in Spec 001).
- **Overpass is flaky (504s)** → the commons cache is a reliability mechanism, not just cost.

**Spec 001 demo area → Rhodes:** richest data, compact walkable medieval old town, Greek exercises non-Latin *without* requiring RTL (M1 is English-first). **Jaffa** → held for M3 RTL/Hebrew validation; **Takayama** → the unrehearsed-city eval (CJK). *(Discovery-spike code discarded per plan; `spike/` is gitignored.)*
