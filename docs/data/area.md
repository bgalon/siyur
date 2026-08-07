# Schema card — resolved area (`ResolvedArea`)

*The area a research pass is scoped to: a user's delimitation (a free-text name, a bbox, or a drawn ring) resolved to
**exactly one stamped EPSG:4326 polygon**. Everything downstream is defined over that polygon — `research()` fans the
adapters out over it, and the `ST_Within` coverage query behind `POST /areas` runs against it. Authoritative
implementation: `planner/nodes/resolve_area.py` (slice 001, T032); contract:
`specs/001-research-cited-sites/contracts/areas.md`. **Never guess this schema; read this card.** Constitution
Article V (provenance is mechanical) applies here exactly as it does to a site: a polygon without a `SourceRef` is not
a resolved area.*

- **Schema version:** `ResolvedArea` — an **in-memory dataclass**, not (yet) a persisted `*V1` model. Slice 001
  resolves an area per request and does not store it; see "Persistence" below, which is **provisional**.
- **CRS:** **EPSG:4326 (lon, lat)** — lon first, always. Same rule, same validators (`commons/geo.py`) as every other
  Siyur geometry. Coordinates are read from geodata or from the user's own input; **the LLM never emits, corrects or
  computes an area geometry** (FR-005, `planner/AGENTS.md`).
- **Genericity:** no place name, bbox, country or language appears anywhere in the resolver (FR-001 / SC-005). Every
  code path is driven by the request. The demo area is a *test fixture*, never a default.
- **Precedence:** explicit geometry first — **`polygon` → `bbox` → `name`**. A user who drew a ring means that ring; a
  name is the only input that needs guessing. Nothing is supplied ⇒ `422`.
- **Sources:** **Overture `divisions` / `division_area` is authoritative; Nominatim is a disambiguation fallback
  only** — consulted only when divisions returns nothing, **one bounded request, never in a loop** (the OSMF usage
  policy in [`/DATA-LICENSES.md`](../../DATA-LICENSES.md): ≤ 1 req/s, honest `User-Agent`, no bulk use). A DuckDB
  failure against the authoritative source is **not** swallowed — an unreachable source must surface as an error, not
  as a false "no such area"; the *fallback* being unavailable returns nothing rather than raising.
- **License & provenance:** a resolved polygon always carries a `SourceRef`, and the stamp is **read, not assumed** —
  see the provenance table below. License pointer → [`/DATA-LICENSES.md`](../../DATA-LICENSES.md).
- **Timezone: the area is where the local frame comes from.** The area carries **`timezone`** (IANA, e.g.
  `Europe/Athens`) and **`country_code`** (ISO 3166-1 alpha-2), both **derived deterministically from the polygon at
  resolve time** — see "The local frame" below. They are not on the shape as decoration: every planned time downstream
  is *area-local wall clock* (`ItineraryV1.date` + `Stop.planned_start` + `Timeline.entries[].start`), and the
  opening-hours evaluator needs the country to resolve public holidays. **Without these two values the frame every
  planned time is expressed in is unrecorded and opening-hours feasibility is structurally uncomputable** — this was
  the highest-risk gap slice 002's design surfaced (`specs/002-plan-compile-offline/data-model.md` G3, ADR-0025).
  A resolved area still carries no `observed_at`: it is resolved per request, not cached. (Staleness lives on the
  *site* records inside it — `docs/data/poi-site.md`.)

## `ResolvedArea` fields

| Field | Type | M1? | Units / notes |
|---|---|---|---|
| `polygon` | shapely `Polygon \| MultiPolygon` | M1 | EPSG:4326 (lon, lat); validated — see "The polygon contract" |
| `source` | `SourceRef` | M1 | where the polygon came from; the same `SourceRef` the commons uses (`docs/data/poi-site.md`) |
| `timezone` | `str` (IANA tz id) | M1 | e.g. `Europe/Athens`. **Derived from `polygon`, never asked and never guessed by the model.** The frame every area-local wall-clock time downstream is read in (`itinerary.md`) |
| `country_code` | `str` (ISO 3166-1 alpha-2) | M1 | e.g. `GR`. **Derived from `polygon`**, same rule. Passed to `opening-hours-py` for **public-holiday** resolution — omit it and holidays misfire silently (FAIL-catalog) |
| `candidates` | `[AreaCandidate]` | M1 | everything considered when a **name** was resolved, winner included, so a caller can still offer "did you mean…". **Empty for a user-supplied geometry** — nothing was guessed |

**`AreaRequest`** — the user's delimitation; `POST /areas` maps onto it one-to-one. Exactly one is used, by the
precedence above; all three may be absent only to produce a `422`.

| Field | Type | Units / notes |
|---|---|---|
| `name` | `str \| null` | free text, any script. Whitespace-collapsed and sent verbatim to the source; matched case-, whitespace- and Unicode-composition-insensitively. **No transliteration, no language guess** |
| `bbox` | `[minLon, minLat, maxLon, maxLat] \| null` | EPSG:4326, **lon first**. Each slot validated against **its own** axis, and both must increase, so a transposed bbox fails instead of quietly delimiting the wrong hemisphere |
| `polygon` | GeoJSON `Polygon \| MultiPolygon \| null` | EPSG:4326; every ring position axis-checked |

**`AreaCandidate`** — one plausible reading of a name.

| Field | Type | Units / notes |
|---|---|---|
| `name` | `str` | the source's own primary name for this candidate |
| `polygon` | shapely `Polygon \| MultiPolygon` | validated, EPSG:4326 |
| `source` | `SourceRef` | stamped per candidate, not per query |
| `confidence` | `float [0..1]` | **name-match strength only** — 1.0 exact · 0.6 prefix · 0.45 substring. It says nothing about geometric quality |

**Resolution outcome.** Exactly one candidate at **strong** strength (≥ 0.8) resolves outright, even if weaker
readings also matched. Otherwise the **plausible** set (≥ 0.4) decides: exactly one resolves; several are a *question
for the user*, never a coin flip → `404` **with** `candidates`; none ⇒ `404`, candidates possibly empty. This is the
one place a name becomes a polygon, and it refuses rather than guesses.

## The polygon contract

A resolved polygon is guaranteed to satisfy **all** of these — anything failing them is rejected as `422`
(user input) or dropped from the candidate list (source data), never returned:

1. **Areal**: `geom_type ∈ {Polygon, MultiPolygon}`. A point or a line is not an area. *(shapely 2.x `.geom_type`,
   never 1.x `.type` — AGENTS.md geo pins.)*
2. **Non-empty** and **valid**: no empty geometry, no self-intersecting or malformed ring.
3. **Non-degenerate**: `area > 0` — a zero-area sliver encloses nothing.
4. **In-range EPSG:4326**: bounds axis-checked, lon ∈ [-180, 180] against lon and lat ∈ [-90, 90] against lat.

Nominatim answers points and lines for many queries; those results are **dropped**, not coerced — an area query wants
areas.

## The local frame — deriving `timezone` and `country_code` (ADR-0025)

**Both are computed from the polygon at resolve time, by a deterministic spatial lookup, and then stored.** Three
things this is *not*: it is **not a question put to the user** (they delimited a place, not a clock); it is **not a
value the LLM emits** (it is spatial arithmetic, and the LLM never does spatial arithmetic — AGENTS.md); and it is
**not re-derived on every read** (it is recorded once with the area, so every consumer of a plan reads the same frame
the planner used, forever).

Why it is load-bearing rather than nice-to-have: `Stop.planned_start` is `10:00` with no offset, and `10:00` is not an
instant until something says *which* local clock and *which* calendar day. `ItineraryV1.date` supplies the day; this
area supplies the clock. `opening-hours-py` then needs `country_code` on top, because `PH` in an OSM `opening_hours`
expression resolves against a **country's** holiday calendar; without it public holidays evaluate wrongly and silently
— which is precisely the failure SC-002 exists to prevent.

**The rule is largest intersecting area, not a sampled point** (ADR-0025 ruling 2, as amended a second time — see the
note below). **Straddling is normal, and the rule must not vary between runs.** SC-009 says this works for *any* area,
so both degenerate cases are specified rather than left to whichever library is called first:

1. **A polygon straddling a timezone boundary** (a bay, a valley, a drawn ring across a line). Resolve to the timezone
   whose zone polygon has the **largest area of intersection** with the resolved polygon; ties break on the
   **lexicographically smallest IANA id**. One area, one clock.
2. **A polygon straddling a country boundary** (a border town, a metro spanning two states). Same rule against country
   polygons: **largest intersecting area**, ties on the lexicographically smallest ISO 3166-1 alpha-2 code.

Both are pure functions of the polygon and the pinned boundary dataset, so the same delimitation always resolves the
same way, in CI and on a server, forever. **What was picked is recorded** — the stored `timezone` / `country_code`
*are* the record, and later reads never re-derive, so a plan is always readable in the frame it was planned in even
after the rule or the boundary data changes. A polygon intersecting **no** zone or **no** country (open ocean) is a
**hard failure at resolve time** — never a silent `UTC` fallback, never a defaulted country; a plan whose frame is a
guess is worse than a plan that was refused.

*Permitted implementation shortcut:* where the polygon intersects exactly one zone (the overwhelmingly common case for
a walkable day), a single point-in-polygon lookup is equivalent and may be used. If a point lookup is used, take
`polygon.representative_point()` and **never the centroid** — a centroid can fall *outside* a concave or multi-part
polygon, the ordinary case for a coastal old town or an island municipality. The shortcut must agree with the rule
above wherever both are defined; the rule is what is normative.

> **Why this is stated twice.** An earlier amendment replaced the largest-intersection rule with a bare
> `representative_point()` lookup, on the reasoning that a centroid can fall outside its polygon. That reasoning is
> sound but applies to *centroids*, which this rule never used — so the amendment fixed a problem that did not exist
> here and, in doing so, made a straddling polygon resolve to whichever zone happened to contain one sampled point
> rather than the zone it mostly lies in. The largest-intersection rule is restored as normative and the point lookup
> demoted to an optimization. Recorded rather than quietly reverted, because the original rule was right and the
> correction was not.

The boundary datasets and the library that queries them are pinned at implementation under ADR-0007's resolve-then-pin
discipline (`tasks.md` T008). Two non-negotiables on that choice: it must work **with no network** — an area resolve
that phones a timezone API is not reproducible in CI and not available to a compile — and it must be
**licence-registered in** [`/DATA-LICENSES.md`](../../DATA-LICENSES.md) before it ships (a timezone-boundary dataset
built from OSM carries ODbL and its attribution obligation with it).

## Overture divisions — the columns actually read

From `s3://overturemaps-us-west-2/release/{release}/theme=divisions/type=division_area/*` (release pinned to the same
default the places adapter uses, `commons/sources/overture.DEFAULT_RELEASE`), read with DuckDB `spatial` (+ `httpfs`
for the hosted release), bounded by a row `limit` (default 20). **This is the whole column set the resolver depends
on — nothing else is read:**

| Column | Type | Used for |
|---|---|---|
| `id` | `str` | `SourceRef.id` — the division's stable id. A row with no id is skipped |
| `names.primary` | `str` | the candidate's display name and the primary match target |
| `names.common` | `map(str → str)` | alternate/localised names; read as `map_values(...)`, i.e. **the values only — the language keys are not used** by matching |
| `sources` | `list(struct)` | the license stamp. Only `sources[].license` and `sources[].property` are read (see below) |
| `geometry` | geometry | the polygon, fetched as WKB (`ST_AsWKB`) and parsed by shapely |

**Matching** is a coarse symmetric-containment prefilter pushed into SQL (a division name may contain the query, or the
query may contain it), then **re-scored in Python** on every returned row. Both sides are folded **in SQL** with
`nfc_normalize(lower(...))` and the needle is passed raw, because DuckDB's `lower` and Python's `str.casefold` are not
the same function — casefold decomposes some precomposed Greek vowels, so a Python-folded needle silently matched
nothing. Script-agnostic throughout: no transliteration, no language assumption.

**License is read per record, never typed from memory.** The divisions theme is ODbL in `DATA-LICENSES.md`, but the
stamp comes from the row's own `sources[]`: a record-level entry (`property` empty/absent) wins, else the first stated
license. **A row that states no license is dropped**, never optimistically stamped — the same discipline
`commons/sources/overture.py` applies to places (ADR-0009), and it matters for the same reason: one theme can mix
licenses across records.

## Provenance — the three ways an area gets stamped

| Origin | `source.kind` | `source.id` | `license` | `attribution` |
|---|---|---|---|---|
| Overture division | `overture` | the division `id` | **whatever the row states** (the divisions theme is ODbL-1.0 per the registry, but it is read per record) | `© OpenStreetMap contributors, Overture Maps Foundation` — rendered **iff** the stated license requires it |
| Nominatim (OSM) fallback | `osm` | `<osm_type>/<osm_id>` (e.g. `relation/12345`), with `url` = `https://www.openstreetmap.org/<that>` | `ODbL-1.0` | `© OpenStreetMap contributors` |
| User-drawn ring or bbox | `user` | `user:polygon` / `user:bbox` | `user-owned` | — |

- **Attribution is a function of the license, not of the source.** A rendered attribution string is attached only when
  the license's terms require one — **ODbL-1.0, CC-BY-4.0, CC-BY-SA-4.0** — and the license id is canonicalised first
  (`commons.licenses.normalize_license`), so a registry spelling and an SPDX spelling behave identically.
- **A user-delimited area is the user's own data, not commons data.** `kind="user"` + `license="user-owned"` ⇒
  **`bundleable=false`** by the quarantine rule (`docs/data/poi-site.md`, `DATA-LICENSES.md`), and — per FR-010 —
  `source.kind="user"` values are refused at the commons write boundary altogether. Resolving an area from a drawn ring
  is fine; *publishing that ring into the shared commons* is not.
- ODbL attribution renders on **every** map regardless (AGENTS.md), so an ODbL-stamped area is never a silent credit.

## Persistence — provisional

**Nothing here is shipped yet; treat this section as a sketch, not a contract.** Slice 001 resolves an area per
request and stores no area row: `planner/pipeline.py` accepts an `area_id` and echoes it in the `done` frame, but
never creates one, and `alembic/versions/0001_commons_spine.py` has no `area` table. `contracts/areas.md` does
promise an `area_id` in the `POST /areas` response, so a persisted shape is expected to land.

When it does, **its schema belongs in this card**, and the questions it must answer are:

- the geometry column type — a site is `geometry(Point,4326)`; an area is presumably `geometry(Geometry,4326)` or
  `geometry(MultiPolygon,4326)`, GiST-indexed, since `ST_Within(site.geom, :area_polygon)` is the coverage query;
- whether the `SourceRef` is stored inline (as `site.fields` does) or in a provenance row;
- whether an area is **per-user or shared** — the standing split is "research commons is global, personal data is
  private" (PRD), and a name-resolved administrative division and a user-drawn ring plainly sit on opposite sides of
  that line. **Unresolved — this card does not assert an answer**;
- whether resolutions are cached/reused, which would give the shape an `observed_at` it does not have today.

**Two corrections to the sketch above, as of slice 002.** First, the `area` table **does now exist** — migrations
`0002_area` (`id`, `geom`, `name`, `created_by`, `created_at`) and `0004_area_researched_at` shipped after this section
was written; the *open* questions it lists (per-user vs shared, caching, `observed_at`) are still open, but "there is
no table" is no longer one of them.

Second, **`timezone` and `country_code` are columns, not derivations at read time.** The `area` table gains
`timezone text NOT NULL` and `country_code text NOT NULL` (ADR-0025), because a plan must be readable in the frame it
was planned in even if the boundary data or the derivation rule later changes. That is an **Alembic migration** —
hand-written like `0002_area`, backfilling existing rows from their stored `geom` by the same derivation before the
`NOT NULL` is applied, and **`ask`-gated: Ben approves it before it runs** (`CLAUDE.md`, "Always ask Ben first").
Nothing else in this section is settled by it.

## Example values

```jsonc
// 1 — name resolved against Overture divisions (authoritative).
//     `timezone`/`country_code` are derived from the polygon, not from the name.
{
  "polygon": { "type": "Polygon", "coordinates": [ [ [28.216, 36.440], /* … */ ] ] },
  "source": { "kind": "overture", "id": "08f2a4…division", "url": null,
    "license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors, Overture Maps Foundation" },
  "timezone": "Europe/Athens", "country_code": "GR",
  "candidates": [ { "name": "…", "confidence": 1.0, "source": { /* as above */ } } ]
}

// 2 — divisions returned nothing; the Nominatim fallback disambiguated (one bounded GET)
{
  "polygon": { "type": "MultiPolygon", "coordinates": [ /* … */ ] },
  "source": { "kind": "osm", "id": "relation/12345",
    "url": "https://www.openstreetmap.org/relation/12345",
    "license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors" },
  "timezone": "Europe/Athens", "country_code": "GR",
  "candidates": [ { "name": "…", "confidence": 0.6, "source": { /* as above */ } } ]
}

// 3 — the user drew the ring: their own data, no attribution, NOT bundleable, candidates empty.
//     The frame is still DERIVED — a drawn ring is delimitation, never a claim about the clock.
{
  "polygon": { "type": "Polygon", "coordinates": [ /* exactly what they drew */ ] },
  "source": { "kind": "user", "id": "user:polygon", "url": null,
    "license": "user-owned", "attribution": null },
  "timezone": "Europe/Athens", "country_code": "GR",
  "candidates": []
}

// 4 — a drawn ring straddling the Swiss–German line: largest intersecting area decides.
//     Note this ring genuinely straddles TWO IANA zones — Europe/Zurich and Europe/Berlin.
//     They share a UTC offset, which is exactly why offset is not the thing being resolved:
//     the field holds an IANA id, and the two zones' holiday calendars differ. Here ~70% of
//     the ring's area lies on the Swiss side, so CH / Europe/Zurich wins outright and the
//     lexicographic tie-break never fires. One area, one clock, one holiday calendar —
//     never a prompt, never a per-run coin flip.
{
  "polygon": { "type": "Polygon", "coordinates": [ /* a ring across the Swiss–German line */ ] },
  "source": { "kind": "user", "id": "user:polygon", "url": null,
    "license": "user-owned", "attribution": null },
  "timezone": "Europe/Zurich", "country_code": "CH",
  "candidates": []
}
```

---

*Written 2026-08-01 (T067, slice 001) from `planner/nodes/resolve_area.py` — the divisions column set, the licence
handling and the confidence bands are transcribed from the code, not from the live Overture theme. The T032 session
flagged the absence of this card: the divisions columns were being read straight off the theme shape, which is exactly
the "never guess a schema" hazard `AGENTS.md` warns about. Sections marked **provisional** are the ones the code does
not yet answer.*

*Amended 2026-08-07 (slice 002, ADR-0025): `timezone` + `country_code` added as M1 fields, derived deterministically
from the polygon at resolve time, with the straddle rules and the persistence consequence stated above. `ResolvedArea`
does not implement them yet — this card leads the code here, deliberately, because slice 002's planner cannot express
an area-local time without them.*
