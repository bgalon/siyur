# DATA-LICENSES.md — Siyur data-license registry

*The registry for **data** licensing. Code licensing is separate (REUSE v3.3 / `LICENSES/` / SPDX headers). This file is
the machinery behind **Constitution Article V — provenance is mechanical**: every curated value is stamped
`source + license + bundleable` at ingestion (`SourcedValue`, see [`docs/data/poi-site.md`](docs/data/poi-site.md)); the
narration and bundle steps **refuse unstamped input** and **refuse anything `bundleable=false`**. `ATTRIBUTION.md` is
**regenerated per bundle** from this registry; ODbL attribution renders on every map.*

Columns: **Source** · **License (SPDX)** · **In-app attribution required** · **Share-alike implications** ·
**Bundleable?** · **Date checked**.

## Registry

| Source | License (SPDX) | In-app attribution required | Share-alike implications | Bundleable? | Date checked |
|---|---|---|---|---|---|
| **OpenStreetMap** (Overpass long-tail tags; the data behind Protomaps tiles, Overture ODbL themes, and Valhalla routing) | **ODbL-1.0** | **Yes — "© OpenStreetMap contributors" on every rendered map (control corner) + credits screen**, per OSMF Produced-Work guidelines | **Share-alike applies to a derived _database_** — an offline bundle compiled from OSM plausibly is one; attribution designed in from day one | ✅ yes | 2026-07-25 |
| **Overture Maps — places** (POI backbone; sources incl. Meta, Microsoft, Foursquare) | **CDLA-Permissive-2.0** | No (courtesy cite) | None — no share-alike; **safe to bundle** (keeps bundle POI data lawyer-free). ⚠ per-record licenses can differ within a theme (some Foursquare rows Apache-2.0) — read the per-source stamp, not the theme | ✅ yes | 2026-07-25 |
| **Overture Maps — divisions / transportation / buildings / base** (boundary polygons, etc.) | **ODbL-1.0** | **Yes — "© OpenStreetMap contributors, Overture Maps Foundation"** | Same DB share-alike as OSM | ✅ yes | 2026-07-25 |
| **Protomaps daily basemap builds** (PMTiles extract → offline tiles) | build pipeline/styles **BSD-3-Clause**; **data © OSM → ODbL** | ODbL attribution as above | ODbL Produced-Work rules apply to the tiles | ✅ yes (don't hotlink build URLs from clients) | 2026-07-25 |
| **PMTiles spec / reader** (`pmtiles` JS, go-pmtiles) | **BSD-3-Clause** | No | None (code) | ✅ yes (format) | 2026-07-25 |
| **Wikidata** (facts, IDs, coords, image pointers, heritage status) | **CC0-1.0** | No (courtesy credit) | None — the preferred machine-facts source, zero obligations | ✅ yes | 2026-07-25 |
| **Wikivoyage** (listings + narrative prose for stories/narrations) | **CC-BY-SA-4.0** | **Yes — per-article attribution link (URL/authors)** in `ATTRIBUTION.md` and on the narration | **Share-alike is viral on the _text_:** bundled narrations derived from the prose must themselves be **CC-BY-SA-4.0** (PRD §7 posture (a) — embrace it). Does **not** infect app code, style, or tiles. Coords/names are facts, not share-alike | ✅ yes (text stamped CC-BY-SA) | 2026-07-25 |
| **Wikipedia** (GeoSearch + summary extracts) | **CC-BY-SA-4.0** | **Yes — per-article attribution** | Same text share-alike as Wikivoyage | ✅ yes (text stamped CC-BY-SA) | 2026-07-25 |
| **Wikimedia Commons — images** (usually via Wikidata P18) | **per-file** (capture at compile) | **Yes — author + license + source URL on the POI card + bundle credits** | Capture `{file, author, license, licenseUrl, sourceUrl}`; **exclude NC/ND**; CC-BY-SA images keep share-alike on modification. Bundleable **only if** the per-file license is PD/CC0/CC-BY/CC-BY-SA | ⚠ per-file — never bundle by URL pattern alone | 2026-07-25 |
| **opening_hours.js** (feasibility evaluator) | **LGPL-3.0** | Note in credits | Use as an **unmodified npm dependency**; publish source of any modifications; don't fork-and-embed silently | ✅ yes (as a dependency) | 2026-07-25 |
| **Noto glyphs / sprites** (bundled fonts) | **OFL-1.1** | Keep `OFL.txt` in the bundle | Don't sell fonts standalone | ✅ yes | 2026-07-25 |
| **Valhalla** (routing engine, per-area build at compile) | engine **MIT**; routed data **© OSM → ODbL** | ODbL attribution on the map | Leg geometry/times are an OSM Produced Work → ODbL | ✅ yes (legs are ODbL data; engine is a code dep) | 2026-07-25 |
| **Review providers** (ratings/summaries, PRD §13 #2 — open) | **proprietary** | link only ("needs connectivity") | — | ❌ **never** (`bundleable=false`, always) | 2026-07-25 |
| **Open web** (search snippets, ranking signal at planning time) | **all-rights-reserved / proprietary** | — | Fine as **ranking signal**; nothing fetched from the open web may be **copied into the bundle** | ❌ **never** (`bundleable=false`, always) | 2026-07-25 |
| **User data** (plans, notes, preferences, identity) | **user-owned** | — | Personal data is **per-user and private, never bundled as personal data**; never auto-published to the commons (PRD §13 #4) | ❌ (privacy, not license) | 2026-07-25 |

## The quarantine rule (enforced, merge-blocking)

A value may be stamped `bundleable=true` **only if** its `source.license` ∈
**{ODbL, CDLA-Permissive-2.0, Apache-2.0, CC0, CC-BY-4.0, CC-BY-SA-4.0, PD, OFL, LGPL-as-dependency}**.
`open_web` and `review_provider` sources are **always** `bundleable=false`. **No bundle may contain a `bundleable=false`
value.** This is the invariant `SourcedValue` exists to carry (tech-design §1.0); it is verified by
`evals/test_structural.py::test_no_unbundleable_in_bundle` and by the DU-06 airplane-mode e2e (zero network requests).

**Apache-2.0 (added 2026-08-01, ADR-0012).** Overture places mixes licenses *within* one theme — in the committed
200-row Rhodes fixture, 165 rows are CDLA-Permissive-2.0, **33 are Apache-2.0** (Foursquare-sourced) and 2 are CC0-1.0.
Omitting Apache-2.0 silently dropped **16.5%** of Overture places from every bundle, while ODbL — which carries
share-alike and is *more* restrictive — was allowlisted. Apache-2.0 is permissive and bundle-safe; its §4 obligations
are **retain the copyright/patent/trademark/attribution notices, ship a copy of the license, and reproduce any NOTICE
file contents**. The DU-05 ATTRIBUTION pipeline discharges these the same way it already does for ODbL and CC-BY; the
NOTICE-reproduction step is the one new mechanic Apache-2.0 adds, and it is a DU-05 acceptance criterion.

## API terms (planning-time only — respect, cache, honest User-Agent)

| Service | Terms |
|---|---|
| **Nominatim** (disambiguation fallback) | max 1 req/s, real User-Agent, no bulk (OSMF policy) |
| **Overpass** (long-tail tags) | fair use (~10k queries/day, 2 concurrent on the public instance; kumi mirror more permissive) — the commons cache is a reliability mechanism, not just cost |
| **OSRM demo / ORS** | OSRM demo not for production; ORS free key ~2,500 req/day (early-dev fallback only) |

## Reference-only (do NOT vendor)

| Source | License | Note |
|---|---|---|
| **Headway** (whole-stack routing reference) | **AGPL** | Architecture reference only — read, don't paste/vendor |

---

*Registry sources: `docs/planning/methods-stack-reference.md` §B (license obligations register) + §4–5; Overture
attribution page; OSMF Produced-Work attribution guidelines. Update the **Date checked** column whenever a source's terms
are re-verified. This registry is the single pointer target for every `docs/data/*` schema card.*
