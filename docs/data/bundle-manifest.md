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
  an **enumerated** `reason`), which is what lets the travel UI render a withheld value as "needs connectivity" rather
  than as a blank or an error.
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
| `textLicense` | `str \| null` | M1 | the license of the **bundled narration text** — `"CC-BY-SA-4.0"` whenever any story is present, `null` when `content.narrations` is empty (ADR-0024). Adapted CC BY-SA prose is a derivative, so the bundle must **declare** share-alike, not merely credit the articles: attribution discharges BY, this discharges SA. Machine-readable on purpose — a reuser reading the manifest must not have to parse `ATTRIBUTION.md` prose to learn the terms |
| `withheld` | `[{ site_id: UUID, field: str, reason: WithheldReason }]` | M1 | what the **quarantine filter removed** and why — dotted `field` path (`reviews`, `notes[2]`, `names.el`); `reason` is a **closed enum**, never free text (below). Empty list = nothing withheld. Renders as "needs connectivity" in travel (FR-021) |
| `integrity` | `{ manifest_sha256 }` | M1 | launch-time check (iOS storage-eviction guard); canonicalization pinned below |
| `schematic` | `{ style_json, sha256 } \| null` | M2+ | illustrated-map render |

**`withheld[].reason` is a closed enum — free text is not permitted here** (ADR-0025 A3). `withheld` ships inside an
artifact the user downloads and can open, so the vocabulary is fixed at exactly what the affordance needs:

```
WithheldReason:
  "license_forbids_redistribution"   # the value's license (or its always-excluded source kind —
                                     #   open_web, review_provider) bars it from an offline bundle
  "source_unavailable"               # the value existed but could not be frozen at compile time
```

Naming *why* a value is missing is enough to render "needs connectivity"; a free-text reason would be a channel
through which a future withholding rule — including one touching the private side of the PRD §13 #4 boundary — could
carry content out inside a downloadable file. The set is closed here and extended only by ADR amendment.

**`withheld[].field` indices are `pre-removal`, and that must be stated because it is not guessable.** A dotted path
into a list (`notes[2]`) addresses the slot the value occupied **in the record as it stood before quarantine ran**.
Quarantine *removes* entries, so surviving `notes` re-index and `notes[2]` no longer names the same slot — or any
slot — in the bundled record. The travel UI therefore uses `field` to say *that* something was withheld and of what
kind, and must not use it to position a placeholder inside the surviving list.

**There is deliberately no `"unstamped"` member.** An earlier draft of this enum had one, which contradicted FR-012:
unstamped input is **refused** — the compile *fails* — it is not withheld and shipped as a placeholder. A value is
withheld **or** refused, never both, and giving "unstamped" a withheld reason would have turned a merge-blocking
refusal into a "needs connectivity" affordance that looks identical to success. Quarantine drops what it may not
redistribute; it does not launder a missing stamp.

**Integrity discipline — one hash per artifact, no shared hashes.** Each artifact is **SHA-256**'d individually:
`tiles.pmtiles.sha256`, `routing.walk_graph_sha256`, `routing.legs_sha256`, `content.sites_sha256`,
`content.narrations_sha256`, `content.itinerary_sha256`, `attribution.sha256`. Hashes are **lowercase hex of the raw
artifact bytes**. A hash that spans two files cannot say *which* one corrupted, so no group hash exists: every path in
this manifest has exactly one hash beside it. The whole manifest is then hashed into `integrity.manifest_sha256` and
checked at launch — the guard against silent OPFS eviction/corruption on iOS.

**The manifest hash is canonicalized by RFC 8785 (JCS) — a named standard, not a list of properties.**
`integrity.manifest_sha256` is the SHA-256 of the manifest serialized per
**[RFC 8785, JSON Canonicalization Scheme](https://www.rfc-editor.org/rfc/rfc8785)**, with the **`integrity` key
omitted** entirely before serialization (a hash cannot cover itself) — *removed*, not set to `null`, not set to an
empty string. The digest is lowercase hex.

**Why a named standard and not "sorted keys, UTF-8, no whitespace".** That description was this card's first attempt
and it is **insufficient in exactly the two ways Python and JavaScript disagree.** The writer is Python and the
launch-time verifier is TypeScript (T053), and they disagree by default:

```
PY  json.dumps(o, sort_keys=True, separators=(",",":"))
    {"attribution":"© OpenStreetMap contributors","bbox":[28.0,36.0,28.5,36.5]}
JS  JSON.stringify(o)
    {"attribution":"© OpenStreetMap contributors","bbox":[28,36,28.5,36.5]}
```

Both divergences are real, and they fail differently — which matters for how they'd be found:

- **Escaping fires on every manifest.** `json.dumps` defaults to `ensure_ascii=True` and escapes the `©` that every
  bundle carries in its ODbL attribution. Constant, universal.
- **Number formatting fires intermittently.** Python renders `28.0` where JavaScript renders `28`, but *only for
  integral-valued floats*: a real Rhodes bbox `[28.216, 36.44, …]` serializes identically on both sides. It fires when
  a `bbox` bound happens to land on a whole degree. **The intermittency makes this the worse of the two** — a hash
  that mismatches for some areas and not others reads like corruption, not like a serialization bug.

Sorted keys and stripped whitespace address neither. A manifest written by one side and verified by the other fails its
own launch check — **offline, on the traveller's device, with no way to diagnose it.**

RFC 8785 pins **string escaping and number formatting** as well as key ordering, which is precisely the gap. Verified
implementations exist on both sides and are pinned at implementation per ADR-0007: **`rfc8785`** (PyPI) and
**`canonicalize`** (npm, Apache-2.0). Hand-rolling either side re-opens the gap this paragraph exists to close.

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
  "textLicense": "CC-BY-SA-4.0",
  "withheld": [
    { "site_id": "c9d1…-uuid", "field": "reviews",
      "reason": "license_forbids_redistribution" }
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
  "textLicense": "CC-BY-SA-4.0",
  "integrity": { "manifest_sha256": "…" },
  "schematic": { "style_json": "schematic/style.json", "sha256": "…" }
}
```
