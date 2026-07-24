# Handoff: Siyur — Tour-Day Map Studio (UX/Visual Design Track)

## Overview
Complete visual language + key-screen designs for **Siyur** (סיור), the multi-user tour-day platform (PRD v2.0). Covers the mobile experience for all four phases — Define area, Research, Plan, Compile — plus the offline Travel field guide with navigation. English (LTR) and Hebrew (RTL) versions of every screen.

## About the Design Files
The `.dc.html` files in this bundle are **design references created in HTML** — interactive prototypes showing intended look and behavior, **not production code**. The task is to recreate these designs in the target stack from the PRD (**PWA: Vite + MapLibre GL JS 5.19 + PMTiles**, React or equivalent), using its established patterns. Map vignettes in the mocks are hand-drawn SVG stand-ins for a real MapLibre render styled to the palette below.

## Fidelity
**High-fidelity.** Colors, type, spacing, copy tone, and interaction patterns are final direction ("The Field Atlas"). Recreate faithfully; map rendering itself comes from MapLibre + a Protomaps Flavor delta tuned to these tokens.

## Design Tokens — "The Field Atlas"

### Color (light / planning phases)
| Token | Hex | Use |
|---|---|---|
| paper | `#f4efe4` | app background, cards |
| paper-deep | `#efe7d5` | map ground, inset panels |
| desk | `#e7e2d5` | page background behind cards |
| ink | `#2b2117` | primary text, active blocks, main rail |
| ink-soft | `#5c503f` | secondary text |
| stone | `#8a7a63` | tertiary text, metadata, dashes |
| border | `#c8bda6` / `#d9cdb2` | hairlines, input borders |
| route | `#b3542e` | route lines, primary CTA, active markers |
| park/plan-B | `#3f6a5a` | greens, Plan-B branch, approve CTA, success |
| water | `#7d9bb0` | water fills (≈40% opacity on paper) |
| meal/anchor | `#d9a441` | meal stops, warnings, pace accents |

### Color (dark "field mode" — Travel)
`#241d15` bg · `#2a2218` map ground · `#31404d` water · `#3a3125` raised panels · `#4a3f30` streets · `#6d5f4c` borders · `#9a8a72` secondary text · `#f4efe4` primary text · `#e08b5e` route/accent (lightened for contrast) · `#d9a441` status.
**DECISION (recorded, not yet re-mocked): Travel defaults to a LIGHT sun-first palette (paper tones, heavier ink weights) for outdoor legibility; the dark field mode shown in the mocks becomes the automatic night variant.** Build both from the same token set.

### Typography
- **Display / stories (EN):** Libre Caslon Text (700 titles, italic 400 for story prose)
- **Display / stories (HE):** Frank Ruhl Libre (700/500)
- **UI (EN):** Work Sans 400–700 · **UI (HE):** Heebo 400–700
- **Metadata / provenance / times:** IBM Plex Mono, letter-spacing .10–.16em, uppercase
- **Hand-written map labels (EN):** Caveat. HE equivalent: pick a Hebrew handwriting face (e.g. Amatic SC HE subset or similar) — open item.
- Mobile minimums: body 11.5–13px in mocks at 340px width — scale to ≥14px at real 375–430px widths. Tap targets ≥44px.

### Radii & shadows
Cards 8–12px · sheets 18px top corners · pills 99px · phone-frame content 26–30px. Shadows sparse: `0 2px 8px rgba(43,33,23,.12)` (floating controls), `0 -8px 30px rgba(0,0,0,.4)` (sheets over dark maps).

## Signature motifs

### Provenance stamps (trust is a feature)
- **Bundleable fact:** mono 8–8.5px chip, solid 1px `stone` border, `paper-deep` fill — e.g. `WIKIVOYAGE`, `OSM`, `CC BY-SA 4.0`, `COMPUTED`.
- **Link-only / needs network:** same chip but **dashed** `route`-colored border — e.g. `GOOGLE 4.7★ ↗`, `WEBSITE ↗ NEEDS CONNECTIVITY`. Dashed = leaves the bundle. This encoding is systemwide.
- Conflict flag: `⚑` + route color, on a dashed top border inside the facts card.

### Schematic map
Real MapLibre base desaturated to paper tones; itinerary layer: wobble/hand-drawn feel (turbulence-displaced strokes in mocks), dashed animated route in `route`, numbered ink circle markers (11px r), Plan-B nodes in `park` green, handwritten Caveat labels with paper-colored halo (`text-shadow: 0 0 4px paper ×2`). `© OpenStreetMap` visible on every map.

### Dynamic timeline — the Fork Strip (chosen: option "2c Fork Duet")
Horizontal metro-line strip: main line = Plan A (2.5px ink), stops as 6px-r nodes; **Plan B arcs below** the main line (dashed `park` green), diverging at its trigger stop and rejoining; Plan C likewise. Pager `A ● B ○ C ○`; strip swipes sideways with snap-to-stop; **tap expands** it into a vertical drag-editable ledger (duration-true blocks, ≡ reorder handles, ▬▬ stretch handles), collapses back to the strip when chat or map has focus. In RTL the strip runs **right-to-left** (mirrored coordinates — see the HE variant in the mocks).

## Screens (all in `Siyur Screens v2.dc.html`, EN + HE side by side)

1. **Define the area** — full-bleed map + floating search pill; draw (✎) & zoom controls; bottom sheet: commons coverage card (green dot, "23 places researched · refreshed 3 days ago", EST-RESEARCH stamp), preference chips (ink pills + dashed "+ add"), CTA `Start researching →` (route color).
2. **Research & collect** — schematic map with live "RESEARCHING…" chip (pulsing); coverage bar chart strip (done=green, active=route+pulse, pending=neutral); place-record sheet: merged facts w/ per-field stamps, conflict flag, link-only review chips, CC BY-SA story with `meal`-colored quote bar; actions `Dig deeper` / `Plan my day →`.
3. **Plan — Fork Duet** — map (ghost-route branch preview) + fork strip + chat. Agent replies are **diff cards**: mono label `PROPOSED · PROMOTES BRANCH B → MAIN LINE`, then `−` (route) / `+` (park) / `→` (stone) rows, actions `Promote B ✓` (park pill) / `Preview first` (outline). Approval gate: full-width park-green `✓ Approve itinerary`.
4. **Compile** — verification checklist rows turn green one by one (tiles → routes A/B/C → narrations → source refs → licenses → integrity hash pending w/ pulsing amber dot); bundle size + `↓ Download to this device` (route CTA). A product moment, never a spinner.
5. **Travel** — see prototype below. Status line always shows `✈ OFFLINE · BUNDLE OK ✓`.

## Travel navigation (chosen: "1a Street Duet", prototyped in `Travel Nav Prototype.dc.html`)
- **Maneuver banner:** icon (↰/⇧/◉) 30px + "In 40 m, turn left" 16.5px/700 + street subline + `IN-BUNDLE ✓` stamp. Text comes from the **precomputed Valhalla leg maneuvers in the bundle** — no voice, no live rerouting.
- **Detailed street map** rendered from bundle PMTiles (street names, buildings) in field palette; GPS dot 12px ring + **heading cone** (35% accent wedge) rotating with bearing; distance chip `450 M LEFT · GPS ±4 M`.
- **STREET ⇄ SCHEMATIC** toggle (top-left pills) — same dot, two renders.
- **Google Maps escape hatch:** `https://www.google.com/maps/dir/?api=1&destination=<lat,lng>&travelmode=walking` deep link, labeled `ONLINE`; greys out with "needs connectivity" when offline. Assume online is available for this; the guide itself never needs it.
- **Arrival:** geofence on the stop → story sheet slides up (`cubic-bezier(.3,1,.35,1)`, 500ms): `YOU'VE ARRIVED · STOP 3 OF 5`, Caslon title, italic story w/ attribution stamps, `▶ Play story` / `Facts` / `Next leg →`.

## Interactions & behavior (cross-cutting)
- Route lines animate: `stroke-dasharray 9 6` + dashoffset keyframes (~1.6s linear infinite).
- Live/in-progress indicators pulse (opacity 1→.35, 2s).
- Chat: user bubbles ink/paper-text, radius `10px 10px 3px 10px` (mirror in RTL); agent bubbles paper-deep w/ border, radius `10 10 10 3`. Every agent claim carries a stamp inline.
- Diff-card `Preview first` highlights the affected strip nodes/blocks before Apply.
- Sheets: 36×4px drag pill, drag-to-collapse; timeline states: expanded ledger ⇄ mini-strip (proportional, branch dot visible, `PLAN B ACTIVE*` when previewing) ⇄ hidden.
- Timeline ledger scrolls vertically under a **sticky time gutter** + bottom fade; composer and COLLAPSE pill never move. Fork strip pans sideways w/ snap.

## RTL rules (Hebrew is first-class)
`dir="rtl"` + CSS **logical properties everywhere** (`inset-inline-start`, `border-inline-start`, `padding-inline`…). Mirror: chat bubble radii, send arrow (→ becomes ←), CTAs (`← להתחיל לחקור`), fork strip direction and branch arc, chip order. Do NOT mirror: clock times, the map itself, numbers. Type swaps: Caslon→Frank Ruhl, Work Sans→Heebo. Hebrew quotes use „…". All strings in the mocks' logic classes are real reviewed Hebrew — reuse them as the seed message catalog.

## State management (per prototype logic)
- Plan: `activePlan (A|B|C)`, strip expanded/collapsed, pending diff (proposal id + preview flag), feasibility status per edit (re-check after every drag).
- Travel: `mode (street|schematic)`, leg progress (from `watchPosition`), current maneuver index, arrived flag, pace vs plan, day/night palette.
- Compile: ordered check states (pending→running→pass) driven by the compiler job's SSE events.

## Assets
- Google Fonts: Libre Caslon Text, Frank Ruhl Libre, Work Sans, Heebo, IBM Plex Mono, Caveat (all OFL — bundle per PRD §7).
- No raster assets. The Google Maps button uses an abstract 4-color conic swatch placeholder — replace with the official icon per Google's brand guidelines when integrating.
- `© OpenStreetMap contributors` attribution is a design element on every map (ODbL requirement).

## Files
| File | What it is |
|---|---|
| `Siyur Screens v2.dc.html` | **Final key screens**, all phases, EN + HE side by side |
| `Travel Nav Prototype.dc.html` | Working simulation of Travel navigation (maneuver banner, STREET⇄SCHEMATIC toggle, arrival sheet, Google deep-link) |

Exploration/alternatives history stays in the design project; everything chosen is fully specified in this README + the two files above.

## Open items for the build session
1. Travel light "sun mode" as default (decision recorded above; mocks still show dark).
2. Hebrew handwriting face for schematic labels.
3. Plan C representation on the strip beyond the pager (currently a walking-leg note).
4. Compile checklist ↔ real compiler-job step mapping.
</content>
