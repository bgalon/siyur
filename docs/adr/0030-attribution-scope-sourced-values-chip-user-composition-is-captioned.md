# 0030 — Attribution scope: sourced values carry a chip, the user's own composition carries a caption

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code (Opus 5) · approved-by: Ben · Date: 2026-08-09 · accepted: 2026-08-11
- Extends: **ADR-0019** (attribution is co-present with its value, not permanently visible)

## Context and Problem Statement

ADR-0019 is ratified on the **value-scoped** reading of FR-004: *no value reaches any surface without its `source + license` stamp in the same element, in the same frame.* Its enforcement is structural rather than procedural — `web/src/map/attribution-chip.ts` returns **`null`** for an unstamped value, and its contract says callers "must treat `null` as *this value may not be displayed*". No default string, no `kind → publisher` map, no fallback. That is what makes "a value without a source is never rendered" a property instead of a review discipline.

ADR-0019's revisit trigger 1 named **"the first surface that renders values with no interaction available at all"** as the point where the decision would need revisiting. That surface arrived at DU-06: the offline travel UI, where there is no hover, no focus peek and no popup-on-demand for attribution to move to. T051 discharged it by **layout** — every commons-derived value renders through the single chip funnel, inline, in the same element.

And then the itinerary itself did not fit.

**`ItineraryV1` carries no `SourceRef` on `Stop.planned_start`, `Stop.order`, `Stop.dwell_min`, or any `Timeline` entry.** Those fields are not observations of the world; they are the *user's own composition* — the shape of their day, produced by a planning session they directed. The card is explicit that an itinerary is user-owned private data.

So the rule, applied literally, forbids rendering the plan:

- `createAttributionChip` returns `null` for a stop time (no `source`)
- its contract says `null` means "this value may not be displayed"
- therefore the traveller's own itinerary may not be displayed

That is obviously wrong, and the wrongness is informative: **FR-004's "value" was written about *curated facts about the world*, and a planned time is not one.** The scope was never stated because until DU-04 every value on every surface came from the commons. Two implementations — `web/src/travel/render.ts` (T051) and `web/src/plan/` (T027) — have now had to take a position on it, and a third (`compiler/attribution.py`, T039) reads the same stamps. Three surfaces working around an unstated boundary is how a convention silently becomes two conventions.

## Considered Options

- **A — Sourced values carry a chip; user-owned composition carries a per-surface caption naming it as the traveller's own.** Two idioms, each honest about what it describes. The absent chip is *explained* rather than indistinguishable from a missing one.
- **B — Stamp plan structure as `SourcedValue` with `kind="user"`, `license="user-owned"`,** so everything chips uniformly and the rule needs no exception.
- **C — Declare plan structure out of FR-004's scope and render it bare,** with nothing said.
- **D — Widen FR-004 so every rendered datum needs a stamp,** and give the itinerary one.

## Decision Outcome

Chosen: **A.**

**Option B is not merely awkward — it is actively harmful, and this is the finding that decides the ADR.** `SourceRef.kind` already contains `"user"` and `license="user-owned"` is a valid spelling, so B *looks* like the tidy answer. But verified by execution:

```
bundleable("user", "user-owned")  →  False
BUNDLEABLE_LICENSES              →  no "user-owned"
```

Stamping a stop time as a `SourcedValue` would therefore make it **`bundleable=False`**, and `compiler/quarantine.py` (T038) drops every `bundleable=False` value before freezing `content`. **The traveller's own itinerary would be quarantined out of the traveller's own bundle** — and it would do so silently, because the bundle would still compile, still hash, and still pass every manifest-path check. That is the same failure shape as the `isinstance(v, Story)` trap already documented on T038, arrived at from the opposite direction.

The alternative repair — allowlisting `user-owned` — is worse still: the quarantine allowlist exists to answer *"may this be redistributed?"*, and `user-owned` is precisely the category the PRD §13 #4 privacy boundary says must **never** be published to the commons. Putting it on a list named "bundleable" invites exactly the confusion the boundary exists to prevent. Personal data belongs in the bundle because it is *the user's own copy*, not because it is redistributable, and that distinction is worth keeping sharp.

**Option C fails for the reason ADR-0019 exists.** An absent chip and a *missing* chip look identical, so silence makes a defect indistinguishable from correct behaviour — the observability failure the chip's `null` contract was designed around.

**Option D inverts the meaning of attribution.** Attribution answers *"who is this from, and under what licence?"* For a time the user's own planning produced, the honest answer is "you", and rendering that as a provenance chip beside chips that mean "© OpenStreetMap contributors" would dilute the ones that carry a real legal obligation.

### The rule

**A value renders with an inline chip if and only if it carries a `SourceRef`.** That is unchanged and remains structural — `createAttributionChip` still returns `null`, and `null` still means *may not be displayed*.

**A surface that renders user-owned composition carries a caption stating so**, once per surface, in words. Not a chip, not a fake stamp, not silence. `web/src/travel/render.ts::createPlanCredit` is the reference implementation; `web/src/plan/` follows it.

**The boundary is provenance, not field type:** if a datum describes the world, it came from somewhere and it is chipped. If it describes the user's own choices, it came from them and it is captioned. A stop's *name* is chipped (it is a fact about a place); a stop's *time* is captioned (it is a choice about a day). The same record carries both, side by side, and that is correct rather than inconsistent.

### Consequences

- Good: the itinerary can be rendered at all, offline, without weakening the `null`-means-do-not-display contract that makes ADR-0019 structural.
- Good: chips retain their meaning. A chip on this surface always signals a real external source with a real licence obligation, which is what makes the ODbL and CC BY-SA credits legible rather than decorative.
- Good: the privacy boundary stays sharp — `user-owned` never enters the bundleable allowlist, so "may be redistributed" and "is the user's own copy" remain different questions.
- Bad / accepted: **two attribution idioms on one screen.** A traveller sees chipped values beside captioned ones. Defensible, because they genuinely have different provenance — but it is more to explain, and a future surface could get it wrong by following whichever example it saw first. That is the cost of this ADR and the reason it exists in writing.
- Bad / accepted: the caption is **per surface, not per value**, so it is a weaker guarantee than the chip. A surface that forgets it degrades to option C silently. The Confirmation below is what makes that catchable.
- Neutral: no schema change. `ItineraryV1` gains no `SourceRef`; `SourcedValue`, `SourceRef` and the allowlist are untouched.

### Confirmation

- **`web/test/travel.test.ts` and `web/test/plan.test.ts`** — a value carrying no `source` is **never rendered** (the existing structural guard, unchanged), *and* a surface rendering plan structure emits its caption. Both were mutation-proved by smuggling a chip-less sourced value through and confirming the guard reddens.
- **The AST guard, still owed and now more clearly owed:** nothing outside `web/src/map/attribution-chip.ts` may read `SourcedValue.value` for display. It is the one property this whole invariant rests on and it is review-enforced only. It belongs beside `evals/test_genericity.py`'s scan. **This ADR does not discharge it** — recorded here so it is not lost a third time.
- **`tests/test_licenses.py`** already pins `user-owned` as **not** allowlisted; that assertion is now load-bearing for this decision, not merely descriptive, and should say so.
- **TODO (DU-06):** the airplane-mode e2e (T056) should assert the caption is present offline, since a caption that only renders online would leave the offline surface at option C — precisely the case ADR-0019's trigger was about.

## Amendments

### A1 — A third category: server-computed verdicts (2026-08-10, drafted-by claude-code · approved-by _pending_)

This ADR was written with two categories in view — **sourced values** (chipped) and **the user's own composition** (captioned). T027 (`web/src/plan/`) then rendered something on neither side, and flagged it rather than deciding it:

```
walking_m 4200 > budget 3000
```

A **feasibility violation** is a *server-computed verdict about the user's plan*. `contracts/plans.md` returns it with no `SourceRef`, and it is stored on the `user_plan` row rather than in the itinerary. So:

- it is **not commons-derived** — there is no stamp to render, and `createAttributionChip` correctly returns `null`;
- it is **not the user's composition** either — the user chose a budget and an ordering; they did not author the sentence saying the day breaches it. The caption "your own plan" is therefore *false* when applied to it.

Left unstated, the third case would resolve itself by whichever example a future surface copied first — the exact failure this ADR was written to prevent, one category down.

**Ruling: a server-computed verdict is captioned, not chipped, and its caption names it as a verdict rather than folding it into the user-composition credit.** Fabricating an ODbL chip for a sentence our own server composed would be a provenance lie in the direction the funnel exists to stop: it would assert an external licensor stands behind our arithmetic. FR-005 requires violations be **named**, so they are rendered verbatim; dropping them was never available.

**Where the caption goes is part of the ruling, not a rendering detail.** A verdict's caption is emitted **inside the section that renders the verdict**, so it travels with it in every branch. It is explicitly *not* folded into the user-composition credit, for two independent reasons — and the second was found by review, in code that had already shipped the mistake:

- *Semantically:* "times, stop order and dwell are your own plan — your data, not sourced data" is a claim about **user composition**. A verdict is a server assertion **about** that composition. Filing it under the user-owned caption tells the reader our arithmetic is their data. Smaller than a fabricated chip, still a false provenance claim.
- *Mechanically:* in `web/src/plan/render.ts` the structure credit was emitted only in the `itinerary` branch while the feasibility section rendered **always**. The one case where a reader most needs the caption — an unreadable itinerary frame with a verdict present — was exactly the case where it was absent. A caption that is not co-emitted with the thing it captions is not a caption.

**The genuinely arguable case: a verdict that quotes commons text.** Some violation messages embedded an OSM `opening_hours` expression verbatim — `Mo-Fr 09:00-13:00` is ODbL-licensed commons text sitting inside a server-composed sentence that carries no chip.

**A first draft of this amendment permitted it conditionally**, on the reasoning that ADR-0019's unit is *the value co-present in the same frame*, and the stop's `opening_hours` is already rendered through the funnel on that same surface — so the quotation was a second appearance of an already-attributed value. The rule read: *a verdict may quote a commons-derived value only if that value is also rendered on the same surface through `renderSourcedValue`.*

**That rule is withdrawn.** It is conditional on a co-presence the implementation does not guarantee: in the unreadable-itinerary branch above, the stop rows do not render at all, so the verdict becomes the **only** place the ODbL string appears, with no stamp anywhere in frame. A rule whose precondition silently fails in one branch is worse than no rule, because it reads as covered. It also pushes the obligation onto every future caller to check a global property of the surface before composing a sentence — which is precisely the review-discipline-instead-of-a-property failure this ADR family exists to eliminate.

The replacement removes the dependency instead of relying on it:

> **A server-computed verdict must not embed commons-derived text.** It refers to the value indirectly — by stop order — and the stop's own chipped value carries the expression. "Stop 2 is outside its opening window", never "…outside opening window `Mo-Fr 09:00-13:00`".

This is enforceable at the point of composition, in one place (`planner/feasibility.py`), with no knowledge of what any surface renders. FR-005 still requires the violation be **named**, and it is: the name is the violation code plus the stop it concerns, which is what a client needs to render an affordance anyway.

**Cost, accepted:** a verdict read in isolation — an API response, a log line, a support ticket — no longer carries the expression that explains it. That is a real loss of diagnostic convenience, and it is the right trade: the alternative leaks unattributed ODbL text onto a surface, and the expression is one join away for anyone holding the plan.

**Scope note.** This amendment does not widen to every server-computed string. It covers values the server *derives about user data* and returns without a `SourceRef`. A server-computed value that summarises **commons** data is a different question and is **not** decided here — if one appears it needs its own ruling, because the honest answer there may well be a chip.

### Confirmation (A1)

- **`web/test/plan.test.ts`** — a rendered feasibility violation carries **no chip**, and appears under a caption naming it a server-computed verdict rather than under the user-composition credit. Mutation: caption the violations as user composition → red.
- **The caption is co-emitted, not merely present.** A feasibility frame arriving with an **unreadable** itinerary frame still renders the verdict caption. Mutation: move the caption back into the itinerary branch → red. **This is the load-bearing assertion of A1**, because it is the exact branch the withdrawn rule failed in.
- **`tests/test_feasibility.py`** — no `Violation.message` contains an `opening_hours` expression, a place name, an address, or any other commons-derived string. Asserted on the message text for every violation code, not on one example. Mutation: re-embed the expression → red. This is the server-side half, and it is where the invariant is cheapest to hold.
- **Owed:** three surfaces now demonstrate three idioms (chip / composition caption / verdict caption), which makes the AST guard in the parent Confirmation more urgent, not less. Still not discharged.

### A1 supersedes nothing in the parent decision

The chip rule is untouched: a value renders with an inline chip **iff** it carries a `SourceRef`, and `null` still means *may not be displayed*. A1 adds a third disposition for data that carries no `SourceRef` and is not the user's own — it does not create an exception to the first two.
