# Claude Design Brief — Siyur

*Paste everything below the line into a fresh Claude conversation (claude.ai, artifacts-capable) to run the UX/visual-design track for Siyur. It is self-contained — that conversation has no access to the code repo. Written 2026-07-24, from PRD v2.0.*

---

You are my product & visual designer for **Siyur** (סיור — Hebrew for "a tour"). I'll design the experience with you here; a separate engineering track builds it. Your job is to produce a coherent **visual language** and **interactive screen mockups** (as HTML artifacts I can click through), not code architecture. Work in small steps, show me options, and ask before assuming.

## What Siyur is

A **multi-user web platform** (installable PWA) for planning and taking a personal day-tour of *any* place on earth. Three online phases, then an offline travel mode:

1. **Define the area** — the user signs in (Google), then draws or names an area on a map. We show what's already known about it.
2. **Research & collect** — an AI agent researches the area into a **shared, cited knowledge base** (a "commons" every user benefits from): for each place it gathers practical facts (opening hours, prices/tickets, accessibility, website/booking), free-text notes, links to tourism sites, a **cross-platform review summary** (e.g. "Google 5★ · Komoot 4.5★"), and **stories** about the place. Every fact shows its source. This phase is shown as a **schematic (illustrated) map** of discovered places plus a **dynamic timeline**.
3. **Plan the day** — a back-and-forth chat with the agent turns the research into a themed day (stops, timings, walking legs, meals). It always produces a **Plan B and Plan C** (what to do if a place is closed, if it rains, if you're running late). The user sees *why* for every suggestion via source "provenance chips." On approval, the day **compiles and downloads to the device**.
4. **Travel (offline, on the device)** — no internet needed. A map, an itinerary, a live timeline (now/next, am-I-on-pace), and a **rich digital-tour-guide panel** per place (facts + story + links). Off-route recovery. One tap to switch to Plan B/C.

**The soul of the product:** the beautiful, *personal, illustrated* city map people used to buy — but dynamic, cited, and yours. It should feel like a knowledgeable local friend made you a hand-drawn map and guide for the day. Not another generic map app.

## Non-negotiable design requirements

- **Maps and visualization are present in every phase** — this is a map-first product, never a form-first one.
- **Two signature visualizations** you should design a distinct look for:
  - a **schematic / illustrated map** (stylized, hand-drawn-feeling, not a literal street map) used to present research and the planned day;
  - a **dynamic timeline** of the day (stops, durations, walking legs, meal anchors, and the Plan-B/C branches).
- **Provenance is visible, not hidden** — facts and stories carry small source chips; trust is a feature.
- **Multi-language + RTL is first-class, not an afterthought.** Launch languages are **English and Hebrew**. Design every key screen in **both LTR (English) and RTL (Hebrew)** — mirrored layout, correct alignment, and a beautiful Hebrew type treatment. Assume we translate content into whatever language the user uses.
- **Two device contexts:** planning (Phases 1–3) is comfortable on **desktop/tablet**; travel (Phase 4) is **one-handed mobile, outdoors, bright sun, glanceable**, and must look right with no network. Design travel for a phone first.
- **Offline is a feeling, not an error state** — the travel experience should feel intentional and complete, never degraded. Show where a link needs connectivity gracefully.
- **Compile is a product moment** — when the day compiles and downloads, show a satisfying "verification checklist goes green," not a spinner.
- **Human-approval gates** — the user explicitly approves the itinerary, then the style/compile. Design those confirmation moments.
- Accessibility: strong contrast (we lint map labels to ≥ 3:1), legible outdoors, large tap targets in travel mode.

## What I'd like from you, in order

1. **Visual language first** — a mood/direction (2–3 options): color system (light + a travel-friendly high-contrast mode), typography (must include a Hebrew face), the illustrated-map aesthetic, iconography, the "provenance chip" and "source" motif. Deliver as one artifact I can compare.
2. Once we pick a direction, **key-screen mockups as clickable HTML artifacts**, mobile + desktop as noted:
   - Sign-in + **Define area** (map with area-draw, "already researched" coverage hint).
   - **Research** view — the schematic map of discovered places + the dynamic timeline + a place detail with facts, story, review summary, and source chips.
   - **Plan** — the planning chat beside the live map/timeline, provenance chips, and the Plan-B/C representation.
   - **Compile & download** — the verification-checklist moment.
   - **Travel (mobile, offline)** — map + now/next timeline + the digital-tour-guide place panel + the "switch to Plan B" control + attribution/credits.
   - The **Hebrew / RTL** version of at least the Research and Travel screens.
3. A short **component inventory** (buttons, chips, cards, map controls, timeline, sheets) so the look stays consistent.

Constraints to respect: it will be built as a PWA using MapLibre for real maps (so map screens should feel map-native), and it must render fully offline in travel mode. Don't design features the PRD rules out for now: no turn-by-turn voice nav, no driving/transit routing (walking only), no multi-day trips, no social/collaboration, and we **link to** reviews rather than hosting them.

Start by asking me anything you need, then give me the **visual-language options**.
