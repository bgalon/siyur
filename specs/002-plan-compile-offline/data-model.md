# Phase 1 — Data Model: Plan a day, compile it, travel it offline

**Feature**: `specs/002-plan-compile-offline` · **Date**: 2026-08-07

**Authority**: This slice realizes `ItineraryV1`, `RouteLegV1`, `BundleManifestV1` and `Story` **exactly as `docs/data/itinerary.md` · `route-leg.md` · `bundle-manifest.md` · `poi-site.md` define them**. **Nothing here re-defines a schema** — it records which fields this slice fills, the storage mapping, and the validation rules the code enforces. Where this doc and a schema card differ, **the card wins**. Genuine gaps are listed in §9, never silently patched. Everything composes with the existing `commons/models.py` spine (`StampedModel`, `SourcedValue`, `SourceRef`, `SiteRecordV1`, `Story`) — new models subclass `StampedModel` and reuse those types rather than paralleling them.

## Entities in scope

| Entity | Realized as | This slice |
|---|---|---|
| **Itinerary** | `ItineraryV1` (`commons/models.py` pydantic + `user_plan` table) | **populate the M1 base plan** |
| **Stop / Timeline / budgets** | sub-structures of `ItineraryV1` | ordered day, area-local wall clock |
| **Route leg** | `RouteLegV1`, embedded in `ItineraryV1.legs` | Valhalla-produced, ODbL-stamped |
| **Approval gate** | `user_plan.status` + `approved_at` (server state, **not** a model field) | proposed → approved; edit reverts |
| **Bundle manifest** | `BundleManifestV1` (pydantic + a GCS object; **never a row**) | tiles/routing/content/attribution/integrity |
| **Story** | `commons.models.Story` on `SiteRecordV1.stories` | **fill-set restored** (Spec 001 deferred it here) |

## 1. `ItineraryV1` — fields this slice populates

| Field | Type | This slice | Rule |
|---|---|---|---|
| `id` | `UUID` | ✅ set | server-generated |
| `user_id` | `str` | ✅ | auth subject (`SessionUser.sub`, ADR-0008); **the row-level scope key** |
| `area_id` | `UUID` | ✅ | FK to the `area` row researched by Spec 001 |
| `lang` | `str` (BCP-47) | ✅ `"en"` | reuse `commons.models.Bcp47Tag`; no RTL, no multi-language (M3) |
| `stops` | `[Stop]` | ✅ ordered | each → an **existing** commons `site_id` |
| `legs` | `[RouteLegV1]` | ✅ | one per consecutive stop pair; §2 |
| `timeline` | `Timeline` | ✅ | simple ordered entries; **rich dynamic timeline is M2** (PRD §13 #5) |
| `budgets` | `{ walking_m: float, hours: float }` | ✅ **must hold** | feasibility limits; §7 rule 1 |
| `meals` | `[Anchor]` | ❌ **exists but stays empty** | M2+. Nobody populates this in 002. |
| `variants` | `{ "B": PlanVariant, "C": PlanVariant }` | ❌ **exists but stays empty** | M2+ Plan B/C. Nobody populates this in 002. |
| `schema_ver` | `"ItineraryV1"` | ✅ literal | |

**Sub-structures** — *transcribed from the card before the ADR-0025 amendments; see §9. The card wins, and the
timeline addressing below is superseded by `stop_order: int` / `leg_id: str`:*

```
Stop:                              # a place in the day
  site_id:        UUID             # references SiteRecordV1.id — MUST already exist in the commons
  order:          int              # 0-based position in the day
  planned_start:  local-time        # AREA-LOCAL wall clock (HH:MM) — not UTC, not device time
  dwell_min:      int              # minutes at this stop

Timeline:
  entries: [ { stop_id | leg_id, start: local-time, duration_min: int } ]   # ordered

Anchor:      [M2+] kind: "meal"|"fixed"; window: {start, end}            — not populated here
PlanVariant: [M2+] trigger; changes: [StopEdit]; legs: [RouteLeg]        — not populated here
```

`Anchor`, `PlanVariant` and `StopEdit` are **not defined as pydantic models in this slice**; `meals`/`variants` are empty-defaulted so the shape leaves room without inviting a fill. **Must-populate for a valid slice-002 itinerary**: `id`, `user_id`, `area_id`, `lang`, ≥1 `stop`, one `leg` per consecutive pair, `timeline`, `budgets`, `schema_ver`.

## 2. `RouteLegV1` — verbatim

| Field | Type | This slice | Rule |
|---|---|---|---|
| `id` | `str` | ✅ `leg-0`… | unique **within the itinerary**; what `Timeline.leg_id` refers to |
| `from_stop` / `to_stop` | `int` | ✅ | the `Stop.order` of origin/destination — **positions, not UUIDs** |
| `mode` | `"walk"` | ✅ literal | pedestrian costing only in M1 |
| `geometry` | `LineString` (EPSG:4326) | ✅ | decoded Valhalla polyline, `[[lon,lat], …]` — **ordered lon-first** |
| `distance_m` | `float` | ✅ metres | |
| `duration_s` | `int` | ✅ seconds | |
| `source` | `SourceRef` | ✅ | **see below** |
| `schema_ver` | `"RouteLegV1"` | ✅ when standalone | embedded legs may omit it (card) |
| `variant` | `"B"\|"C"\|null` | ❌ **stays `null`** | M2+ |

**The leg's `source` is a Produced Work from OSM.** Routing runs over OSM data, so leg geometry *and* time are ODbL-licensed derivatives — exactly the produced-work-inherits-its-parent's-stamp rule Spec 001 established for `*-Latn` names (ADR-0010). Every M1 leg carries:

```jsonc
{ "kind": "osm", "id": "valhalla:pedestrian", "url": null,
  "license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors" }
```

`bundleable=true` follows **derived, never author-set**: `commons.licenses.bundleable("osm", "ODbL-1.0") is True`. Valhalla itself (MIT) is a code dependency, not bundled data, and is named in `source.id` — it is not a `SourceKind`. ODbL attribution renders on **every** map (FR-015) and in `ATTRIBUTION.md`.

## 3. `BundleManifestV1` — verbatim

| Field | Type | This slice | Rule |
|---|---|---|---|
| `bundle_id` | `str` | ✅ | stable id of this compiled bundle |
| `itinerary_id` | `UUID` | ✅ | the `ItineraryV1` frozen here |
| `created_at` | `timestamptz` | ✅ **UTC** | the one UTC field in the bundle |
| `size_bytes` | `int` | ✅ | reported **before** download (FR-014); ≤200 MB budget |
| `tiles` | `{ pmtiles: {path, sha256, bbox, maxzoom} }` | ✅ | `bbox` = itinerary bbox + buffer, **EPSG:4326** `[minLon,minLat,maxLon,maxLat]` |
| `routing` | `{ walk_graph, legs, sha256 }` | ✅ | pruned noded walk graph + frozen `RouteLegV1`s |
| `content` | `{ sites, narrations, sha256 }` | ✅ | **post-quarantine only** |
| `attribution` | `{ path }` | ✅ | `ATTRIBUTION.md`, regenerated per bundle |
| `integrity` | `{ manifest_sha256 }` | ✅ | launch-time check (iOS eviction guard) |
| `schematic` | `{style_json, sha256} \| null` | ❌ **stays `null`** | M2+ |
| `schema_ver` | `"BundleManifestV1"` | ✅ literal | |

**Hash discipline (card "Integrity discipline"):** every artifact is SHA-256'd — `tiles.pmtiles.sha256`, `routing.sha256`, `content.sha256` — **then the whole manifest is hashed into `integrity.manifest_sha256`** and re-checked at launch. Hashes are lowercase hex of the raw artifact bytes. The manifest hash is computed over the manifest's canonical JSON **with `integrity` omitted** (it cannot cover itself) — see gap G7. Compile order is fixed (tech-design §5.3): extract → style → Valhalla build → legs + graph → **quarantine** → freeze content → `ATTRIBUTION.md` → hash each artifact → write manifest → upload.

## 4. `Story` — as narration realizes it (Spec 002 Q1 = A)

`commons.models.Story` already exists and is **unchanged**: `text_by_lang: {bcp47: str}`, `source: SourceRef`, `claims: [Claim]`.

- **Per-article CC BY-SA `SourceRef`.** Each story carries the article it was adapted from — `kind ∈ {wikivoyage, wikipedia}`, `id` = article title, `url` = the article URL, `license = "CC-BY-SA-4.0"`, `attribution` = the rendered per-article credit (e.g. `"Wikivoyage: Rhodes (CC BY-SA 4.0)"`). Attribution is **non-null and required** — `CC-BY-SA-4.0` is in the bundleable allowlist, so an unattributed story would bundle silently (§7 rule 8).
- **`claims` stays empty in M1.** Per-claim provenance is the narration *generator*'s output and is **deliberately unpopulated in this slice** (spec Q1 = A: ingestion here, generator M2). `Claim` exists in `commons/models.py`; nothing in 002 writes one.
- **A `Story` is not a `SourcedValue`** — it has a bare `source` and no `bundleable`/`confidence`/`observed_at` stamp. The quarantine filter therefore **derives** a story's bundleability with `licenses.bundleable(story.source.kind, story.source.license)` rather than reading a stamp (gap G4).
- Where no openly-licensed article exists the place carries **no story and nothing is invented** (FR-023).

## 5. Time, timezone and CRS discipline

**Two clocks, never mixed — this is a real bug source, stated by every card:**

| Kind | Fields | Frame |
|---|---|---|
| **Area-local wall clock** | `Stop.planned_start`, `Timeline.entries[].start`, `SiteRecordV1.opening_hours` windows | the *area's* local time. Not UTC, not the traveller's device clock. Frozen as planned into the bundle. |
| **UTC `timestamptz`** | `BundleManifestV1.created_at`, `SiteRecordV1.updated_at`, every `user_plan` audit column | UTC, tz-aware; naive datetimes are refused by `_require_utc`. |

Feasibility compares `planned_start + dwell_min` against `opening_hours` **in the same local frame**, with the area's locale/country passed to the opening-hours evaluator for PH/SH resolution (the FAIL-catalog lesson: omit it and holidays misfire). Durations are frame-free: `duration_s` (legs, seconds), `duration_min` (timeline, minutes), `budgets.hours` (float hours) — conversions happen in one place in `planner/feasibility.py`.

**CRS**: all geometry is **EPSG:4326, (lon, lat)** — `RouteLegV1.geometry` LineStrings, `tiles.pmtiles.bbox`, the walk graph, frozen site points. Reuse `commons/geo.py` validators; a LineString type follows `Wgs84Point`'s pattern (validate lon-first per vertex, serialise GeoJSON). Shapely `~=2.1` idioms only (`.geom_type`, `unary_union`); no h3/OSMnx/GeoPandas call in this slice may use a pre-v4/v2/v1 name (`tests/test_geo_api_pins.py`).

## 6. Storage mapping — the `user_plan` table

**The privacy boundary is this table's single most important property.** An itinerary is personal data: it exists only here, row-scoped to the auth subject, and never touches `site`/`site_source`/`site_conflict`. `user_plan` is a *sibling* of the commons in exactly the way `user_note` is (Constitution Article V; PRD §13 #4).

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `user_id` | `text NOT NULL` | **the auth subject.** Every read of this table filters on it — no query without `WHERE user_id = :sub` |
| `area_id` | `uuid NOT NULL` FK → `area.id` | plain `NO ACTION`, like `site_source` |
| `itinerary` | `jsonb NOT NULL` | the serialised `ItineraryV1` (stops, legs, timeline, budgets) |
| `status` | `text NOT NULL` | the lifecycle, **seven states** (below). The gate compile may not cross |
| `revision` | `int NOT NULL DEFAULT 1` | incremented per edit; a new row, never an update in place |
| `superseded_by` | `uuid NULL` FK → `user_plan.id` | the successor row when an edit replaced this one |
| `approved_at` | `timestamptz NULL` | UTC; set at approval |
| `approved_by` | `text NULL` | the auth subject that approved |
| `itinerary_hash` | `text NOT NULL` | SHA-256 over the canonical `ItineraryV1` JSON — what an approval is *of* |
| `feasible` | `boolean NOT NULL` | the verdict |
| `violations` | `jsonb NOT NULL DEFAULT '[]'` | **everything the feasibility check found, severity stamped** — `[{"message": …, "blocking": true\|false}]`. The blocking entries are the named violations of FR-005 and are what `feasible` is computed from; the advisory ones are `warnings` on the wire (ADR-0022, amended 2026-08-14). One column, because `jsonb` holds the objects without a schema change — the *name* stays `violations` precisely because renaming it would need an `ask`-gated migration for no behavioural gain. Rows written before the split hold **bare strings** and read back as all-blocking, which is what they meant |
| `feasibility_checked_at` | `timestamptz NULL` | UTC; when feasibility last ran. **`contracts/plans.md` returns it as `feasibility.checked_at`** and had no column behind it — T024 would have either violated the contract or filled it from `updated_at`, which bumps on any write and would report a time feasibility did not run. Adding it after T009 would need a second `ask`-gated migration |
| `created_at` / `updated_at` | `timestamptz NOT NULL` | UTC |

### The reconciled shape (T007b) — five documents previously disagreed

`data-model.md` said two states, **ADR-0023** defines seven, `contracts/plans.md` exposed three, **ADR-0025 ruling 3** split the verdict into two columns while this table had one `jsonb`, and the contract required a `superseded_by` that ADR-0023 never defined. Resolved as follows; **this is the shape migration `0005_user_plan` is written from.**

**Status is ADR-0023's seven states** — `proposing` · `proposed` · `approved` · `superseded` · `compiling` · `compiled` · `failed` — because they model the real lifecycle including compile. **The API exposes this same vocabulary verbatim** (`contracts/plans.md`), with no mapping layer: hiding `compiling` would leave the UI unable to tell "approved, idle" from "approved, compile running", and a translation table between DB and API states is a thing that drifts.

**The verdict is two columns, not one `jsonb`** (ADR-0025 ruling 3): `feasible boolean` takes a real `CHECK` and an index, where `feasibility->>'ok' = 'true'` is a string comparison against JSON — fragile in exactly the place we least want fragility.

**The constraints, written over the post-approval set:**

```sql
-- Post-approval states REQUIRE an approval timestamp. An IMPLICATION, not a
-- biconditional: a `superseded` row keeps its `approved_at` as history, and a
-- biconditional would reject exactly that row.
CHECK (status NOT IN ('approved','compiling','compiled') OR approved_at IS NOT NULL)
CHECK (status NOT IN ('approved','compiling','compiled') OR feasible)
CHECK ((approved_at IS NULL) = (approved_by IS NULL))
CHECK ((status = 'superseded') = (superseded_by IS NOT NULL))
```

⚠️ **Two ways to get the first constraint wrong, and this document has now made both.**

The *first* draft wrote `CHECK ((status='approved') = (approved_at IS NOT NULL))`, which **rejects `compiling` and
`compiled` rows outright** — an approved plan violates its own constraint the moment the state machine advances it.

The reconciliation then widened the left side to the post-approval set but **kept the biconditional**, which is wrong a
second time: a `superseded` row that *was* approved keeps `approved_at` as history, giving `FALSE = TRUE` → violation.
So superseding an approved plan — the transition ADR-0023's Confirmation (d) and T026 both assert — was **impossible at
runtime**. Only an **implication** is correct: post-approval states *require* a timestamp; other states neither require
nor forbid one. The `(approved_at IS NULL) = (approved_by IS NULL)` pairing stays a biconditional because those two
genuinely move together.

*(Original note, retained:)* **The obvious first constraint is wrong and was written down before this reconciliation:** `CHECK ((status='approved') = (approved_at IS NOT NULL))` **rejects `compiling` and `compiled` rows outright** — an approved plan violates its own constraint the moment ADR-0023's state machine advances it. Same trap for the infeasibility check. Both must name the whole post-approval set, not the single `approved` state.

- Indexes: `ix_user_plan_user_id_area_id` (the scoped list read, mirroring `ix_user_note_user_id_site_id`) and `ix_user_plan_user_id_status`.
- **Approval is a compare-and-set** on `(id, user_id, status='proposed', itinerary_hash)`. One row updates; a concurrent second updates **zero**, and the handler then *reads the row* and branches on its actual state — `approved` with the same hash ⇒ idempotent `200`; `superseded` ⇒ `409` with `superseded_by`; `proposed` with a different hash ⇒ `409` stale. **Do not infer staleness from which predicate failed**: because an edit writes a *new* row rather than mutating this one, the stale row's hash still matches and it is the *status* predicate that fails (ADR-0023, corrected).
- **Compile claims in the same transaction**: `UPDATE … SET status='compiling' WHERE id=:id AND status='approved'`, proceeding only if one row changed. A `proposed` plan is not refused by an `if`; it is unclaimable.
- Editing writes a **new row** at `revision+1` in `proposing` and sets the prior row `superseded`. Because state lives in Postgres, an approval **survives process restart** (FR-006, SC-003).
- **What must NEVER be in `user_plan`**: no commons rows (stops reference `site.id`, never copy site content); no credentials, tokens or email; no free-text personal notes (those are `user_note`); no `bundleable=false` value; and **no path from this table into the commons** — no trigger, no view, no join in a commons read, no auto-publish. Symmetrically, **no `user_plan` row is ever written to `site`/`site_source`** — the `CommonsWriteRefused` boundary (Spec 001 FR-010) already refuses `source.kind="user"` and covers this.
- **Bundles are objects, not rows.** The `BundleManifestV1` and its artifacts live in GCS (`fake-gcs-server` locally) and OPFS on device. No `bundle` table in this slice.
- Alembic migration `0005_user_plan` adds the table (hand-written, like `0002_area`; **`ask`-gated — Ben approves**).

## 7. Validation rules (enforced in code + evals)

1. **Feasibility holds before approval (FR-004/FR-005, SC-002)**: `Σ legs.distance_m ≤ budgets.walking_m` **and** total day span `≤ budgets.hours` **and** every stop's **half-open** `[planned_start, planned_start+dwell_min)` falls inside its site's `opening_hours` window in area-local time. **Half-open is deliberate and the closed reading is wrong**: OSM's `09:00-10:00` means the place *closes at* 10:00, so a stop ending exactly at 10:00 fits. Reading the interval closed turns every perfectly-fitted stop into a violation. *(pinned by a test; an earlier draft of this line said `]` and contradicted the implementation, which was right)* A violation is **named**, the plan is flagged, and approval is refused — never silently shipped. *(test: `test_feasibility.py`; DB `CHECK`; eval: feasibility)*

   **Amended 2026-08-14 (ADR-0022): a window that cannot be *evaluated* is a warning, not a violation.** The rule above holds for every window the evaluator answers — including `closed`, which still refuses the day. `hours_unknown` — no tag at all, an unparseable one, `SH`/sun/`PH`-without-country, or an area with no resolved timezone — is now **advisory**: named per stop, carried on the wire as `warnings[]`, and outside `feasible`. Measured cause: 1 of 25 fixture records carries `opening_hours`, and a live 6-stop day produced `no_expression` on every stop, so the fail-closed reading made **no real day approvable**. `unknown_site` stays blocking — an unresolvable stop is a missing *place*, not missing hours. *(test: `test_feasibility.py::test_a_stop_we_know_is_shut_blocks_and_a_stop_we_cannot_check_does_not`)*
2. **The LLM never emits a coordinate, distance, duration or time (FR-004)**: the model **ranks and orders only**. Distances/geometry come from Valhalla + PostGIS/shapely, times from the opening-hours evaluator and leg durations. A model-emitted numeric in any of those slots is a defect, not a fallback. *(eval: trajectory + a planner-output schema check that the proposal carries no geometry)*
3. **Every stop references an existing commons site (FR-002)**: `Stop.site_id` MUST resolve to a `site` row inside `area_id`'s researched extent. A site the commons does not hold is rejected — the planner may not invent a place. *(test: planner node; eval: structural)*
4. **An itinerary is never written to the commons (FR-007)**: no `ItineraryV1`, `Stop`, or plan-derived value reaches `site`/`site_source`/`site_conflict`; every `user_plan` read is scoped to the auth subject. *(test: `test_hitl_gate.py` + a repository guard; continuous with Spec 001 rule 7)*
5. **No compile without approval (FR-006, SC-003)**: the compiler refuses a `user_plan` row whose `status <> 'approved'`, and the pause survives restart.
6. **A bundle contains zero `bundleable=false` values (FR-011/FR-012, SC-004)**: the quarantine filter **removes** them before freezing — flagging is not enough — and **refuses unstamped input** outright. `open_web`/`review_provider` are always dropped. *(merge-blocking: `evals/test_structural.py::test_no_unbundleable_in_bundle`, extended to bundled narration)*
7. **Every manifest path resolves (FR-021, SC-006)**: every path named in the manifest exists in the bundle, every artifact's SHA-256 matches, and `integrity.manifest_sha256` verifies at launch. Conversely, everything the travel UI reads resolves to a manifest path — quarantined content presents as "needs connectivity", never as an error or a blank. *(test: `test_compiler_*.py`; gate: DU-06 airplane-mode e2e, **zero network requests**)*
8. **Attribution completeness (FR-015/FR-024, SC-010)**: `ATTRIBUTION.md` is regenerated per bundle and names `"© OpenStreetMap contributors"` for every OSM-derived artifact (tiles, legs, walk graph) plus an **individual** credit per bundled story. Zero bundled stories without attribution.
9. **Route legs are real (FR-003)**: every leg carries a `LineString` with ≥2 vertices from the walking network; a straight-line two-point fallback is never presented as a route, and an unroutable stop is excluded from the plan rather than joined by one.
10. **Genericity (SC-009)**: no place literal in `planner`/`compiler`/`api` product code; the flow completes for a second area. *(eval: `test_genericity.py`)*

## 8. State / lifecycle

```
researched area ─▶ POST /plans (SSE) ─▶ propose_itinerary (Opus; ranks + orders only)
                                            │
                              deterministic feasibility (Valhalla · PostGIS · opening hours)
                       ┌────────────────────┴────────────────────┐
                  ok=false → status='proposed', violations   ok=true → status='proposed'
                  named, approval REFUSED ◀── edit ──┐              │  user approves (explicit)
                                                     └──── status='approved', approved_at=UTC
                                                                    │
               compile: extract → legs+graph → QUARANTINE → freeze → hash → manifest
                                                                    │
                          GCS ─▶ download (size shown first) ─▶ OPFS ─▶ travel:
                          map · timeline · stories · recovery, ZERO network requests
```

Editing an approved plan returns it to `'proposed'` and re-runs feasibility. Nothing in this flow writes to the commons except narration ingestion (US4), which writes `Story` onto `site` through the ordinary Spec 001 upsert path.

## 9. Schema card gaps found

> **Status: these are RULED, not open.** Every gap below except G9 and G12 was decided by **ADR-0025** (with amendments
> A1–A4) and the cards were amended accordingly on 2026-08-07. The list is kept as the record of *what was wrong and
> why it was found*, not as an open queue.
>
> **Consequently §§1, 3 and 4 above are stale in specific, known ways** and the amended cards win over every one of
> them — `docs/data/*.md` is ground truth, this document is a transcription of it. Concretely: §1's sub-structures
> still show the old `stop_id | leg_id` timeline addressing (now `stop_order: int` / `leg_id: str`) and omit
> `ItineraryV1.date`; §3 still shows the 4-field `tiles.pmtiles` (now a full embedded `TileSourceV1`), the per-*group*
> hashes (now seven per-artifact hashes) and `attribution: {path}` (now `{path, sha256}`), and omits `textLicense`,
> `withheld` and `content.itinerary`; §4 says `Story` is "unchanged", which ruling 8 superseded by adding
> `observed_at`. **`tasks.md` T007 re-syncs this document against the cards**; until it does, read the card.

Genuine gaps, contradictions and silences found while transcribing. **None is patched here** — each needs a card amendment (Ben) before the code guesses.

- **G1 — the frozen itinerary has no manifest slot.** `BundleManifestV1` carries `itinerary_id` and `routing.legs`, but **no path to the itinerary itself** (stops, timeline, budgets). FR-017/FR-021 require the traveller's timeline to render from the bundle and every read to resolve to a manifest path. As written, it cannot. *(bundle-manifest.md)*
- **G2 — the feasibility verdict has no schema home.** Example 2 in `itinerary.md` shows `"_feasibility": {ok, violations}`, but no such field is in the table — and it *cannot* be an `ItineraryV1` field: `StampedModel` is `extra="forbid"` and pydantic rejects leading-underscore field names. Parked on `user_plan.feasibility` here; the card should say where it lives.
- **G3 — the day has no date and the area has no timezone.** Every time is "area-local wall clock", yet `ItineraryV1` has no date field and the `area` table has no timezone/country column, so the local frame and the PH/SH locale are **not derivable from stored data**. Opening-hours evaluation (FR-004) needs both. Load-bearing.
- **G4 — `Story` carries no `bundleable` stamp.** It is the only fact-bearing structure in the commons that is not a `SourcedValue` (bare `source`, no `bundleable`/`confidence`/`observed_at`), so the quarantine filter has no stamp to read and must re-derive from the license. *(poi-site.md)*
- **G5 — `Timeline.stop_id` is unresolvable.** `Stop` has no `id`; the card's example uses a *site* UUID as `stop_id`, while `RouteLegV1` addresses stops by `order` (int). Two addressing schemes for one collection, and a day that visits a site twice is ambiguous. *(itinerary.md + route-leg.md)*
- **G6 — one hash covers two artifacts.** `routing.sha256` spans `walk_graph` **and** `legs`; `content.sha256` spans `sites` **and** `narrations`; `attribution` has **no hash at all** — contradicting the card's own "every artifact is SHA-256'd" and FR-013's corruption detection. *(bundle-manifest.md)*
- **G7 — `integrity.manifest_sha256` has no stated canonicalization.** The manifest contains the field that hashes it. Assumed here: canonical JSON with `integrity` omitted. Unstated = two implementations will disagree.
- **G8 — `tiles.pmtiles` vs `TileSourceV1`.** `bundle-manifest.md` gives a 4-field object `{path, sha256, bbox, maxzoom}`; `tile-source.md` says "in M1 this **is** the `tiles.pmtiles` object" and defines 13 fields including `build_date`, `tile_license`, `attribution`, `style`, `glyphs`. Direct contradiction about what the manifest holds.
- **G9 — the itinerary card's leg examples omit `geometry` and `source`**, both of which `route-leg.md` marks M1-required. Resolved in favour of `route-leg.md` (field-level ground truth for legs); the itinerary examples read as abbreviated, which a transcriber could easily take literally.
- **G10 — nothing records what quarantine withheld.** The spec's edge case says "the bundle records what was withheld and why"; `BundleManifestV1` has no such field, and FR-021's "needs connectivity" affordance has nothing to render from.
- **G11 — no `SourceKind` for a routing engine.** `route-leg.md`'s example stamps a Valhalla-produced leg as `kind:"osm", id:"valhalla:pedestrian"`. That is the only statement of the convention and it lives in an example, not in prose — worth promoting, since the produced-work chain is what makes the leg bundleable.
- **G12 — minor**: `ItineraryV1` has no `created_at`/`updated_at` (the card mentions "any `timestamptz` audit fields" but names none — parked on the table); `bundle_id` has no stated format; `itinerary.md` writes the leg type as `RouteLeg` where `route-leg.md` names it `RouteLegV1`; and the per-user column is `created_by` on `area` but `user_id` on `user_note`/`user_plan`.
