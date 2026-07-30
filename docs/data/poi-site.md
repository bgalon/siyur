# Schema card — POI / site (`SiteRecordV1`)

*The commons record. One row per real-world place, **globally shared**, assembled by merging many sources.
Authoritative source: `docs/design/tech-design.md` §1.0–1.2. This card is the field-level ground truth — **never guess this
schema; read this card.** Constitution Article V (provenance is mechanical) is enforced at this boundary.*

- **Schema version:** `SiteRecordV1` (`schema_ver` literal). M1 populates a subset; later fields exist but may be empty.
- **Storage:** Cloud SQL Postgres + PostGIS — table `site` (`fields jsonb` holds the `SourcedValue` map),
  append-only `site_source` provenance rows, `story`, `site_conflict`. See tech-design §2.
- **CRS:** **EPSG:4326 (lon, lat)** for all geometry. Geometry type: **Point** (`geometry(Point,4326)`, GiST-indexed).
  Never let the LLM emit or arithmetic on coordinates — PostGIS/shapely compute them (AGENTS.md geo rules).
- **Timezone:** `updated_at` is `timestamptz` (UTC). `opening_hours` is an **opening_hours.js** string evaluated in the
  **area's local wall-clock time** (locale/country passed for PH/SH resolution — FAIL-catalog: pass it or holidays misfire).
  `observed_at` (inside each `SourcedValue`) is a UTC date driving staleness / refresh-on-reuse.
- **License & provenance:** every value is a `SourcedValue` stamped `source + license + bundleable` at ingestion.
  License pointer → [`/DATA-LICENSES.md`](../../DATA-LICENSES.md). **Quarantine invariant** (merge-blocking test
  `test_structural.py::test_no_unbundleable_in_bundle`): `bundleable=true` only if `source.license` ∈
  {ODbL, CDLA-Permissive-2.0, CC0, CC-BY-4.0, CC-BY-SA-4.0, PD, OFL, LGPL-as-dependency}; `open_web` and
  `review_provider` are **always** `bundleable=false`; no bundle may carry a `bundleable=false` value.

## The primitive — `SourcedValue<T>`

Every fact Siyur shows is *stamped*, not bare.

| Field | Type | Units / notes |
|---|---|---|
| `value` | `T` | the fact itself |
| `source` | `SourceRef` | where it came from (below) |
| `bundleable` | `bool` | may this be baked into an offline bundle? (gated by license, see quarantine rule) |
| `confidence` | `float [0..1]` | curation/merge confidence |
| `observed_at` | `date` | when we fetched/derived it (UTC; drives staleness) |

**`SourceRef`**

| Field | Type | Notes |
|---|---|---|
| `kind` | enum | `overture` \| `osm` \| `wikivoyage` \| `wikipedia` \| `wikidata` \| `commons` \| `opening_hours_js` \| `review_provider` \| `open_web` \| `user` |
| `id` | `str` | GERS id / OSM `type+id` / QID / article title / URL |
| `url` | `str \| null` | |
| `license` | `SPDX str \| "proprietary" \| "user-owned"` | drives `bundleable`; registry = `DATA-LICENSES.md` |
| `attribution` | `str \| null` | rendered string when the license requires it (ODbL, CC-BY-SA) |

## `SiteRecordV1` fields

| Field | Type | M1? | Units / notes |
|---|---|---|---|
| `id` | `UUID` | M1 | our stable id |
| `gers_id` | `str \| null` | M1 | Overture GERS — cross-source join key when present (Overture↔OSM share none → joins are mostly fuzzy) |
| `names` | `{ bcp47: SourcedValue<str> }` | M1 | keys are **BCP-47 subtags** (`en`, `ja`, `ja-Hira`, `ja-Latn`), not bare lang codes. `he` at M3. Local-script names are sparse in sources → name transliteration is an **M1 sliver** (accepted 2026-07-24) |
| `location` | `SourcedValue<Point>` | M1 | EPSG:4326 (lon,lat); PostGIS `geometry(Point,4326)` |
| `categories` | `[SourcedValue<str>]` | M1 | Overture `basic_category` (post-Sept-2026 field) + OSM tags |
| `address` | `SourcedValue<str> \| null` | M1 | **source scripts untrustworthy** — a Hebrew address was found stored in Cyrillic; normalize/validate, never trust the script (FAIL-001) |
| `opening_hours` | `SourcedValue<str> \| null` | M1 | opening_hours.js syntax + parsed windows; local wall-clock (see Timezone) |
| `stories` | `[Story]` | M1 | ≥1 adapted **CC-BY-SA** story with per-article attribution (PRD §7 rich narration posture) |
| `notes` | `[SourcedValue<str>]` | M1 | free text; user notes are `source.kind="user"`, stored **private**, never auto-published |
| `phone` | `SourcedValue<str> \| null` | M2+ | |
| `price` | `SourcedValue<str> \| null` | M2+ | tickets/fees |
| `accessibility` | `SourcedValue<str> \| null` | M2+ | |
| `website` | `SourcedValue<str> \| null` | M2+ | official / booking |
| `links` | `[SourcedValue<str>]` | M2+ | tourism-site links (bundleable if just URLs) |
| `reviews` | `ReviewSummary \| null` | M2+ | link-and-summarize; **always `bundleable=false`** (PRD §13 #2 open) |
| `conflicts` | `[FieldConflict]` | M1 | unresolved disagreements between sources (merge never discards a source) |
| `updated_at` | `timestamptz` | M1 | UTC |
| `schema_ver` | `"SiteRecordV1"` | M1 | literal |

**Sub-structures**

```
Story:
  text_by_lang:  { bcp47: str }          # en canonical (+ translations at M3)
  source:        SourceRef                # CC-BY-SA article; attribution required
  claims:        [ {span, SourceRef} ]    # [M2+] per-claim provenance

ReviewSummary:                            # [M2+] bundleable=false, live-online-only
  ratings:   [ { provider, stars: float, count: int|null, url: str } ]
  fetched_at: timestamptz

FieldConflict:
  field:      str
  candidates: [SourcedValue]              # the disagreeing values, each still sourced
  resolution: "unresolved" | "picked:<source.id>" | "user-override"
```

**M1 must populate:** `id`, `location`, `names.en`, `categories`, and — where the source has it — `address`,
`opening_hours`, and ≥1 `story`; every populated value carries a real `SourceRef` and `bundleable` stamp. Empty M2+
fields are valid in M1.

**Merge (per-field, union-first; thresholds from the discovery spike):** join on `gers_id` when present, else fuzzy
**spatial+name** (PostGIS distance ≤ **ε = 25 m** AND same-language name similarity ≥ **τ = 0.6**, after transliteration).
Distance alone never merges — a name signal is required. Each field keeps the winning `SourcedValue`; a losing *different*
value becomes a `FieldConflict`; **no source ref is ever lost** (tested). Winner = highest `confidence`, tie-break by
source-trust (Overture/Wikidata > Wikivoyage > OSM tags > open_web) then most recent `observed_at`. Details: tech-design §1.2.

## Example rows

```jsonc
// 1 — Overture place, permissively licensed (CDLA-P → bundleable)
{
  "id": "6f1c…-uuid", "gers_id": "08f394…gers", "schema_ver": "SiteRecordV1",
  "names": { "en": { "value": "Palace of the Grand Master", "source": {
    "kind": "overture", "id": "08f394…gers", "url": null,
    "license": "CDLA-Permissive-2.0", "attribution": null }, "bundleable": true,
    "confidence": 0.82, "observed_at": "2026-07-22" } },
  "location": { "value": { "type": "Point", "coordinates": [28.2247, 36.4443] },
    "source": { "kind": "overture", "id": "08f394…gers", "license": "CDLA-Permissive-2.0" },
    "bundleable": true, "confidence": 0.9, "observed_at": "2026-07-22" },
  "categories": [ { "value": "attraction.castle", "source": { "kind": "overture",
    "id": "08f394…gers", "license": "CDLA-Permissive-2.0" }, "bundleable": true,
    "confidence": 0.8, "observed_at": "2026-07-22" } ],
  "conflicts": [], "updated_at": "2026-07-22T09:00:00Z"
}

// 2 — OSM long-tail POI with opening_hours; ODbL (attribution required, bundleable)
{
  "id": "a20e…-uuid", "gers_id": null, "schema_ver": "SiteRecordV1",
  "names": { "el": { "value": "Ρολόι", "source": { "kind": "osm", "id": "node/123456",
    "license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors" },
    "bundleable": true, "confidence": 0.7, "observed_at": "2026-07-20" } },
  "location": { "value": { "type": "Point", "coordinates": [28.2235, 36.4451] },
    "source": { "kind": "osm", "id": "node/123456", "license": "ODbL-1.0",
    "attribution": "© OpenStreetMap contributors" }, "bundleable": true,
    "confidence": 0.7, "observed_at": "2026-07-20" },
  "opening_hours": { "value": "Mo-Su 09:00-19:00", "source": { "kind": "osm",
    "id": "node/123456", "license": "ODbL-1.0" }, "bundleable": true,
    "confidence": 0.5, "observed_at": "2026-07-20" },
  "categories": [], "conflicts": [], "updated_at": "2026-07-20T14:00:00Z"
}

// 3 — record with a story (CC-BY-SA, bundleable) and a review summary (NOT bundleable)
{
  "id": "c9d1…-uuid", "gers_id": "08f3aa…gers", "schema_ver": "SiteRecordV1",
  "names": { "en": { "value": "Street of the Knights", "source": { "kind": "wikidata",
    "id": "Q1049981", "license": "CC0-1.0" }, "bundleable": true, "confidence": 0.95,
    "observed_at": "2026-07-21" } },
  "location": { "value": { "type": "Point", "coordinates": [28.2238, 36.4447] },
    "source": { "kind": "wikidata", "id": "Q1049981", "license": "CC0-1.0" },
    "bundleable": true, "confidence": 0.95, "observed_at": "2026-07-21" },
  "categories": [],
  "stories": [ { "text_by_lang": { "en": "The cobbled street once housed the …" },
    "source": { "kind": "wikivoyage", "id": "Rhodes", "url": "https://en.wikivoyage.org/wiki/Rhodes",
    "license": "CC-BY-SA-4.0", "attribution": "Wikivoyage: Rhodes (CC BY-SA 4.0)" } } ],
  "reviews": { "ratings": [ { "provider": "example", "stars": 4.6, "count": 1200,
    "url": "https://…" } ], "fetched_at": "2026-07-25T10:00:00Z" },
  "conflicts": [], "updated_at": "2026-07-25T10:00:00Z"
}
```
