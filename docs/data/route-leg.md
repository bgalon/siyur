# Schema card — route leg (`RouteLegV1`)

*A precomputed walking leg between two stops. Embedded in `ItineraryV1.legs` and frozen into `BundleManifestV1.routing.legs`;
not a standalone commons table in M1. Authoritative context: `docs/design/tech-design.md` §1.3–1.4, §5.3, and the routing
components in `methods-stack-reference.md` §5. The routing engine choice (Valhalla) is **ADR-0020** — read this card, not
guesswork, for the field shape.*

- **Schema version:** `RouteLegV1` (`schema_ver` literal where serialized standalone; embedded legs may omit it).
  **`RouteLegV1` is the type's only name** — `ItineraryV1.legs` is a list of `RouteLegV1`, and any card, spec or
  docstring writing bare `RouteLeg` means this. This card wins on the naming as it does on the fields.
- **Producer:** **Valhalla**, built per-area during compile (MIT; pedestrian costing) → decoded leg geometry + time.
  The bundle also ships a **pruned walking network** + **geojson-path-finder** (ISC) for offline off-route recovery;
  the straight-line fallback is last resort. No maintained OSRM/Valhalla-wasm exists → precomputed legs carry the
  experience (stack reference §5).
- **CRS:** **EPSG:4326 (lon, lat)**. Geometry type: **LineString** (ordered `[lon, lat]` vertices, Valhalla-decoded polyline).
  The LLM never emits geometry or does spatial arithmetic — Valhalla / shapely do (AGENTS.md geo rules).
- **Units:** `distance_m` in **metres**, `duration_s` in **seconds** (walking, at Valhalla's default pedestrian speed).
- **Timezone:** legs are duration-only (no wall-clock of their own); the itinerary `timeline` places them in **area-local**
  time. No timezone field on the leg itself.
- **License & provenance:** leg geometry and times are a **Produced Work derived from OSM → ODbL** (routing runs over OSM
  data): `bundleable=true`, and **ODbL attribution ("© OpenStreetMap contributors") renders on every map**. License pointer
  → [`/DATA-LICENSES.md`](../../DATA-LICENSES.md) (ODbL row). The engine (Valhalla) is MIT — a code dependency, not bundled data.
- **The routing `SourceRef` convention — stated here, not left to an example.** Every M1 leg carries exactly:
  `kind: "osm"` · `id: "valhalla:pedestrian"` · `url: null` · `license: "ODbL-1.0"` ·
  `attribution: "© OpenStreetMap contributors"`. `kind` is `osm` because the *data* the leg derives from is OSM;
  **there is no `SourceKind` for a routing engine and none is being added** — the engine is named inside `id`
  (`<engine>:<costing>`), which is where a produced work records the machinery without claiming to be a source. This
  stamp is precisely what makes a leg bundleable: `commons/licenses.py::bundleable("osm", "ODbL-1.0")` is `True`,
  **derived, never author-set**. A leg with any other `kind`/`license` pair is a defect, not a variation.
  **`id` names the engine that actually ran, and only `kind`/`license`/`attribution` are fixed.**
  `valhalla:pedestrian` is the M1 *production* stamp because Valhalla is the M1 engine (ADR-0020);
  a leg produced by the sanctioned OpenRouteService dev fallback is stamped
  `openrouteservice:foot-walking`, because stamping it `valhalla:pedestrian` would be a
  **false provenance claim about machinery that never ran** — and provenance that lies is worse
  than provenance that varies. Both route over OSM, so `kind="osm"`, ODbL and the ODbL
  attribution are identical either way, and `bundleable` derives the same.
  **`RouteLegV1` has no `bundleable` field to read** — like `Story` ([`poi-site.md`](./poi-site.md)), `ResolvedArea` and
  `AreaCandidate` ([`area.md`](./area.md)), it carries a bare `SourceRef`. The quarantine filter must therefore derive
  structurally — *only a `SourcedValue` carries a `bundleable` field* — not special-case `Story`. A filter that reads
  the attribute off a leg finds nothing and **drops every walking leg from every bundle**, past every hash and path
  check, leaving a day with no routes.

## `RouteLegV1` fields

| Field | Type | M1? | Units / notes |
|---|---|---|---|
| `id` | `str` | M1 | leg id, unique within the itinerary (e.g. `leg-0`) |
| `from_stop` | `int` | M1 | `order` of the origin `Stop` in `ItineraryV1.stops` |
| `to_stop` | `int` | M1 | `order` of the destination `Stop` |
| `mode` | `"walk"` | M1 | walking only in M1 (pedestrian costing) |
| `geometry` | `LineString` (EPSG:4326) | M1 | decoded leg polyline; `[[lon,lat], …]` |
| `distance_m` | `float` | M1 | metres |
| `duration_s` | `int` | M1 | seconds (walking) |
| `source` | `SourceRef` | M1 | the fixed routing convention above: `kind="osm"`, `id="valhalla:pedestrian"`, `license="ODbL-1.0"`, attribution "© OpenStreetMap contributors" → `bundleable=true` (derived) |
| `schema_ver` | `"RouteLegV1"` | M1 | literal (when serialized standalone) |
| `variant` | `"B" \| "C" \| null` | M2+ | which Plan variant this leg belongs to (base plan = null) |

**Offline recovery network (bundle, not the leg):** the pruned walking graph frozen into the bundle is a
**topologically-noded GeoJSON line network** (EPSG:4326) consumed by geojson-path-finder for on-device deviation recovery.
It is a separate artifact in the manifest (`routing.walk_graph`), also ODbL, `bundleable=true`. Approximate (no turn
restrictions, naive costing) by design — see [`bundle-manifest.md`](./bundle-manifest.md) and stack reference §5.

## Example rows

```jsonc
// 1 — base-plan walking leg between two stops (short old-town hop)
{
  "id": "leg-0", "schema_ver": "RouteLegV1", "mode": "walk",
  "from_stop": 0, "to_stop": 1,
  "distance_m": 380, "duration_s": 300,
  "geometry": { "type": "LineString", "coordinates": [
    [28.2247, 36.4443], [28.2242, 36.4446], [28.2238, 36.4447] ] },
  "source": { "kind": "osm", "id": "valhalla:pedestrian",
    "url": null, "license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors" }
}

// 2 — longer leg (illustrates duration/distance in a feasibility check)
{
  "id": "leg-1", "schema_ver": "RouteLegV1", "mode": "walk",
  "from_stop": 1, "to_stop": 2,
  "distance_m": 1240, "duration_s": 960,
  "geometry": { "type": "LineString", "coordinates": [
    [28.2238, 36.4447], [28.2251, 36.4459], [28.2277, 36.4471] ] },
  "source": { "kind": "osm", "id": "valhalla:pedestrian", "url": null,
    "license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors" }
}

// 3 — M2+ leg belonging to Plan variant B
{
  "id": "leg-B0", "schema_ver": "RouteLegV1", "mode": "walk", "variant": "B",
  "from_stop": 0, "to_stop": 1,
  "distance_m": 520, "duration_s": 410,
  "geometry": { "type": "LineString", "coordinates": [
    [28.2247, 36.4443], [28.2255, 36.4440], [28.2260, 36.4436] ] },
  "source": { "kind": "osm", "id": "valhalla:pedestrian", "url": null,
    "license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors" }
}
```
