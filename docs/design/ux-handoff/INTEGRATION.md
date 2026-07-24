# UX Handoff — integration notes

Imported 2026-07-24 from the Claude-design track (`design_handoff_siyur 2`). `README.md` here ("The Field Atlas") is the **authoritative UX spec**; the two `.dc.html` files are high-fidelity design references (not production code) to recreate in the PWA stack. Exploration/alternative mocks stay in the design project. This note connects the design to the engineering design (`../tech-design.md`, `../delivery-plan.md`) and flags staging.

## The design and the data model line up (keep this alignment)

| Design element (README) | Engineering counterpart |
|---|---|
| Provenance stamp; **dashed border = "leaves the bundle / needs connectivity"** | `SourcedValue.bundleable = false` (tech-design §1.0). The visual encoding *is* the quarantine flag — keep them the same predicate. |
| Solid stamp `WIKIVOYAGE`/`OSM`/`CC BY-SA`/`COMPUTED` | `SourceRef.kind` + `license` |
| Conflict flag `⚑` in the facts card | `SiteRecordV1.conflicts` / `FieldConflict` |
| Maneuver banner `IN-BUNDLE ✓`, "no live rerouting" | precomputed **Valhalla leg maneuvers** frozen in the bundle (tech-design §5.3) |
| **Fork Strip** (Plan A line, Plan B/C arcs) | `ItineraryV1.variants {B,C}` + `timeline` |
| `© OpenStreetMap` on every map | ODbL attribution requirement (PRD §7) |
| Compile checklist rows → green via SSE | the compile pipeline's SSE progress (tech-design §5.3) |

## Screens → deliverable units

Define area → **DU-01** · Research & collect → **DU-02/03** · Plan (Fork Duet) → **DU-04** · Compile → **DU-05** · Travel (Street Duet + arrival) → **DU-06**.

## Staging flag (important — don't build all the richness in M1)

The mocks show the **full** vision spanning M1–M3. Per `delivery-plan.md`, M1 is deliberately thin:
- **M1:** standard MapLibre render (paper-toned Flavor), a **simple** timeline, English (LTR) — plus the accepted name/address **transliteration sliver**. The provenance-stamp system and `© OSM` attribution are M1 (they're the trust + license spine).
- **M2:** the **schematic (hand-drawn) map**, the **Fork Strip / Plan B/C**, the diff-card planning interaction, narration story sheets.
- **M3:** **RTL / Hebrew** end-to-end (the HE mocks + the reviewed Hebrew strings seed the message catalog), day/night "field mode" palette polish, arrival geofencing depth.

Build the token set and the provenance-stamp components once (they're used everywhere); phase the signature visualizations per the milestones above.

## Decisions this raises (→ ADRs at ramp-up)

1. **Web framework:** README suggests "React or equivalent" — pick React vs Svelte/SolidJS/vanilla for the PWA (`web/`). Not yet chosen in tech-design.
2. **Travel default palette:** README records a decision — Travel defaults to a **LIGHT sun-first** palette (mocks still show the dark "field mode," which becomes the automatic night variant). Build both from one token set.
3. **Hebrew handwriting face** for schematic map labels — open (README open item #2).
4. Bundle the six fonts (Libre Caslon Text, Frank Ruhl Libre, Work Sans, Heebo, IBM Plex Mono, Caveat — all OFL) per PRD §7; capture in `DATA-LICENSES.md`.

## Open items (from README, carried forward)

1. Travel light "sun mode" as default (decision recorded; re-mock later). 2. Hebrew handwriting face. 3. Plan C representation on the strip beyond the pager. 4. Compile-checklist ↔ real compiler-step mapping.

*Not imported (stayed in the design project): the exploration mocks + the standalone Visual Language file — available in the sibling `design_handoff_siyur` folder if we later want them in-repo.*
