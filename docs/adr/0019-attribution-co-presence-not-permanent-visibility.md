# 0019 — Attribution is *co-present* with its value, not *permanently visible*

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: Ben · Date: 2026-08-06 · accepted: 2026-08-08

> **Ratified 2026-08-08 on the value-scoped reading of FR-004**, with option F as an **explicitly
> interim** state rather than the destination. One consequence below is discharged, three items
> are pulled forward, and one contradiction is flagged rather than settled. See the amendment at
> the end.

## Context and Problem Statement

PR #64 (`fix(web): render dense site markers as dots, peek attribution on demand`, merged as
`bb9f05e`) changed how attribution reaches the user. Before it, every marker rendered the site's
display name **and** its source + license chip permanently. Over the Rhodes old town `GET /sites`
returns ~780 records; ~780 name+chip labels inside a 700 px square overlap into unreadable noise.
After it, a marker is a **dot**, the name arrives with its chip on hover/focus or in the popup on
click, and permanent labels survive only at or below `SITE_LABEL_DENSITY_LIMIT = 12` sites.

That is a reinterpretation of a merge-blocking requirement, and the implementing session said so
in its own module header rather than shipping it silently:

> "attribution may move behind an interaction when density makes it illegible; the invariant is
> co-presence of value and stamp, not permanent visibility."

The requirement being reinterpreted, verbatim:

- **FR-004** (`specs/001-research-cited-sites/spec.md:77`): "The map MUST render each researched
  place at its real location, **each carrying a visible source + license attribution chip**. The
  ODbL attribution '© OpenStreetMap contributors' MUST render whenever OpenStreetMap-derived data
  is shown."
- **SC-002** (`spec.md:100`): "**100%** of values displayed on the map carry a source + license
  stamp — zero unstamped values are ever shown (provenance completeness is total, not sampled)."
- **Constitution Article V** (`.specify/memory/constitution.md:80-87`), "Provenance is mechanical":
  "every curated value is stamped at ingestion with `source + license + bundleable` flag; the
  narration and bundle steps **refuse unstamped input** … ODbL attribution ('© OpenStreetMap
  contributors') **renders on every map and credits screen**".
- **`contracts/sites.md:35`**: "Each marker shows, **per displayed value**, a source + license
  attribution chip (FR-004). The chip text comes from the value's `source` … **the client never
  invents attribution**."

**The three clauses are not equally strained, and that matters.**

- *Article V is not strained at all.* Its data clause governs **ingestion and refusal**, not render
  permanence; its only rendering clause is the map-level ODbL credit, which #64 did not touch. The
  claim that this ADR reinterprets the constitution is, on the actual wording, **too strong** — the
  constitution's rendering obligation is satisfied unchanged.
- *SC-002 is not strained either.* It quantifies over **values displayed** ("100% of values
  displayed … carry a stamp"). A dot displays fewer values; of those it displays, all are stamped.
  A criterion phrased as a rate over what is shown cannot be broken by showing less.
- *FR-004 is where the strain is real.* Read literally, its "each carrying" attaches to "each
  researched place" — so a place, once rendered, carries a visible chip. Under dot mode a place
  rendered without interaction carries none. **`contracts/sites.md` already glosses FR-004 the
  other way** — "per displayed value" — and that value-scoped reading is the one this ADR adopts.
  The gloss predates #64; it is not a rationalisation written for it.

### What was checked against the merged code, and what it showed

The implementing session's defence rests on four claims. All four were checked; three hold as
stated, one holds with a caveat.

1. **"A dot displays no *nameable* value; the only value it renders is `site.location`, whose chip
   has always lived in the popup, never on the marker."** **Verified.** In the pre-#64 tree
   (`f24c75d:web/src/map/sites.ts`) `buildMarkerElement` appended exactly a pin plus
   `renderSourcedValue(displayName(site))`; `location`'s chip appeared only in
   `buildPopupContent`'s `LOCATION` row. The dot's *position* is a rendering of `site.location`
   without a co-present chip — but that was true before #64 and is unchanged by it. Dot mode
   inherits location's existing footing; it does not mint a new exemption.
   A stronger version of the same point also holds: pre-#64, a site with **no** stamped name
   already rendered as a bare pin with no label and no `aria-label` at all. The "marker showing no
   attributed value" state was already shipped and merged.
2. **"There is no code path from a value's text to the DOM that skips its chip."** **Verified.**
   `renderSourcedValue` (`web/src/map/attribution-chip.ts:74-100`) builds the chip *first*, returns
   `null` when the value is unstamped, and otherwise emits `wrapper.append(text, chip)` — one
   element, both parts. A grep of `web/src` for `.value` reads outside the funnel returns exactly
   one display-relevant hit, `site.location.value.coordinates` (`sites.ts:347`), which positions
   the marker rather than rendering text. **This is a review discipline, not a mechanical guard** —
   nothing fails CI if a future module reads `.value` directly.
3. **"The ODbL / `attribution[]` control stays visible at all times."** **Verified.**
   `OdblAttributionControl` (`web/src/map/attribution.ts`) renders `ODBL_ATTRIBUTION`
   unconditionally in `onAdd`, before any response, with `maplibregl-compact-show` so it is not
   collapsed behind the "ⓘ" toggle. No interaction gates it.
   ✎ *This is the clause Article V actually requires, and it is intact.*
4. **"The dot's accessible name is `<name> <chip text>`, so assistive tech needs no interaction."**
   **Verified for named sites, and false for unnamed ones.** `buildMarkerElement` sets
   `aria-label` from `label.textContent` — but only after the `if (!label) return element` early
   exit, and `tabIndex = 0` / `role="button"` are set *before* it. A site with no stamped name is
   therefore a **focusable `role="button"` with no accessible name**, which is a WCAG 4.1.2 defect.
   `web/test/sites.test.ts:516-521` currently pins that behaviour
   (`expect(element.getAttribute('aria-label')).toBeNull()`). This is a pre-existing condition #64
   carried forward, not one it introduced, and it lives outside this ADR's file set — flagged, not
   fixed.

### What the ux-handoff actually says (the claim did not fully survive checking)

The implementing session cited the mock as drawing "plain ink circles with **no** chips on the map
surface". **Half of that is right and half is not**, and the half that is not cuts against the
shipped design:

- ✅ **No chips on the map surface.** `docs/design/ux-handoff/README.md:52` (§ Schematic map)
  specifies "numbered ink circle markers (11px r) … handwritten Caveat labels with paper-colored
  halo … `© OpenStreetMap` visible on every map". Per-field stamps appear in the place-record
  sheet, not on markers — README:60 (Screen 2) lists "place-record sheet: merged facts w/ per-field
  stamps", and `siyur-screens-v2.dc.html:128-141` renders them there (`WIKIVOYAGE`, `OSM`,
  `WIKIPEDIA`, `CC BY-SA 4.0`).
- ❌ **Not unlabelled circles.** `siyur-screens-v2.dc.html:99-110` draws **five** markers of which
  **four carry permanent handwritten name labels** (`{{ L.site1 }}`…`{{ L.site4 }}`). Five sites is
  the *sparse* case — below `SITE_LABEL_DENSITY_LIMIT` — so the mock depicts the `labelled` branch,
  not the dot branch. The dot branch has no depiction in the handoff at all.
- ⚠️ **The mock's split is a different one, and this ADR's invariant forbids it.** The handoff puts
  the **name on the map permanently and its chip nowhere near it** (the sheet). That is precisely
  "value without co-present stamp". The shipped implementation is **stricter than the authoritative
  UX spec**, not looser. Whoever builds the M2 schematic map will hit this head-on.
- ✅ `INTEGRATION.md:25` does stage the schematic map at **M2**; the handoff keeps "the
  provenance-stamp system and `© OSM` attribution" in **M1** (`INTEGRATION.md:24`) as "the trust +
  license spine".

## Considered Options

**A — Keep permanent name+chip on every marker (status quo ante).** Literal FR-004 compliance.
Costs: at ~780 markers the labels overlap into noise, and a chip like
`OSM · ODbL-1.0 · © OPENSTREETMAP CONTRIBUTORS` runs ~330 px, so collisions start at a dozen
labels within ~100 px. A requirement to make provenance *visible* is not met by rendering it
unreadable: this option passes the letter while defeating the purpose. Also ~780 permanent label
subtrees in the DOM.

**B — Cluster markers.** Standard, familiar, and it genuinely fixes density. Costs: a cluster
bubble displays a **count**, which is a client-computed value with no `source` stamp of its own —
so clustering *introduces* an unstamped displayed value onto the map. Against SC-002 ("zero
unstamped values are ever shown") that is a **worse** position than a dot, which displays nothing
new. It also hides individual places entirely until the user zooms.

**C — A fixed top-N labelled.** Bounded label count by construction. Costs: it requires **ranking
places by importance**, and there is no place-neutral way to do that. Client-invented importance
is invention; imported importance carries the source's bias. FR-001 / SC-005 make genericity a
standing eval ("nothing hardcoded per place"), and "which places deserve a name" is exactly the
judgement that eval exists to prevent. Rejected on genericity grounds, not ergonomics.

**D — Zoom-gated labels.** Cheap, conventional, no ranking. Costs: **zoom is a proxy for density
that fails on the any-area requirement.** A dense old town and a rural valley at the same zoom
differ by orders of magnitude in marker count, so any zoom threshold is tuned to one kind of place
— the failure mode SC-005 (≥3 areas of different character) is designed to catch.

**E — A MapLibre `symbol` layer over a GeoJSON source, with collision-aware labelling.**
MapLibre's own label engine does collision detection natively (`text-allow-overlap: false`), which
is the real end state for a map with hundreds of labels, and it decides collisions geometrically
rather than by ranking places. Costs: chips are **DOM** — a `siyur-chip` span whose solid/dashed
border encodes `bundleable` (the ux-handoff's systemwide encoding). A `symbol` layer renders text
and sprites, not DOM, so the chip's visual encoding must be re-implemented as a sprite or dropped
— and dropping it is exactly the co-presence violation. It also replaces `Marker`-per-site
throughout `sites.ts`. Correct destination, materially larger than a presentational fix.

**F — Density-gated permanent labels + interaction-revealed name+chip (shipped in #64).**
Permanent labels at ≤12 sites; above that, dots whose name+chip appear together on hover, on focus,
or in the popup. The reveal is `renderSourcedValue`'s output either way, so name and chip are
physically the same element.

## Decision Outcome

Chosen: **F**, and the invariant it implies is stated here as a rule rather than left in a code
comment.

> **The attribution invariant — co-presence, not permanence.**
> Whenever a value's text is rendered on any surface, its `source + license` stamp is rendered
> **in the same element, in the same frame, on the same surface**. What may vary with density is
> **whether a value is rendered at all** — never whether its stamp accompanies it.
> Independently, and not subject to density: the map-level ODbL / `attribution[]` credit renders
> unconditionally, gated by no interaction (Article V, FR-004 second sentence).

**This forbids, concretely:**

1. Rendering a value's text anywhere without its chip in the same element. *(This, not dot mode, is
   what an FR-004 violation looks like.)*
2. A "names always, chips on request" mode — splitting value and stamp across two surfaces or two
   interactions. **The ux-handoff's own Screen 2 does exactly this**, so the M2 schematic map is
   already on a collision course with the rule (see the trigger below).
3. Putting the map-level ODbL credit behind a toggle, a hover, or a collapsed control.
4. Deciding *which* places keep labels by ranking them. The density rule **counts** the response
   and applies one decision uniformly (`SitesLayer.render`); it never ranks, so no place loses its
   name for being the wrong kind of place.
5. Gating co-presence on a pointer. Where a value is interaction-revealed, the accessible name must
   carry value **and** stamp so assistive technology needs no interaction at all.

**Why the reinterpretation is defensible rather than convenient:** the load-bearing claim — that a
dot's only rendered value is `location`, whose chip was already popup-only — was checked against
the pre-#64 tree and holds (§1 above). So dot mode does not create an exemption; it extends one
that FR-004 has tolerated since the feature shipped. And literal compliance is preserved wherever
it is physically achievable: at ≤12 sites every marker keeps its permanent name+chip label.

**This ADR recommends; it does not decide.** FR-004 is a merge-blocking requirement and the reading
adopted here narrows it from place-scoped to value-scoped. That the `contracts/sites.md` gloss
already reads it that way is strong support, but reconciling spec and contract is Ben's call. If
the place-scoped reading is preferred, **E** is the option that satisfies it at ~780 markers, and
#64 should be treated as an interim state rather than the destination.

### Consequences

- Good: the dense research map is legible. Both the value and its stamp remain reachable at every
  density, by hover, by keyboard focus, and by click.
- Good: co-presence is **structural, not procedural** — the label element *is* `renderSourcedValue`'s
  output, so "name without chip" is not a mistake this module can make.
- Good: the sparse case is byte-for-byte the old behaviour, so literal FR-004 compliance is retained
  wherever it can actually be honoured.
- Good (incidental): popups are now built on first open, not for every marker up front — ~20 000
  DOM nodes measured on a dense viewport, of which the user opens perhaps one.
- **Bad / accepted cost: a user who never interacts sees no names above 12 sites.** At Rhodes the
  research map is 782 anonymous dots until the user hovers, tabs, or taps. This is a real product
  regression against the ux-handoff, which draws *named* places on the research map, and it is the
  strongest argument for reaching **E** sooner rather than later.
- **Bad / accepted cost: the hover peek is effectively a desktop affordance.** Touch has no hover;
  a touch user's path to a name is the popup (tap), which is the full cited fact list. Correct, but
  a heavier interaction than the desktop peek, and undocumented in the UI.
- **Bad — accessibility, where co-presence is easiest to lose.** Three distinct gaps:
  (a) ~~an unnamed site is a focusable `role="button"` with **no accessible name** (WCAG 4.1.2),
  pinned today by `sites.test.ts:516-521`~~ — **✎ FIXED, and more completely than this ADR asked.
  Verified 2026-08-08.** `aria-label` is now set **before** the early return
  (`web/src/map/sites.ts:305-310`, with the SC 4.1.2 reasoning in the comment), and
  `markerAccessibleName` gives an unnamed marker `"Unnamed place <lat, lon> <location chip>"` —
  carrying **the location's own stamp, read per record** (an Overture site reads
  `OVERTURE · CDLA-Permissive-2.0`, never a hardcoded OSM credit). Five cases pin it under
  *"a focusable marker always has an accessible name (WCAG 2.2 SC 4.1.2)"*, including that it
  holds in labelled mode and that nothing from `names` leaks in.
  **This strengthens the decision's central argument rather than merely closing a bug.** §1 above
  defends dot mode on the grounds that a dot's only rendered value is `site.location`, whose chip
  "has always lived in the popup" — i.e. it leaned on a *tolerated exemption*. That exemption is
  now closed: the coordinates a dot renders carry their stamp co-presently, so rule 5 is
  implemented and tested rather than asserted;
  (b) a screen-reader user tabbing a dense viewport hears the full chip text on **every** marker —
  "· ODbL-1.0 · © OpenStreetMap contributors" ~780 times — which is co-presence honoured at the
  cost of usability;
  (c) a **low-vision user who magnifies but uses no screen reader** gets neither the visual label
  nor the `aria-label`: they see dots. `aria-label` discharges the invariant for AT, not for them.
- Accepted: `SITE_LABEL_DENSITY_LIMIT = 12` **reduces** collisions in sparse viewports; it does not
  guarantee none. Twelve labels can still collide if their places sit within ~100 px of each other.
- Accepted: "no code path renders text without its chip" is enforced by the shape of
  `renderSourcedValue` plus review, **not by a check**. See the owed guard below.

### Confirmation

- **`web/test/sites.test.ts` § "dot markers keep dense viewports readable without weakening
  FR-004"** (8 cases) — in particular *"never shows the name without its chip — the label IS the
  chip's element"* and *"carries name + attribution in the dot's accessible name, with no
  interaction"*. Also pins reveal-on-`focus` (not mouse-only) and the `≤ limit` / `> limit` branch
  in `SitesLayer`.
- **`web/test/attribution-chip.test.ts`** — *"a value without a source is NEVER rendered"*, *"never
  renders a value element without a chip inside it"*, and *"renders the ODbL notice
  unconditionally, before any response"* (the Article V clause).
- **`evals/test_structural.py`** provenance-completeness — SC-002 as a rate over a researched
  fixture area, demanded at 1.0. Note this measures the **data**, not the render; no eval currently
  measures co-presence at the DOM.
- **Owed, and not yet written:** a mechanical guard that no module outside `attribution-chip.ts`
  reads `SourcedValue.value` for display — the one property this ADR leans on that is presently
  review-enforced. A lint rule or an AST test alongside the genericity scan would close it. Also
  owed: a case asserting that an interaction-revealed value's **accessible name** carries the
  stamp, generalised beyond markers, so rule 5 survives the next surface.

### Revisit trigger — the first surface with no interaction model, not a date

Revisit on **either**, whichever comes first:

1. **The first surface that renders values with no interaction available at all** — the compiled
   offline bundle's own renderer, or a printed / PDF itinerary. There "on interaction" is not a
   place attribution can move to, so co-presence must be discharged by **layout**: either a chip
   inline with every value, or a per-page credit block that names each value's source. Note that
   the bundle's existing answer, a regenerated `ATTRIBUTION.md` (Article V), is an **aggregate**
   credit — it does not establish per-value co-presence, and the gap between the two is exactly
   what this trigger exists to force a decision on. Constitution Article I makes the bundle the
   travel guarantee, so this surface is not optional and it is not far off.
2. **The M2 schematic map** (`INTEGRATION.md:25`). The authoritative UX spec draws permanent
   handwritten names on the map with their stamps in the place-record sheet — which rule 2 above
   forbids. Building M2 to the mock means amending this invariant; building M2 to this invariant
   means deviating from the mock. That contradiction is live now and should be resolved when M2
   starts, not discovered inside it.

One forward-looking note, not a trigger: **E** is the destination this decision is deferring, and
it gets cheaper the earlier it is taken — every surface built against `Marker`-per-site DOM is
another surface to port.

---

## Amendment — ratification terms (2026-08-08)

**FR-004 is read value-scoped**, as `contracts/sites.md` already glossed it before #64 existed.
Option **F is ratified as an interim state, not the destination**: the sparse case keeps literal
place-scoped compliance, and **E remains the end state**.

**What is settled.** A value's text never reaches any surface without its `source + license` stamp
in the same element, in the same frame. Density may vary **whether a value is rendered at all** —
never whether its stamp accompanies it. The map-level ODbL credit renders unconditionally, gated
by no interaction. Neither of those is up for revisiting.

**Three items pulled forward, because two of them fire inside the slice already in flight:**

1. **Write the owed mechanical guard.** *(Verified absent 2026-08-08.)* An AST test alongside the
   genericity scan asserting that **no module outside `attribution-chip.ts` reads
   `SourcedValue.value` for display**. This is the one property the whole invariant rests on and
   the only one with nothing behind it but review — and review is exactly what the 2026-08-07
   Footgun-1 violation showed is not a guard. `renderSourcedValue`'s shape makes "name without
   chip" hard; it does not make it impossible for the *next* module.
2. **Decide the bundle's co-presence mechanism at T051, not after it.** Revisit trigger 1 above —
   "the first surface that renders values with no interaction available" — **is the compiled
   offline bundle**, which is DU-06 and in flight now. There, "on interaction" is not somewhere
   attribution can move to, so co-presence must be discharged by **layout**: a chip inline with
   every value, or a per-page credit block naming each value's source. The bundle's existing
   answer, a regenerated `ATTRIBUTION.md`, is an **aggregate** credit and does **not** establish
   per-value co-presence. Whoever implements T051 owes that decision explicitly rather than
   discovering it.
3. **Schedule E rather than treating it as hypothetical.** It is the only option that closes
   consequence (c) — the low-vision user who magnifies but runs no screen reader, and therefore
   gets neither the visual label nor the `aria-label`. No test can catch that gap and `aria-label`
   does not discharge it. Every surface built on `Marker`-per-site DOM is another port.

**Flagged, deliberately not settled here: the authoritative UX spec contradicts this invariant.**
`ux-handoff` Screen 2 puts the place name permanently on the map with its stamps in the
place-record sheet — value and stamp on two surfaces, which rule 2 forbids. The shipped
implementation is therefore **stricter than the design authority**, not looser. Building M2's
schematic map to the mock means amending this ADR; building it to this ADR means deviating from
the mock. That is a live contradiction with a known owner, and it should be resolved **when M2
starts, not inside it**.
