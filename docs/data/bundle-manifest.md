# Schema card — bundle manifest (`BundleManifestV1`)

*The frozen offline artifact. Compile freezes `SiteRecordV1` + `ItineraryV1` into a hashed bundle in GCS, downloaded
whole to OPFS. This manifest is the **airplane-mode contract**: everything the travel UI reads resolves to a path here.
Authoritative source: `docs/design/tech-design.md` §1.4, §5.3. Never guess this schema; read this card.*

- **Schema version:** `BundleManifestV1` (`schema_ver` literal). M1 populates `tiles`/`routing`/`content`/`attribution`/`integrity`;
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
| `tiles` | `{ pmtiles: {path, sha256, bbox, maxzoom} }` | M1 | PMTiles extract; `bbox` = tight itinerary bbox + buffer, EPSG:4326; see [`tile-source.md`](./tile-source.md) |
| `routing` | `{ walk_graph, legs, sha256 }` | M1 | pruned walk graph (geojson-path-finder) + precomputed legs (incl. B/C branches at M2); see [`route-leg.md`](./route-leg.md) |
| `content` | `{ sites, narrations, sha256 }` | M1 | **only `bundleable=true` values** (post-quarantine): `SiteRecordV1` subset + CC-BY-SA narrations |
| `attribution` | `{ path }` | M1 | `ATTRIBUTION.md`, regenerated **per bundle** (ODbL + per-article CC-BY-SA credits) |
| `integrity` | `{ manifest_sha256 }` | M1 | launch-time check (iOS storage-eviction guard) |
| `schematic` | `{ style_json, sha256 } \| null` | M2+ | illustrated-map render |

**Integrity discipline:** every artifact is **SHA-256**'d (`tiles.pmtiles.sha256`, `routing.sha256`, `content.sha256`),
then the whole manifest is hashed (`integrity.manifest_sha256`) and checked at launch — the guard against silent OPFS
eviction/corruption on iOS. The compile order that produces this manifest (tech-design §5.3): `pmtiles extract` → base
MapLibre style → Valhalla per-area build → legs + pruned graph → **quarantine filter** → freeze `content` → assemble
`ATTRIBUTION.md` → SHA-256 each artifact → write manifest → upload to GCS → client downloads whole archive to OPFS.

## Example rows

```jsonc
// 1 — M1 bundle manifest (Rhodes old-town day)
{
  "bundle_id": "bnd_01J…", "itinerary_id": "7be2…-uuid",
  "created_at": "2026-07-25T12:30:00Z", "size_bytes": 5242880,
  "schema_ver": "BundleManifestV1",
  "tiles": { "pmtiles": { "path": "tiles/rhodes.pmtiles",
    "sha256": "9f2c…", "bbox": [28.216, 36.440, 28.232, 36.451], "maxzoom": 15 } },
  "routing": { "walk_graph": "routing/walk_graph.geojson", "legs": "routing/legs.json",
    "sha256": "1a7b…" },
  "content": { "sites": "content/sites.json", "narrations": "content/narrations.json",
    "sha256": "c40e…" },
  "attribution": { "path": "ATTRIBUTION.md" },
  "integrity": { "manifest_sha256": "e11d…" }
}

// 2 — post-quarantine content head (reviews/open_web stripped; only bundleable=true survives)
{
  "content_sites_head": [
    { "id": "6f1c…-uuid", "names": { "en": "Palace of the Grand Master" },
      "location": [28.2247, 36.4443], "license": "CDLA-Permissive-2.0" }
    /* note: `reviews` field absent — bundleable=false, dropped by the quarantine filter */
  ]
}

// 3 — M2+ manifest adding the schematic style artifact
{
  "bundle_id": "bnd_01K…", "itinerary_id": "ee31…-uuid",
  "created_at": "2026-09-01T08:00:00Z", "size_bytes": 8388608,
  "schema_ver": "BundleManifestV1",
  "tiles": { "pmtiles": { "path": "tiles/area.pmtiles", "sha256": "…",
    "bbox": [28.20, 36.43, 28.25, 36.46], "maxzoom": 15 } },
  "routing": { "walk_graph": "routing/walk_graph.geojson", "legs": "routing/legs.json", "sha256": "…" },
  "content": { "sites": "content/sites.json", "narrations": "content/narrations.json", "sha256": "…" },
  "attribution": { "path": "ATTRIBUTION.md" },
  "integrity": { "manifest_sha256": "…" },
  "schematic": { "style_json": "schematic/style.json", "sha256": "…" }
}
```
