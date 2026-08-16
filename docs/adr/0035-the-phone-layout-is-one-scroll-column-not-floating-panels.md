# 0035 — Below 760 px the app is one scroll column, not floating panels

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code (Opus 5) · approved-by: Ben · Date: 2026-08-15 · accepted: 2026-08-15
## Context and Problem Statement

`docs/design/ux-handoff/README.md` leads with a mobile-first contract: 375–430 px, tap targets
≥ 44 px, body ≥ 14 px at real widths. Nine deliverable units shipped without that contract ever
being rendered (FAIL-012). The audit in `docs/design/ux-audit-2026-08-15.md` measured the result,
and the numbers are not a set of unrelated CSS slips — they are one composition failure with four
symptoms.

Today the phone layout is **three independently-positioned fixed layers over a full-bleed map**:

| Layer | Position | `z-index` |
|---|---|---|
| `.siyur-controls` | fixed, `inset-block-start: 10px`, two rows, ~90 px tall | 2 |
| `.siyur-plan-panel` | fixed, `inset-block: 58px auto`, `max-height: 40vh`, own scroller | **3** |
| `.siyur-sheet` | fixed, bottom-anchored, own scroller | 2 |

There is exactly **one** layout media query in the whole application (`plan.css:34`,
`width <= 760px`), and all it does is widen the panel and cap it. Measured consequences at
390 × 844:

- the panel (`z: 3`) starts at y = 58 and **occludes the second controls row at y = 66**, making
  `Use this view` — the 0.18 s delimit path — unreachable at every scroll position;
- the panel clips **294 px** of its own content (365 px at 375 px), putting both
  `Plan this day →` and `Approve this day` below the fold of an inner scroller with no affordance,
  and rendering validation errors at y = 412/436 outside the visible band 58–428;
- the sheet covers the ODbL attribution — **at every width, including 1440 px**;
- the map, in a map product, is left a **154 px band, 18.3% of the screen**, containing 0 of 957
  markers.

Four findings, one cause: **layers that are positioned independently cannot be reasoned about
together, and at phone widths they have nowhere to go but on top of each other.** Any fix that
adjusts one offset re-breaks another, which is what makes this an architectural decision rather
than a CSS bug.

Two constraints frame the options. First, the design's own intent is a full-bleed map with a
bottom sheet over it (`ux-handoff/README.md` §Screens 1) — the overlay is not an accident, it is
the design, and it works at 1440 px where only the attribution defect survives. Second, RTL is
deferred to M3 but the CSS is already fully logical (**zero** physical direction properties
against 35 logical ones), and that property must not be spent.

## Considered Options

- **A — Keep the overlay; tune offsets and `z-index` per breakpoint.** Smallest diff. Fixes each
  symptom individually: raise the controls above the panel, move the panel below them, inset the
  sheet off the attribution, shrink the panel's cap. Preserves the desktop design exactly.
- **B — Below 760 px, one scroll column.** The map becomes a sized block (not a full-bleed
  backdrop), followed in normal flow by the coverage sheet and the plan panel. Nothing is fixed,
  nothing overlaps, there is one scroll region. Desktop keeps today's overlay unchanged.
- **C — A single real bottom sheet with snap points.** The map stays full-bleed; the coverage card
  and the plan panel become *content of one sheet* with collapsed/half/expanded snap points, per
  the design's "drag pill, drag-to-collapse" motif. One overlay instead of three.
- **D — Route-based screens.** Delimit / research / plan / travel become separate views with one
  visible at a time.

## Decision Outcome

Chosen: **B for M1, as the deliberate stepping stone to C**, because it removes the entire class
of defect in one change rather than removing four instances of it, and because it is the only
option that is cheap now *and* does not have to be undone to reach the design's real target.

The reasoning, in the order that decided it:

**A is rejected because it preserves the generator of the bug.** Every symptom above is a
different pair of independently-positioned fixed layers colliding. Tuning offsets fixes the four
collisions we measured, at four widths we happened to test, and leaves the next content-length
change free to produce a fifth. It is also not obviously cheaper: four coordinated offset fixes
plus the `z-index` inversion is comparable work to B, with a worse end state.

**B is chosen because a normal-flow column cannot occlude anything.** Reachability, the clipped
CTAs, the invisible validation errors and the map's 18% squeeze all stop being possible rather
than stopping being present — the property is structural, not tuned. It is ~0.5 day, it touches
two files (`plan.css:18-40`, `style.css:194-203`), and it leaves the ≥ 1440 px overlay untouched,
so the one width the team has always looked at does not regress.

**C is the right long-term answer and is explicitly not M1.** It is what `ux-handoff` actually
specifies (36 × 4 px drag pill, drag-to-collapse, snap points) and it keeps the map full-bleed,
which B sacrifices. It is rejected *for now* only on cost and risk: sheet gesture handling is
genuinely fiddly, it wants the fork-strip work that is not built, and it would put a
multi-day interaction problem in front of a 3-day usability fix. **B is chosen in a shape that
C can absorb** — collapsing a flow column into sheet content is additive; re-deriving three
tuned overlays into one sheet is not.

**D is rejected as out of proportion.** It solves the layout problem by changing the product's
navigation model, which is a PRD-level move, and PRD §13 is Ben's.

### Consequences

- Good: **four audit findings (UX-01, UX-05, UX-06, UX-10) close with one change**, and they close
  structurally — no offset can reintroduce them.
- Good: no change at desktop width; the design's overlay survives where it works.
- Good: one scroll region instead of three nested ones, which is also what makes
  scroll-into-view for validation errors work at all.
- Bad / accepted cost: **the phone map is no longer full-bleed.** This is a real departure from
  `ux-handoff` §Screens 1 and the reason C exists. Accepted for M1 because an 18.3% map that is
  full-bleed in principle is worse than a sized map that is actually visible, and because a
  sized map is a *fixed* height we control rather than a residue of two panels' heights.
- Bad / accepted cost: the fork-strip / schematic-timeline motifs assume an overlay composition
  and will need revisiting when C lands.
- Neutral: unchanged for RTL. Every rule stays logical (`inset-inline`, `padding-inline`,
  `border-inline-start`); a flow column is if anything *more* direction-agnostic than tuned
  absolute offsets. The zero-physical-properties property is preserved as a review condition.

### Confirmation

The rendered-viewport suite required by **FAIL-012's guardrail**, in CI (Playwright, over
`web/test/e2e`), at **375 × 667, 390 × 844 and 430 × 932**:

1. every visible interactive element returns itself (or a descendant) from `elementFromPoint` at
   its own centre — the assertion that fails on today's `main` and is the direct test of this ADR;
2. every visible interactive element is ≥ 44 × 44 px;
3. `document.scrollWidth === document.clientWidth`;
4. the ODbL attribution passes check 1 **at 1440 px too**;
5. a lint over `web/src/*.css` asserting **zero** physical direction properties, so the RTL
   property this ADR promises to preserve is enforced rather than asserted.

This ADR is **not discharged** until checks 1–5 are green on a build where they have first been
demonstrated red (per FAIL-014's lesson: a check nobody has seen fail is not yet a control).
</content>
