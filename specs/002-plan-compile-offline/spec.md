# Feature Specification: Plan a day, compile it, travel it offline

**Feature Branch**: `002-plan-compile-offline`

**Created**: 2026-08-07

**Status**: Clarified (Q1 resolved = A, Ben, 2026-08-07) — ready for `/speckit-plan`

**Input**: User description: "Plan a day, compile it, travel it offline — the second vertical slice, spanning DU-04 (Plan, no variants), DU-05 (Compile to bundle) and DU-06 (Offline render, the M1 release gate). Builds directly on Spec 001's cited commons. Scope: propose an ItineraryV1 from commons sites over the Opus tier of the ModelRouter seam, gate it behind an explicit persisted HITL approval, compile the approved plan into a hashed BundleManifestV1 (pmtiles extract, Valhalla walking legs, quarantine filter, regenerated ATTRIBUTION.md), and render the whole travel experience from that bundle alone with zero connectivity. Explicitly OUT of scope (M2+): Plan B/C variants, schematic map, the narration generator, rich dynamic timeline, multi-language/RTL."

## Why this slice exists

Spec 001 filled a shared, cited commons and put it on a map. That is research, not a product. **This slice is where Siyur becomes the thing the constitution says it is**: a guided tour-day whose travel mode works with zero connectivity. Article I names the airplane-mode end-to-end eval as *the release gate for every milestone* — and today that gate is a green stub. This slice is what makes it real, and M1 is not done until it is.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Get a feasible day plan and approve it (Priority: P1)

A signed-in user who has researched an area says what kind of day they want — how long they have, how far they are willing to walk, what they are interested in. The system proposes an ordered day: which places, in what order, at what times, with the walk between each. Every place traces back to a cited commons record, so the user can see *why* it was chosen and where its facts came from. The plan honours the stated limits — it does not propose a five-hour walk to someone who asked for two. The user reads it, and **nothing proceeds until they explicitly approve it**.

**Why this priority**: Nothing downstream exists without an approved plan — there is nothing to compile and nothing to travel. It is also the first moment the product feels like a guide rather than a database, and it is independently demonstrable the moment it works.

**Independent Test**: Sign in, use an already-researched area, ask for a half-day of art and coffee, and confirm an ordered itinerary comes back within the stated walking and time budgets, every stop carrying its provenance, and that it stays in a pending state until explicitly approved.

**Acceptance Scenarios**:

1. **Given** a researched area and a stated set of preferences and limits, **When** the user asks for a plan, **Then** an ordered itinerary of stops with planned times, dwell durations, and the walking legs between them is returned.
2. **Given** a proposed itinerary, **When** it is presented, **Then** total walking distance and total duration are within the user's stated budgets, and every stop falls inside its place's opening window.
3. **Given** a proposal that cannot satisfy the budgets or the opening windows, **When** it is presented, **Then** the specific conflicts are named and the plan **cannot be approved** until they are resolved.
4. **Given** a proposed itinerary, **When** the user reviews it, **Then** the plan is held at an explicit pause and **no compilation or downstream work begins** until the user approves it.
5. **Given** an approved itinerary, **When** it is stored, **Then** it is private to that user and is never written into the shared commons.
6. **Given** any stop in the plan, **When** its information is displayed, **Then** every value shown carries the source and license stamp it inherited from the commons record.

---

### User Story 2 - Compile the approved day into a self-contained, verified bundle (Priority: P2)

Once the user approves the day, the system freezes it into a single self-contained artifact: the map for exactly the area the day covers, the walking routes, the places and their information, and the credits that the data licenses require. Before the user commits to downloading it, they are told how big it is. The bundle carries its own integrity checks so it can prove, later and offline, that it arrived intact. Anything whose license forbids redistribution is **removed** on the way in — not flagged, removed.

**Why this priority**: The bundle is the mechanism by which the offline promise is kept; the quarantine filter is the mechanism by which the license promise is kept. Both are load-bearing invariants and both are testable the moment compile exists, before any offline runtime does.

**Independent Test**: Approve a plan, run compile, and confirm a manifest is produced that lists every artifact with its hash, reports its total size, includes credits for every license that demands them, and contains **zero** values marked non-bundleable.

**Acceptance Scenarios**:

1. **Given** an approved itinerary, **When** compile runs, **Then** a bundle is produced containing map tiles covering the day's area, the walking routes, the places and their content, and a credits file.
2. **Given** a compiled bundle, **When** its manifest is inspected, **Then** every artifact carries a content hash, the manifest itself carries a hash, and the total size is reported.
3. **Given** commons records that include values whose license forbids redistribution, **When** compile runs, **Then** **none** of those values appear anywhere in the bundle.
4. **Given** a bundle containing data derived from OpenStreetMap, **When** the credits file is produced, **Then** it names "© OpenStreetMap contributors" and every other attribution the bundled licenses require.
5. **Given** a compiled bundle, **When** the user is asked to download it, **Then** its size is shown **before** the download begins.
6. **Given** a bundle whose contents have been altered or partially lost, **When** its integrity is checked, **Then** the mismatch is detected rather than silently accepted.

---

### User Story 3 - Travel the day with zero connectivity (Priority: P3)

The user is on the ground with no signal. They open the app and the day works: the map draws, the itinerary and its timeline are there, each place tells them what it is, and when they wander off the planned route the app gets them back on it. Nothing reaches for the network, and nothing is missing because the network is gone.

**Why this priority**: This is the payoff and the milestone's release gate (Constitution Article I). It is last only because it depends on US1 and US2 existing; in value it is the point of the entire slice.

**Independent Test**: Load the app with a downloaded bundle, cut the network entirely, reload, and confirm the map, itinerary, timeline, place information, and off-route recovery all work, with **zero** network requests made.

**Acceptance Scenarios**:

1. **Given** a downloaded bundle and a device with no connectivity, **When** the app is reloaded, **Then** the map renders, the itinerary and timeline display, and each place's information is readable — all from the bundle alone.
2. **Given** the device is offline, **When** the traveller uses the app for the whole day's flow, **Then** **zero** network requests are made.
3. **Given** the traveller has strayed from the planned route, **When** recovery is requested, **Then** a walking route back to the plan is produced on-device without connectivity.
4. **Given** a bundle whose integrity check fails at launch, **When** the app starts, **Then** the traveller is told the bundle is unusable rather than shown a silently broken day.
5. **Given** content that could not be bundled because its license forbids it, **When** the traveller reaches it offline, **Then** it is presented as needing connectivity — never as an error or a blank.

---

### User Story 4 - Places tell their story offline (Priority: P4)

Each place in the day carries a short, readable account of what it is and why it matters, adapted from openly-licensed encyclopedic sources, with the credit that license requires attached to it. It reads offline like a guide, not like a database row.

**Why this priority**: It is the difference between a map with pins and a *guided* tour, and Spec 001 explicitly deferred it to this slice. It is P4 because it is the one part of this slice that can be thinned without breaking its spine: the plan/compile/travel path and the airplane-mode gate all hold with sparse or absent stories.

**Independent Test**: Compile a day for the demo area and confirm that places with an available openly-licensed article carry a readable account offline, each showing its article credit, with no non-redistributable text present.

**Acceptance Scenarios**:

1. **Given** a place with an available openly-licensed article, **When** its record is assembled, **Then** it carries a readable account with that article's required credit attached.
2. **Given** bundled accounts, **When** the credits file is produced, **Then** each contributing article is credited individually, as its license requires.
3. **Given** a place with no available openly-licensed article, **When** the day is travelled, **Then** the place displays its cited facts without a story and **nothing is invented** to fill the gap.

---

### Edge Cases

- **Too little to plan with**: an area with fewer usable places than a day needs yields a shorter honest plan (or a clear "not enough here"), never padding invented to reach a target length.
- **Everything is closed**: a requested day where opening windows make any ordering infeasible is reported as infeasible with the blocking windows named, rather than a plan that quietly ignores hours.
- **Unroutable stop**: a place the walking network cannot reach is flagged and excluded from the plan rather than joined by a straight line pretending to be a route.
- **Approval raced or repeated**: approving the same plan twice, or approving a plan that has since been superseded, resolves to one consistent outcome and never two divergent bundles.
- **Plan edited after approval**: an edit to an approved plan returns it to unapproved and re-runs feasibility before it may be compiled again.
- **Bundle too large**: a day whose bundle would exceed the size budget reports that before download rather than after.
- **Storage refused or evicted**: a device that denies persistent storage, or evicts the bundle between sessions, is detected at launch and reported — the traveller is never handed a half-present day.
- **Compile with nothing bundleable**: if quarantine removes everything a place had, the place still appears with whatever survives, and the bundle records what was withheld and why.
- **Partially downloaded bundle**: an interrupted download is detected by integrity check and is not treated as a usable bundle.
- **Offline the whole way**: the traveller who was offline before opening the app for the first time that day is still served entirely from the previously-downloaded bundle.

## Requirements *(mandatory)*

### Functional Requirements

**Planning (US1)**

- **FR-001**: A signed-in user MUST be able to request a day plan for an area already researched into the commons, stating at minimum their available time and their walking limit, plus free-form interests.
- **FR-002**: The system MUST produce an ordered itinerary of stops drawn **only** from existing cited commons records — it MUST NOT invent a place, and MUST NOT include a place that is not in the commons.
- **FR-003**: The itinerary MUST include, for each consecutive pair of stops, a walking leg with a real distance, duration, and route geometry derived from a walking network — never a straight-line approximation presented as a route.
- **FR-004**: The system MUST verify feasibility before approval: total walking within the stated limit, total duration within the stated time, and every stop inside its place's opening window. Distances, durations, and time arithmetic MUST be computed by deterministic geospatial and schedule machinery, **never asserted by the model**. *(Determinism discipline; PRD §5 EARS.)*
- **FR-005**: An infeasible plan MUST be flagged with its specific violations named, and MUST NOT be approvable until resolved. *(PRD §5 EARS: "flag the conflict before allowing approval.")*
- **FR-006**: The system MUST hold the proposed itinerary at an **explicit, persisted pause** and MUST NOT begin compilation or any downstream work until the user approves. The pause MUST survive process restart — an approval decision is never lost because the server bounced.
- **FR-007**: An itinerary MUST be private to its user, scoped to the authenticated subject, and MUST NOT be written into the shared commons. *(Constitution Article V; PRD §13 #4 privacy boundary.)*
- **FR-008**: Every value displayed on a plan MUST carry the source and license stamp inherited from its commons record; the planner MUST NOT introduce an unstamped value. *(Constitution Article V; continuous with Spec 001 FR-003.)*
- **FR-009**: The system MUST report progress while a plan is being produced, so a user is never left with an unexplained wait.

**Compiling (US2)**

- **FR-010**: On approval, the system MUST compile the itinerary into a single self-contained bundle containing: map tiles covering the day's extent, the walking routes and a network sufficient for on-device recovery, the referenced places and their content, and a credits file.
- **FR-011**: The compile step MUST remove every value whose license forbids redistribution before anything is frozen. No such value may appear anywhere in the bundle. *(Constitution Article V; the quarantine invariant.)*
- **FR-012**: The compile step MUST refuse unstamped input — a value without provenance is never bundled. *(Constitution Article V.)*
- **FR-013**: The bundle MUST carry integrity information sufficient to detect corruption, truncation, or partial loss: a hash per artifact and a hash over the manifest itself, verifiable offline at launch.
- **FR-014**: The bundle MUST report its total size **before** download begins, and the system MUST surface when a day exceeds the size budget. *(PRD §5 EARS: "total size is reported before download"; target ≤200 MB.)*
- **FR-015**: The credits file MUST be regenerated per bundle and MUST name every attribution the bundled licenses require, including "© OpenStreetMap contributors" for any OpenStreetMap-derived data. *(Constitution Article V.)*
- **FR-016**: Map tiles MUST cover the itinerary's extent plus a margin sufficient for a traveller who strays, and no more — the bundle is scoped to the day, not the region.

**Travelling (US3)**

- **FR-017**: With zero connectivity, the system MUST render the map, the itinerary, the timeline, and each place's information entirely from the bundle. *(Constitution Article I; PRD §5 EARS.)*
- **FR-018**: With zero connectivity, the system MUST make **zero** network requests during the traveller's flow. This is the milestone release gate. *(Constitution Article I.)*
- **FR-019**: With zero connectivity, the system MUST produce a walking route from an off-plan position back to the plan, computed on-device. Approximate recovery is acceptable and expected; silence is not.
- **FR-020**: The system MUST verify bundle integrity at launch and MUST report an unusable bundle plainly rather than rendering a partial day. *(Guards storage eviction.)*
- **FR-021**: Everything the traveller depends on MUST resolve to a path present in the bundle manifest. Content withheld by quarantine MUST present as needing connectivity, never as an error or a blank. *(Airplane-mode invariant.)*
- **FR-022**: The bundle MUST be stored on-device durably enough to survive between sessions, and the system MUST detect and report when the device has evicted or refused it.

**Narration (US4)**

- **FR-023**: Where an openly-licensed encyclopedic article is available for a place, the system MUST attach a readable adapted account to that place's record, carrying that article's own attribution. Where none is available, the place MUST carry no story and **nothing may be invented**.
- **FR-024**: Bundled accounts MUST credit each contributing article individually, per the standing narration decision (rich, CC BY-SA, per-article attribution).

### Key Entities *(include if feature involves data)*

- **Itinerary**: the planned day — private to one user, referencing commons places by id, holding the ordered stops, the walking legs between them, the timeline, and the budgets it must satisfy. The single source of truth for both the planner's output and the bundle.
- **Stop**: one place in the day at a position in the order, with a planned start and a dwell duration.
- **Route leg**: a precomputed walking connection between two consecutive stops — real geometry, distance, and duration, derived from the walking network and therefore carrying that network's license and attribution.
- **Timeline**: the ordered wall-clock placement of stops and legs across the day, in the area's local time.
- **Approval gate**: the persisted state distinguishing a *proposed* plan from an *approved* one; the boundary that compilation may not cross unbidden.
- **Bundle**: the frozen, self-contained, hashed artifact the traveller carries — tiles, routes, recovery network, content, credits, integrity.
- **Bundle manifest**: the bundle's index and contract — what is inside, where, and its hash. Everything the travel experience reads resolves to a path here.
- **Story**: an adapted, openly-licensed account attached to a place, carrying its source article's required credit.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From a researched area, a user can state a day's constraints and receive an ordered, feasible itinerary in a single action.
- **SC-002**: **100%** of proposed itineraries satisfy their stated walking and time budgets and their places' opening windows, or are explicitly flagged infeasible with the violations named — never silently shipped.
- **SC-003**: **No** itinerary can reach compilation without an explicit user approval, and an approval decision survives a process restart in **100%** of cases.
- **SC-004**: **100%** of values shown on a plan and inside a bundle carry a source and license stamp; **zero** non-redistributable values appear in any bundle. *(Merge-blocking.)*
- **SC-005**: **Zero** network requests occur during the offline travel flow with connectivity disabled. *(The milestone release gate.)*
- **SC-006**: Every path the travel experience reads resolves to an artifact present in the manifest — **100%**, with corruption detectable at launch.
- **SC-007**: A compiled bundle for the demo day reports its size before download and stays within the size budget (≤200 MB).
- **SC-008**: An off-plan traveller receives a route back to the plan **without connectivity**, in every tested deviation case.
- **SC-009**: **Nothing is hardcoded to the demo area** — the same plan→compile→travel flow completes for at least one additional area of different character with no place-specific code changes. *(Genericity standing eval.)*
- **SC-010**: Every place bundled with a story shows that story's individual credit; **zero** stories exist without attribution.

## Assumptions

- **Builds on Spec 001.** An area researched into the cited commons is the starting state. This slice adds no new place-discovery sources beyond what narration (US4) requires.
- **Demo area.** Rhodes medieval old town remains the demonstration area, for continuity with Spec 001 and because it is compact and walkable. Genericity is proven against a second area, not by changing the first.
- **English-first, no RTL.** Presentation language is English. Multi-language and RTL remain M3.
- **Base plan only.** Plan B/C contingency variants, the schematic map render, the rich dynamic timeline, and meal/appointment anchors are all M2+ and out of scope. The data shapes leave room for them; this slice does not populate them.
- **Walking only.** The day is walked. No transit, driving, or mixed-mode routing.
- **Approval is the only gate in this slice.** A second approval gate for map style is M2.
- **Narration extent — resolved (Ben, 2026-08-07).** Spec 001 FR-011 explicitly deferred stories "to the next slice (002)", and the data spine lists a story per place as M1; the delivery plan lists "narration + quarantine" under M2. Resolved as: **story ingestion, per-article attribution, and quarantine land here (US4); the narration *generator* with per-claim provenance stays M2.** See "Reserved decisions" below.
- **Off-route recovery is approximate by design.** On-device recovery over a pruned walking network, without turn restrictions or precise costing, is the accepted M1 bar; recovery depth is an explicit M3 hardening item.
- **Size budget.** The ≤200 MB target is a metro-scale-day budget from the PRD; the compact demo day is expected far below it.
- **Deployment.** Compilation runs in-process for this slice; moving it to a dedicated job is M2 and changes no behaviour named here.
- **Identity.** Google SSO from DU-00 provides "signed-in user"; the private-data scoping in FR-007 rides on that subject.

## Reserved decisions — resolution status

Per the constitution, PRD §13 decisions are Ben's; agents flag rather than resolve.

- **#5 schematic map / dynamic timeline milestone** — *not resolved here*. This spec assumes both are M2+ and ships the simple ordered timeline the data spine specifies for M1. If #5 lands them in M1, this spec grows and must be revised.
- **#2 review-data policy** — *not triggered*. Reviews are M2+; no review data is planned, compiled, or bundled in this slice.
- **#3 course-scope / GCP** — *not triggered*. This slice runs on the local dev stack; the first cloud deploy is M3.
- **Q1 narration extent** → **resolved 2026-08-07 (Ben): option A — story ingestion lands in this slice, the generator stays M2.** Adapted openly-licensed (CC BY-SA) articles are attached to site records with per-article attribution and pass the same quarantine filter as every other value; the narration *generator* with per-claim provenance remains M2. Governs US4, FR-023, FR-024, SC-010.
  - *Why it needed asking:* three authoritative documents disagreed — Spec 001 FR-011 promised stories to "the next slice (002)", tech-design §1.1 lists `stories` as M1-populated, and the delivery plan schedules "narration + quarantine" under M2. The resolution reconciles them by splitting *ingestion* (here) from *generation* (M2), which is the reading that leaves all three documents true. **The delivery plan's M2 line should be amended to say "narration generator" when this slice lands.**
