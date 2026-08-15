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
                                                    "stop 2 is outside its opening window"],
                                       "warnings":["stop 3 hours cannot be evaluated
                                                    (no_expression), so it may be shut …"]}
event: done         data: {"plan_id":"7be2…-uuid","state":"proposed"}
```

**Invariants asserted on the stream / persisted plan**:
- **Stops come only from existing commons records** (`Stop.site_id` → an extant `SiteRecordV1`). No place is invented and no place absent from the commons appears (FR-002). A site the walking network cannot reach is reported in `route.excluded` and dropped — **never joined by a straight line pretending to be a route** (FR-003).
- **Every value shown on a stop is the commons record's own `SourcedValue`**, inherited unchanged with its `source` + `license` + `bundleable` stamp. The planner **introduces no unstamped value**; an unstamped value is refused at the boundary, not rendered (FR-008 / SC-004, continuous with slice 001 FR-003).
- **Legs carry their own provenance**: `RouteLegV1.source` is derived-from-OSM (`ODbL-1.0`, "© OpenStreetMap contributors") — routing over OSM is a Produced Work, so ODbL attribution renders wherever a leg does.
- **Feasibility is deterministic and always emitted**, `ok` true or false. `violations[]` names the specific budget or opening window breached (FR-005). A day with too little in it yields a **shorter honest plan** or `candidates: 0` with an explicit "not enough here" — **never padding** (edge case).
- **`violations[]` blocks; `warnings[]` does not** (**ADR-0022, amended 2026-08-14**). `ok` is the approval predicate and is false **iff `violations[]` is non-empty**. `warnings[]` is the advisory half — today, one entry per stop whose `opening_hours` could not be evaluated — and `{"ok": true, "warnings": [...]}` is the **normal** answer, not a contradiction: most OSM/Overture records carry no `opening_hours` at all (1 of 25 in the fixture set; every stop of a live 6-stop day), so blocking on "we do not know" means no real day is ever approvable. **"We know it is shut" still blocks**: `outside_opening_window` is a violation. A stop the commons cannot resolve (`unknown_site`) also still blocks — what is missing there is the place, not its hours. The accepted cost is that a traveller can approve a day containing places that may be shut, which is why each warning **names its stop** and is rendered per stop rather than aggregated. A client that has never heard of `warnings` sees an unchanged `violations`/`ok` pair.
- **A violation never embeds commons-derived text** (ADR-0030 A1). It names the breach and the **stop order** — `"stop 2 is outside its opening window"`, never `"… outside opening window Tu 09:00-14:00"`. The `opening_hours` expression is ODbL-licensed commons text; quoting it inside a server-composed sentence puts it on a surface with no attribution stamp in frame. The client joins the verdict to the stop by `order` and renders the stop's own `opening_hours` through the attribution funnel, where it carries its chip. An earlier draft of A1 permitted the quotation *conditionally* on that chip being rendered — withdrawn, because the condition fails in exactly the branch where the itinerary is unreadable and the verdict is all that renders.
- **The plan is persisted `proposed` and stops there.** No compile, no downstream work, no bundle (FR-006). The pause is a durable `user_plan` row, so it survives process restart (SC-003).
- **Itineraries are private.** Written to `user_plan` scoped to `user.sub`, **never** into the shared commons (FR-007).

**Errors**: `401` unauthenticated · `404` unknown `area_id`, or an area belonging to another user · `422` missing/invalid `budgets` (non-positive `walking_m`/`hours`) or unparseable `day_start` · `409` a proposal is already running for this area (idempotency guard, mirroring `POST /areas/{id}/research`).

## GET /plans — the caller's plans, newest first

Added by **Phase A of `docs/design/usable-m1-plan.md`**, and the reason it exists is worth stating: until it did, a `plan_id` existed only in the `POST /plans` stream that created it. Closing the tab made the day unreachable — the plan row was durable (SC-003) and nothing could find it. Two of the product's four journeys ("tour an existing plan", "edit a plan") are blocked on this before any UI question arises.

**Request**: `GET /plans?limit=50`.

**Response `200`**:
```jsonc
{
  "plans": [
    {
      "plan_id": "7be2…-uuid",
      "area_id": "3a11…-uuid",
      "date": "2026-09-01",                       // ItineraryV1.date — the day planned for
      "state": "approved",                        // ADR-0023's seven states, verbatim
      "feasible": true,
      "stop_count": 6,
      "created_at": "2026-08-15T09:12:00+00:00",
      "approved_at": "2026-08-15T09:14:00+00:00", // null until the gate is passed
      "superseded_by": "9af0…-uuid"               // null unless an edit replaced this revision
    }
  ]
}
```

- **`state` is the same closed set as `approval.state` below** — `proposing` | `proposed` | `approved` | `superseded` | `compiling` | `compiled` | `failed`, exposed verbatim with no DB→API mapping layer. One vocabulary for the list and the detail read, for the reason the detail section gives.
- **Row-scoped to the auth subject** (`WHERE user_id`), and asserted **on the emitted SQL** (`tests/test_api_plans.py`, mirroring `test_hitl_gate.py::test_every_plan_statement_filters_on_user_id`): a list is where a missing scope stops being one leaked row and becomes the whole table, and an implementation that read every plan and filtered in Python would return the same empty list a correct one does — having read them all first.
- **The itinerary is not in this body.** A list is how a traveller *finds* a day; `GET /plans/{plan_id}` is how they read it. Shipping every `ItineraryV1` here would also put every stop's commons-derived value on a surface that renders no attribution (ADR-0019), which is a licence problem before it is a size one. `stop_count` is the one derived number a chooser needs, and it carries no commons text. It and `date` are computed **in Postgres** from the `jsonb`, so the blob is never transferred; a row whose `stops` is not an array counts `0` rather than taking the whole page down, on `load_plan`'s never-a-half-record rule.
- **Ordered `created_at DESC, id DESC`** — total, so two rows written in one transaction cannot swap places between requests and a keyset cursor over the same pair stays available when this needs to page.
- **`limit`** defaults to **50**, capped at **200** (`commons.repository.LIST_LIMIT_DEFAULT` / `LIST_LIMIT_MAX`); outside `1…200` is a `422`. There is deliberately no unbounded variant: the cap is applied to the query as well as validated on the request. **There is no `offset`/cursor yet** — an account with more than 200 plans cannot reach the oldest ones from this endpoint, which is a stated limit rather than an oversight, and the ordering above is what a cursor will key on.
- **Empty is a success**: `200` `{"plans": []}`, never a `404`. The web lane renders a first-run state from exactly this body, and "you have not planned anything" must not share a status code with "something broke".
- **Timestamps are offset-bearing ISO-8601 in UTC** (`…+00:00`, pydantic's rendering) — the same spelling `approved_at` already has on `GET /plans/{plan_id}` and `POST /plans/{id}/approve`, not a `Z` suffix. Deviating here would make two endpoints disagree about one column.
- **Errors**: `401` unauthenticated · `422` a `limit` outside the range.

## GET /plans/{plan_id} — the plan and its approval state

**Response `200`**:
```jsonc
{
  "plan": { /* ItineraryV1 verbatim, per the card */ },
  "feasibility": { "ok": true, "violations": [], "warnings": [], "checked_at": "2026-08-07T09:12:00Z" },
  // ↑ `ok`/`violations`/`checked_at` map to user_plan.feasible / .violations /
  //   .feasibility_checked_at. `checked_at` is UTC and is set ONLY when feasibility
  //   runs — never from `updated_at`, which bumps on any write and would report a
  //   time the check did not happen.
  //   `violations` and `warnings` are BOTH stored in the one `violations` jsonb column
  //   as severity-stamped entries and split apart on read, so the two lists here are
  //   the row's own record of which was which — never re-derived from the wording.
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
- **`409` when stale** — the plan changed between the client reading it and approving it, so the `itinerary_hash` the compare-and-set was made against no longer matches: `{"error":"plan_stale"}`. The client must re-read the plan and approve what it now says. This is what makes approval an approval **of a specific day** rather than of a plan id — without it, an edit racing an approve would silently approve the version the user never saw.
- **`409` when not approvable** — the plan is in a state from which approval is not defined at all (`compiling`, `compiled`, `failed`, or already `superseded` without a successor to name): `{"error":"plan_not_approvable","state":"failed"}`.
- **Errors**: `401` unauthenticated · `404` unknown `plan_id` or another user's plan · `409` as above.

**The four `error` codes above — `infeasible`, `plan_superseded`, `plan_stale`, `plan_not_approvable` — are the complete closed set**, and they are spelled identically to `commons.repository.RefusalReason`'s members. That identity is deliberate: `api/plans.py` maps a refusal to a status code and **translates no vocabulary**, so a new refusal reason cannot reach the wire under an invented third spelling. Adding a member to either side without the other is a contract break.

## Contract tests (T1 unit + T2 component)

- A proposal within budget streams `feasibility.ok=true`; one over budget streams the named violation and its `approve` returns `409` (`tests/test_feasibility.py`).
- Approve twice ⇒ one `approved_at`, one approval, one compilable plan; the approval survives a process restart (`tests/test_hitl_gate.py`, SC-003).
- `GET`/`approve` on a plan owned by another subject ⇒ `404`, byte-identical to the unknown-id response.
- `GET /plans` lists the caller's plans newest-first with `state`/`date`/`stop_count` read off each row; a second subject's list is **empty**, asserted alongside a Tier-1 check that `user_plan.user_id` is in the `WHERE`; an account with no plans gets `200 {"plans": []}`; `limit` is honoured and out-of-range is `422` (`tests/test_api_plans.py`).
- Every `Stop.site_id` resolves to a commons record and every displayed value carries a `source`; a synthetic unstamped value is refused, not streamed (provenance-completeness eval → SC-004).
- Trajectory eval: emitted `phase` sequence is a `superset` of `… → curate → propose_itinerary` (mocked model, no API key).

**Resolved since drafting**: the calendar date and locale that `opening_hours` evaluation needs are settled by **ADR-0025 ruling 2** — `ItineraryV1` gains `date` (supplied on this request, above), and the `area` row carries `timezone` + `country_code`, derived deterministically from the polygon at resolve time. `day_start` stays a **request-only** input: it anchors the day for the planner but is not a field on `ItineraryV1`, whose schedule is expressed in `stops[].planned_start` and `timeline`.
