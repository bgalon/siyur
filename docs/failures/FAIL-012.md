# FAIL-012 — Nine deliverable units shipped green; nobody ever rendered a phone

- Date: 2026-08-15 · Severity: high (the product's stated primary form factor was never once
  exercised; the first step of the journey is untappable on it)
- Root-cause class: **a verification surface that cannot see the failure mode** (every gate read
  attributes and status codes; the defects live in pixels)

## Symptom

`docs/design/ux-handoff/README.md` states the product is mobile-first — 375–430 px, tap targets
≥ 44 px, body ≥ 14 px at real widths. Nine deliverable units merged with passing Tier 1/2 tests,
passing vitest, and green CI. The first time anyone rendered the app below desktop width, at
390 × 844:

```
Use this view      OCCLUDED by .siyur-plan-panel__title   (unreachable in every scroll state)
Plan this day →    clipped 294 px below an inner scroller with no affordance
Approve this day   clipped, same
© OpenStreetMap    OCCLUDED by .siyur-sheet  — at 375, 390, 430 AND 1440
11 of 12 controls  below the 44 px floor (delimit input: 19 px)
every font size    9–13.5 px; exactly one element meets the 14 px floor
```

The occlusions are **not** uniform noise — they scale cleanly with width (6 occluded controls at
375 px, 4 at 390, 2 at 430, 1 at 1440), which is the signature of a layout that was only ever
composed at one width.

## Why nothing caught it

Every gate the project runs is blind to this class by construction:

1. **vitest + jsdom** asserts structure and attributes. jsdom does no layout: it has no
   `getBoundingClientRect` worth reading, no stacking contexts, no `elementFromPoint`. A test can
   assert `Use this view` exists, is enabled and is labelled, and pass while the button is painted
   under an opaque panel.
2. **`curl` and pytest** verify the API. `POST /areas` with a bbox answers `200` in 0.18 s, and it
   does so whether or not any human can reach the control that sends it.
3. **CI job coverage** is lint, types, unit, integration, evals — no viewport dimension appears in
   any of them.
4. The single layout media query in the entire app (`plan.css:34`, `width <= 760px`) was itself
   written without ever being rendered: it anchors a full-width panel at `inset-block: 58px`,
   which lands directly on top of the second row of map controls.

So the honest statement is not "we forgot to test mobile". It is: **the project's whole
verification surface is incapable of expressing "is this control reachable by a thumb", and
nobody noticed that the one property the design document leads with had no gate at all.**

This is the same shape as FAIL-009 (a status command that lied because it was read through
`tail`) one level up: the check ran, the check passed, and the check could not have failed.

## Root cause

Two layers, and the second is the durable one.

**Proximate:** `plan.css:34-40` composes the ≤ 760 px case as a fixed overlay anchored at 58 px
with `max-height: 40vh`, over `.siyur-controls` at a lower `z-index`. Nothing about that is
unreasonable on paper; it is simply wrong when rendered, and it was never rendered.

**Underlying:** the definition of "done" for a deliverable unit is a green pipeline, and the
pipeline measures the machine's view of the app. `AGENTS.md` tells agents to verify with
`pytest`, `vitest` and `curl`; it does not tell anyone to look at the thing. Nine units complied
exactly and produced an app whose first step cannot be completed.

## Guardrail

**A rendered-viewport gate, in CI, that fails on the properties the design document actually
states.** Not a screenshot-diff suite (too brittle, and it would have passed here too — the
"correct" rendering was never captured). An **assertion suite over real layout**:

For each of **375 × 667, 390 × 844, 430 × 932**, in a real browser engine (Playwright; the repo
already has `web/test/e2e`), on the primary screens:

1. **Reachability** — for every visible interactive element, `elementFromPoint` at its own centre
   must return that element or a descendant. This is the assertion that would have caught
   `Use this view`, and it is the one jsdom can never make.
2. **Tap targets** — every visible interactive element ≥ 44 × 44 px.
3. **Type floor** — every element with a text node ≥ 14 px (chips allowlisted at ≥ 11 px, listed
   explicitly so the allowlist is a decision and not a leak).
4. **No horizontal overflow** — `scrollWidth === clientWidth` on the document. (Passes today;
   keep it passing.)
5. **Attribution is visible** — the ODbL credit must pass the same `elementFromPoint` check at
   every viewport **including desktop**, because that is where it fails today.

Checks 1 and 5 are the load-bearing ones: they are cheap, they are binary, and both defects that
most embarrass this audit are exactly what they assert.

**Why in CI and not a checklist:** a checklist is a discipline, and this project has already
recorded (AGENTS.md, on branch protection) that a discipline nobody can enforce is a discipline
that lapses. The occlusion check is three lines of Playwright per screen.

**This entry does not close until that suite is in CI and failing on today's `main`** — it must
be demonstrated red before it is allowed to be green (Article IV).

## What it cost, stated plainly

Nothing in production — there is no production. What it cost is the credibility of the phrase
"nine units merged with green CI", and roughly three days of work now sequenced after the fact
(the Tier 1 fix plan in `docs/design/ux-audit-2026-08-15.md`). The cheaper lesson, available at
DU-00 for the price of one browser window, is that **a design document's leading constraint
should acquire a gate in the same commit that adopts the document.**

## Related

- **FAIL-009** — a check that ran, passed, and could not have failed. Same class, different tool.
- **ADR-0035** — the mobile-first layout strategy proposed in response to this.
- `docs/design/ux-audit-2026-08-15.md` — findings UX-01, UX-05, UX-06, UX-10, UX-11, UX-12, UX-15.
</content>
