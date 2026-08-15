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

## GET /areas — the caller's own areas, newest first

Added by **Phase A of `docs/design/usable-m1-plan.md`**. Without it an `area_id` exists only in the `POST /areas` response that minted it, so an area cannot be revisited without re-delimiting it — the same "the app has no memory" gap as `GET /plans` (`specs/002-plan-compile-offline/contracts/plans.md`).

**Request**: `GET /areas?limit=50`.

**Response `200`**:
```jsonc
{
  "areas": [
    {
      "area_id": "…-uuid",
      "name": "Rhodes medieval old town",     // the free text asked for; null for a bbox/drawn ring
      "bbox": [28.216, 36.440, 28.232, 36.451], // [minLon,minLat,maxLon,maxLat] EPSG:4326 — lon first
      "created_at": "2026-08-15T09:12:00+00:00",
      "researched_at": "2026-08-15T09:20:00+00:00"  // null = delimited, never researched
    }
  ]
}
```

- **Scoped to `created_by`** (ADR-0015, PRD §13 #4): another subject's areas are not in the list, and the filter is in the `WHERE` — asserted on the emitted SQL (`tests/test_api_areas.py`), because an empty list is exactly what a read that fetched everything and filtered afterwards would also return.
- **Ordered `created_at DESC, id DESC`.** The tiebreak makes the order total, so a keyset cursor over the same pair is available when this needs to page.
- **`bbox`, never the polygon.** A resolved division ring can carry tens of thousands of vertices; the four ordinates come from PostGIS (`ST_XMin`/`ST_YMin`/`ST_XMax`/`ST_YMax`), and `POST /areas` still returns the geometry it resolved.
- **`known_site_count` is deliberately absent.** Coverage is a PostGIS count per polygon, so including it would make one list N spatial queries. Coverage stays on `POST /areas`, which computes it for the area actually being opened.
- **`limit`** defaults to **50** and is capped at **200** (`commons.repository.LIST_LIMIT_DEFAULT` / `LIST_LIMIT_MAX`); outside `1…200` is a `422`. The cap is also applied to the query itself, so no call site can produce an unbounded read.
- **Empty is a success**: a caller with no areas gets `200` `{"areas": []}`, never a `404`. "Nothing here yet" and "something broke" must not share a status code.
- **Errors**: `401` unauthenticated · `422` a `limit` outside the range.

## Contract tests (T2 component — over real PostGIS)

- Authenticated `POST /areas` with the Rhodes bbox returns a polygon and `known_site_count` = the `ST_Within` count (fixture-seeded).
- Re-`POST` the same area after research ⇒ `covered=true`, `refresh_available=true` (backs `test_commons_reuse_dedupe`, ADR-0008).
- `POST` a polygon **much larger than** the researched one ⇒ `covered=false`, `researched_fraction` small, `known_site_count > 0` (the ADR-0018 regression).
- Unauthenticated ⇒ `401` (backs the auth half of `test_commons_write_shared`, ADR-0008).
- Invalid/empty geometry ⇒ `422`.
- `GET /areas` lists the caller's areas newest-first with the row's bbox, and flips `researched_at` from null once a pass commits; a second subject's list is **empty**, asserted alongside a Tier-1 check that `area.created_by` is in the `WHERE`.
