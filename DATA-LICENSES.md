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
| **Protomaps daily basemap builds** (PMTiles extract → offline tiles; `pmtiles extract` per itinerary bbox at compile, ADR-0021) | build pipeline/styles **BSD-3-Clause**; **data © OSM → ODbL** | ODbL attribution as above — carried in `TileSourceV1.attribution`, rendered on every map | **ODbL Produced Work:** the extracted `.pmtiles` in the bundle is a produced work of OSM and inherits ODbL + its attribution obligation; the BSD build pipeline adds nothing viral | ✅ yes (don't hotlink build URLs from clients; **resolve the build URL at run time** — builds are retained ~1 week) | 2026-08-07 |
| **PMTiles spec / reader** (`pmtiles` JS, go-pmtiles) | **BSD-3-Clause** | No | None (code) | ✅ yes (format) | 2026-07-25 |
| **Wikidata** (facts, IDs, coords, image pointers, heritage status) | **CC0-1.0** | No (courtesy credit) | None — the preferred machine-facts source, zero obligations | ✅ yes | 2026-07-25 |
| **Wikivoyage narration** (listings + narrative prose adapted into `Story.text_by_lang`; MediaWiki Action API, ADR-0024) | **CC-BY-SA-4.0** | **Yes — per-article _and_ per-revision attribution**: title + canonical URL + the `revid` adapted, in `ATTRIBUTION.md` **and** on the narration itself. Captured at ingestion into the `Story.source` `SourceRef`; `attribution` is non-null and required | **Share-alike is viral on the _text_:** an adapted story is a derivative work, so **the bundled story text is itself CC-BY-SA-4.0 and the bundle says so** (PRD §7 posture (a) — embrace it). Does **not** infect app code, style, tiles or itinerary data. Coords/names are facts, not share-alike | ✅ yes (text stamped CC-BY-SA; bundleability is **derived** from this row, not read off the `Story` — see [`poi-site.md`](docs/data/poi-site.md)) | 2026-08-07 |
| **Wikipedia narration** (GeoSearch + summary extracts, same adapter path) | **CC-BY-SA-4.0** | **Yes — per-article + per-revision attribution**, as above | Same text share-alike as Wikivoyage | ✅ yes (text stamped CC-BY-SA) | 2026-08-07 |
| **Wikimedia Commons — images** (usually via Wikidata P18) | **per-file** (capture at compile) | **Yes — author + license + source URL on the POI card + bundle credits** | Capture `{file, author, license, licenseUrl, sourceUrl}`; **exclude NC/ND**; CC-BY-SA images keep share-alike on modification. Bundleable **only if** the per-file license is PD/CC0/CC-BY/CC-BY-SA | ⚠ per-file — never bundle by URL pattern alone | 2026-07-25 |
| **`opening-hours-py`** (feasibility evaluator; PyPI dist `opening_hours_py`, imports as `opening_hours` — bindings over the Rust `opening-hours` crate) | **MIT OR Apache-2.0** | Note in credits (retain the copyright + license text of whichever arm is elected) | **None — permissive, no copyleft.** Under Apache-2.0 §4, reproduce NOTICE contents if the wheel ships one; nothing propagates to `commons/` or to bundled data | ✅ yes (as a dependency) | 2026-08-07 |
| **Noto glyphs / sprites** (bundled fonts) | **OFL-1.1** | Keep `OFL.txt` in the bundle | Don't sell fonts standalone | ✅ yes | 2026-07-25 |
| **Valhalla** (routing engine, per-area graph built at compile, ADR-0020) | engine **MIT** (a **code dependency, not bundled data**); routed data **© OSM → ODbL** | ODbL attribution on the map | **Leg geometry/times and the pruned walk graph are an OSM Produced Work → ODbL.** MIT on the engine propagates nothing to the output; every `RouteLegV1.source` is stamped `kind="osm"`, `id="valhalla:pedestrian"`, `license="ODbL-1.0"` ([`route-leg.md`](docs/data/route-leg.md)) | ✅ yes (legs + graph are ODbL data; engine is a code dep) | 2026-08-07 |
| **Review providers** (ratings/summaries, PRD §13 #2 — open) | **proprietary** | link only ("needs connectivity") | — | ❌ **never** (`bundleable=false`, always) | 2026-07-25 |
| **Open web** (search snippets, ranking signal at planning time) | **all-rights-reserved / proprietary** | — | Fine as **ranking signal**; nothing fetched from the open web may be **copied into the bundle** | ❌ **never** (`bundleable=false`, always) | 2026-07-25 |
| **User data** (plans, notes, preferences, identity) | **user-owned** | — | Personal data is **per-user and private, never bundled as personal data**; never auto-published to the commons (PRD §13 #4) | ❌ (privacy, not license) | 2026-07-25 |

## The quarantine rule (enforced, merge-blocking)

A value may be stamped `bundleable=true` **only if** its `source.license` ∈
**{ODbL, CDLA-Permissive-2.0, Apache-2.0, MIT, CC0, CC-BY-4.0, CC-BY-SA-4.0, PD, OFL, LGPL-as-dependency}**.
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

**The opening-hours evaluator changed, and the obligation got *smaller* (2026-08-07, ADR-0022).** The registry used to
carry `opening_hours.js` / **LGPL-3.0**, used as an unmodified npm dependency with the "publish source of any
modifications, don't fork-and-embed" discipline that copyleft-lite imposes. It is replaced by **`opening-hours-py`** /
**MIT OR Apache-2.0** — a permissive, actively-maintained, Rust-backed *Python* binding. **This is a reduction in
obligation, not a new one:** the LGPL row disappears, no copyleft term reaches `commons/`, and the second (JS) runtime
that an LGPL npm dependency would have dragged into every compile and CI job disappears with it. What remains is
ordinary permissive housekeeping — retain the notice, ship the license text, reproduce a NOTICE file if the wheel has
one. Two things deliberately did **not** change: `LGPL-3.0` stays in the bundleable allowlist below (it is the
allowlist's general "as-a-dependency" arm, not a row about this one library), and **`opening_hours_js` remains a live
`SourceKind`** — it now names *deterministic opening-hours evaluation*, whatever engine backs it (renaming an enum
value in stored stamps is a `SiteRecordV2` concern). This row is a **code dependency**; no value in the commons is
stamped with it either way. (`MIT` *is* now allowlisted as a data license — see below — but that was decided on its own
merits, not because of this row.)

**MIT (added 2026-08-07, ADR-0026).** Added on a **consistency** argument rather than a measured loss, which makes it
the first entry here without a data source behind it — stated plainly because the allowlist's discipline until now was
"add only on demonstrated need" (Apache-2.0, above). The argument: **Apache-2.0 is allowlisted and MIT is strictly more
permissive than Apache-2.0.** Apache-2.0 carries a patent grant, a modification-notice requirement and the NOTICE-file
reproduction obligation; MIT carries one obligation — retain the copyright notice and the license text. Allowlisting
the more-encumbered license while quarantining the less-encumbered one cannot be defended: an MIT-stamped value would
be refused from a bundle that happily carries Apache-2.0 beside it. The obligation MIT does carry is real and the
DU-05 ATTRIBUTION pipeline discharges it the same way it discharges Apache-2.0's — reproduce the copyright line and
ship the license text. **MIT is permissive, not public domain; "no obligation" would be the wrong summary.**

## API terms (planning-time only — respect, cache, honest User-Agent)

| Service | Terms |
|---|---|
| **Nominatim** (disambiguation fallback) | max 1 req/s, real User-Agent, no bulk (OSMF policy) |
| **Overpass** (long-tail tags) | fair use (~10k queries/day, 2 concurrent on the public instance; kumi mirror more permissive) — the commons cache is a reliability mechanism, not just cost. **We default to the `overpass.kumi.systems` mirror, honour `Retry-After`, and back off exponentially (ADR-0027)** after a real pass lost every OSM `relation` to a `429` on the main instance. Being a well-behaved guest is the fair-use relationship working, not a courtesy. **ADR-0028 proposes removing this dependency entirely** by reading OSM from the per-area PBF already fetched for Valhalla — deferred until after M1 |
| **OSRM demo / ORS** | OSRM demo not for production; ORS free key ~2,500 req/day (early-dev fallback only) |

## Reference-only (do NOT vendor)

| Source | License | Note |
|---|---|---|
| **Headway** (whole-stack routing reference) | **AGPL** | Architecture reference only — read, don't paste/vendor |

---

*Registry sources: `docs/planning/methods-stack-reference.md` §B (license obligations register) + §4–5; Overture
attribution page; OSMF Produced-Work attribution guidelines. Update the **Date checked** column whenever a source's terms
are re-verified. This registry is the single pointer target for every `docs/data/*` schema card.*
