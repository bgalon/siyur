# 0018 — The delimit gesture: map-viewport-as-bbox, not the mock's draw tool

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: Ben · Date: 2026-08-01 · accepted: 2026-08-07

## Context and Problem Statement

US1 begins "sign in → **delimit the area** → trigger research". Every other piece shipped before
the affordance that *produces* an area did. `web/src/map/delimit.ts` is that affordance, and it
ships two gestures:

- a **search pill** → `{ name }`, resolved server-side;
- a **"Use this view" button** → `{ bbox }` read from the **current map viewport** at click time.

The design authority says something else. `docs/design/ux-handoff/README.md` § Screens 1 ("Define
the area") specifies "full-bleed map + floating search pill; **draw (✎)** & zoom controls". The
shipped viewport button stands in the mock's ✎ slot. The implementing session said so in the
module docstring rather than quietly claiming parity, and flagged it as reversible.

**Two live constraints shape the choice, and both are measured rather than assumed:**

1. **The name path is currently too slow to use.** `POST /areas` by name scans the hosted Overture
   divisions theme with **no bbox pushdown**; a name resolve can hang for minutes and froze a
   browser tab (`docs/TRY-IT.md`, which concludes "treat the search pill as unfinished"). Every
   verified browser run used the `bbox` path. So the geometry gesture is not a secondary
   affordance today — **it is the only working one.**
2. ~~**`covered` is `count > 0`.** `commons/repository.py::coverage` marks an area covered if
   **one** known site falls inside it, and `api/areas.py` then serves a covered area from reuse
   frames unless `force_refresh` is set. An over-wide delimitation is therefore not merely
   wasteful — it can suppress the research pass entirely.~~

   **✎ Amended 2026-08-07, before approval — no longer true.** PR #67 (`DU-03-coverage-semantics`)
   replaced the rule with `covered = researched_fraction >= 0.99`: the fraction of the requested
   polygon, by true WGS84 surface area, inside the union of *completed* research passes. Site
   count is not part of it (`commons/repository.py:146`), and
   `specs/001-research-cited-sites/contracts/areas.md:46` names this ADR as the case it fixes. An
   over-wide delimitation now reports `covered=false` with a small `researched_fraction`, which is
   the correct answer — so this constraint no longer bears on the choice below. The stale text is
   struck rather than deleted, because the reasoning that led to option A is only legible
   alongside the constraint that applied when it was written. **Approving this ADR does not
   approve the struck claims.**

## Considered Options

**A — Viewport-as-bbox + name pill (shipped).** No drawing code. `contracts/areas.md` already
accepts `bbox`. Genericity is structural: the module contains no place name, no coordinate and no
default extent — every number comes from the user's input box or the map's own bounds at click
time, so there is nothing to hardcode a place *into*.

**B — Drag-to-draw rectangle.** Still a `bbox`, so **no contract change and no server change**.
Costs pointer-drag state, a rubber-band overlay, touch handling, and a cancel gesture. Strictly
richer than A over the same request field: the user can select a *part* of what they see.

**C — Polygon lasso.** Emits `polygon` (GeoJSON), which `AreaRequest`, `contracts/areas.md` and
`resolve_area` already accept — again **no contract change**. Costs vertex placement/editing,
undo, close-the-ring UX, and client-side self-intersection prevention: the server rejects a
self-intersecting ring as `422` (`resolve_area._validate_area`), so a lasso that merely submits
what was drawn will hand users a rejection they cannot interpret.

**D — Name pill only (the mock's primary affordance, no geometry gesture).** Rejected outright by
constraint 1: it would ship US1 with no working delimitation at all.

## Decision Outcome

Chosen: **A**, and this ADR **records** the shipped choice rather than recommending it — the
alternative to A was not shipping a delimit gesture in slice 001.

The driver is that A is the smallest thing that makes US1 real end-to-end, and it is **reversible
at essentially zero cost**: `delimit.ts` builds an `AreaRequest` and hands it to `onDelimit`; it
performs no request and makes no coverage decision. B and C replace the gesture without touching
`areas.ts`, `sites.ts`, `contracts/areas.md`, or anything server-side — the request fields they
need already exist. That is why the mock's draw tool can be deferred without incurring a
rewrite: the seam was drawn in the right place first.

**The cost of A, stated concretely rather than as "less precise".** A bbox from the viewport is
always axis-aligned and always the *whole* view, so a user cannot research an old town without
also researching the water and suburbs framing it. ~~Combined with `covered = count > 0`, that has a
sharp consequence: **pan out one zoom step, catch one already-known site at the periphery, and
the whole enlarged area reports `covered: true` — the default research pass is replaced by reuse
frames for a region the user has not actually researched.** Over-selection here is not just
wasted adapter fan-out; it degrades the correctness of the US2 reuse signal.~~

**✎ That second-order cost is discharged** (see the amendment above). Under
`covered = researched_fraction >= 0.99` an over-wide area reports `covered=false`, so
over-selection no longer corrupts the reuse signal — it costs a wider research pass and nothing
more. **This removes the sharpest argument for B**, which is why A is approved as the standing
choice rather than as a stopgap. What survives is the plain ergonomic cost: the user researches
water and suburbs they did not want, paying adapter fan-out and `curate` spend for them.

**B is the recommended landing point when the trigger fires, not C.** It removes the
over-selection above for the cost of a drag interaction, needs no new request field, and inherits
A's genericity property unchanged. C is the mock's freehand gesture and is the right end state for
a walkable old town whose shape is nothing like a rectangle — but it carries the self-intersection
and vertex-editing surface, and should be justified by a real delimitation a rectangle cannot
express rather than by fidelity to the mock.

### Consequences

- Good: US1 has a working, demonstrated delimit gesture with no drawing code, no new contract
  surface, and no place-specific value anywhere in the module.
- Good: the reversal cost is bounded to one file — the `AreaRequest` boundary is what makes B and
  C drop-in replacements.
- Bad / accepted cost: **the shipped product does not match the ux-handoff mock's Screen 1.** The
  ✎ affordance is a viewport button, not a draw tool. Recorded here so it is a known deviation
  with a trigger, not drift.
- Bad / accepted cost: over-selection — the user researches the water and suburbs framing what they
  wanted, paying a wider adapter fan-out and `curate` spend. Mitigated only by the user zooming
  appropriately, which is not a mitigation. ~~and through `covered = count > 0` its effect on the
  reuse signal~~ — **that second-order effect is gone**; `researched_fraction` reports an over-wide
  area as uncovered, which is correct.
- Accepted: with the name path effectively unusable, A carries **all** the delimitation weight the
  design intended to split across two affordances. The search pill is present and correct but is
  not, today, a path a user can complete.

### Confirmation

- **`delimit.ts`'s own guards** — `viewportBbox` clamps a world-wrapped MapLibre viewport to valid
  EPSG:4326 ranges, and `isUsableBbox` refuses a degenerate extent locally rather than sending a
  request the contract will answer `422`. Bounds are read **at click time, never captured at
  mount**, which is what keeps "this view" honest.
- **The genericity AST scan** (SC-005 / FR-001) — no place name, coordinate or default extent
  appears in this module; it passed with no exemptions.
- **Owed by whichever of B or C lands:** a test that the emitted `AreaRequest` still contains no
  literal geometry, and — for C — a client-side self-intersection refusal, so the server's `422`
  is a backstop rather than the user's first feedback.

### Revisit trigger — the first area a rectangle cannot express, not a date

Revisit on **either** of these, whichever comes first:

1. **The divisions scan gets bbox pushdown and the name path becomes usable.** That changes the
   question rather than answering it: the search pill becomes the primary affordance the mock
   intended, the viewport button's job shrinks to "the area I'm looking at", and B/C then have to
   earn their complexity on their own merits instead of by default.
2. **The first delimitation an axis-aligned viewport rectangle demonstrably distorts** — a walkable
   area whose useful shape is plainly not a rectangle. ~~in practice, a case where the
   over-selection above flips `coverage.covered` for a region the user did not research, or~~ —
   **that half of the trigger can no longer fire**, since `researched_fraction` reports an
   over-wide area as uncovered. The shape argument is now the *only* thing that should buy B or C:
   they must be justified by a delimitation a rectangle cannot express — not by fidelity to the
   mock, and not by a coverage bug that has been fixed.

One forward-looking note, not a trigger: M2 itinerary and routing work is defined over the area
polygon, so a sloppy area costs more downstream than it does today. If B or C is going to be
built at all, building it before M2 is cheaper than after.
