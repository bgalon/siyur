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
    "known_site_count": 42,             // ST_Within(geom, polygon) count — commons-wide
    "researched_fraction": 1.0,         // 0.0–1.0: how much of `polygon` has been researched
    "covered": true,                     // researched_fraction >= 0.99
    "stalest_observed_at": "2026-07-10", // min observed_at among covered records; null if none
    "refresh_available": true            // always true when covered (US2 / FR-006)
  }
}
```

- **`covered` means "this area has been researched", not "this area contains sites."** It is `researched_fraction >= 0.99` — the fraction of the requested polygon (by true surface area, WGS84) inside the union of research passes that have **completed** over it. Site count is not part of it: an area researched and found empty is covered (SC-006 — "nothing found here" is a correct result), and an area holding sites nobody researched *in that extent* is not.
  - **Changed in `0004_area_researched_at`.** `covered` was `known_site_count > 0`, which answered a different question and failed silently: the client delimits by map viewport, so panning out one step produced a polygon that still contained the already-researched sites and reported `covered: true` for a region nobody had looked at — the client then reused instead of researching (ADR-0018). The field's name and type are unchanged; its **derivation** is. Clients that only branch on `covered` keep working and get the right answer.
  - `researched_fraction` is **additive** and is the evidence behind `covered`. A client can now distinguish "fully covered" from "you are looking at 23 % researched ground", which the boolean alone cannot express. Areas researched before `0004` have no recorded completion and read as `0.0` — they will be researched once more, which is the safe direction.
  - **Scope**: the researched extent is read from the caller's own `area` rows (`created_by`, ADR-0015). `known_site_count`/`stalest_observed_at`/`GET /sites` remain commons-wide and shared (ADR-0008), so another user's records are always visible — what is not inherited is the *reuse decision*. Widening this is a privacy question reserved to PRD §13 #4.
- `covered=true` ⇒ the client SHOULD show existing cited data (via `GET /sites`) **without** triggering research, and surface a **refresh** affordance.
- `covered=false` with `known_site_count > 0` is a normal, meaningful state: show the records that exist **and** research, because most of what was asked about has not been looked at.
- **Genericity**: `name`/`bbox`/`polygon` are user input; no place is hardcoded (FR-001). Rhodes is only the demo default.
- **Errors**: `401` unauthenticated · `422` neither name nor geometry given / invalid geometry · `404` name not resolvable (with disambiguation candidates when Nominatim returns several).

## Contract tests (T2 component — over real PostGIS)

- Authenticated `POST /areas` with the Rhodes bbox returns a polygon and `known_site_count` = the `ST_Within` count (fixture-seeded).
- Re-`POST` the same area after research ⇒ `covered=true`, `refresh_available=true` (backs `test_commons_reuse_dedupe`, ADR-0008).
- `POST` a polygon **much larger than** the researched one ⇒ `covered=false`, `researched_fraction` small, `known_site_count > 0` (the ADR-0018 regression).
- Unauthenticated ⇒ `401` (backs the auth half of `test_commons_write_shared`, ADR-0008).
- Invalid/empty geometry ⇒ `422`.
