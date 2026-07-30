# Schema card — tile source (`TileSourceV1`)

*Where the offline basemap comes from and how it is described in the bundle. M1 uses a **PMTiles** extract from the
**Protomaps** daily planet build; MapLibre renders it. Authoritative context: `docs/design/tech-design.md` §1.4, §5.3 and
`methods-stack-reference.md` §1–3. The tile-source ADR (Protomaps) lands at DU-05. Never guess this schema; read this card.*

- **Schema version:** `TileSourceV1`. In M1 this is the `tiles.pmtiles` object inside `BundleManifestV1` plus the base
  MapLibre style; this card describes both. Custom styling / schematic map = M2+ (no customization at M1).
- **Producer / path:** `pmtiles extract https://build.protomaps.com/<YYYYMMDD>.pmtiles out.pmtiles --bbox=…` against the
  hosted daily build — one CLI call, no API key, no fee. **Resolve the build URL at run time; do not hotlink it from
  clients** (Protomaps warns build URLs may change — copy what you need). Keep the **z0→maxzoom** sub-pyramid (partial-zoom
  extracts are inefficient). Fallback: Planetiler self-build from a Geofabrik PBF (also unlocks custom POI layers).
- **CRS:** tile pyramid is **Web Mercator (EPSG:3857)** internally (standard vector-tile grid); **`bbox` is expressed in
  EPSG:4326 (lon,lat)** — `[minLon, minLat, maxLon, maxLat]` — matching every other Siyur geometry. Runtime coordinates
  the app reasons about stay EPSG:4326; MapLibre handles the projection.
- **Timezone:** none on the tiles themselves. `build_date` is the Protomaps planet build date (UTC calendar date); it
  drives freshness (rebuild-on-recompile), not display.
- **Runtime:** MapLibre GL JS **5.19.x** + the `pmtiles` v4 protocol adapter (`pmtiles.Protocol` + `addProtocol`).
  **Do not cache PMTiles range requests in the service worker** (Cache API mishandles 206 partials) — download the whole
  archive once into OPFS and satisfy tile reads as local byte-range reads (stack reference §2–3, ADR-0003).
- **License & provenance:** tile **data is © OpenStreetMap contributors → ODbL** (Produced Work); the Protomaps build
  pipeline/styles and the PMTiles spec are BSD; glyphs/sprites are **OFL** (Noto), vendored from basemaps-assets.
  **ODbL attribution renders on every map** (control corner) + credits screen. License pointer →
  [`/DATA-LICENSES.md`](../../DATA-LICENSES.md). `bundleable=true` (ODbL is in the allowed set).

## `TileSourceV1` fields

| Field | Type | M1? | Units / notes |
|---|---|---|---|
| `path` | `str` | M1 | archive path inside the bundle (e.g. `tiles/rhodes.pmtiles`) |
| `format` | `"pmtiles"` | M1 | PMTiles spec **v3** (single-file, cluster-ordered, HTTP-range-readable) |
| `sha256` | `str` | M1 | integrity hash (also referenced from `BundleManifestV1.tiles`) |
| `bbox` | `[minLon,minLat,maxLon,maxLat]` | M1 | **EPSG:4326**; tight itinerary bbox + buffer |
| `minzoom` | `int` | M1 | 0 (keep the full sub-pyramid) |
| `maxzoom` | `int` | M1 | typically 15 (Protomaps basemap max) |
| `build_source` | `str` | M1 | `protomaps-daily` (or `planetiler` for the self-build fallback) |
| `build_date` | `date` | M1 | Protomaps planet build date (UTC); freshness key |
| `tile_license` | `SPDX str` | M1 | `ODbL-1.0` (data); attribution required |
| `attribution` | `str` | M1 | `© OpenStreetMap contributors` |
| `style` | `{ path, sha256 }` | M1 | base MapLibre style JSON (no customization at M1) |
| `glyphs` | `{ path, license }` | M1 | Noto glyphs, **OFL**; `sprites` likewise |
| `schema_ver` | `"TileSourceV1"` | M1 | literal |

## Example rows

```jsonc
// 1 — M1 PMTiles extract for a compact old town (Rhodes)
{
  "path": "tiles/rhodes.pmtiles", "format": "pmtiles", "schema_ver": "TileSourceV1",
  "sha256": "9f2c…", "bbox": [28.216, 36.440, 28.232, 36.451],
  "minzoom": 0, "maxzoom": 15,
  "build_source": "protomaps-daily", "build_date": "2026-07-24",
  "tile_license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors",
  "style": { "path": "style/base.json", "sha256": "77aa…" },
  "glyphs": { "path": "glyphs/", "license": "OFL-1.1" }
}

// 2 — larger area, deeper zoom (metro-scale extract)
{
  "path": "tiles/area.pmtiles", "format": "pmtiles", "schema_ver": "TileSourceV1",
  "sha256": "b410…", "bbox": [28.180, 36.400, 28.260, 36.470],
  "minzoom": 0, "maxzoom": 15,
  "build_source": "protomaps-daily", "build_date": "2026-07-24",
  "tile_license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors",
  "style": { "path": "style/base.json", "sha256": "77aa…" },
  "glyphs": { "path": "glyphs/", "license": "OFL-1.1" }
}

// 3 — Planetiler self-build fallback (custom schema, own POI layer)
{
  "path": "tiles/custom.pmtiles", "format": "pmtiles", "schema_ver": "TileSourceV1",
  "sha256": "cc01…", "bbox": [139.560, 36.120, 139.620, 36.160],
  "minzoom": 0, "maxzoom": 15,
  "build_source": "planetiler", "build_date": "2026-07-20",
  "tile_license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors",
  "style": { "path": "style/base.json", "sha256": "d901…" },
  "glyphs": { "path": "glyphs/", "license": "OFL-1.1" }
}
```
