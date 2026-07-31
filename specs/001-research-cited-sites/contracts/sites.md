# Contract — Sites (read cited records for the map)

**Service**: `api/sites.py`. **Auth**: bearer token required (commons is world-readable **to any signed-in user**). **CRS**: EPSG:4326. Maps to **FR-003, FR-004, FR-008, US1, US3**. Consumed by `web/src/map/sites.ts` to render markers + attribution chips.

## GET /sites?bbox=minLon,minLat,maxLon,maxLat

Return the cited `SiteRecordV1`s whose location falls within `bbox` (the map viewport / resolved area). Read-only; no research triggered.

**Response `200`**:
```jsonc
{
  "sites": [
    {
      "id": "…-uuid",
      "gers_id": "08f394…gers",
      "schema_ver": "SiteRecordV1",
      "names": {
        "en":     { "value": "Palace of the Grand Master", "source": {"kind":"overture","id":"08f394…","license":"CDLA-Permissive-2.0","attribution":null}, "bundleable": true, "confidence": 0.82, "observed_at": "2026-07-22" },
        "el":     { "value": "Ρολόι", "source": {"kind":"osm","id":"node/123456","license":"ODbL-1.0","attribution":"© OpenStreetMap contributors"}, "bundleable": true, "confidence": 0.7, "observed_at": "2026-07-20" },
        "el-Latn":{ "value": "Roloi", "source": {"kind":"osm","id":"node/123456","license":"ODbL-1.0","attribution":"© OpenStreetMap contributors"}, "bundleable": true, "confidence": 0.6, "observed_at": "2026-07-31" }
      },
      "location":   { "value": {"type":"Point","coordinates":[28.2247,36.4443]}, "source": {"kind":"overture","id":"08f394…","license":"CDLA-Permissive-2.0"}, "bundleable": true, "confidence": 0.9, "observed_at": "2026-07-22" },
      "categories": [ { "value": "attraction.castle", "source": {"kind":"overture","id":"08f394…","license":"CDLA-Permissive-2.0"}, "bundleable": true, "confidence": 0.8, "observed_at": "2026-07-22" } ],
      "address":       null,
      "opening_hours": null,
      "conflicts":     [],
      "updated_at":    "2026-07-22T09:00:00Z"
    }
  ],
  "attribution": ["© OpenStreetMap contributors"]   // union of required attributions across returned values
}
```

**Rendering contract (web)**:
- Each marker shows, per displayed value, a **source + license attribution chip** (FR-004). The chip text comes from the value's `source` (`kind`/`license`/`attribution`) — **the client never invents attribution**; it renders only what the stamp carries.
- The **display name** prefers a Latin/`en` form: `en` → `<lang>-Latn` → source-script, so an English-first user always sees a readable name (FR-008 / US3); the original-script value remains available on the record.
- Whenever **any** returned value has `kind="osm"` (or otherwise ODbL), the map renders **"© OpenStreetMap contributors"** in the attribution control (FR-004 / SC-002). `attribution[]` gives the client the exact strings.
- **No value without a `source` is ever returned** — the endpoint rejects unstamped rows at the DB boundary (FR-003 / SC-002).

**Errors**: `401` unauthenticated · `422` missing/malformed `bbox`.

## Contract tests (T1 schema + T2 component)

- Every value in every returned site has a non-null `source`; a synthetic unstamped row is never returned (provenance-completeness eval → SC-002 = 100%).
- A returned OSM-derived site ⇒ `"© OpenStreetMap contributors"` present in `attribution[]` (FR-004).
- A Greek-named fixture site returns both `el` and `el-Latn`, original preserved (FR-008).
- `bbox` filtering matches the PostGIS `geom && ST_MakeEnvelope(...)` / `ST_Within` result on the fixture.
