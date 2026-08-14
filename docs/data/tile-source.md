# Schema card — tile source (`TileSourceV1`)

*Where the offline basemap comes from and how it is described in the bundle. M1 uses a **PMTiles** extract from the
**Protomaps** daily planet build; MapLibre renders it. Authoritative context: `docs/design/tech-design.md` §1.4, §5.3 and
`methods-stack-reference.md` §1–3. The tile-source ADR (Protomaps) lands at DU-05. Never guess this schema; read this card.*

> **A dev-only half of this exists ahead of DU-05.** `web/src/map/basemap.ts` builds the MapLibre style (`pmtiles://`
> protocol + Protomaps layers + vendored Noto glyphs); `scripts/fetch-basemap.sh` writes the extract into
> `web/dev-assets/` — gitignored, served by a dev-only Vite middleware, absent from production builds. It makes the map
> legible while developing and settles nothing this card leaves open. **Still DU-05:** `TileSourceV1` in the manifest,
> sha256/integrity + quarantine, the ATTRIBUTION pipeline, the OPFS transport swap, the ADR. **Known dev gaps:** glyphs
> pruned to U+0000–U+04FF (a Hebrew/Arabic/CJK label renders as nothing); the extract is a fixed bbox, not itinerary-derived.

- **Schema version:** `TileSourceV1`. In M1 this **is** the object embedded at `BundleManifestV1.tiles.pmtiles` — the
  manifest holds a whole `TileSourceV1`, not a summary of one — plus the base MapLibre style it names; this card
  describes both. **The manifest's required subset** of these fields is `path`, `sha256`, `bbox`, `maxzoom` (what the
  travel client must read to open the archive and frame the map); every other M1 field below is still written, because
  provenance (`build_source`, `build_date`, `tile_license`, `attribution`) is what discharges the ODbL obligation and
  drives freshness. Where this card and [`bundle-manifest.md`](./bundle-manifest.md) describe the same object, they are
  the same object. Custom styling / schematic map = M2+ (no customization at M1).
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
  pipeline/styles and the PMTiles spec are BSD; the vendored **glyphs are OFL-1.1** (Noto) and the **sprite sheets MIT**
  (tangrams/icons via basemaps-assets) — two upstreams, two licences, which is why they are two fields and not one.
  **ODbL attribution renders on every map** (control corner) + credits screen. License pointer →
  [`/DATA-LICENSES.md`](../../DATA-LICENSES.md). `bundleable=true` (ODbL is in the allowed set).

## `TileSourceV1` fields

| Field | Type | M1? | Units / notes |
|---|---|---|---|
| `path` | `str` | M1 | **manifest-required subset** — archive path inside the bundle (e.g. `tiles/rhodes.pmtiles`) |
| `format` | `"pmtiles"` | M1 | PMTiles spec **v3** (single-file, cluster-ordered, HTTP-range-readable) |
| `sha256` | `str` | M1 | **manifest-required subset** — integrity hash of the archive bytes; this *is* `BundleManifestV1.tiles.pmtiles.sha256`, not a copy of it |
| `bbox` | `[minLon,minLat,maxLon,maxLat]` | M1 | **manifest-required subset** — **EPSG:4326**; tight itinerary bbox + buffer |
| `minzoom` | `int` | M1 | 0 (keep the full sub-pyramid) |
| `maxzoom` | `int` | M1 | **manifest-required subset** — typically 15 (Protomaps basemap max) |
| `build_source` | `str` | M1 | `protomaps-daily` (or `planetiler` for the self-build fallback) |
| `build_date` | `date` | M1 | Protomaps planet build date (UTC); freshness key |
| `tile_license` | `SPDX str` | M1 | `ODbL-1.0` (data); attribution required |
| `attribution` | `str` | M1 | `© OpenStreetMap contributors` |
| `style` | `{ path, sha256 }` | M1 | base MapLibre style JSON (no customization at M1) |
| `glyphs` | `{ path, license, sha256 }` | M1 | Noto glyphs, **OFL**; `path` is a directory prefix (`glyphs/`) and `sha256` is the **directory digest** below. The directory also holds **`glyphs/OFL.txt`** — the licence text, inside the digest |
| `sprites` | `{ path, license, sha256 }` | M1 | Protomaps sprite sheets, **MIT**; same shape, same directory digest (`sprites/`). Also holds **`sprites/LICENSE.md`**, likewise inside the digest |
| `schema_ver` | `"TileSourceV1"` | M1 | literal |

### Directory digests — `glyphs.sha256` / `sprites.sha256`

`glyphs` and `sprites` name **directory prefixes**, not files, so there is no single byte stream to hash. Their
`sha256` is a SHA-256 over the **RFC 8785 (JCS)** serialization of the directory's `[[path, sha256], …]` listing,
sorted by path — the same canonicalization the manifest seal uses, and for the same reason: the writer is Python and
the verifier is TypeScript. Covering the *paths* as well as the bytes is what makes a renamed, added or removed range
file detectable; a digest over concatenated bytes would not be.
`compiler.tiles.directory_digest` is the one implementation — never hand-roll a second one.

### The licence text in each directory

Each of the two directories carries the licence of the assets in it: **`glyphs/OFL.txt`** (the SIL Open Font License
1.1 with the Noto copyright notice) and **`sprites/LICENSE.md`** (the MIT notice the sheets derive from). Both are
committed under `data/licenses/`, in a tree that mirrors these bundle paths, and are copied in by `compiler/tiles.py`
— **vendored, never fetched**, because the asset host is allowed to 404 a glyph range and the same shrug applied to a
licence would ship fonts in breach. They are ordinary artifacts: hashed individually and **inside their directory's
digest**, so a licence text lost or altered in transit fails the same check a truncated glyph does.

This is not decoration. Generated `ATTRIBUTION.md` states both obligations by name — OFL-1.1's *"`OFL.txt` ships in
the bundle beside the glyphs it covers"* and MIT's *"the copyright notice and the license text travel with the work"*
— and OFL §2 genuinely requires the licence to accompany a redistributed font. Until 2026-08-14 nothing wrote either
file, so a bundle would have asserted compliance in the same artifact that failed it. Provenance for both texts is in
[`/DATA-LICENSES.md`](../../DATA-LICENSES.md).

*Amended 2026-08-14 (slice 002 T035b, ADR-0031): **two changes, and they are not the same change.***

*(a) **`glyphs` gains a required `sha256`.** Until now glyphs were a bundled artifact with no integrity hash, while the
manifest claimed one hash per artifact. A corrupted or truncated glyph range is not a crash: MapLibre draws no glyph
and reports no error, so every label renders as nothing — a blank map, offline, under a manifest that verifies.*

*(b) **The `sprites` ref was created, not merely given a hash.** There was no sprite field at all; the card described
sprites in prose under `glyphs` ("`sprites` likewise") and `compiler/tiles.py` wrote `sprites/*` into the bundle with
**nothing in the manifest pointing at them**. That is not an integrity gap, it is an **FR-021 gap** — everything the
traveller depends on resolves to a manifest path, and the sheet the map draws its icons from did not. It was found
only because implementing (a) required naming the field that would hold the hash.*

*Both hashes are **required, not optional**, because an optional integrity field re-opens the same gap for every
producer that omits it, and the omission is invisible — the property that let this survive design review. Affordable
as a `V1` correction because no bundle has been compiled and stored yet; that window closes at first use. Consequences:
the two example rows in [`bundle-manifest.md`](./bundle-manifest.md) that embed a whole `TileSourceV1` carry both
fields too, and `web/src/bundle/` **records but does not verify** either digest at launch (its parser drops the keys),
so until that is closed the bundle states integrity coverage it does not enforce — recording precedes checking, and
the check is its own task.*

**Each example below is exactly what appears at `BundleManifestV1.tiles.pmtiles`** — paste it there verbatim, do not
reduce it to the four required-subset keys.

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
  "glyphs": { "path": "glyphs/", "license": "OFL-1.1", "sha256": "3d5e…" },
  "sprites": { "path": "sprites/", "license": "MIT", "sha256": "0b71…" }
}

// 2 — larger area, deeper zoom (metro-scale extract)
{
  "path": "tiles/area.pmtiles", "format": "pmtiles", "schema_ver": "TileSourceV1",
  "sha256": "b410…", "bbox": [28.180, 36.400, 28.260, 36.470],
  "minzoom": 0, "maxzoom": 15,
  "build_source": "protomaps-daily", "build_date": "2026-07-24",
  "tile_license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors",
  "style": { "path": "style/base.json", "sha256": "77aa…" },
  // A wider area selects more glyph ranges, so the *directory digest* differs from row 1's
  // even though both vendor the same three fontstacks from the same asset host.
  "glyphs": { "path": "glyphs/", "license": "OFL-1.1", "sha256": "a904…" },
  "sprites": { "path": "sprites/", "license": "MIT", "sha256": "0b71…" }
}

// 3 — Planetiler self-build fallback (custom schema, own POI layer)
{
  "path": "tiles/custom.pmtiles", "format": "pmtiles", "schema_ver": "TileSourceV1",
  "sha256": "cc01…", "bbox": [139.560, 36.120, 139.620, 36.160],
  "minzoom": 0, "maxzoom": 15,
  "build_source": "planetiler", "build_date": "2026-07-20",
  "tile_license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors",
  "style": { "path": "style/base.json", "sha256": "d901…" },
  "glyphs": { "path": "glyphs/", "license": "OFL-1.1", "sha256": "6c18…" },
  "sprites": { "path": "sprites/", "license": "MIT", "sha256": "0b71…" }
}
```
