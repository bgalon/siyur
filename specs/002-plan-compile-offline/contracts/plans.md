# Contract — Plans (propose a day, read it, approve it)

**Service**: `api/plans.py` → drives `planner/pipeline.py` (`… → curate → propose_itinerary → [HITL pause]`) and `planner/feasibility.py`. **Auth**: authenticated session required (`api/security.py::require_user` → Google `sub`); unauthenticated → `401`. **Scope**: `user_plan` rows are **row-scoped to the auth subject** — another user's `plan_id` is a **`404`, never a `403`** (existence is not leaked; same rule as `api/areas.py::_load_area`). **CRS**: EPSG:4326 (lon,lat); **times** are area-local wall clock. Schema: [`docs/data/itinerary.md`](../../../docs/data/itinerary.md) + [`route-leg.md`](../../../docs/data/route-leg.md) — the card wins. Maps to **FR-001…FR-009, US1**.

## POST /plans — propose an itinerary (SSE)

Ranks and orders commons sites into an `ItineraryV1` on the **Opus tier** of the `ModelRouter` seam, routes the legs, then checks feasibility. The model **ranks and orders only**: every distance, duration and opening window is computed by `commons/routing.py`, PostGIS/shapely and `commons/opening_hours.py` (FR-004).

**Request**:
```jsonc
{
  "area_id": "3a11…-uuid",                       // must be the caller's own area (ADR-0015 scope)
  "budgets": { "walking_m": 4000, "hours": 4.0 }, // ItineraryV1.budgets — the feasibility limits
  "interests": "art and coffee, nothing crowded", // free-form; the model's only free input
  "date": "2026-08-14",                           // ItineraryV1.date — REQUIRED; the calendar day planned for
  "day_start": "10:00",                           // area-local wall clock the day begins at
  "lang": "en"                                    // ItineraryV1.lang; `en` at M1
}
```

**`date` is required and is never defaulted server-side.** It populates `ItineraryV1.date` (M1, ADR-0025 ruling 2) and, with the area's `timezone`/`country_code`, is the frame `opening-hours-py` evaluates against — including which public holidays apply. Defaulting it to the server's `date.today()` in UTC is a real bug, not a convenience: a user in `Pacific/Auckland` (UTC+13) planning at 10:00 local on a Monday would get **Sunday**, and every stop would be checked against Sunday hours. `422` if absent or unparseable.

**`day_start` is the day's anchor.** `budgets.hours` is a duration, not a schedule; without an explicit start the day's beginning is only implicit in `stops[0].planned_start`, which is the very thing the planner is producing. Both are request-only inputs — neither is a field on `ItineraryV1` beyond `date`.

**Response**: `200` `text/event-stream`. Event sequence (trajectory `superset` extends slice 001's to `resolve_area → research → curate → propose_itinerary`):
```
event: status       data: {"phase":"load_sites","area_id":"3a11…","candidates":39}
event: status       data: {"phase":"propose_itinerary","tier":"opus","stops":5}
event: status       data: {"phase":"route","provider":"valhalla","legs":4,"excluded":[{"site_id":"…","reason":"unroutable"}]}
event: itinerary    data: { /* the proposed ItineraryV1 — stops, legs, timeline, budgets */ }
event: feasibility  data: {"ok":false,"violations":["walking_m 4200 > budget 3000",
                                                    "stop 2 outside opening window Tu 09:00-14:00"]}
event: done         data: {"plan_id":"7be2…-uuid","state":"proposed"}
```

**Invariants asserted on the stream / persisted plan**:
- **Stops come only from existing commons records** (`Stop.site_id` → an extant `SiteRecordV1`). No place is invented and no place absent from the commons appears (FR-002). A site the walking network cannot reach is reported in `route.excluded` and dropped — **never joined by a straight line pretending to be a route** (FR-003).
- **Every value shown on a stop is the commons record's own `SourcedValue`**, inherited unchanged with its `source` + `license` + `bundleable` stamp. The planner **introduces no unstamped value**; an unstamped value is refused at the boundary, not rendered (FR-008 / SC-004, continuous with slice 001 FR-003).
- **Legs carry their own provenance**: `RouteLegV1.source` is derived-from-OSM (`ODbL-1.0`, "© OpenStreetMap contributors") — routing over OSM is a Produced Work, so ODbL attribution renders wherever a leg does.
- **Feasibility is deterministic and always emitted**, `ok` true or false. `violations[]` names the specific budget or opening window breached (FR-005). A day with too little in it yields a **shorter honest plan** or `candidates: 0` with an explicit "not enough here" — **never padding** (edge case).
- **The plan is persisted `proposed` and stops there.** No compile, no downstream work, no bundle (FR-006). The pause is a durable `user_plan` row, so it survives process restart (SC-003).
- **Itineraries are private.** Written to `user_plan` scoped to `user.sub`, **never** into the shared commons (FR-007).

**Errors**: `401` unauthenticated · `404` unknown `area_id`, or an area belonging to another user · `422` missing/invalid `budgets` (non-positive `walking_m`/`hours`) or unparseable `day_start` · `409` a proposal is already running for this area (idempotency guard, mirroring `POST /areas/{id}/research`).

## GET /plans/{plan_id} — the plan and its approval state

**Response `200`**:
```jsonc
{
  "plan": { /* ItineraryV1 verbatim, per the card */ },
  "feasibility": { "ok": true, "violations": [], "checked_at": "2026-08-07T09:12:00Z" },
  // ↑ `ok`/`violations`/`checked_at` map to user_plan.feasible / .violations /
  //   .feasibility_checked_at. `checked_at` is UTC and is set ONLY when feasibility
  //   runs — never from `updated_at`, which bumps on any write and would report a
  //   time the check did not happen.
  "approval": { "state": "proposed", "approved_at": null, "superseded_by": null },
  "attribution": ["© OpenStreetMap contributors"]   // union across the plan's values and legs
}
```
- `approval.state` ∈ `proposing` | `proposed` | `approved` | `superseded` | `compiling` | `compiled` | `failed` — **ADR-0023's seven states, exposed verbatim** with no DB→API mapping layer (T007b reconciliation). An earlier draft of this contract listed only three, which would have rendered a `compiling` plan as an unknown state; and hiding `compiling` leaves the UI unable to distinguish "approved, idle" from "approved, compile running". State lives on the `user_plan` row, **not** inside `ItineraryV1` — the card has no approval field and none is added here. **Editing an approved plan supersedes it** and the successor re-runs feasibility before it may be compiled (edge case; FR-006).
- **Errors**: `401` unauthenticated · `404` unknown `plan_id` **or another user's plan** — indistinguishable by design.

## POST /plans/{plan_id}/approve — the HITL gate

Empty body. Transitions `proposed → approved`, the only transition that unlocks `POST /bundles`.

- **`200`** → `{"plan_id":"…","state":"approved","approved_at":"2026-08-07T09:14:00Z"}`.
- **Idempotent.** A second approve of an already-`approved` plan returns `200` with the **same `approved_at`** — the transition is applied once, so a double-approve (or a raced pair) can never produce two divergent bundles (edge case / SC-003).
- **`409` when infeasible** — approval is **BLOCKED** until the violations are resolved: `{"error":"infeasible","violations":[…]}`. This is the mechanical form of FR-005; nothing downstream can be reached around it.
- **`409` when superseded** — approving a plan a newer proposal replaced returns `{"error":"plan_superseded","superseded_by":"9af0…-uuid"}` and **does not approve it**. The superseding plan must be approved on its own id.
- **Errors**: `401` unauthenticated · `404` unknown `plan_id` or another user's plan · `409` as above.

## Contract tests (T1 unit + T2 component)

- A proposal within budget streams `feasibility.ok=true`; one over budget streams the named violation and its `approve` returns `409` (`tests/test_feasibility.py`).
- Approve twice ⇒ one `approved_at`, one approval, one compilable plan; the approval survives a process restart (`tests/test_hitl_gate.py`, SC-003).
- `GET`/`approve` on a plan owned by another subject ⇒ `404`, byte-identical to the unknown-id response.
- Every `Stop.site_id` resolves to a commons record and every displayed value carries a `source`; a synthetic unstamped value is refused, not streamed (provenance-completeness eval → SC-004).
- Trajectory eval: emitted `phase` sequence is a `superset` of `… → curate → propose_itinerary` (mocked model, no API key).

**Resolved since drafting**: the calendar date and locale that `opening_hours` evaluation needs are settled by **ADR-0025 ruling 2** — `ItineraryV1` gains `date` (supplied on this request, above), and the `area` row carries `timezone` + `country_code`, derived deterministically from the polygon at resolve time. `day_start` stays a **request-only** input: it anchors the day for the planner but is not a field on `ItineraryV1`, whose schedule is expressed in `stops[].planned_start` and `timeline`.
