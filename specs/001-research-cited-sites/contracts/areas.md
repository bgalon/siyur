# Contract — Areas (delimit, resolve, coverage)

**Service**: `api/areas.py` (FastAPI). **Auth**: all endpoints require a valid Google-OIDC bearer token (JWT-verify dependency → `user_id`); unauthenticated → `401`. **CRS**: every coordinate is EPSG:4326 (lon,lat). Maps to **FR-001, FR-006, US1, US2**.

## POST /areas — delimit + resolve + coverage check

Resolve a user-delimited area to a polygon and report existing commons coverage (drives reuse/refresh, US2).

**Request** (one of `name` or `bbox`/`polygon`):
```jsonc
{
  "name":   "Rhodes medieval old town",          // optional — resolved via Overture divisions (+ Nominatim disambig)
  "bbox":   [28.216, 36.440, 28.232, 36.451],    // optional — [minLon,minLat,maxLon,maxLat] EPSG:4326
  "polygon": { "type": "Polygon", "coordinates": [ /* … */ ] }  // optional — GeoJSON, EPSG:4326
}
```

**Response `200`**:
```jsonc
{
  "area_id": "…-uuid",
  "polygon": { "type": "Polygon", "coordinates": [ /* resolved */ ] },  // EPSG:4326
  "coverage": {
    "known_site_count": 42,           // ST_Within(geom, polygon) count
    "covered": true,                   // known_site_count > 0
    "stalest_observed_at": "2026-07-10", // min observed_at among covered records; null if none
    "refresh_available": true          // always true when covered (US2 / FR-006)
  }
}
```

- `covered=true` ⇒ the client SHOULD show existing cited data (via `GET /sites`) **without** triggering research, and surface a **refresh** affordance.
- **Genericity**: `name`/`bbox`/`polygon` are user input; no place is hardcoded (FR-001). Rhodes is only the demo default.
- **Errors**: `401` unauthenticated · `422` neither name nor geometry given / invalid geometry · `404` name not resolvable (with disambiguation candidates when Nominatim returns several).

## Contract tests (T2 component — over real PostGIS)

- Authenticated `POST /areas` with the Rhodes bbox returns a polygon and `known_site_count` = the `ST_Within` count (fixture-seeded).
- Re-`POST` the same area after research ⇒ `covered=true`, `refresh_available=true` (backs `test_commons_reuse_dedupe`, ADR-0008).
- Unauthenticated ⇒ `401` (backs the auth half of `test_commons_write_shared`, ADR-0008).
- Invalid/empty geometry ⇒ `422`.
