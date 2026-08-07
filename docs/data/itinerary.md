# Schema card — itinerary (`ItineraryV1`)

*The planned day — composed, **per-user, private**. The single source of truth for planner output *and* the compiled bundle.
Authoritative source: `docs/design/tech-design.md` §1.3. Never guess this schema; read this card.*

- **Schema version:** `ItineraryV1` (`schema_ver` literal). M1 populates the base plan; Plan B/C variants are M2+.
- **Storage:** Cloud SQL Postgres — table `user_plan` (holds the `ItineraryV1`), **row-level scoped to the auth subject**
  (`user_id`). This is the PRD §13 #4 privacy boundary: **personal data is never bundled as personal data** — the compile
  step freezes only the itinerary's own structure + the referenced `bundleable=true` site content. See tech-design §2.
- **CRS:** references `SiteRecordV1` by `site_id`; any embedded geometry is **EPSG:4326 (lon, lat)**. Route leg geometry
  lives in `RouteLegV1` — see [`route-leg.md`](./route-leg.md), which is the field-level ground truth for a leg.
- **Timezone:** `timeline` / `Stop` planned windows are **local wall-clock** times in the area's timezone, on the
  plan's `date` — the two together are the only complete instant this schema carries. Feasibility is checked against
  `opening_hours` in the same local frame; **`opening-hours-py`** (ADR-0022) needs the area locale for PH resolution.
  **The local frame comes from the `area` row**, which carries `timezone` (IANA, e.g. `Europe/Athens`) and
  `country_code` (ISO 3166-1 alpha-2) — see [`area.md`](./area.md); `ItineraryV1` never restates them, it resolves
  them through `area_id`. Any `timestamptz` audit fields are UTC.
- **License & provenance:** the itinerary is user-owned personal data (`source.kind="user"`, `license="user-owned"`,
  **never auto-published to the commons**). The *sites* it references carry their own `SourcedValue` stamps; only their
  `bundleable=true` values survive the compile quarantine filter → [`/DATA-LICENSES.md`](../../DATA-LICENSES.md),
  [`bundle-manifest.md`](./bundle-manifest.md). The frozen copy of this object lives at
  `BundleManifestV1.content.itinerary` (hashed as `content.itinerary_sha256`) — that path is what the offline timeline
  renders from. Constitution Article V: personal data is per-user, private, never bundled.

## `ItineraryV1` fields

| Field | Type | M1? | Units / notes |
|---|---|---|---|
| `id` | `UUID` | M1 | |
| `user_id` | `str` | M1 | auth subject; row-level scope key (privacy boundary) |
| `area_id` | `UUID` | M1 | the resolved area this plan covers (see area / `ST_Within` coverage, DU-01); **supplies the `timezone` + `country_code` every local time here is read in** |
| `date` | `date` | M1 | the **calendar date the day is planned for**, in the area's local frame (`YYYY-MM-DD`, no offset). Without it a `planned_start` of `10:00` is not an instant and opening hours cannot be evaluated — weekday rules, `PH`, and seasonal ranges all need the date |
| `lang` | `str` (BCP-47) | M1 | presentation language (`en` at M1) |
| `stops` | `[Stop]` | M1 | ordered; each → `site_id` + planned window + dwell |
| `legs` | `[RouteLegV1]` | M1 | walking legs between stops (precomputed, Valhalla) — schema in [`route-leg.md`](./route-leg.md) |
| `timeline` | `Timeline` | M1 | simple ordered times/durations (rich dynamic timeline = M2, PRD §13 #5) |
| `budgets` | `{ walking_m: float, hours: float }` | M1 | feasibility limits (**must hold**) — walking metres, day hours |
| `meals` | `[Anchor]` | M2+ | |
| `variants` | `{ "B": PlanVariant, "C": PlanVariant }` | M2+ | Plan B/C contingencies |
| `schema_ver` | `"ItineraryV1"` | M1 | literal |

**Sub-structures**

```
Stop:
  site_id:        UUID            # references SiteRecordV1.id
  order:          int             # 0-based position in the day
  planned_start:  local-time      # area-local wall clock (HH:MM)
  dwell_min:      int             # minutes at this stop

Timeline:
  entries: [ { stop_order: int | leg_id: str,      # exactly one of the two per entry
               start: local-time, duration_min: int } ]   # ordered

Anchor:                           # [M2+] a meal / fixed appointment
  kind:  "meal" | "fixed"
  window: { start: local-time, end: local-time }

PlanVariant:                      # [M2+] a divergence from the base plan
  trigger: "site_closed" | "rain" | "behind_pace"
  changes: [StopEdit]
  legs:    [RouteLegV1]
```

**How a timeline entry addresses the day — one scheme, no `stop_id`.** A stop entry carries **`stop_order: int`**, the
`Stop.order` of the stop it times; a leg entry carries **`leg_id: str`**, the `RouteLegV1.id`. This is the *same*
addressing `RouteLegV1.from_stop` / `to_stop` already use, so the whole plan refers to stops by position and legs by id
— never both. A `Stop` has **no `id` of its own**, and a *site* UUID is not a stop address: a day that visits one site
twice has two stops with different `order`s and one `site_id`, which `stop_order` disambiguates and `site_id` cannot.

**Feasibility (EARS §5, tested, merge-blocking):** the base plan (and each variant, at M2) satisfies `budgets`
(walking ≤ `walking_m`, total ≤ `hours`) **and** every stop falls within its site's opening window on `date` — else it
is **flagged before approval**, never silently shipped. The LLM never computes times or distances: Valhalla emits leg
times, PostGIS distances, **`opening-hours-py`** the windows — evaluated with the area's `timezone` and `country_code`
(ADR-0022; determinism discipline, tech-design §5.2).

**The feasibility verdict is not a field of `ItineraryV1`.** It lives on the `user_plan` row that holds this itinerary,
as `feasible` (bool) and `violations` (the named reasons), beside `status`/`approved_at`. It is server state about a
plan, not part of the plan: `ItineraryV1` is `extra="forbid"`, so an ad-hoc `_feasibility` key is rejected outright —
and a leading-underscore field name is not constructible in pydantic at all. Read the verdict from the row, never from
the itinerary JSON; the frozen `content.itinerary` in a bundle carries no verdict because only a feasible, approved
plan is ever compiled.

**HITL gate:** `propose_itinerary` streams over SSE → the user **approves at an explicit persisted pause**
(`interrupt()`-equivalent over the owned Postgres checkpoint, ADR-0004) → the approved `ItineraryV1` is saved to
`user_plan`, then compile may run. Trajectory eval (superset): `resolve_area → research → curate → propose_itinerary`.

## Example rows

```jsonc
// 1 — minimal M1 base plan (2 stops, 1 walking leg), Rhodes old town.
//     Leg objects are shown in FULL here: `geometry` and `source` are M1-required
//     (route-leg.md), not optional decoration.
{
  "id": "7be2…-uuid", "user_id": "google-oauth2|1103…", "area_id": "3a11…-uuid",
  "date": "2026-07-25", "lang": "en", "schema_ver": "ItineraryV1",
  "budgets": { "walking_m": 4000, "hours": 4.0 },
  "stops": [
    { "site_id": "6f1c…-uuid", "order": 0, "planned_start": "10:00", "dwell_min": 60 },
    { "site_id": "c9d1…-uuid", "order": 1, "planned_start": "11:15", "dwell_min": 45 }
  ],
  "legs": [ { "id": "leg-0", "from_stop": 0, "to_stop": 1, "mode": "walk",
    "distance_m": 380, "duration_s": 300,
    "geometry": { "type": "LineString", "coordinates": [
      [28.2247, 36.4443], [28.2242, 36.4446], [28.2238, 36.4447] ] },
    "source": { "kind": "osm", "id": "valhalla:pedestrian", "url": null,
      "license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors" } } ],
  // Stops are addressed by `stop_order` (= Stop.order), legs by `leg_id`.
  "timeline": { "entries": [
    { "stop_order": 0, "start": "10:00", "duration_min": 60 },
    { "leg_id": "leg-0", "start": "11:00", "duration_min": 5 },
    { "stop_order": 1, "start": "11:15", "duration_min": 45 } ] }
}

// 2 — plan flagged infeasible (walking budget exceeded → blocked before approval).
//     The verdict is NOT in this object: it is `user_plan.feasible = false` +
//     `user_plan.violations = ["walking_m 4200 > budget 3000"]` on the row holding it.
//     Legs abbreviated — `geometry` + `source` omitted for brevity only; see route-leg.md.
{
  "id": "9af0…-uuid", "user_id": "google-oauth2|1103…", "area_id": "3a11…-uuid",
  "date": "2026-07-26", "lang": "en", "schema_ver": "ItineraryV1",
  "budgets": { "walking_m": 3000, "hours": 3.0 },
  "stops": [ { "site_id": "…", "order": 0, "planned_start": "09:00", "dwell_min": 90 },
             { "site_id": "…", "order": 1, "planned_start": "11:30", "dwell_min": 60 } ],
  "legs": [ { "id": "leg-0", "from_stop": 0, "to_stop": 1, "mode": "walk",
    "distance_m": 4200, "duration_s": 3600 /* , geometry, source — see route-leg.md */ } ],
  "timeline": { "entries": [] }
}

// 3 — M2+ shape with a Plan B variant (site_closed) — illustrative, not populated in M1
{
  "id": "ee31…-uuid", "user_id": "google-oauth2|1103…", "area_id": "3a11…-uuid",
  "date": "2026-09-01", "lang": "en", "schema_ver": "ItineraryV1",
  "budgets": { "walking_m": 5000, "hours": 5.0 },
  "stops": [ { "site_id": "…", "order": 0, "planned_start": "10:00", "dwell_min": 60 } ],
  "legs": [], "timeline": { "entries": [] },
  "variants": { "B": { "trigger": "site_closed",
    "changes": [ { "op": "replace", "stop_order": 0, "with_site_id": "…" } ],
    "legs": [] } }
}
```
