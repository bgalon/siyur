# Phase 1 — Data Model: Research an area into a cited commons

**Feature**: `specs/001-research-cited-sites` · **Date**: 2026-07-31

**Authority**: This slice populates a **subset** of `SiteRecordV1` **exactly as `docs/data/poi-site.md` defines it** (which is itself ground-truthed to `tech-design.md` §1.0–§1.2). **Nothing here re-defines the schema** — it records which fields this slice fills, the storage mapping, and the validation rules the code enforces. Where this doc and the schema card differ, **the card wins**.

## Entities in scope

| Entity | Realized as | This slice |
|---|---|---|
| **Research Area** | transient request + optional persisted area row bounding a research pass | delimit → resolve polygon → coverage check |
| **Site record** | `SiteRecordV1` (`commons/models.py` pydantic + `site` table) | **populate the M1 subset minus stories** |
| **Sourced value** | `SourcedValue<T>` | every field value |
| **Source / attribution** | `SourceRef` | Overture + OSM (this slice) |
| **Field conflict** | `FieldConflict` | recorded on disagreement, no source lost |

## 1. `SourcedValue<T>` and `SourceRef` (the primitive — verbatim from the card)

```
SourcedValue<T>:
  value:       T
  source:      SourceRef
  bundleable:  bool          # allowlist-gated (see §5 quarantine); never author-set true blindly
  confidence:  float [0..1]
  observed_at: date          # UTC; drives staleness / refresh-on-reuse

SourceRef:
  kind:        "overture" | "osm" | "wikivoyage" | "wikipedia" | "wikidata"
             | "commons" | "opening_hours_js" | "review_provider" | "open_web" | "user"
  id:          str           # GERS id | OSM "type/id" | …
  url:         str | null
  license:     SPDX str | "proprietary" | "user-owned"
  attribution: str | null    # rendered when the license requires it (ODbL, CC-BY-SA)
```

**This slice emits only `kind ∈ {overture, osm}`.** `wikivoyage/wikipedia/wikidata/commons` (stories, extra facts) are slice 002+; `review_provider`/`open_web` never appear (and are always `bundleable=false`); `user` appears only for **private** notes (never written to the commons — FR-010).

## 2. `SiteRecordV1` — fields this slice populates

| Field | Type | This slice | Rule |
|---|---|---|---|
| `id` | `UUID` | ✅ set | server-generated stable id |
| `gers_id` | `str \| null` | ✅ when Overture has it | cross-source join key; usually null between Overture↔OSM (fuzzy join then) |
| `names` | `{ bcp47: SourcedValue<str> }` | ✅ `en`, source-script (e.g. `el`), and **derived `el-Latn`** | keys are **BCP-47 subtags**; see §3 |
| `location` | `SourcedValue<Point>` | ✅ **required** | EPSG:4326 (lon,lat); from authoritative geodata **only** — never model-emitted (FR-005) |
| `categories` | `[SourcedValue<str>]` | ✅ | Overture `basic_category` + OSM tags |
| `address` | `SourcedValue<str> \| null` | ✅ where source has it | **script validated, never transliterated** (FAIL-001) |
| `opening_hours` | `SourcedValue<str> \| null` | ✅ where source has it | opening_hours.js string; local wall-clock (not evaluated in this slice) |
| `stories` | `[Story]` | ❌ **OUT (slice 002)** | leave empty — valid |
| `notes` | `[SourcedValue<str>]` | ⚠ private only | `kind="user"`, never auto-published |
| `phone`/`price`/`accessibility`/`website`/`links`/`reviews` | — | ❌ M2+ | empty is valid |
| `conflicts` | `[FieldConflict]` | ✅ | populated by merge on disagreement |
| `updated_at` | `timestamptz` | ✅ | UTC |
| `schema_ver` | `"SiteRecordV1"` | ✅ literal | |

**Must-populate for a valid slice-001 record**: `id`, `location`, `names.en` (or a transliterated `*-Latn` where only non-Latin exists), `categories`, `schema_ver`, `updated_at`; plus `address`/`opening_hours` where the source carries them. Empty `stories` and all M2+ fields are valid.

> **Divergence flag (deliberate, not a schema change):** the card lists `stories` under "M1 must populate: … ≥1 story". This *slice* (001) explicitly defers stories to slice 002 per FR-011 and the spec's Q3=A resolution. The **schema is unchanged** (empty `stories` was always valid); only *this slice's* fill-set narrows. Slice 002 restores the story fill.

## 3. Names, BCP-47 keys, and the transliteration sliver (ADR-0010)

- Keys are **BCP-47 subtags** (`en`, `el`, `el-Latn`, `ja-Hira`, `ja-Latn`), **not** bare lang codes.
- Source-script display name (e.g. Greek) lands under its language key (`el`) with its own `SourcedValue` (OSM `name:el` → `kind="osm"`, ODbL).
- The **derived Latin display name** lands under `el-Latn`:
  - `value` = deterministic ICU `Greek-Latin` transform of the `el` value,
  - `source` = **inherited** from the `el` value's `SourceRef` (produced-work chain → same license, same attribution, same `bundleable`),
  - `confidence` = transliteration certainty,
  - `observed_at` = derivation date.
- The **original `el` value and its attribution are never overwritten** (FR-008, SC-004).
- **Script-validation guard (FAIL-001)**: before deriving, assert the source value's script matches its declared language; a mismatch (e.g. Cyrillic in a `he`/`el` field) is normalised/flagged, never trusted. **Addresses are not transliterated** for the same reason.

## 4. Storage mapping (PostGIS — tech-design §2)

| Table | Columns | Holds |
|---|---|---|
| `site` | `id uuid pk`, `gers_id text`, `geom geometry(Point,4326)` **GiST**, `fields jsonb`, `updated_at timestamptz` | one row per place; `fields` = the `SourcedValue` map (names/categories/address/hours) |
| `site_source` | `id`, `site_id fk`, `field`, `source jsonb`, `value jsonb`, `observed_at` | **append-only** provenance audit — lets a merge re-run without data loss and the UI show *why* a value is what it is |
| `site_conflict` | `id`, `site_id fk`, `field`, `candidates jsonb`, `resolution` | recorded disagreements |
| `user_note` (private) | `id`, `user_id`, `site_id`, `value jsonb` | **row-scoped**, never joined into commons reads |

- Coverage query (reuse): `SELECT … FROM site WHERE ST_Within(geom, :area_polygon)`.
- Alembic migration adds/confirms these tables; identical local + Cloud SQL.
- `location.value` is the single source of truth for `geom` (kept consistent; geometry is computed/validated by PostGIS/shapely, **never the LLM**).

## 5. Validation rules (enforced in code + evals)

1. **Provenance completeness (FR-003 / SC-002)**: every populated field value MUST be a `SourcedValue` with a non-null `source`. A record with any bare/unstamped value is **rejected at ingestion** and never persisted or displayed. *(eval: schema + provenance-completeness)*
2. **Quarantine invariant (Constitution V)**: `bundleable=true` ⟺ `source.license ∈` allowlist; `open_web`/`review_provider` ⇒ `false`. *(merge-blocking: `evals/test_structural.py::test_no_unbundleable_in_bundle`)*
3. **Geometry validity (FR-005)**: `location.value` is a valid `Point` in EPSG:4326 with plausible lon∈[-180,180]/lat∈[-90,90]; the value's `source.kind ∈ {overture, osm}` (authoritative), never `user`/model-emitted. *(eval: geometry validity; hypothesis property test)*
4. **No source lost on merge (FR-009)**: after merging N candidate values for a field, every distinct source ref is still reachable — as the winning `SourcedValue` or a `FieldConflict` candidate. *(test: `test_merge.py`; eval: merge golden cases)*
5. **Merge join rule (tech-design §1.2)**: `gers_id` join, else spatial≤ε=25m **AND** name-sim≥τ=0.6 (same language, post-transliteration); **distance alone never merges**.
6. **Transliteration (FR-008 / SC-004)**: for ≥95% of non-Latin display names a `*-Latn` value exists; the original is preserved in **every** case; script-validation guard runs first (FAIL-001). *(test: `test_translit.py`)*
7. **Privacy (FR-010)**: no `source.kind="user"` value is ever written to `site`/`site_source`; user notes live only in `user_note`, row-scoped.
8. **Genericity (FR-001 / SC-005)**: no place literal (no "Rhodes"/bbox constant) in `commons`/`planner`/`api` product code. *(eval: `test_genericity.py` runs the flow on Rhodes + ≥1 other area)*

## 6. State / lifecycle

```
delimit area ──▶ resolve polygon ──▶ ST_Within coverage?
                                        │
                 ┌──────────────────────┴───────────────────────┐
             covered                                        not covered (or refresh)
                 │                                                │
     return existing cited records                      research: Overture + OSM adapters
     + offer refresh (observed_at)                      → stamp SourcedValues (ingestion)
                 │                                       → curate/dedupe → per-field merge
                 └───────────────► shared commons ◄──────  → upsert site/site_source/site_conflict
                                        │
                                   GET /sites?bbox → map markers + attribution chips
```

No destructive transitions: refresh **enriches** (new `observed_at`, new `FieldConflict`s), never overwrites a source. Every write is auth-gated (ADR-0008).
