# Contract — Bundles (compile an approved plan, read its manifest, fetch its artifacts)

**Service**: `api/bundles.py` → drives `compiler/pipeline.py` (tech-design §5.3). **Auth**: authenticated session required (`api/security.py::require_user`); unauthenticated → `401`. **Scope**: a bundle inherits its plan's row scope — another user's `bundle_id` is a **`404`, never a `403`**. **Transport**: SSE for compile, so the user watches a multi-minute pipeline instead of a spinner. **CRS**: all bundled geometry EPSG:4326 (lon,lat). Schema: [`docs/data/bundle-manifest.md`](../../../docs/data/bundle-manifest.md) + [`tile-source.md`](../../../docs/data/tile-source.md) — the card wins. Maps to **FR-010…FR-016, FR-020, FR-021, US2**.

## POST /bundles — compile an approved plan (SSE)

**Request**: `{ "plan_id": "7be2…-uuid" }`

**`409` if the plan is not approved.** This endpoint is the mechanical enforcement of the HITL gate: `{"error":"plan_not_approved","state":"proposed"}`. There is no flag, no override and no internal path that compiles a `proposed` plan (FR-006 / SC-003).

**Response**: `200` `text/event-stream`. Stages are **ordered** and emitted in the §5.3 order — the pipeline reads as its own spec:
```
event: status    data: {"phase":"tiles","bbox":[28.216,36.440,28.232,36.451],"maxzoom":15,"bytes":4194304}
event: status    data: {"phase":"routes","legs":4,"walk_graph_edges":812,"connected":true}
event: status    data: {"phase":"quarantine","values_dropped":12,
                        "withheld":[{"site_id":"c9d1…","field":"reviews",
                                     "reason":"license_forbids_redistribution"}]}
event: status    data: {"phase":"content","sites":5,"narrations":3}
event: status    data: {"phase":"attribution","licenses":["ODbL-1.0","CDLA-Permissive-2.0","CC-BY-SA-4.0"]}
event: status    data: {"phase":"hash","artifacts":4}
event: manifest  data: { /* the complete BundleManifestV1 */ }
event: done      data: {"bundle_id":"bnd_01J…","size_bytes":5242880,
                        "budget_bytes":209715200,"over_budget":false}
```

**Invariants asserted on the stream / frozen output**:
- **Quarantine removes, it does not flag.** Every `bundleable=false` value is dropped before anything is frozen; **no such value appears anywhere in the bundle** (FR-011 / SC-004, merge-blocking). Unstamped input is **refused**, never bundled (FR-012). The `withheld[]` list records **what was withheld and why** so the travel UI can present it as *needing connectivity* rather than as an error or a blank (FR-021).
- **A place stripped bare still ships.** If quarantine removes everything a site had beyond its stamped core, the site still appears with whatever survives (edge case).
- **`attribution` is regenerated per bundle**, never carried over: `ATTRIBUTION.md` names every credit the bundled licenses require — "© OpenStreetMap contributors" for any OSM-derived data **including the Valhalla legs and the walk graph** (Produced Work), and **per-article CC BY-SA credit** for every bundled story (FR-015 / SC-010; see [`narration.md`](./narration.md)).
- **Integrity is per artifact and over the whole.** SHA-256 on `tiles.pmtiles.sha256`, `routing.sha256`, `content.sha256`, then `integrity.manifest_sha256` over the manifest — verifiable **offline** at launch (FR-013 / FR-020).
- **Tiles are scoped to the day**: `tiles.pmtiles.bbox` is the tight itinerary bbox **plus a stray margin**, and no more (FR-016).
- **Walk-graph connectivity is asserted**, not assumed: `connected:false` fails the compile rather than shipping silently disconnected islands (plan.md risk 3).

**Errors**: `401` unauthenticated · `404` unknown `plan_id` **or another user's plan** · `409` plan not approved · `409` a compile is already running for this plan (idempotency guard) · in-band `event: error` for a mid-stream failure, after which nothing is persisted and no partial bundle is published.

## GET /bundles/{bundle_id}/manifest — the airplane-mode contract

**Response `200`**: a `BundleManifestV1` **verbatim per the card** — `bundle_id`, `itinerary_id`, `created_at`, `size_bytes`, `schema_ver`, `tiles`, `routing`, `content`, `attribution`, `integrity`. No field is added here.

- **`size_bytes` is reported before download begins** (FR-014 / SC-007): the client reads the manifest, shows the size, and only then fetches artifacts. A day over the ≤200 MB budget is therefore known **before** the first artifact byte moves.
- **Airplane-mode invariant**: *everything the travel UI reads resolves to a path in this manifest.* Tiles, legs, walk graph, sites, narrations, credits — each is a manifest path. A read that is not a manifest path is a bug the offline e2e is designed to catch (FR-017/FR-021, SC-005/SC-006).
- **Provenance invariant**: every value inside `content` carries its `source` + `license` stamp and every one is `bundleable=true`; the manifest's `attribution` path is the discharge of the credits those stamps require.
- **Errors**: `401` unauthenticated · `404` unknown `bundle_id` or a bundle belonging to another user.

## GET /bundles/{bundle_id}/artifacts/{path} — resumable artifact fetch

`{path}` is a path **taken from the manifest** (`tiles/rhodes.pmtiles`, `routing/walk_graph.geojson`, `content/sites.json`, `ATTRIBUTION.md`). A path absent from the manifest is a `404` **by construction** — the manifest is the index, not a hint.

- **Range-request friendly**: `Accept-Ranges: bytes` on every response; a `Range` request answers **`206 Partial Content`** with `Content-Range`, an unsatisfiable range answers **`416`**. `ETag` is the artifact's manifest `sha256` and `Content-Length` is exact, so an interrupted download resumes rather than restarting, and a **partially downloaded bundle is detected by integrity check and never treated as usable** (edge case / FR-013).
- **The same contract survives the DU-06 OPFS transport swap unchanged.** Per **ADR-0002** the bundle is the client's read model from day one: in M1 these bytes arrive over HTTP; at DU-06 the whole archive is downloaded into **OPFS** and the identical manifest paths are satisfied as local byte-range reads behind the same seam. **Nothing in this contract changes** — no path, no ETag semantics, no range behaviour. That is the entire point of ADR-0002: offline is a transport swap, not a read-path rewrite.
- **Errors**: `401` unauthenticated · `404` unknown bundle, another user's bundle, or a path not in the manifest · `416` unsatisfiable range.

## Contract tests (T1 unit + T2 component + T3 e2e)

- Compile a `proposed` plan ⇒ `409`; approve it, compile ⇒ `200` and a manifest (`tests/test_hitl_gate.py`).
- A fixture site carrying a `bundleable=false` value ⇒ **zero** occurrences of it anywhere under the bundle root, and a `withheld[]` entry naming it (`test_structural.py::test_no_unbundleable_in_bundle`, merge-blocking).
- An unstamped input value ⇒ compile refuses rather than freezing it (FR-012).
- Flip one byte in `content/sites.json` ⇒ the recomputed hash mismatches `content.sha256`, and a mutated manifest mismatches `integrity.manifest_sha256` — **integrity mismatch is detected, never silently accepted** (FR-013/FR-020).
- An OSM-derived bundle ⇒ `ATTRIBUTION.md` contains "© OpenStreetMap contributors"; a bundle with stories ⇒ one credit line per contributing article (SC-010).
- `Range: bytes=0-1023` on the PMTiles artifact ⇒ `206` with exactly 1024 bytes and the manifest `sha256` as `ETag`.
- Another user's `bundle_id` ⇒ `404`, byte-identical to the unknown-id response.

**Undetermined — flagged, not decided**: (1) `BundleManifestV1` has **no over-budget field**, so the ≤200 MB verdict is reported on the compile stream's `done` frame (`over_budget`/`budget_bytes`) and computed client-side from `size_bytes` on a bare `GET manifest`. If the budget verdict must be durable, the card needs a field — a card change, not a contract choice. (2) The card does not say **how `integrity.manifest_sha256` is computed over the manifest that contains it** (which serialization, and whether the `integrity` key is excluded); `data-model.md` must pin it or two implementations will disagree. (3) Whether bundle objects are served through the API or via a signed object-store URL is left to `compiler/storage.py`; this contract specifies the API path, which is what the client reads.
