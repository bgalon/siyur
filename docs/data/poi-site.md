# Schema card — POI / site (`SiteRecordV1`)

*The commons record. One row per real-world place, **globally shared**, assembled by merging many sources.
Authoritative source: `docs/design/tech-design.md` §1.0–1.2. This card is the field-level ground truth — **never guess this
schema; read this card.** Constitution Article V (provenance is mechanical) is enforced at this boundary.*

- **Schema version:** `SiteRecordV1` (`schema_ver` literal). M1 populates a subset; later fields exist but may be empty.
- **Storage:** Cloud SQL Postgres + PostGIS — table `site` (`fields jsonb` holds the `SourcedValue` map),
  append-only `site_source` provenance rows, `site_conflict`, and (M1, not yet shipped) `story`. See tech-design §2.
  **The commons is source-derived only:** `user_note` (private, row-scoped to `user_id`) is a *sibling* of these tables,
  never part of them — a value whose `source.kind="user"` is **refused at the commons boundary** before any row is
  written (`commons/repository.py::CommonsWriteRefused`, FR-010 / validation rule 7), winning value and provenance
  ledger alike.
- **CRS:** **EPSG:4326 (lon, lat)** for all geometry. Geometry type: **Point** (`geometry(Point,4326)`, GiST-indexed).
  Never let the LLM emit or arithmetic on coordinates — PostGIS/shapely compute them (AGENTS.md geo rules).
- **Timezone:** `updated_at` is `timestamptz` (UTC). `opening_hours` is an **OSM `opening_hours` syntax** string,
  evaluated by **`opening-hours-py`** (ADR-0022 — MIT OR Apache-2.0; it replaced `opening_hours.js`) in the **area's
  local wall-clock time**, with the area's `timezone` and `country_code` passed for PH resolution (FAIL-catalog: pass
  them or holidays misfire). `SH`-bearing and unparseable expressions **fail closed** — `hours_unknown`, never "open".
  `observed_at` (inside each `SourcedValue`, and on each `Story`) is a UTC date driving staleness / refresh-on-reuse.
- **License & provenance:** every value is a `SourcedValue` stamped `source + license + bundleable` at ingestion.
  License pointer → [`/DATA-LICENSES.md`](../../DATA-LICENSES.md), which is the **registry of record** — this list is
  transcribed from it, never invented here. **Quarantine invariant** (merge-blocking test
  `test_structural.py::test_no_unbundleable_in_bundle`): `bundleable=true` **⟺** `source.license` ∈
  {ODbL, CDLA-Permissive-2.0, **Apache-2.0**, CC0, CC-BY-4.0, CC-BY-SA-4.0, PD, OFL, LGPL-as-dependency} **and**
  `source.kind ∉ {open_web, review_provider}` — those two kinds are **always** `bundleable=false` whatever license they
  claim; no bundle may carry a `bundleable=false` value. It is an **equivalence, not "only if"**: `bundleable=false`
  over an allowlisted license is refused too, so the stamp is *derived* from the registry and can never be author-set
  in either direction (`commons/licenses.py::bundleable`, `SourcedValue.stamp`).
  *Apache-2.0 added 2026-08-01 (ADR-0012): Overture places mixes licenses within one theme (33 of 200 fixture rows are
  Foursquare Apache-2.0), so omitting it quarantined 16.5 % of places. **Read the per-record stamp, never the theme's
  usual license.***

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
| `kind` | enum | `overture` \| `osm` \| `wikivoyage` \| `wikipedia` \| `wikidata` \| `commons` \| `opening_hours_js` \| `review_provider` \| `open_web` \| `user`. **`opening_hours_js` keeps its spelling** — it is a live enum value in `commons/licenses.py` and a trust weight in `merge.py`, and now means "deterministic opening-hours evaluation" whatever engine backs it (`opening-hours-py` since ADR-0022). Renaming it is a `SiteRecordV2` concern |
| `id` | `str` | GERS id / OSM `type+id` / QID / article title / URL |
| `url` | `str \| null` | |
| `license` | `SPDX str \| "proprietary" \| "user-owned"` | drives `bundleable`; registry = `DATA-LICENSES.md` |
| `attribution` | `str \| null` | rendered string when the license requires it (ODbL, CC-BY-SA) |

## `SiteRecordV1` fields

| Field | Type | M1? | Units / notes |
|---|---|---|---|
| `id` | `UUID` | M1 | our stable id |
| `gers_id` | `str \| null` | M1 | Overture GERS — cross-source join key when present (Overture↔OSM share none → joins are mostly fuzzy) |
| `names` | `{ bcp47: SourcedValue<str> }` | M1 | keys are **BCP-47 subtags** (`en`, `ja`, `ja-Hira`, `ja-Latn`), not bare lang codes — **plus `und`**, see below. `he` at M3. Local-script names are sparse in sources → name transliteration is an **M1 sliver** (accepted 2026-07-24) |
| `location` | `SourcedValue<Point>` | M1 | EPSG:4326 (lon,lat); PostGIS `geometry(Point,4326)` |
| `categories` | `[SourcedValue<str>]` | M1 | Overture `basic_category` (post-Sept-2026 field) + OSM tags |
| `address` | `SourcedValue<str> \| null` | M1 | **source scripts untrustworthy** — a Hebrew address was found stored in Cyrillic; normalize/validate, never trust the script (FAIL-001) |
| `opening_hours` | `SourcedValue<str> \| null` | M1 | OSM `opening_hours` syntax + parsed windows (evaluator: `opening-hours-py`); local wall-clock (see Timezone) |
| `stories` | `[Story]` | M1 | adapted **CC-BY-SA** stories with per-article attribution (PRD §7 rich narration posture). **Fill-set aspiration, not a validation rule** — see below |
| `notes` | `[SourcedValue<str>]` | M1 | free text. A `source.kind="user"` note is **private**: it lives in `user_note`, never in `site`/`site_source` (see Storage) — it is never auto-published and never merged into the commons record |
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
  source:        SourceRef                # CC-BY-SA article; attribution required — a BARE ref, not a SourcedValue
  observed_at:   date                     # [M1] UTC date the article revision was fetched — the staleness key
                                          #      for bundled narration (there is no SourcedValue stamp to carry one)
  claims:        [ {span, SourceRef} ]    # [M2+] per-claim provenance

ReviewSummary:                            # [M2+] bundleable=false, live-online-only
  ratings:   [ { provider, stars: float, count: int|null, url: str } ]
  fetched_at: timestamptz

FieldConflict:
  field:      str                         # dotted path: `location`, `names.el-Latn`, `address`, …
  candidates: [SourcedValue]              # the disagreeing values, each still sourced — winner INCLUDED,
                                          # so a conflict is self-contained. A geometry candidate carries
                                          # its value as **GeoJSON**, not a bare Point: candidates land in
                                          # `site_conflict.candidates jsonb` and must be serialisable.
  resolution: "unresolved" | "picked:<source.id>" | "user-override"
```

**`Story` is one of several fact-bearing structures that are *not* `SourcedValue`s — the quarantine filter must derive,
not read.** A `Story` carries a **bare `SourceRef`** (plus `observed_at`); it has **no `bundleable` and no `confidence`
field**, because the whole story is one attributed adaptation of one article, not a stamped value in a field map. So
the compile quarantine filter cannot ask a story whether it may be bundled — it must **compute** the answer:
`commons/licenses.py::bundleable(story.source.kind, story.source.license)`, exactly the function that derives the
`SourcedValue` stamp everywhere else. **A filter that reads `bundleable` off a story reads `None` and silently drops
(or silently ships) every narration** — this asymmetry is the trap, and it is why the rule is written here rather than
inferred. A story whose `source` is missing or unstamped is refused like any other unstamped input.

**And `Story` is not alone — this is the important half.** The same bare-`SourceRef` shape is carried by **`RouteLegV1`**
([`route-leg.md`](./route-leg.md)), **`ResolvedArea`** and **`AreaCandidate`** ([`area.md`](./area.md)). Every one of
them must have its bundleability **derived** the same way. The rule is therefore *structural*, not a `Story` special
case: **derive for anything that is not a `SourcedValue`; only a `SourcedValue` carries a `bundleable` field to read.**

A filter written as `derive if isinstance(v, Story) else v.bundleable` — the shape the single-case wording invites —
reaches `routing.legs`, finds no `bundleable` attribute on `RouteLegV1`, and either raises mid-compile or, with the
likelier `getattr(v, "bundleable", False)` repair, **drops every walking leg from every bundle**. The bundle still
compiles, still hashes, still passes every path check, and the traveller's day has no routes. That failure is silent
at every gate, which is why the enumeration above is normative rather than illustrative.

**M1 must populate:** `id`, `location`, `names.en`, `categories`, and — where the source has it — `address` and
`opening_hours`; every populated value carries a real `SourceRef` and `bundleable` stamp. Empty M2+ fields are valid
in M1.

**Stories are a fill-set aspiration, not a validation rule.** The M1 *goal* is that a place a traveller stands in front
of has something to read: ≥1 adapted CC-BY-SA story wherever an openly-licensed article exists. But **where no such
article exists the place carries no story, and nothing is invented** (FR-023) — an empty `stories` list is a valid,
correct `SiteRecordV1`, never a validation failure, and a generated story with no article behind it is the actual
defect. "≥1 story" measures coverage across a researched area; it never gates a single record.

## Name keys — `und` and the derived `*-Latn` twins

**`und` is a first-class key.** A source that publishes a display name **without declaring its language** (Overture
`names.primary`, a bare OSM `name` tag) keys it `und` ("undetermined", BCP-47) rather than guessing — guessing a
language would be inventing provenance. `und` is the *absence* of a language claim, not a language.

**Derived Latin display names inherit, they do not invent** (FR-008 / SC-004; ADR-0010). `el` → `el-Latn` is produced
by a deterministic, offline rule table — never by the LLM, never by an ASCII-folding library. The derived
`SourcedValue` carries the **same `SourceRef` as its parent** (a transliteration is a *produced work* of the source
datum), so `license`, `attribution` and therefore `bundleable` are identical to the parent's; `confidence` is the
transliteration's certainty and `observed_at` is the derivation date. **The original-script value is never
overwritten**, and a `*-Latn` key a source supplied itself is never replaced by a derived one.

**The script guard runs first** (FAIL-001): a value's *actual* script is asserted against the script its tag declares,
and a mismatch is **flagged and never transliterated** — the Hebrew address stored in Cyrillic is why. "No expectation
on record" and "no letters" are *not* passes; nothing is derived from them either. Addresses are validated by the same
guard but **never transliterated** (ADR-0010).

**Merge (per-field, union-first; thresholds from the discovery spike):** join on `gers_id` when present, else fuzzy
**spatial+name** (PostGIS distance ≤ **ε = 25 m** AND same-language name similarity ≥ **τ = 0.6**, after transliteration).
Distance alone never merges — a name signal is required. Each field keeps the winning `SourcedValue`; a losing *different*
value becomes a `FieldConflict`; **no source ref is ever lost** (tested). Winner = highest `confidence`, tie-break by
source-trust (Overture/Wikidata > Wikivoyage > OSM tags > open_web) then most recent `observed_at`. Details: tech-design §1.2.

Five rules the join actually turns on, stated here because the code depends on every one of them:

1. **`gers_id` is authoritative both ways.** The id rule applies when **both** records carry one: equal ids join, and
   **different ids are different places and are never retried fuzzily**. A fuzzy chain may likewise not pull two
   *different* `gers_id`s into one cluster — that union is refused.
2. **`und` is comparable to any tagged key.** Same-language comparison is exact-key by default, but an `und` value on
   one side is compared against **every** key on the other, and the score is filed under the *determined* tag. Without
   this the commonest cross-source case cannot merge at all: the same place, the same name bytes, keyed `und` by one
   source and `el` by the other, scored 0.00 (**FAIL-004**). This changes which names are *comparable*, never the
   thresholds — ε **and** τ must still both hold, and two *differently tagged* languages still never compare.
3. **Multi-valued fields union; they do not conflict.** `categories`, `notes` and `links` are merged by **union** —
   two sources listing different categories are complementary, and **no `FieldConflict` is recorded**. `FieldConflict`
   is for single-valued fields (`location`, each `names.<tag>`, `address`, `opening_hours`, `phone`, `price`,
   `accessibility`, `website`) where the sources state *different* values for the same slot.
4. **Agreement is not a conflict — the ledger is what makes "no source lost" true.** When two sources state the *same*
   value there is nothing to conflict over, so the loser's stamp survives only as an append-only `site_source` row.
   The invariant is therefore over *record ∪ ledger*, not over the record alone: **every** candidate for every field
   is written to `site_source`, deduped on `(site_id, field, source, value, observed_at)` so a refresh appends nothing
   new. Conflicts already recorded on an input travel with it through a re-merge and are never dropped.
   **That dedupe is a database invariant, not a code path** (migration `0003_dedupe_natural_keys`): `site_source` carries
   a unique index over that natural key and `site_conflict` over `(site_id, field, candidates, resolution)`, and the
   repository writes both with `INSERT … ON CONFLICT DO NOTHING`. The `jsonb` halves are compared as `md5(col::text)` —
   Postgres's own canonical rendering, so key order does not matter — rather than by indexing the `jsonb` columns
   directly, which would make an oversized `candidates` array *fail* the btree 2704-byte index-tuple limit. `observed_at`
   is part of the key precisely so `site_source` stays genuinely append-only: **the same value observed on a different
   date is a new row and still inserts.**
5. **The winner is a pure function of the candidate set.** After `confidence` → source-trust → `observed_at`, ties fall
   through to `source.kind`, `source.id`, then the value itself, so the same candidates in any input order always elect
   the same winner. A geometry is compared at **7 decimals (≈1 cm)**: float noise from a JSON/PostGIS round-trip is not
   a disagreement and must not manufacture a `FieldConflict`.

**Upsert (refresh) uses the same rule, never a second one:** an incoming record joins the existing commons row by
`gers_id`, else by `decide_match` against the rows PostGIS returns within ε (`ST_DWithin` on `geography`, the same
WGS84 metric the Python join uses); on a hit both are re-merged and written back **onto the existing row's id**, so a
refresh enriches and never forks. A stored `fields` blob that fails `SiteRecordV1` validation is **dropped on read and
logged**, never returned as a half-record (FR-003).

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
  // A Story has a bare `source` + `observed_at` — no `bundleable`/`confidence` stamp.
  // Bundleability is DERIVED: bundleable("wikivoyage", "CC-BY-SA-4.0") is True.
  "stories": [ { "text_by_lang": { "en": "The cobbled street once housed the …" },
    "source": { "kind": "wikivoyage", "id": "en:Rhodes",
    "url": "https://en.wikivoyage.org/wiki/Rhodes?oldid=4812301",
    "license": "CC-BY-SA-4.0",
    "attribution": "\"Rhodes\", Wikivoyage, https://en.wikivoyage.org/wiki/Rhodes — authors via page history" },
    "observed_at": "2026-07-25" } ],
  "reviews": { "ratings": [ { "provider": "example", "stars": 4.6, "count": 1200,
    "url": "https://…" } ], "fetched_at": "2026-07-25T10:00:00Z" },
  "conflicts": [], "updated_at": "2026-07-25T10:00:00Z"
}

// 4 — merged from two sources: `und` ↔ `el` join (FAIL-004), a derived `el-Latn` twin
//     inheriting its parent's stamp, and a `location` conflict carrying GeoJSON candidates
{
  // Only the Overture side carried a GERS id, so the join was the fuzzy ε∧τ rule; the
  // merged record keeps the id, which is what a later refresh joins on.
  "id": "31b7…-uuid", "gers_id": "08f39c…gers", "schema_ver": "SiteRecordV1",
  "names": {
    // Overture published the name without declaring a language ⇒ `und`, never a guess.
    "und": { "value": "Πύλη Ταρσανά", "source": { "kind": "overture", "id": "08f39c…gers",
      "license": "CDLA-Permissive-2.0" }, "bundleable": true, "confidence": 0.8,
      "observed_at": "2026-07-22" },
    "el": { "value": "Πύλη Ταρσανά", "source": { "kind": "osm", "id": "way/987654",
      "license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors" },
      "bundleable": true, "confidence": 0.7, "observed_at": "2026-07-20" },
    // Derived: same SourceRef as its `el` parent — same license, same attribution, same
    // `bundleable`. `confidence` is the transliteration's, `observed_at` the derivation date.
    "el-Latn": { "value": "Pyli Tarsana", "source": { "kind": "osm", "id": "way/987654",
      "license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors" },
      "bundleable": true, "confidence": 0.9, "observed_at": "2026-08-01" }
  },
  "location": { "value": { "type": "Point", "coordinates": [28.2229, 36.4462] },
    "source": { "kind": "overture", "id": "08f39c…gers", "license": "CDLA-Permissive-2.0" },
    "bundleable": true, "confidence": 0.8, "observed_at": "2026-07-22" },
  // Union, not conflict: two sources' categories are complementary.
  "categories": [ { "value": "attraction.gate", "source": { "kind": "overture",
    "id": "08f39c…gers", "license": "CDLA-Permissive-2.0" }, "bundleable": true,
    "confidence": 0.8, "observed_at": "2026-07-22" } ],
  "conflicts": [ { "field": "location", "resolution": "picked:08f39c…gers", "candidates": [
    { "value": { "type": "Point", "coordinates": [28.2229, 36.4462] },
      "source": { "kind": "overture", "id": "08f39c…gers", "license": "CDLA-Permissive-2.0" },
      "bundleable": true, "confidence": 0.8, "observed_at": "2026-07-22" },
    { "value": { "type": "Point", "coordinates": [28.2229, 36.4461] },
      "source": { "kind": "osm", "id": "way/987654", "license": "ODbL-1.0",
      "attribution": "© OpenStreetMap contributors" }, "bundleable": true,
      "confidence": 0.7, "observed_at": "2026-07-20" } ] } ],
  "updated_at": "2026-07-22T09:00:00Z"
}
```

## Audit trail

**Slice 001 audit (T067) — 2026-08-01.** The card was re-read field by field against everything slice 001 shipped
(`commons/models.py`, `merge.py`, `repository.py`, `db.py`, `translit.py`, `planner/nodes/*`). `SiteRecordV1` itself
**stood unchanged**: every field, type, `M1?` flag and the `SourceRef.kind` enum are still exactly what `commons/models.py`
implements (T007 transcribed the card verbatim and nothing since contradicted it), and no field was added, removed or
retyped. What the implementation *did* reveal was six rules the code depends on that the card did not state, all now
written down above — the `und` name key and its merge comparability (FAIL-004), the provenance-inheritance contract for
derived `*-Latn` names (T058/T059), `FieldConflict` candidates carrying geometry as GeoJSON and the winner among them
(T029), the union-not-conflict rule for multi-valued fields, the `site_source` ledger as the thing that makes "no source
is ever lost" true when sources *agree*, and the `user_note` privacy boundary (FR-010) — plus one place where the card
had genuinely gone **stale**: the bundleable allowlist was missing **Apache-2.0** (added to `DATA-LICENSES.md` and
`commons/licenses.py` on 2026-08-01 by ADR-0012), and stated the quarantine as "only if" where the code enforces an
equivalence in both directions.

Deliberately **not** changed: `stories` stays `M1 / ≥1 story`. Slice 001 defers stories to slice 002 (FR-011), but empty
`stories` was always valid, so this is a narrower *fill-set for one slice*, not a schema change
(`specs/001-research-cited-sites/data-model.md` §2 carries the same divergence flag).

**Slice 002 amendment — 2026-08-07.** Three changes, all from `specs/002-plan-compile-offline` Phase-1 gaps G4/G12:
(a) **`Story` gains `observed_at`** (a UTC date) — bundled narration had no staleness key at all, because a `Story` is
not a `SourcedValue` and therefore inherits none; (b) the **derive-don't-read rule for story bundleability** is now
stated in prose above rather than left as an inference from the missing stamp; (c) the "≥1 story" line, which the
paragraph above already softened to a fill-set, is **restated as an aspiration** — it read as a validation rule and in
that reading it contradicted FR-023's correct no-article outcome, which is *no story*. Also: the opening-hours
evaluator is named **`opening-hours-py`** (ADR-0022) wherever prose named `opening_hours.js`; the OSM `opening_hours`
*syntax* is unchanged and the `opening_hours_js` **`SourceKind` enum value is deliberately kept**.
