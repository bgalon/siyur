# Schema card — bundle manifest (`BundleManifestV1`)

*The frozen offline artifact. Compile freezes `SiteRecordV1` + `ItineraryV1` into a hashed bundle in GCS, downloaded
whole to OPFS. This manifest is the **airplane-mode contract**: everything the travel UI reads resolves to a path here.
Authoritative source: `docs/design/tech-design.md` §1.4, §5.3. Never guess this schema; read this card.*

- **Schema version:** `BundleManifestV1` (`schema_ver` literal). M1 populates `tiles`/`routing`/`content`/`attribution`/`withheld`/`integrity`;
  `schematic` is M2+.
- **Storage:** written to **GCS**, downloaded as a whole archive to **OPFS** with `navigator.storage.persist()`
  (ADR-0002: served over HTTP in M1, OPFS transport swap at DU-06). The PMTiles archive is **runtime-fetched into OPFS**,
  never `import`ed and never in `public/` (ADR-0003 Vite config invariants).
- **CRS:** all bundled geometry is **EPSG:4326 (lon, lat)** — tiles (`tiles.pmtiles.bbox`), route legs, walk graph, site points.
- **Timezone:** `created_at` is `timestamptz` (UTC). Bundled itinerary/timeline times are **area-local wall-clock** (frozen as
  planned); the traveller's device clock is not required to match.
- **License & provenance — the mechanical gate (Constitution Article V):** the compile **quarantine filter drops every
  `bundleable=false` value** before freezing `content` (the §1.0 invariant applied), then regenerates `attribution` →
  `ATTRIBUTION.md` (ODbL + CC-BY-SA credits). License registry → [`/DATA-LICENSES.md`](../../DATA-LICENSES.md).
  **What the filter drops, the manifest records:** every removal is listed in `withheld` (`site_id` + dotted `field` +
  `reason`), which is what lets the travel UI render a withheld value as "needs connectivity" rather than as a blank or
  an error.
  **Airplane-mode invariant (release gate):** everything the travel UI reads resolves to a manifest path; **no
  `bundleable=false` value is present**; review links (M2) render as "needs connectivity," never errors. Tested by
  `test_structural.py::test_no_unbundleable_in_bundle` and the DU-06 airplane-mode e2e (zero network requests).

## `BundleManifestV1` fields

| Field | Type | M1? | Units / notes |
|---|---|---|---|
| `bundle_id` | `str` | M1 | stable id of this compiled bundle |
| `itinerary_id` | `UUID` | M1 | the `ItineraryV1` this bundle freezes |
| `created_at` | `timestamptz` | M1 | UTC |
| `size_bytes` | `int` | M1 | total bundle size (reported before download; ≤200 MB budget target) |
| `schema_ver` | `"BundleManifestV1"` | M1 | literal |
| `tiles` | `{ pmtiles: TileSourceV1 }` | M1 | **the full `TileSourceV1` object** ([`tile-source.md`](./tile-source.md)), embedded — not a 4-field summary of it. Required subset here: `path`, `sha256`, `bbox`, `maxzoom`; `bbox` = tight itinerary bbox + buffer, EPSG:4326 |
| `routing` | `{ walk_graph, walk_graph_sha256, legs, legs_sha256 }` | M1 | pruned walk graph (geojson-path-finder) + precomputed legs (incl. B/C branches at M2), **each hashed on its own**; see [`route-leg.md`](./route-leg.md) |
| `content` | `{ sites, sites_sha256, narrations, narrations_sha256, itinerary, itinerary_sha256 }` | M1 | **only `bundleable=true` values** (post-quarantine): `SiteRecordV1` subset + CC-BY-SA narrations + the **frozen `ItineraryV1` JSON** (`itinerary`) the travel timeline renders from; each artifact hashed on its own |
| `attribution` | `{ path, sha256 }` | M1 | `ATTRIBUTION.md`, regenerated **per bundle** (ODbL + per-article CC-BY-SA credits); hashed like every other artifact — it is legally obligated content, not a decoration |
| `withheld` | `[{ site_id: UUID, field: str, reason: str }]` | M1 | what the **quarantine filter removed** and why — dotted `field` path (`reviews`, `notes[2]`, `names.el`), `reason` from `licenses.quarantine_reason`. Empty list = nothing withheld. Renders as "needs connectivity" in travel (FR-021) |
| `integrity` | `{ manifest_sha256 }` | M1 | launch-time check (iOS storage-eviction guard); canonicalization pinned below |
| `schematic` | `{ style_json, sha256 } \| null` | M2+ | illustrated-map render |

**Integrity discipline — one hash per artifact, no shared hashes.** Each artifact is **SHA-256**'d individually:
`tiles.pmtiles.sha256`, `routing.walk_graph_sha256`, `routing.legs_sha256`, `content.sites_sha256`,
`content.narrations_sha256`, `content.itinerary_sha256`, `attribution.sha256`. Hashes are **lowercase hex of the raw
artifact bytes**. A hash that spans two files cannot say *which* one corrupted, so no group hash exists: every path in
this manifest has exactly one hash beside it. The whole manifest is then hashed into `integrity.manifest_sha256` and
checked at launch — the guard against silent OPFS eviction/corruption on iOS.

**The manifest hash is canonicalized — this is a rule, not an implementation note.** `integrity.manifest_sha256` is the
SHA-256 of the manifest serialized as **canonical JSON**: UTF-8 encoded, object keys sorted **lexicographically at every
level**, no insignificant whitespace (`,` and `:` separators, no indentation), and the **`integrity` key omitted**
entirely (a hash cannot cover itself). The digest is lowercase hex. Two implementations that canonicalize differently
produce different digests and every bundle fails its own launch check, so this is fixed here rather than left to the
writer: **sorted keys, no whitespace, `integrity` removed — not `integrity` set to `null`, not `integrity` set to an
empty string.**

The compile order that produces this manifest (tech-design §5.3): `pmtiles extract` → base
MapLibre style → Valhalla per-area build → legs + pruned graph → **quarantine filter** (recording `withheld`) → freeze
`content` (sites, narrations, **itinerary**) → assemble `ATTRIBUTION.md` → SHA-256 each artifact → write manifest →
upload to GCS → client downloads whole archive to OPFS.

## Example rows

```jsonc
// 1 — M1 bundle manifest (Rhodes old-town day). `tiles.pmtiles` is a full TileSourceV1.
{
  "bundle_id": "bnd_01J…", "itinerary_id": "7be2…-uuid",
  "created_at": "2026-07-25T12:30:00Z", "size_bytes": 5242880,
  "schema_ver": "BundleManifestV1",
  "tiles": { "pmtiles": {
    "path": "tiles/rhodes.pmtiles", "format": "pmtiles", "schema_ver": "TileSourceV1",
    "sha256": "9f2c…", "bbox": [28.216, 36.440, 28.232, 36.451],
    "minzoom": 0, "maxzoom": 15,
    "build_source": "protomaps-daily", "build_date": "2026-07-24",
    "tile_license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors",
    "style": { "path": "style/base.json", "sha256": "77aa…" },
    "glyphs": { "path": "glyphs/", "license": "OFL-1.1" } } },
  "routing": { "walk_graph": "routing/walk_graph.geojson", "walk_graph_sha256": "1a7b…",
    "legs": "routing/legs.json", "legs_sha256": "5d92…" },
  "content": { "sites": "content/sites.json", "sites_sha256": "c40e…",
    "narrations": "content/narrations.json", "narrations_sha256": "8ba3…",
    "itinerary": "content/itinerary.json", "itinerary_sha256": "2f70…" },
  "attribution": { "path": "ATTRIBUTION.md", "sha256": "6cd1…" },
  "withheld": [
    { "site_id": "c9d1…-uuid", "field": "reviews",
      "reason": "source.kind='review_provider' is never bundleable" }
  ],
  "integrity": { "manifest_sha256": "e11d…" }
}

// 2 — post-quarantine content head (reviews/open_web stripped; only bundleable=true survives)
{
  "content_sites_head": [
    { "id": "6f1c…-uuid", "names": { "en": "Palace of the Grand Master" },
      "location": [28.2247, 36.4443], "license": "CDLA-Permissive-2.0" }
    /* note: `reviews` field absent — bundleable=false, dropped by the quarantine filter
       and recorded in the manifest's `withheld` list (example 1) so travel can say
       "needs connectivity" instead of showing a blank */
  ]
}

// 3 — M2+ manifest adding the schematic style artifact
{
  "bundle_id": "bnd_01K…", "itinerary_id": "ee31…-uuid",
  "created_at": "2026-09-01T08:00:00Z", "size_bytes": 8388608,
  "schema_ver": "BundleManifestV1",
  "tiles": { "pmtiles": {
    "path": "tiles/area.pmtiles", "format": "pmtiles", "schema_ver": "TileSourceV1",
    "sha256": "…", "bbox": [28.20, 36.43, 28.25, 36.46], "minzoom": 0, "maxzoom": 15,
    "build_source": "protomaps-daily", "build_date": "2026-08-28",
    "tile_license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors",
    "style": { "path": "style/base.json", "sha256": "…" },
    "glyphs": { "path": "glyphs/", "license": "OFL-1.1" } } },
  "routing": { "walk_graph": "routing/walk_graph.geojson", "walk_graph_sha256": "…",
    "legs": "routing/legs.json", "legs_sha256": "…" },
  "content": { "sites": "content/sites.json", "sites_sha256": "…",
    "narrations": "content/narrations.json", "narrations_sha256": "…",
    "itinerary": "content/itinerary.json", "itinerary_sha256": "…" },
  "attribution": { "path": "ATTRIBUTION.md", "sha256": "…" },
  "withheld": [],
  "integrity": { "manifest_sha256": "…" },
  "schematic": { "style_json": "schematic/style.json", "sha256": "…" }
}
```
