# 0030 — Attribution scope: sourced values carry a chip, the user's own composition carries a caption

- Status: proposed
- Decision Maker(s): Ben
- drafted-by: claude-code (Opus 5) · approved-by: _pending_ · Date: 2026-08-09
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

**The genuinely arguable case, and the rule that resolves it.** Some violation messages **quote an OSM `opening_hours` expression verbatim** — `Mo-Fr 09:00-13:00` is commons-derived text, ODbL-licensed, appearing inside a server-composed sentence that carries no chip. That is a real co-presence question and not a pedantic one.

It resolves without a new idiom, because ADR-0019's unit is **the value, co-present in the same frame** — and the stop's `opening_hours` *is* rendered on this same surface, through the funnel, with its ODbL chip, as part of that stop's card. The expression inside the verdict is a **quotation of a value already attributed in frame**, not a second unattributed appearance of it.

So the constraint is:

> **A verdict may quote a commons-derived value only if that same value is also rendered on the same surface through `renderSourcedValue`.** Otherwise the verdict must reference it indirectly — by stop order or name — rather than inlining it.

This keeps the honest case (the hours are on screen, chipped, beside the sentence about them) and forbids the dishonest one (a verdict that is the *only* place an OSM string appears, with no stamp anywhere in frame). It is checkable rather than a matter of taste, which is the point.

**Scope note.** This amendment does not widen to every server-computed string. It covers values the server *derives about user data* and returns without a `SourceRef`. A server-computed value that summarises **commons** data is a different question and is **not** decided here — if one appears, it needs its own ruling, because the honest answer there may well be a chip.

### Confirmation (A1)

- **`web/test/plan.test.ts`** — a rendered feasibility violation carries **no chip**, and appears under a caption that names it a server-computed verdict rather than under the user-composition credit. Mutation: caption the violations as user composition → red.
- **The quotation rule is tested, not just written:** a violation message quoting an `opening_hours` expression renders only on a surface where that stop's `opening_hours` is itself rendered through the funnel. Mutation: render the violation while suppressing the stop's hours chip → red. **This is the load-bearing assertion of A1** — without it the rule is a paragraph, and the failure it guards against (an OSM string on screen with no stamp anywhere in frame) is exactly an FR-004 breach.
- **Owed:** the three surfaces now demonstrating three idioms (chip / composition caption / verdict caption) make the AST guard in the parent Confirmation more urgent, not less. Still not discharged.
