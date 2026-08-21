# 0037 — Attribution is co-present with its *stop*, not with every value

- Status: **accepted**
- Decision Maker(s): Ben
- drafted-by: claude-code (Opus 5) · approved-by: Ben · Date: 2026-08-21 · accepted 2026-08-21
- **Amends ADR-0019.** This narrows the co-presence invariant's *scope*; it does not repeal it.
  ADR-0019 stays in force everywhere this ADR does not name.

## Context and Problem Statement

Driving the product after DU-06b (`docs/design/ux-review-2026-08-21.md`, R-05) measured the plan
panel rather than judging it:

```
2,874  characters of text in the plan panel
  840  of them attribution           →  29 %
   21  chips rendered
    2  distinct chip strings
```

**Two strings, repeated twenty-one times, are 29 % of the day.** `236 m` carries a 45-character
`OSM · ODbL-1.0 · © OpenStreetMap contributors` chip. So does `3 min`. So does the next leg, and
the next. The day cannot be skimmed, which is a problem for the one artefact a traveller reads
while walking.

This is not a styling complaint and it cannot be fixed in CSS. **ADR-0019 ratified co-presence as
a rule about elements**, and it forbids the obvious remedy in as many words:

> Whenever a value's text is rendered on any surface, its `source + license` stamp is rendered
> **in the same element, in the same frame, on the same surface**.

with the first forbidden case being "rendering a value's text anywhere without its chip in the
same element", and the second being any split of value and stamp "across two surfaces or two
interactions". A per-stop `Sources:` line is squarely inside both prohibitions. Constitution
Article V and FR-004 sit behind ADR-0019, so this is a licence question wearing a CSS costume and
must be decided as one.

**What the licence actually requires is weaker than what ADR-0019 requires.** ODbL-1.0 obliges a
work to carry the attribution notice and name the licence; it does not oblige the notice to be
adjacent to each individual derived figure. CDLA-Permissive-2.0 likewise. ADR-0019 chose a
stricter product invariant than the licences compel — deliberately, and for a good reason: it
makes compliance *mechanical* rather than a judgement someone has to re-make per surface. The
question here is whether the mechanism can be re-scoped without becoming a judgement again.

## Considered Options

- **(a) Keep per-value.** Zero cost, zero risk to ADR-0019, and the day stays unreadable at 29 %.
- **(b) Per stop — one `Sources: …` line per stop.** 21 chips → ~6 lines. Same licences named,
  same sources named, in the same frame and on the same surface as the values they cover.
- **(c) Per view — one line per rendered day.** Cheapest to build. The link between a value and
  its source becomes "somewhere in this day", which is where mechanical compliance stops being
  mechanical: a reader cannot tell which source a given figure came from.
- **(d) Chip on hover/expand, source line always visible.** Preserves the per-value link exactly,
  but is the case ADR-0019 forbids *by name* ("a 'names always, chips on request' mode"), costs
  the most, and puts a hover interaction on a phone.

## Decision Outcome

Chosen: **(b), per stop**, with the invariant restated rather than deleted.

> **The attribution invariant, amended — co-presence with the smallest block that owns the value.**
> Whenever a value's text is rendered, its `source + license` stamp is rendered **in the same
> frame, on the same surface, within the same enclosing block**, where the enclosing block is the
> smallest structural unit that owns the value — a **stop** in an itinerary, a **site** in a
> detail view, a **value** wherever no larger owning block exists.
> Unchanged and not subject to this amendment: the map-level ODbL / `attribution[]` credit
> renders unconditionally, gated by no interaction (Article V, FR-004 second sentence).

**What stays forbidden**, carried forward from ADR-0019 unchanged:

1. Splitting value and stamp across two **surfaces** or two **interactions**. A stop's sources
   line is in the same frame as its values, visible without a pointer. Option (d) remains refused.
2. Putting the map-level ODbL credit behind a toggle, hover or collapsed control.
3. Ranking which values keep their stamps. The rule applies uniformly to every stop or to none;
   it never decides that *this* figure is important enough to stamp.
4. Gating co-presence on a pointer: where anything is interaction-revealed, the accessible name
   carries value **and** stamp.

**What changes:** the unit of co-presence moves from the element to the smallest owning block.
A stop's `Sources:` line must name every distinct `source + license` among the values inside that
stop — derived by the renderer from those values, never authored — so that a stop showing a value
whose licence is not in its own sources line is a bug a test can catch, not a judgement call.

**Why this is defensible rather than convenient.** The property ADR-0019 was protecting is that
*compliance is computed, not remembered*. Per-stop keeps that property exactly: the line is a
`Set` over the stop's own values, so adding a value with a new source changes the line for free,
and a value can never appear on a surface whose sources line does not name its licence. Option
(c) is the one that would have broken it, because "the day" is not a block that owns any
particular value, and that is why (c) is refused despite being cheaper.

## Consequences

- **C-1 (R-05) is unblocked** and stays at ~1.5 d rather than the 2.5 d+ an unratified reading
  would have cost.
- **ADR-0019's tests change.** The current suite asserts chip/value co-presence *in the same
  element*; those assertions become "in the same owning block", and the renderer gains a
  derivation that must be tested for completeness (every licence among a stop's values appears in
  its line) and for honesty (no licence appears that no value carries).
- **The M2 schematic-map collision noted in ADR-0019 is unchanged.** Screen 2 splits value and
  stamp across interactions, which this amendment still forbids.
- **Nothing about bundles or `bundleable` changes.** This is a rendering-scope decision only.

## Trigger to revisit

Any surface where the "smallest owning block" is not obvious from the data model — a mixed list,
a summary drawn from several stops — is a signal that the block-scoped rule has run out, and the
answer there is per-value, not a new judgement.
