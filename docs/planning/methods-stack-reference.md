# Siyur Stack Reference — State of the Art, mid-2026

Research snapshot: 2026-07-24. Scope: every component of the Siyur tour-day map studio
(plan online with an LLM agent → compile a self-contained offline bundle → travel with zero
connectivity, zero LLM). Open-source-first; hosted APIs where they genuinely serve; every
license obligation flagged. Versions pinned as verified on the research date.

## Index

1. [Offline vector tiles, any-city](#1-offline-vector-tiles-any-city) — PMTiles, Protomaps builds, Planetiler, MapLibre, glyphs/sprites
2. [Offline PWA for maps](#2-offline-pwa-for-maps) — range-request caching, OPFS/IndexedDB, iOS Safari 2026, prior art
3. [LLM-designed map styles](#3-llm-designed-map-styles) — style JSON generation, Maputnik, base styles, validation guardrails
4. [Grounded any-city curation](#4-grounded-any-city-curation) — Overture places/divisions, Overpass, Wikivoyage, Wikipedia/Wikidata, opening hours
5. [Any-city walking routing](#5-any-city-walking-routing) — ORS, OSRM, Valhalla, BRouter, GraphHopper, in-bundle offline routing
6. [Planning-time city research](#6-planning-time-city-research-from-user-preferences) — research patterns, what is license-clean to bundle, images
7. [The LangGraph planning agent](#7-the-langgraph-planning-agent) — HITL checkpoints, structured itinerary output, streaming, persistence
- [A. MVP reference architecture](#a-mvp-reference-architecture)
- [B. License obligations register](#b-license-obligations-register)
- [C. Open risks / unknowns](#c-open-risks--unknowns)

---

## 1. Offline vector tiles, any-city

### References
- **PMTiles spec v3** — https://github.com/protomaps/PMTiles/blob/main/spec/v3/spec.md — current spec generation; single-file, cluster-ordered, HTTP-range-readable archive. JS reader `pmtiles` npm ~v4.x; Go CLI `pmtiles` (go-pmtiles). BSD-3-Clause.
- **Protomaps daily basemap builds** — https://docs.protomaps.com/basemaps/downloads + https://maps.protomaps.com/builds — daily planet PMTiles (v4 build channel), z0–15, ~120 GB planet. Free to download/extract; retained ~1 week + latest patch. Data © OpenStreetMap (ODbL Produced Work rules apply); build pipeline/styles BSD.
- **`pmtiles extract`** — https://docs.protomaps.com/pmtiles/cli — `pmtiles extract https://build.protomaps.com/YYYYMMDD.pmtiles out.pmtiles --bbox=minLon,minLat,maxLon,maxLat` (or `--region=city.geojson`, `--maxzoom=N`). Extracting a full sub-pyramid z0→maxzoom is explicitly efficient (minimal range requests). Docs demonstrate extraction against Protomaps-hosted archives, i.e. permitted and free; Protomaps warns URLs may change — don't hotlink, copy what you need.
- **Planetiler v0.10.2** (2026-03-29) — https://github.com/onthegomap/planetiler — Apache-2.0, Java 21+. Planet in ~40 min on 64-CPU/128 GB (~3.5 h on 8-CPU/16 GB); country/city extracts in minutes. Needs ~0.5× PBF size in RAM, 5–10× in disk.
- **MapLibre GL JS** — https://github.com/maplibre/maplibre-gl-js/releases — stable **5.19.x** (Feb 2026); **v6.0.0 pre-releases** live mid-2026 (ESM-only transition, style-spec v25 with `split`/`join` expressions, breaking legacy-expression validation). BSD-3-Clause. PMTiles integration via `pmtiles.Protocol` + `maplibre.addProtocol("pmtiles", ...)` — first-class documented example: https://maplibre.org/maplibre-gl-js/docs/examples/pmtiles-source-and-protocol/. Newsletter: https://maplibre.org/news/2026-03-03-maplibre-newsletter-february-2026/
- **Glyphs/sprites offline** — https://github.com/protomaps/basemaps-assets — Noto Sans Regular/Medium/Italic PBF glyphs (SIL OFL) + light/dark sprites 1x/2x (MIT, from tangrams/icons). Copy into the bundle; regenerate with `font-maker` (custom fonts → PBF ranges) and `spreet` (SVG → spritesheet, works with CC0 Maki icons).

### Takeaways
1. The extract path is the whole trick: one CLI call against the hosted daily planet build yields a city archive in seconds-to-minutes with no infrastructure. No API key, no fee; the only obligation is OSM/ODbL attribution in the rendered map.
2. City-scale sizes (rule of thumb; each extra zoom ≈ 2× size): compact old-town bbox at z0–15 ≈ 10–40 MB; full metro bbox ≈ 50–200 MB. `--maxzoom=14` roughly halves it if bundle budget is tight; tour-day bboxes should be drawn tight around the itinerary + buffer, not the administrative city.
3. Planetiler is the sovereignty fallback (own schema, own layers) at the cost of running Java + downloading a Geofabrik PBF per city (city extract builds in ~1–5 min). Its default profile emits the OpenMapTiles-compatible schema, not Protomaps' — schema choice binds you to a style family. Sources: [Planetiler](https://github.com/onthegomap/planetiler), [build-on-CI writeup](https://christianmahnke.de/en/post/build-vector-tiles-on-github/).
4. Pin MapLibre 5.19.x for MVP; v6 is mid-migration (ESM) and plugin ecosystem (pmtiles protocol, plugins) will lag for a while.
5. Glyphs are a classic offline footgun: text disappears without bundled PBF glyph ranges. Bundle only the ranges the style references (Noto Sans covers global scripts; ~10–20 MB if you bundle everything — prune to used ranges).

### Practical notes / gotchas
```bash
# resolve latest daily build, then extract a tour-day bbox (example: central Lisbon)
pmtiles extract https://build.protomaps.com/$(date +%Y%m%d).pmtiles lisbon.pmtiles \
  --bbox=-9.23,38.69,-9.09,38.75 --maxzoom=15 --download-threads=8
# or clip to the exact division polygon fetched in §4:
pmtiles extract <build-url> city.pmtiles --region=city-boundary.geojson

# Planetiler fallback (city PBF from Geofabrik or protomaps/osmx):
java -Xmx4g -jar planetiler.jar --download --area=lisbon --output=city.pmtiles
```
- Extraction requires the source archive to be **clustered** (Protomaps builds are). `--minzoom` extracts are inefficient; always keep z0→maxzoom pyramids.
- Size planning table (empirical rules from Protomaps docs' "each zoom ≈ 2×"):

| Extract | z0–15 est. | z0–14 est. |
|---|---|---|
| Old town + center (~5×5 km) | 10–40 MB | 6–20 MB |
| Full metro (~30×30 km) | 50–200 MB | 25–100 MB |
| Metro + day-trip radius | 150–400 MB | 75–200 MB |

- Wire-up in the client is 5 lines: `import { Protocol } from "pmtiles"; const p = new Protocol(); maplibregl.addProtocol("pmtiles", p.tile);` then source url `pmtiles:///bundle/city.pmtiles` — for offline swap the Protocol's source for an OPFS-backed `FileSource` (§2).
- The style must reference **relative/bundled** `glyphs:` and `sprite:` URLs (e.g. `/bundle/fonts/{fontstack}/{range}.pbf`) — any absolute https URL becomes a broken label offline.

### RECOMMENDED CHOICE for Siyur MVP
`pmtiles extract` from the Protomaps daily build per planning session (server-side, during compile), archive dropped into the bundle; MapLibre GL JS 5.19.x + `pmtiles` v4 protocol adapter; glyphs/sprites vendored from basemaps-assets. **Fallback:** Planetiler self-build from Geofabrik per-city PBF (also unlocks custom layers, e.g. a POI layer of the itinerary baked as tiles).

---

## 2. Offline PWA for maps

### References
- **makinacorpus/maplibre-offline-pmtiles v2.1.1** (2026-03) — https://github.com/makinacorpus/maplibre-offline-pmtiles — MIT. MapLibre plugin: downloads a PMTiles archive into **OPFS**, exposes `offline-pmtiles://` protocol reading byte ranges locally. Explicit rationale: OPFS ≫ IndexedDB for large-file random reads ("near-native performance… without significant memory overhead").
- **Workbox v7 `workbox-range-requests`** — https://developer.chrome.com/docs/workbox — MIT. Serves 206 responses out of a fully-cached body in a service worker.
- **WebKit storage policy** (official) — https://webkit.org/blog/14403/updates-to-storage-policy/ — origin quota up to ~60% of disk in a browser app; **home-screen web apps get the same quota**; `navigator.storage.persist()` granted heuristically (installed-to-home-screen is a positive signal) and protects against LRU eviction.
- **iOS PWA field guides 2026** — https://www.magicbell.com/blog/pwa-ios-limitations-safari-support-complete-guide, https://www.mobiloud.com/blog/progressive-web-apps-ios — no install prompt on iOS (manual Share→Add to Home Screen; you must teach it); no Background Sync; push only iOS 16.4+ and removed for EU users on iOS 17.4+ (DMA); 7-day script-writable-storage eviction for *unused browser* origins (blogs disagree on whether installed PWAs are exempt — WebKit's own policy says persisted origins are protected; treat exemption as untrusted, see risks).
- **Prior art** — [reyemtm/pwa-maps](https://github.com/reyemtm/pwa-maps) (SW-cached vector tiles talk/demo), [bmcbride/gps-map](https://github.com/bmcbride/gps-map) (offline viewer + GPS), [blog.wxm.be Protomaps offline series](https://blog.wxm.be/2024/01/14/offline-map-with-protomaps-maplibre.html), [Headway](https://github.com/headwaymaps/headway) (AGPL self-hosted full maps stack — architecture reference, not embeddable), Organic Maps/CoMaps (native only, no web PWA to copy).

### Takeaways
1. **Do not cache PMTiles range requests in the service worker.** The Cache API rejects/mishandles 206 partial responses, and intercepting arbitrary `Range` headers is the flakiest corner of SW implementations. The 2026-proven pattern inverts it: download the *whole* city archive once into OPFS, then satisfy tile reads as local byte-range reads via a custom MapLibre protocol (pmtiles JS `FileSource` over an OPFS file handle). Workbox range-requests is only a fallback shim.
2. Bundle layout: one PMTiles file in OPFS; itinerary JSON, style JSON, narration text/audio, glyphs, sprites, app shell precached via ordinary SW precache (Workbox). A manifest with content hashes makes the bundle resumable/verifiable.
3. iOS reality: quota is a non-issue for a 50–200 MB bundle (persist() + installed ≈ GB-scale headroom), but **eviction is a UX problem, not a size problem** — design for bundle re-download (keep the compiled bundle addressable server-side) and verify integrity on each launch.
4. Install: Android/desktop `beforeinstallprompt`; iOS needs in-app instructions. Geolocation works offline on-device (GPS needs no network) but each PWA session may re-prompt for permission on iOS; Web Bluetooth is absent on iOS Safari entirely — don't build features on it.
5. Headway is the best whole-stack reference (tileserver + Valhalla + Pelias + MapLibre) for how the pieces fit; its AGPL license means read, don't paste.

### Practical notes / gotchas
- **Why SW range-caching fails**: `cache.put()` rejects 206 responses; a SW that caches the *full* file then answers `Range:` requests must slice `ArrayBuffer`s manually (what `workbox-range-requests` does) — workable for small files, memory-hostile for a 150 MB archive. The OPFS + custom protocol path never issues an HTTP range request at all after download.
- Safari's OPFS support: `FileSystemSyncAccessHandle` (worker) since 16.4 — use sync handles in a dedicated worker for tile reads; main-thread `createWritable` is the part with patchy Safari history, so do the *download* via sync-access-handle writes in the worker too.
- Bundle manifest sketch (drives download UI, resume, and integrity):
```json
{ "bundle": "lisbon-2026-07-24", "version": 1,
  "files": [
    {"path": "city.pmtiles", "bytes": 84213760, "sha256": "..."},
    {"path": "style.json", "bytes": 51200, "sha256": "..."},
    {"path": "itinerary.json", "bytes": 20480, "sha256": "..."},
    {"path": "walk-graph.geojson.gz", "bytes": 3145728, "sha256": "..."},
    {"path": "narrations/", "count": 14}, {"path": "fonts/", "count": 6}],
  "attribution": "ATTRIBUTION.md", "textLicense": "CC-BY-SA-4.0" }
```
- Download resumably with plain `fetch` + `Range` per chunk (server side supports it trivially for static files), write chunks to OPFS as they land; re-verify hashes on every launch and re-fetch only bad chunks.
- App-shell strategy: Workbox `precacheAndRoute` for code/UI assets (cache-first, versioned), `NetworkOnly` for planning APIs, and **no runtime caching** for bundle files (they're OPFS, not Cache API — keeps quotas and eviction reasoning in one place).
- iOS checklist: call `navigator.storage.persist()` right after bundle download; show `navigator.storage.estimate()` in a debug screen; ship in-app "Add to Home Screen" walkthrough; test the 7-day scenario with device date-hopping before trusting any exemption claim.

### RECOMMENDED CHOICE for Siyur MVP
Vite PWA + Workbox v7 precache for the app shell; bundle download manager writing the PMTiles archive to **OPFS** with `navigator.storage.persist()`; custom `pmtiles://` protocol reading from OPFS (adopt or imitate `maplibre-offline-pmtiles`, MIT). **Fallback:** IndexedDB chunk store (Safari < 16.4 lacks OPFS `createWritable`; use sync-access-handle in a worker, or IDB blobs).

---

## 3. LLM-designed map styles

### References
- **MapLibre Style Spec v25** — https://maplibre.org/maplibre-style-spec/ + https://github.com/maplibre/maplibre-style-spec — ISC. npm `@maplibre/maplibre-gl-style-spec` ships `validateStyleMin` (in-process validation, returns error list) and CLI `gl-style-validate`; also `diff`, `migrate`, `format` utilities — exactly the toolbox for machine-edited styles.
- **@protomaps/basemaps** (successor of `protomaps-themes-base` 4.x) — https://docs.protomaps.com/basemaps/flavors — BSD-3. Styles are *generated from a `Flavor` object*: a flat map of ~dozens of named colors + fonts + POI toggles → full layer stack. Five stock flavors: light, dark, white, grayscale, black.
- **Maputnik** — https://github.com/maplibre/maputnik — MIT, maintained under the MapLibre org, active releases through 2025/26. Visual editor; runs as static site; good as the human escape hatch, not as a programmatic API.
- **maplibre-agent-skills** (launched Feb 2026) — https://github.com/maplibre/maplibre-agent-skills — community agent skills (SKILL.md files) teaching AI coding assistants MapLibre patterns incl. `maplibre-pmtiles-patterns`, tile sources, Mapbox migration. Directly consumable by the Siyur build agent.
- **json-maps** — https://jsonmaps.dev/ — React MapRenderer driven by a JSON spec designed to be streamed from an LLM (markers, layers, choropleths; DuckDB-WASM). Evidence the "LLM emits validated map JSON" pattern is productized in 2026; license unverified.
- **Alternative base styles** — [OpenFreeMap](https://openfreemap.org) (free hosted OMT-schema tiles + Bright/Liberty/Positron styles; donation-funded), [VersaTiles](https://versatiles.org) (colorful style family, permissive), both bound to the OpenMapTiles schema, not Protomaps'.

### Takeaways
1. There is no turnkey "LLM cartographer" product; the working 2026 pattern is exactly the one Siyur planned: **LLM proposes a constrained delta → deterministic code regenerates the full style → schema validation → render smoke-test**. Never let the model emit a raw 4,000-line style.
2. The Protomaps `Flavor` object is the ideal LLM surface: ~40 semantic color slots ("water", "buildings", "pois.civic"…) plus fonts. Have the LLM output a Flavor JSON (or a delta to a stock flavor) — small, meaningful, hard to break — then call `namedFlavor()`/style generator to produce the layer stack. Spread-syntax overrides are the documented customization path.
3. Guardrail stack: (a) JSON-schema-validate the Flavor delta; (b) `validateStyleMin` on the generated style; (c) cartographic lint you write yourself — WCAG contrast between label halo/fill and land/water colors, min text size, no invisible road hierarchy; (d) headless render (maplibre-gl in Playwright) of 3 fixed viewports for a vision-model sanity check. LLMs cheerfully produce beautiful-but-unreadable maps; contrast checks are the cheap fix.
4. Sprites constrain palettes: stock sprite PNGs have baked-in colors per flavor (light/dark). Radical recolors need `spreet` regeneration from SVG (Maki, CC0) at compile time — feasible, budget for it post-MVP.
5. Keep Maputnik in the loop as the human override: load the generated style, hand-tweak, save back — no programmatic coupling needed.

### Practical notes / gotchas
```ts
// The whole LLM surface is a Flavor delta, e.g. for a "sun-bleached riviera" theme:
const delta = { background: "#f6f1e7", water: "#8fc7d1", buildings: "#eadfce",
  earth: "#f2ead9", park_a: "#c9d8a7", roads: {minor: "#ffffff", major: "#f3d9a4"},
  regular: "Noto Sans Regular", pois: { enabled: true } };
const style = { version: 8, glyphs: "/bundle/fonts/{fontstack}/{range}.pbf",
  sprite: "/bundle/sprites/light",
  sources: { p: { type: "vector", url: "pmtiles:///bundle/city.pmtiles" } },
  layers: layers("p", { ...namedFlavor("light"), ...delta }, { lang: "en" }) };
// Guardrail: hard-fail compile on any validation error
import { validateStyleMin } from "@maplibre/maplibre-gl-style-spec";
const errors = validateStyleMin(style); if (errors.length) reject(errors);
```
- Contrast lint worth writing first (each ~10 lines with a WCAG-ratio helper): label text vs its halo ≥ 3:1; halo vs dominant underlying fill ≥ 1.5:1; water vs land ≥ 1.3:1; road casing vs fill distinguishable; POI icon color present in sprite variant.
- The itinerary overlay (route line, numbered stops, highlighted POIs) should be *appended layers* on top of the generated base — keep them out of the LLM's reach so navigation affordances can never be styled away.
- `namedFlavor()` + spread means the LLM can also be asked for *partial* deltas per feedback round ("warmer water, mute the parks") — diff-able, undo-able, and each round re-validates.
- Vision-check loop: render 3 fixed viewports (overview z12, streets z15, POI close-up z17) headlessly, send PNGs to a vision model with a fixed rubric (legibility, hierarchy, vibe-match to user brief), gate on score — cheap and catches what linting can't.

### RECOMMENDED CHOICE for Siyur MVP
Fork `@protomaps/basemaps` flavors; LLM designs a **Flavor delta** (JSON-schema-constrained tool call), pipeline regenerates + validates with `@maplibre/maplibre-gl-style-spec` v25 + custom contrast lint, bundle ships the frozen style JSON. **Fallback:** curated palette presets ("riviera", "noir", "botanical"…) the LLM merely selects and lightly tweaks — near-zero risk, still feels custom.

---

## 4. Grounded any-city curation

### References
- **Overture Maps release 2026-07-22.0, schema v1.18.0** — https://docs.overturemaps.org/blog/2026/07/22/release-notes/ — monthly releases; all 5 primary themes GA. Places: new top-level categories (arts & entertainment, lodging, cultural & historic); `categories` deprecated → `basic_category` + `taxonomy` (through Sept 2026 transition). Every feature has a stable **GERS ID**; places carry a `confidence` score (filter ≥ ~0.5–0.6 in practice).
- **Access** — GeoParquet on S3/Azure: DuckDB (`httpfs` + `spatial`, bbox pushdown on the `bbox` struct column) per https://docs.overturemaps.org/getting-data/duckdb/; `overturemaps` Python CLI (`pip install overturemaps; overturemaps download --bbox=... --type=place -f geoparquet`); DuckDB community extension `overture`.
- **Licenses/attribution** — https://docs.overturemaps.org/attribution/ — **places: CDLA-Permissive-2.0** (no attribution mandated; sources incl. Meta, Microsoft, Foursquare); **divisions, transportation, buildings, base: ODbL** ("© OpenStreetMap contributors, Overture Maps Foundation"); addresses: per-country.
- **Boundary lookup ("the city the user chose")** — Overture **divisions** theme (division_area polygons, admin levels; ODbL) queried by name+country in DuckDB; or **Nominatim** (`polygon_geojson=1`) under the OSMF policy — max 1 req/s, real User-Agent, no bulk: https://operations.osmfoundation.org/policies/nominatim/. Who's On First (CC BY etc.) is a third option but stale-ish; Overture divisions has effectively absorbed this niche.
- **OSM Overpass** — public instance overpass-api.de: fair-use ~10k queries/day, 2 concurrent; kumi.systems mirror is more permissive: https://overpass.kumi.systems/ — long-tail tags (viewpoints, artwork, drinking_water, benches, `opening_hours`).
- **Wikivoyage** — CC BY-SA 4.0; reuse rules: https://en.wikivoyage.org/wiki/Wikivoyage:How_to_re-use_Wikivoyage_guides (attribute with article URL/authors; share-alike on derivatives). Access via MediaWiki Action API (wikitext with structured `{{see|...}}/{{do}}/{{eat}}` listing templates carrying name/lat/long/hours/price), dumps, or Wikimedia Enterprise Travel API (commercial): https://enterprise.wikimedia.com/project-data/wikivoyage-api/. Quality: excellent for major destinations, thin for small towns.
- **Wikipedia + Wikidata** — GeoSearch: Action API `list=geosearch` (radius around POI) + REST `page/summary` for extracts; text **CC BY-SA 4.0**. Wikidata: **CC0** — IDs, images (P18), official website, coordinates; SPARQL for "notable sights in admin area". Commons images: **per-file license** via `imageinfo&iiprop=extmetadata`.
- **opening_hours.js v3.9** — https://github.com/opening-hours/opening_hours.js — the canonical evaluator for OSM `opening_hours` syntax (holidays, PH/SH, intervals); **LGPL-3.0** (copyleft-lite — fine to use as an npm dependency, don't fork-and-embed silently).

### Takeaways
1. Two-source grounding wins: **Overture places** for the clean, confidence-scored, deduped POI backbone (and it's the only permissively-licensed one — CDLA-P keeps bundle data lawyer-free) + **Overpass** for the long-tail OSM tags Overture drops. Join via name+distance matching; keep GERS IDs as Siyur's stable POI keys.
2. City boundary: resolve name → Overture divisions polygon in the same DuckDB session that fetches places (one engine, no rate limits, offline-capable if you mirror the release). Nominatim only as interactive fallback for disambiguation UX ("Which Springfield?").
3. Wikivoyage is the narrative gold mine *and* the license landmine: its listing templates are parseable structured data, but any prose you carry into narrations drags **CC BY-SA share-alike** onto the narration text (see §6).
4. Opening hours: OSM's `opening_hours` strings are ~90% machine-evaluable with opening_hours.js but freshness is uneven; Overture places carry operating status/hours increasingly (July 2026 API added operating status). Feasibility checks at *planning time* should mark hours as "verify" confidence, and the bundle should show "typical hours" with a disclaimer, not promises.
5. Wikidata being CC0 makes it the preferred machine-facts source (coords, image pointers, heritage status) — zero obligations in the bundle.

### Practical notes / gotchas
```sql
-- DuckDB: places for an arbitrary city bbox straight off S3 (bbox column enables pushdown)
INSTALL spatial; INSTALL httpfs; LOAD spatial; LOAD httpfs;
SELECT id, names.primary AS name, basic_category, confidence,
       ST_AsText(geometry) AS wkt, websites, phones
FROM read_parquet('s3://overturemaps-us-west-2/release/2026-07-22.0/theme=places/type=place/*',
                  filename=true, hive_partitioning=1)
WHERE bbox.xmin BETWEEN -9.23 AND -9.09 AND bbox.ymin BETWEEN 38.69 AND 38.75
  AND confidence >= 0.6;
-- Boundary: theme=divisions/type=division_area, filter names.primary ILIKE + country + subtype='locality'
```
```
// Overpass long-tail example (viewpoints + fountains + artworks with hours, in bbox):
[out:json][timeout:60];
( nwr["tourism"="viewpoint"]({{bbox}}); nwr["amenity"="fountain"]({{bbox}});
  nwr["tourism"="artwork"]({{bbox}}); nwr["opening_hours"]({{bbox}})[tourism]; );
out center tags;
```
- Wikivoyage listing templates are the hidden structured DB: `{{see | name=... | lat=... | long=... | hours=... | price=... | content=...}}` — parse wikitext (mwparserfromhell) rather than scraping rendered HTML; `content=` prose is the CC BY-SA part, coordinates/names are facts.
- Name-matching Overture↔OSM↔Wikivoyage: match on normalized name + ≤75 m distance + category compatibility; keep all three source IDs (GERS, osm_id, wikivoyage listing) on the Siyur POI record for provenance.
- opening_hours.js needs locale context (`nominatim_object` or address for PH/SH holiday resolution) — pass the city's country/state or holiday rules silently misfire.
- Cache the per-city curation result (parquet + JSON) keyed by city+release: repeat planning sessions for the same city then cost zero external calls.

### RECOMMENDED CHOICE for Siyur MVP
DuckDB (+spatial/httpfs) over Overture **2026-07-22.0** S3 for places (confidence ≥ 0.6) and divisions boundary; Overpass (kumi mirror, cached) for long-tail tags + `opening_hours`; Wikivoyage listings + Wikipedia GeoSearch for narrative grounding; Wikidata/Commons for images with per-file license capture. opening_hours.js v3.9 for feasibility checks. **Fallback:** pure-OSM pipeline (Overpass/Nominatim only) if Overture querying is too slow per session — pre-warm by caching per-city parquet extracts.

---

## 5. Any-city walking routing

### References (ranked for Siyur)
1. **Valhalla** — https://github.com/valhalla/valhalla — MIT, official GHCR docker image; per-city graph build from a Geofabrik extract in ~1–5 min (Germany-scale ~15–20 min per [2026 comparison](https://www.pistack.xyz/posts/2026-04-25-graphhopper-vs-osrm-vs-valhalla-self-hosted-routing-engines-guide-2026/)); tile-based (disk-heavy, RAM-light); best-in-class pedestrian costing (elevation-aware, `pedestrian` costing options); also matrix + optimized route (TSP-ish) + isochrones — everything the feasibility checker needs. Setup ref: [Robin Wilson's docker walkthrough](https://blog.rtwilson.com/simple-self-hosted-openstreetmap-routing-using-valhalla-and-docker/).
2. **OpenRouteService public API** — https://openrouteservice.org/ — backend GPL-3.0; free key ≈ **2,500 req/day / 40,000 per month** ([2026 pricing digest](https://apispine.com/openrouteserviceorg/pricing)); request-shape caps (≤50 waypoints, ≤6,000 km) per https://openrouteservice.org/restrictions/. Fine for planning-time volumes; attribution expected.
3. **OSRM demo server** — https://github.com/Project-OSRM/osrm-backend/wiki/Api-usage-policy — engine BSD-2, demo server explicitly **not for production** ("access shall be withdrawn at any time"), mandatory honest User-Agent. Dev-time only.
4. **GraphHopper** — engine Apache-2.0 self-host (community docker only); hosted Directions API free tier is small (order 500 credits/day) — https://www.graphhopper.com/pricing/. BRouter (MIT, brouter.de) remains a fine bike/hike secondary opinion.
5. **In-bundle offline routing** — **geojson-path-finder v2** — https://github.com/perliedman/geojson-path-finder — ISC, pure-JS Dijkstra/A* over a GeoJSON line network, built for "serverless, offline routing in the browser" ([demo](https://www.liedman.net/geojson-path-finder/)); needs a topologically noded network and a custom weight fn. No production wasm port of OSRM/Valhalla exists in mid-2026 (tracked wishfully in e.g. [route_snapper#22](https://github.com/dabreegster/route_snapper/issues/22)); native apps (Organic Maps/CoMaps, OsmAnd) do offline routing but nothing reusable for web.

### Takeaways
1. Split the problem: **planning-time routing** (server, quality matters) vs **travel-time routing** (device, robustness matters). They need not share an engine.
2. Planning: Valhalla per-city docker build is genuinely feasible on-demand — the city PBF is already being downloaded for other pipeline steps; a 1–5 min graph build folds into bundle compilation. It gives route + leg times + optimized stop order + isochrones from one MIT-licensed box.
3. Travel: **precompute everything predictable** — the tour polyline(s) with per-leg geometry/timings baked into the itinerary JSON. For "I wandered off" recovery, ship a pruned pedestrian way-graph (GeoJSON extracted from the same PBF, walking-relevant highway=* only; a tour-day area is typically < 5 MB gzipped) and run geojson-path-finder for approximate re-routing, straight-line as last resort. This is honestly good enough: walking re-routes in a city are short and forgiving.
4. Verdict on "real" in-bundle routing in 2026: **not off-the-shelf**. Nobody ships a maintained OSRM/Valhalla-wasm; geojson-path-finder is the only credible browser option and it's approximate (no turn restrictions, naive costing). Design the product so precomputed routes carry the experience.
5. Keep ORS as the zero-infra dev-mode router behind the same internal interface, so early demos need no docker.

### Practical notes / gotchas
```bash
# Per-city Valhalla: official image, tiles build on first start from mounted PBFs (~1–5 min/city)
docker run -v $PWD/valhalla:/custom_files -e tile_urls=<geofabrik-city-url> \
  -p 8002:8002 ghcr.io/valhalla/valhalla-scripted:latest
# Planner then hits: POST /route  {"locations":[...],"costing":"pedestrian",
#   "costing_options":{"pedestrian":{"walking_speed":4.8,"use_hills":0.3}}}
# POST /sources_to_targets (time matrix for ordering) ; POST /optimized_route (stop order)
```
- Feasibility check = matrix times × dwell times × opening windows: Valhalla's `sources_to_targets` for N stops is one call; re-check after every itinerary edit (cheap, local).
- Walking-speed realism: default 5.1 km/h is brisk for a sightseeing day; expose "pace" as a user preference (3.5–5 km/h) and multiply — this changes feasibility verdicts more than routing engine choice does.
- Pruned in-bundle walk graph: from the city PBF keep `highway` in {footway, path, pedestrian, steps, living_street, residential, service, tertiary+sidewalk}, drop everything else, node the graph (osmium + small script or `osm2geojson`), simplify geometry ~1 m; a tour-day area lands well under 5 MB gzipped. geojson-path-finder wants a *noded* network — un-noded input silently produces disconnected islands (top field bug).
- Decision honesty: precomputed route + recovery routing covers ~all real tour-day needs; full offline turn-by-turn is native-app territory (Organic Maps/CoMaps) — explicitly out of MVP scope.

### RECOMMENDED CHOICE for Siyur MVP
**Valhalla self-hosted, built per-city during compile** (MIT, docker, minutes) for planning-time routes, time matrices, and stop-order optimization; bundle ships precomputed leg geometries + a pruned walking network + **geojson-path-finder** for offline deviation recovery, straight-line fallback. **Fallback:** OpenRouteService free key (2,500 req/day) during early development.

---

## 6. Planning-time city research from user preferences

### References
- Wikivoyage reuse terms (CC BY-SA 4.0, attribution + share-alike): https://en.wikivoyage.org/wiki/Wikivoyage:How_to_re-use_Wikivoyage_guides
- Wikimedia APIs portal: https://www.mediawiki.org/wiki/Wikimedia_APIs (Action API, REST, Enterprise tiers; the former /wiki/Travel use-case page now redirects here)
- Commons per-file licensing via `extmetadata` (LicenseShortName, Artist, LicenseUrl): https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia
- Overture attribution page (CDLA-P places → clean to bundle): https://docs.overturemaps.org/attribution/
- OSMF attribution guideline for ODbL Produced Works: https://osmfoundation.org/wiki/Licence/Attribution_Guidelines

### Takeaways
1. Working pattern for grounded curation: **retrieve-then-reason**. The agent pulls candidate POIs from Overture/Overpass (facts), then pulls open narrative context (Wikivoyage listings + Wikipedia summaries) *per candidate*, then LLM-ranks against user preferences with citations back to source IDs. Open web search at planning time is fine for *ranking signal* ("is this actually popular/closed?") but nothing fetched from the open web should be **copied into the bundle** — search snippets and review sites are all-rights-reserved.
2. **The share-alike trap, stated plainly:** Wikipedia/Wikivoyage prose is CC BY-SA 4.0. If bundled narrations are written by paraphrasing/adapting that prose, they are derivative works → the narration text must itself be licensed **CC BY-SA 4.0** with attribution links per article. That's viral only for the *text*: it does not infect the app code, the style, or the tiles. Two clean postures: (a) embrace it — label bundle narrations "text CC BY-SA 4.0, sources: …" (Siyur is open-source-first; this is cheap); or (b) generate narrations from **CC0 Wikidata facts + Overture/OSM data only**, keeping CC BY-SA sources as unbundled planning-time context. Pick (a) for richness, (b) if a future business model needs proprietary text.
3. Images: never bundle by URL pattern alone. For each Commons image (usually via Wikidata P18), fetch `extmetadata`, keep only PD/CC0/CC BY/CC BY-SA files, and store `{file, author, license, licenseUrl, sourceUrl}` in the bundle manifest; render credits on the POI card and a bundle-wide credits screen. CC BY-SA images additionally require noting license on modification.
4. LLM-generated *original* text from facts (names, dates, categories) carries no upstream text license, but keep provenance metadata per narration anyway — it doubles as the hallucination audit trail ("every claim links a source ID").
5. Wikimedia APIs are free with a descriptive User-Agent and modest rates; Enterprise API only matters at real scale.

### Practical notes / gotchas
- Generated `ATTRIBUTION.md` sketch (one per bundle, rendered in-app):
```
Map data © OpenStreetMap contributors (ODbL), Overture Maps Foundation.
Narration text: CC BY-SA 4.0 — adapted from:
  - "Lisbon/Alfama", Wikivoyage, https://en.wikivoyage.org/wiki/Lisbon/Alfama (CC BY-SA 4.0)
  - "Castelo de São Jorge", Wikipedia, https://en.wikipedia.org/wiki/... (CC BY-SA 4.0)
Images: "Alfama rooftops.jpg" by <Author>, CC BY-SA 4.0, via Wikimedia Commons (link)
Facts: Wikidata (CC0). Fonts: Noto Sans (SIL OFL 1.1).
```
- Per-source quarantine rule, mechanically enforceable: every narration-generator context document carries a `bundleable: true/false` flag set by its source adapter (Wikivoyage → true+BY-SA, web search snippet → false); the generator refuses to run with un-flagged input. This turns license policy into a type check.
- Commons `extmetadata` query: `action=query&titles=File:X.jpg&prop=imageinfo&iiprop=extmetadata|url&iiurlwidth=1024` → check `LicenseShortName` against an allowlist {PD, CC0, CC BY *, CC BY-SA *}; store the 1024px thumb URL's bytes, not the original (size + licensing of faithful reproductions is cleaner).
- TTS narration audio generated from CC BY-SA text is still a derivative of the text — same attribution/license note covers it; say so in ATTRIBUTION.

### RECOMMENDED CHOICE for Siyur MVP
Retrieve-then-reason over open sources only for bundled content; narrations generated with per-claim source IDs; **bundle text licensed CC BY-SA 4.0 with a generated ATTRIBUTION file** (per-article links, per-image credits); open-web search allowed at planning time but quarantined from the bundle. **Fallback:** facts-only narration mode (Wikidata/OSM/Overture) producing unencumbered text.

---

## 7. The LangGraph planning agent

### References
- **LangGraph 1.0 GA** (Oct 2025; 1.x line — 1.2 current in 2026) — https://www.langchain.com/blog/langchain-langgraph-1dot0 + https://changelog.langchain.com/announcements/langgraph-1-0-is-now-generally-available — MIT; 1.0 was deliberately non-breaking; durable execution/checkpointing is the headline ([2026 overview](https://www.jbinternational.co.uk/article/view/4680)).
- **Human-in-the-loop**: `interrupt()` inside a node + resume via `Command(resume=payload)`; requires a checkpointer; docs & patterns: https://deepwiki.com/langchain-ai/langgraph/3.7-human-in-the-loop-and-interrupts, worked template (FastAPI + Next.js): https://github.com/KirtiJha/langgraph-interrupt-workflow-template
- **Persistence**: `langgraph-checkpoint-sqlite` (dev) / `langgraph-checkpoint-postgres` (prod); `thread_id` = planning session; time-travel/forking from any checkpoint.
- **Streaming**: `graph.astream(..., stream_mode=["updates","messages","custom"])` → map onto SSE from FastAPI; or run **LangGraph Server** (`langgraph dev` / self-hosted container) which exposes threads/runs/streaming HTTP API out of the box (Platform is the paid hosted tier — not needed).
- **Structured output**: Pydantic-typed `Itinerary` via provider structured-output/tool-calling from the compile node; state schema itself is typed (Pydantic/TypedDict).

### Takeaways
1. Fit is exact: Siyur's planner is a graph — `intake → research(city) → curate_POIs → route+feasibility → design_style → HITL review → compile_bundle` — with `interrupt()` at itinerary approval and style approval. Checkpoints make "close laptop Tuesday, resume Thursday" free.
2. Keep the big state *out* of the checkpointer: store POI tables/tiles on disk or object storage, keep IDs + decisions in graph state. Checkpointers serialize full state every superstep; megabyte states make every step slow.
3. Structured itinerary: make the itinerary a versioned Pydantic model (`ItineraryV1`) emitted by a dedicated node with structured output + a validation node (feasibility re-check) — never free-text-parse. The same model is the bundle's `itinerary.json` schema: one source of truth.
4. Feasibility/routing/DuckDB calls are plain tools (nodes), so the deterministic parts stay deterministic — LLM only ranks, writes, and designs. This is what keeps the "grounded" promise auditable.
5. For MVP UI plumbing, FastAPI + SSE over `astream` is less magic than running LangGraph Server; switch to the server when multi-session management gets annoying. Pin `langgraph 1.x` + `langgraph-checkpoint-postgres`.

### Practical notes / gotchas
```python
class ItineraryV1(BaseModel):
    city: CityRef                      # name, country, division GERS id, bbox
    stops: list[Stop]                  # poi_id (GERS), arrive/depart, dwell_min,
                                       #   narration_id, sources: list[SourceRef]
    legs: list[Leg]                    # from/to stop, polyline, meters, minutes, mode
    feasibility: FeasibilityReport     # per-stop open/closed, slack, warnings
    style: StyleRef                    # flavor delta + validated style hash

def review_itinerary(state: State) -> Command:
    decision = interrupt({"itinerary": state.itinerary.model_dump(),
                          "question": "Approve, or describe changes?"})
    return Command(goto="curate" if decision["edit"] else "design_style",
                   update={"feedback": decision.get("notes")})

graph = builder.compile(checkpointer=PostgresSaver.from_conn_string(DB_URL))
# FastAPI: async for ev in graph.astream(input, config={"configurable":
#   {"thread_id": session_id}}, stream_mode=["updates","custom"]): yield sse(ev)
```
- `interrupt()` re-executes the *node* from its start on resume — keep review nodes side-effect-free (pure read of state), do expensive work in prior nodes.
- Emit compile progress ("extracting tiles… 34 MB") via `get_stream_writer()` custom events — the UI's perceived quality lives here.
- Version the state schema from day one (`ItineraryV1`); old checkpoints deserialize against old code otherwise. Store bundle artifacts under a content-addressed path recorded in state, not in state itself.
- Two graphs, not one: `planner` (interactive, checkpointed, days-long threads) and `compiler` (batch, restartable, no HITL) — decoupling lets you re-compile a bundle (new tile build, style tweak) without replaying conversation.

### RECOMMENDED CHOICE for Siyur MVP
LangGraph 1.x (MIT) StateGraph with Postgres checkpointer, `interrupt()`-based HITL at itinerary + style gates, Pydantic `ItineraryV1` structured output, FastAPI + SSE streaming to the planning web UI. **Fallback:** LangGraph Server (self-hosted, free) if hand-rolled thread management becomes a time sink.

> **Amended 2026-07-25 (ADR-0004):** superseded for Siyur by **PydanticAI + LiteLLM over an owned `ModelRouter` seam + Postgres checkpoint** — an Anthropic-native adapter in M1 (full prompt caching / adaptive thinking), per-task model routing (Haiku=research, Sonnet=curate, Opus=plan), cross-provider deferred behind the seam. LangGraph's checkpointer/HITL was judged not worth the dependency weight over a single owned `user_plan` row.

---

## A. MVP reference architecture

```
 PLANNING (online, server)                              TRAVEL (offline, device)
 ┌──────────────────────────────────────────────┐       ┌──────────────────────────────┐
 │ PydanticAI planner over model seam (SSE)     │       │ PWA (Vite + Workbox v7)      │
 │  intake ─ research ─ curate ─ route ─ style  │       │  MapLibre GL JS 5.19         │
 │     │        │         │       │       │     │       │   └ pmtiles:// ← OPFS archive│
 │     ▼        ▼         ▼       ▼       ▼     │       │  itinerary.json + narrations │
 │  prefs   DuckDB→    Wikivoyage Valhalla Flavor│      │  geojson-path-finder (dev.   │
 │          Overture   Wikipedia  (docker, delta │       │   recovery) + straight-line  │
 │          Overpass   Wikidata   per-city) +val.│      │  glyphs/sprites (OFL/MIT)    │
 │        [interrupt: itinerary OK?] [style OK?] │       │  ATTRIBUTION screen          │
 │                      │                        │       └──────────────▲───────────────┘
 │            COMPILE BUNDLE                     │                      │ one-time download
 │  pmtiles extract (Protomaps daily build)      │──────────────────────┘ (persist())
 │  + style.json + itinerary.json + narrations   │
 │  + walk-graph.geojson + credits manifest      │
 └──────────────────────────────────────────────┘
```

| Component | Choice | Pinned version | License |
|---|---|---|---|
| Tile format | PMTiles | spec v3; `pmtiles` js v4.x; go-pmtiles CLI | BSD-3 |
| Tile source | Protomaps daily planet build, `pmtiles extract` | v4 build channel | data ODbL |
| Tile self-build fallback | Planetiler | v0.10.2 | Apache-2.0 |
| Renderer | MapLibre GL JS | 5.19.x (defer v6 ESM) | BSD-3 |
| Style base | @protomaps/basemaps flavors | current npm (succ. themes-base 4.5) | BSD-3 |
| Style validation | @maplibre/maplibre-gl-style-spec | v25.x | ISC |
| Style editor (human) | Maputnik | maplibre org, current | MIT |
| Glyphs/sprites | protomaps/basemaps-assets (Noto Sans; spreet) | current | OFL / MIT |
| PWA offline | Workbox precache + OPFS archive + persist() | Workbox 7.x | MIT |
| Offline PMTiles plugin | maplibre-offline-pmtiles (or imitate) | 2.1.1 | MIT |
| POIs | Overture places (CDLA-P), confidence ≥0.6 | release 2026-07-22.0 / schema 1.18.0 | CDLA-P-2.0 |
| Boundaries | Overture divisions (DuckDB), Nominatim fallback | same release | ODbL |
| Long-tail tags/hours | Overpass (kumi mirror) + opening_hours.js | oh.js 3.9.x | LGPL-3.0 (lib) |
| Narrative sources | Wikivoyage + Wikipedia (CC BY-SA 4.0), Wikidata (CC0), Commons (per-file) | live APIs | see register |
| Planning routing | Valhalla per-city docker | 3.5.x line | MIT |
| Dev routing | OpenRouteService free key | 2,500 req/day | GPL-3 backend |
| Offline routing | precomputed legs + geojson-path-finder | v2.x | ISC |
| Agent framework | PydanticAI + LiteLLM over model seam; own Postgres checkpoint (ADR-0004) | pinned at scaffolding | MIT |
| Query engine | DuckDB + spatial + httpfs | 1.3+ | MIT |
| Web build tool / dev server | Vite + vite-plugin-pwa (ADR-0003) | pinned at scaffolding | MIT |

## B. License obligations register

| Obligation | Trigger | What Siyur must do |
|---|---|---|
| **ODbL attribution** (© OpenStreetMap contributors) | Tiles (Protomaps build), Overture divisions/transportation/buildings, Overpass data, routing over OSM | Visible attribution on every rendered map (control corner) + bundle credits screen; per OSMF guidelines |
| **Overture attribution** | Any Overture theme | "© OpenStreetMap contributors, Overture Maps Foundation" for ODbL themes; courtesy cite for places |
| **CDLA-Permissive-2.0** (Overture places) | Bundling POI records | No attribution mandated; no share-alike — safe to bundle |
| **CC BY-SA 4.0 share-alike** (Wikivoyage/Wikipedia text) | Narrations derived from their prose | License bundled narration text CC BY-SA 4.0; per-article attribution links in ATTRIBUTION file; flag in product docs |
| **CC0** (Wikidata) | Facts/IDs/coords | Nothing required (courtesy credit) |
| **Commons per-file licenses** | Each bundled image | Capture author/license/URL at compile; render credits; exclude NC/ND files; CC BY-SA images keep share-alike on modification |
| **OFL** (Noto glyphs) | Bundled fonts | Keep OFL.txt in bundle; don't sell fonts standalone |
| **LGPL-3.0** (opening_hours.js) | Using the lib | Use as unmodified dependency; publish source of any modifications; note in credits |
| **AGPL** (Headway) | — | Reference only; do not vendor code |
| **API terms** | Nominatim (1 req/s, UA), Overpass fair use, OSRM demo (no prod), ORS quota | Respect at planning time; cache aggressively; honest User-Agent |
| **Protomaps builds** | Extracting from hosted planet | Free; don't hotlink build URLs from clients; ODbL attribution as above |

## C. Open risks / unknowns

1. **iOS eviction ambiguity**: WebKit policy says persisted/installed origins are protected; field reports still claim 7-day wipes. Mitigation is architectural (re-downloadable bundles + launch-time integrity check) — verify on real devices early.
2. **MapLibre v6 transition**: ESM-only + style-spec validation changes will ripple through the pmtiles protocol and plugins during 2026; pinning 5.19 is safe but creates a known migration debt.
3. **Overture places churn**: `categories` → `basic_category`/`taxonomy` migration completes ~Sept 2026 — code written today against `categories` breaks; hours/operating-status coverage still uneven vs. OSM.
4. **Protomaps build availability**: daily builds retained ~1 week and "URLs may change"; a compile pipeline must not assume a stable archive URL — resolve latest build at run time, or mirror.
5. **Offline re-route quality**: geojson-path-finder ignores turn restrictions/one-ways (mostly irrelevant for pedestrians) and needs careful graph noding; validate against Valhalla outputs per city before trusting it in the bundle.
6. **Share-alike posture is a product decision** (bundle text CC BY-SA vs facts-only narration) — decide before writing the narration generator, not after.
7. **Style-quality evaluation** is the least-proven link (no established LLM-cartography benchmark); the contrast-lint + vision-check loop is homegrown and needs iteration.
8. **Unverified details to confirm at build time**: exact current Maputnik release; `@protomaps/basemaps` npm major; json-maps license; ORS per-minute cap (daily cap confirmed at 2,500).
