# FAIL-015 — The fix worked in dev and shipped broken, because the artifact under test was not the artifact that ships

- Date: 2026-08-15 · Severity: high (would have shipped the exact defect the change existed to
  fix — a 398 px panel inside a 375 px viewport — while every test and every manual check said
  it was fixed)
- Root-cause class: build/tooling (a stylesheet ordering that only exists after bundling, and a
  test suite that never looked at the bundle)

## Symptom

Phase B's F-01 moved the plan panel out of a fixed overlay into the flow column below 760 px
(ADR-0035). In the dev server it was correct at 375 and 390 px: panel in flow, nothing
occluded, no overflow. The stylesheet scan passed. Unit tests passed.

**In `dist/` the panel was still `position: fixed`**, and because a fixed box with `inset: auto`
shrink-to-fits, it came out **398 px wide inside a 375 px viewport**, dragging seven descendants
past the edge.

## Root cause

Three ordinary facts, none of them a bug on its own:

1. The mobile override (`position: static`) was written into **`style.css`**, while the base
   rule it overrides (`position: fixed`) lives in **`plan.css`**.
2. **A media query adds no specificity.** `@media (max-width: 760px) { .siyur-plan-panel { … } }`
   and `.siyur-plan-panel { … }` have identical specificity, so the winner is whichever comes
   **last in source order**.
3. `main.ts` imports `style.css` **before** `plan.css`, and the bundler concatenates in import
   order — so in the bundle the base rule comes *after* the override and wins.

In the dev server the same files are served as separate stylesheets in an order that happened to
put the override last. **The cascade differed between dev and build**, and only the build ships.

## Why nothing caught it

- **The unit test asserted the declaration existed**, not that it won. `position: static` was
  present in the CSS text at every stage, in both environments. A grep-shaped assertion cannot
  see the cascade.
- **Every manual check used the dev server**, which is the environment where it worked.
- **No test looked at `dist/`.** The suite tested source; the user gets a bundle.

The general shape, and the reason this entry is worth its length:

> **A test that examines source can only ever be evidence about source.** Anything the build
> *does* — concatenation order, minification, tree-shaking, chunk splitting, CSS ordering — is
> invisible to it, and those are precisely the transformations that can change behaviour without
> changing a single character of what you wrote.

This is the same family as FAIL-010 (a healthcheck measuring the wrong proposition) and the
`gh pr checks | tail` truncation of FAIL-009: **a control that was truthful about the thing it
examined, and silent about the thing that mattered.**

## Guardrail

1. **The override lives in the file that owns the base rule.** `plan.css` now carries both, so
   import order cannot separate them. Cheap and structural — it removes the failure rather than
   detecting it.
2. **The unit test asserts *same file, later offset*,** not "the declaration exists". A rule that
   loses the cascade now fails, and the assertion says why in those terms.
3. **The viewport gate runs against the built app** (`web/test/e2e/viewport.spec.ts`, CI job 5).
   DU-06a built it that way independently; this entry is the strongest argument for that choice,
   because the defect was invisible to every source-level check and obvious to one rendered
   frame of the bundle.

**This entry closes with (1)–(3) in CI.** All three are in.

## What it cost, stated plainly

Nothing, because it was caught before merge — but only by measuring the built artifact on a
whim, after the change had already been declared done. Had the artifact test not existed, the PR
would have merged with a passing suite, a passing stylesheet scan, and the precise defect it was
written to remove.

## Related

- **FAIL-012** — the rendered-viewport gate. Written before this happened; this is the incident
  that justifies its "run against the build" decision after the fact.
- **FAIL-010** — `Up (healthy)` for a router that could not route where we needed. Same class:
  a green signal about the wrong proposition.
- **FAIL-009** — a truthful command truncated into a misleading answer.
