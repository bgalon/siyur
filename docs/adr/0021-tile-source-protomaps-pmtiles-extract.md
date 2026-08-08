# 0021 — Offline basemap: `pmtiles extract` off the Protomaps daily build, over the itinerary bbox + 1 km buffer

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code (Opus 5) · approved-by: Ben · Date: 2026-08-07 · accepted: 2026-08-08

## Context and Problem Statement

Spec 002 FR-016 requires bundled map tiles to cover **the itinerary's extent plus a margin sufficient for a traveller who strays, and no more** — the bundle is scoped to the day, not the region — while FR-017/FR-018 require the map to render with **zero network requests** and SC-007 caps the bundle at ≤200 MB. `methods-stack-reference.md` §1 and `docs/data/tile-source.md` already fix the producer (Protomaps basemap), the CRS split (pyramid EPSG:3857, `bbox` EPSG:4326), the offline runtime (MapLibre 5.19.x + `pmtiles` v4) and the licence position (data ODbL, pipeline BSD, glyphs OFL). What compile still needs decided is **where the bytes come from at run time, how big a margin is right, and how deep the pyramid goes**.

Two upstream facts force the shape of the answer. Protomaps' own documentation says *"URLs may change and hotlinking to these downloads are discouraged"* and retains **builds for the past week** — so a hardcoded build URL is a bug with a one-week fuse, and there is no `/latest` redirect or JSON index to resolve against. And a **partial-zoom extract is inefficient against a clustered source**: taking a z10–15 slice of a clustered archive costs more, not less, than taking the full sub-pyramid.

## Considered Options

- **A — `pmtiles extract` (go-pmtiles CLI) against the Protomaps daily build**, `--bbox` = itinerary extent + buffer, `--maxzoom=15`, keeping the full **z0→15** sub-pyramid, with the build URL resolved at run time.
- **B — Planetiler self-build.** Java + a per-area PBF, and the OpenMapTiles schema binds the style family — kept as the **sovereignty fallback** and the route to a baked POI layer, M2+.
- **C — Hosted Protomaps API tiles.** Needs network at travel time — disqualified by Constitution Article I.
- **D — Pinning one build URL.** Discouraged upstream, and retention is ~1 week, so it dies within days.

## Decision Outcome

Chosen: **A** — compile runs `pmtiles extract` against the Protomaps daily build over the itinerary bbox + buffer at `--maxzoom=15`, keeping the full **z0→15** sub-pyramid. Minzoom 0 is a **clustered-source efficiency requirement**, not a completeness preference.

**Build-URL resolution at run time — never hardcoded, never hotlinked from a client.** `compiler/tiles.py` lifts the resolver already proven in `scripts/fetch-basemap.sh`: `HEAD https://build.protomaps.com/<YYYYMMDD>.pmtiles`, walking back from today up to **7 days**, take the first that answers, record the resolved date as **`TileSourceV1.build_date`**, and **fail loudly** if none answers. Protomaps' own recommendation — copy the tileset to your own Cloud Storage — is the **M2 mirror** once compile volume justifies it.

**Buffer = 1 km geodesic** around the union of stop points and leg geometries, then the bbox of that, **with a minimum bbox span of 2 km**. Computed with **shapely 2.x**: `unary_union` over the leg `LineString`s + stop points, buffered in a **local projected CRS**, envelope reprojected back to **EPSG:4326** — **never a naive degree offset**, which is anisotropic away from the equator.

*Why 1 km and not tighter:* the demo-day extract measures **~1.3 MB against a 200 MB budget**, so margin here is essentially free and a tight number buys nothing. 500 m is roughly six minutes of straying at a sightseeing pace, which is thin for the FR-019 recovery promise; and a compact single-block day can otherwise yield a bbox **smaller than the map viewport**, which the 2 km minimum span prevents. This is also the *same* bbox the pruned walk graph is cut to, so offline recovery routing can never route off the edge of the map.

**maxzoom = 15** because that is the **Protomaps build ceiling**, not a budget choice. Each zoom level is ≈2× the size, so `--maxzoom=14` is the documented lever if a metro day ever breaches ≤200 MB; M1 holds z15 for old-town legibility at walking scale.

**Size — this repo's own measurement, not a rule of thumb.** `scripts/fetch-basemap.sh` extracts the Rhodes window `28.20,36.42,28.25,36.46` (≈4.5 × 4.4 km) at z0–15 to **~1.3 MB**, plus ~1.5 MB of glyphs and sprites. A compact old-town day is therefore **single-digit MB — two orders of magnitude inside SC-007's ≤200 MB**. (The stack reference's 10–40 MB figure is a conservative 5×5 km rule of thumb; the measured extract is the better number.) **The size budget is not the binding constraint at M1**, which is precisely what makes the generous buffer affordable.

**Licensing.** The Protomaps *pipeline* is BSD and the glyphs are OFL, but **the tile data is OSM**: an extract is a **Produced Work** under ODbL, carrying **"© OpenStreetMap contributors"** rendered on every map plus an ODbL entry in the bundle's regenerated `ATTRIBUTION.md` (Constitution V). Attribution is not a footer we may drop because the map is offline.

**The ADR-0003 invariant is restated here because this archive is the artifact that would break it:** the `.pmtiles` file is **runtime-fetched into OPFS**. It is **never `import`ed into Vite's asset graph and never placed in `web/public/`** — it must not be hashed by the build, precached by Workbox, or shipped in `dist/`. It is downloaded once by the bundle download manager and read by byte range from an OPFS file handle.

### Consequences

- Good: FR-016 is a computed geodesic rule with a floor, not a hand-tuned bbox; the tile payload is measured in single-digit MB against a 200 MB budget, leaving enormous headroom for narration, glyphs and the walk graph.
- Good: run-time resolution means a bundle always draws from a fresh build and cannot rot in place; `build_date` on `TileSourceV1` makes any bundle's basemap vintage auditable.
- Good: the tile bbox and the pruned walk-graph bbox are the same geometry, so FR-019 recovery cannot route past the map.
- Bad / accepted cost: the 7-day walk-back is **our own mechanism, not an upstream contract** — Protomaps could change the URL scheme and break it. Contained by being one function with one call site that **fails loudly** rather than emitting an empty archive.
- Bad / accepted cost: keeping z0 and a 1 km buffer both trade bytes we measurably have for robustness we would otherwise have to re-litigate in the field.
- **Carried-forward gap, flagged not solved here:** the dev script prunes glyph ranges to **U+0000–U+04FF**. The *compiled* bundle must select ranges from the **area's own label scripts**, or SC-009 (genericity against a second area of different character) fails **silently** on a Hebrew/Arabic/CJK area — labels render as nothing. A **DU-05 compile requirement**, not a styling nicety.

### Confirmation

- **`tests/test_compiler_tiles.py`** (Tier 1, deterministic): the resolver selects a build **inside the retention window** and **raises when none answers**; the buffer is asserted against a **known metric distance** (projected, not degrees), including the 2 km minimum span on a single-block day; `TileSourceV1` fields validate against `docs/data/tile-source.md`.
- **Tier 2 compile contract test** asserts the demo-day bundle is **< 20 MB** total — a real ceiling well under SC-007, so a regression shows up as a failure rather than as headroom quietly consumed.
- **`evals/test_genericity.py`** runs the same extract path for a **second area** (SC-009) with no place-specific constants.
- **ADR-0003 precache-leak tripwire** (`maximumFileSizeToCacheInBytes`) remains the guard that the archive never enters the Workbox precache or the Vite asset graph.
- **`tests/test_compiler_attribution.py`**: a bundle containing a `TileSourceV1` must carry the ODbL + "© OpenStreetMap contributors" credit.
- **TODO (lands with DU-05):** `compiler/tiles.py` (the resolver lifted out of `scripts/fetch-basemap.sh` so there is exactly one implementation), `tests/test_compiler_tiles.py`, the area-derived glyph-range selection, and the go-pmtiles CLI version **resolved-then-pinned at implementation** (ADR-0007).
