# Feature Specification: Research an area into a cited commons, rendered on the map

**Feature Branch**: `001-research-cited-sites`

**Created**: 2026-07-31

**Status**: Clarified (Q1/Q2/Q3 resolved A/A/A) — ready for `/speckit-plan`

**Input**: User description: "Spec 001 — the first end-to-end vertical slice for Siyur (M1). A signed-in user delimits an area (demo area = the Rhodes medieval old town); the system researches that area with an LLM into a shared, cited commons of sites; and renders the resulting cited sites on the offline-capable map, each site carrying a source + license attribution chip. Includes the accepted M1 name transliteration sliver (Greek→Latin display names). English-first, no RTL. A thin research→cited-sites-on-map slice, not the full plan/compile/travel product."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Research a delimited area and see cited sites on the map (Priority: P1)

A signed-in user chooses an area to explore (for the demo, the Rhodes medieval old town) and asks the system to research it. The system gathers real-world places in that area from authoritative sources into cited site records, and the map fills with those places. Each place shows a chip naming where its information came from and under what license. The user can tell, at a glance, that what they are looking at is real, sourced, and attributable — not invented by a model.

**Why this priority**: This is the entire value of the slice and the foundation everything downstream (planning, compiling, travelling) builds on. Without a trustworthy, cited commons rendered on a map, there is no product. It is independently demonstrable and the first thing a stakeholder can *see*.

**Independent Test**: Sign in, delimit the Rhodes old-town demo area, trigger research, and confirm that cited places appear as map markers, each with a visible source + license attribution, with zero unstamped values shown.

**Acceptance Scenarios**:

1. **Given** a signed-in user and a delimited area with available source data, **When** they trigger research, **Then** the area's real-world places render on the map, each carrying a visible source + license attribution chip.
2. **Given** research has produced site records, **When** any place shows information (name, location, category, address, hours), **Then** every displayed value is stamped with its source and license, and no unstamped value is ever shown.
3. **Given** places sourced from OpenStreetMap are displayed, **When** the map renders, **Then** the ODbL attribution "© OpenStreetMap contributors" is visible on the map.
4. **Given** two sources disagree about a place's value, **When** the record is assembled, **Then** both values are preserved as a recorded conflict and no source is discarded.

---

### User Story 2 - Reuse already-researched areas and offer a refresh (Priority: P2)

A user delimits an area that has already been researched (by them or, depending on the commons policy, by anyone). Instead of paying to research it again, the system recognises the overlap, shows the existing cited data immediately, and offers to refresh it if the data may be stale.

**Why this priority**: The research commons is a shared, cumulative resource (a standing project decision); re-researching covered ground wastes cost and time and fragments the commons. Reuse is what makes the commons compound in value. It builds directly on US1 and is testable once US1 exists.

**Independent Test**: Research the demo area once; delimit the same (or overlapping) area again and confirm the existing cited data appears without a fresh research pass, with an explicit option to refresh.

**Acceptance Scenarios**:

1. **Given** an area already covered by the commons, **When** a user delimits it again, **Then** the existing cited data is shown without re-running research, and a refresh option is offered.
2. **Given** a user chooses to refresh, **When** refresh completes, **Then** updated values carry new observation dates and any newly-disagreeing values are recorded as conflicts (no source lost).

---

### User Story 3 - Non-Latin place names are readable to an English-first user (Priority: P3)

Many places in the demo area have names only in Greek in the underlying sources. An English-first user sees a readable Latin/English rendering of each such name, while the original-script name and its attribution are preserved (never overwritten).

**Why this priority**: Real source data is sparse in local-script names and untrustworthy in script (a Hebrew address was found stored in Cyrillic — FAIL-001), so a name the user cannot read is a real usability gap even in the first slice. It was accepted into M1 as a deliberate "sliver," so it belongs here — but it is the lowest-priority of the three because the slice still delivers value without it.

**Independent Test**: Research the demo area and confirm that places whose source name is non-Latin (Greek) display a readable Latin rendering alongside/with the preserved original and its attribution.

**Acceptance Scenarios**:

1. **Given** a place whose source name is in Greek, **When** it renders on the map, **Then** a readable Latin/English form is shown and the original-script value plus its source attribution are preserved.
2. **Given** an address whose stored script does not match its actual language (FAIL-001), **When** it is processed, **Then** the script is normalised/validated rather than trusted as-is.

---

### Edge Cases

- **Sparse or empty area**: an area with little or no source data returns few or zero sites; the system reports "nothing found here" rather than fabricating places.
- **Non-bundleable-licensed values**: a value whose license forbids offline bundling is stamped `bundleable=false`; it may be shown in this online phase but must never be mistaken for bundleable (the downstream offline guarantee depends on this stamp being correct from ingestion).
- **Unstamped or model-invented output**: any value that arrives without a real source stamp — including anything the model tried to assert on its own — is rejected, not displayed.
- **Model-emitted coordinates**: place locations must come from authoritative geodata; a location the model tried to state or compute is not trusted.
- **Conflicting sources**: disagreeing values are kept as recorded conflicts; the merge never silently discards a source.
- **Private data**: a user's own notes about a place stay private and are never written into the shared commons.
- **Oversized or slow research**: an area far larger than the demo, or a slow/failed source, degrades gracefully (partial results with clear status) rather than hanging or losing already-gathered data.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A signed-in user MUST be able to delimit an area and trigger research on it. The demo area is the Rhodes medieval old town; nothing may be hardcoded to that place (genericity is a standing requirement).
- **FR-002**: The system MUST research a delimited area into a set of site records, each representing one distinct real-world place, drawn from authoritative sources (not model-invented).
- **FR-003**: Every value on every site record MUST be stamped at ingestion with its source, license, and a `bundleable` flag. The system MUST refuse unstamped input and MUST NOT display any unstamped value. *(Constitution Article V — provenance is mechanical.)*
- **FR-004**: The map MUST render each researched place at its real location, each carrying a visible source + license attribution chip. The ODbL attribution "© OpenStreetMap contributors" MUST render whenever OpenStreetMap-derived data is shown.
- **FR-005**: Place locations MUST be accurate to the real place, derived from authoritative geodata; the system MUST NOT let the model emit or compute coordinates.
- **FR-006**: When a user delimits an area already covered by the commons, the system MUST reuse the existing cited data instead of re-researching, and MUST offer a refresh. *(Delivery-plan EARS.)*
- **FR-007**: Researched records MUST be written **directly into the shared, global research commons**, gated to signed-in users, with the established merge rules (union-first, spatial+name dedupe, no source lost) preventing duplicates. This slice resolves only *how a record enters* the shared commons (directly, deduped); the broader commons-governance policy (spam control, trust tiers, rate limits, moderation) is **additive and deferred** to a later unit. *(Resolves PRD §13 #4 to the extent this slice requires — ADR-0008.)*
- **FR-008**: The system MUST present non-Latin place names in a readable Latin/English form, **automatically, on the display-name field only**, while preserving the original-script value and its attribution. Address transliteration is **out of scope** for this M1 sliver because source address scripts are untrustworthy (FAIL-001). *(Matches the `poi-site.md` schema card: "name transliteration is an M1 sliver.")*
- **FR-009**: The merge of multiple sources into one record MUST NOT lose any source: disagreeing values are preserved as recorded conflicts.
- **FR-010**: Personal/private data (a user's notes, plans, identity) MUST NOT be written into the shared commons.
- **FR-011**: Research for this first slice MUST locate and cite **points of interest only**, drawn from Overture + OpenStreetMap. Adapted narration/stories (CC-BY-SA, per-article attribution) are **deferred to the next slice (002)**. Every located place still carries full provenance stamps (FR-003).
- **FR-012**: The system MUST report what it did and did not find for an area (counts, thin coverage, source failures) so the user is never shown a silently-incomplete result as if it were complete.

### Key Entities *(include if feature involves data)*

- **Research Area**: the user-delimited region to research (demo = Rhodes medieval old town). Bounds a research pass and the reuse/overlap check.
- **Site record**: the commons record — one row per real-world place, globally shared, assembled by merging many sources. This slice populates name(s), location, categories, and where present address and hours; narration/stories are added in slice 002.
- **Sourced value**: the atomic unit — a fact plus its stamp (source, license, `bundleable`, confidence, observation date). Everything the product shows is a sourced value, never a bare fact.
- **Source / attribution**: where a value came from (Overture, OSM, Wikivoyage, Wikidata, …), its license, and the attribution string rendered when the license requires it (ODbL, CC BY-SA).
- **Field conflict**: a recorded disagreement between sources for one field; preserves the losing value(s) rather than discarding them.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For the Rhodes demo area, a user can go from delimiting the area to seeing cited places on the map in a single research action, with the map populated for the demo area (≥20 cited places, reflecting the area's known density).
- **SC-002**: **100%** of values displayed on the map carry a source + license stamp — zero unstamped values are ever shown (provenance completeness is total, not sampled).
- **SC-003**: Re-delimiting an already-researched area shows existing cited data **without** a new research pass, and always presents a refresh option.
- **SC-004**: For places whose source name is non-Latin (Greek), a readable Latin/English rendering is shown for **≥95%** of them, with the original-script value preserved in every case.
- **SC-005**: **Nothing is hardcoded to Rhodes** — the same research flow produces cited sites on the map for at least one additional area of different character, with no place-specific code changes. *(Genericity standing eval; the full ≥3-area bar, including an unrehearsed area, is a milestone-level gate, not this slice's.)*
- **SC-006**: A researched area that has no source data returns a clear "nothing found" result and **zero fabricated places**.

## Assumptions

- **Online phase.** This slice covers the online Define→Research phase only. The offline/airplane-mode travel guarantee (Constitution Article I) applies to what the traveller depends on downstream (the compiled bundle) and is out of scope here; the map itself remains the offline-capable map stood up at DU-00. `bundleable` stamps are nonetheless correct from ingestion so the downstream guarantee holds.
- **Identity.** Google SSO (the one sanctioned hosted identity dependency) provides "signed-in user"; provisioning real OAuth credentials is an operator step outside this spec.
- **Demo area.** Rhodes medieval old town is the primary demonstration area (richest data, compact walkable old town, Greek exercises non-Latin script without requiring RTL). Jaffa/Hebrew (RTL) is explicitly deferred to M3.
- **Merge behaviour** (union-first, no source lost, spatial+name matching) follows the established data-spine rules; this spec asserts the *outcomes* (no source lost, conflicts recorded), not the mechanism.
- **Genericity target for this slice** is Rhodes + ≥1 additional area; the constitution's ≥3-areas-including-unrehearsed bar is a milestone gate that matures across units, not a blocker for the first slice.
- **Volume/latency targets** (≥20 sites, single action) are reasonable defaults for the demo area drawn from discovery findings; they will be tightened, not loosened, as real numbers land.

## Reserved decisions — resolution status

Per the constitution, PRD §13 decisions are Ben's. Resolved for this slice (2026-07-31, Ben):
- **#4 commons write policy** → **direct auth-gated write to the shared commons** (Q1 = A); broader governance deferred. Captured in **ADR-0008**. FR-007.
- **M1 transliteration extent** (accepted 2026-07-24, "pinned in Spec 001") → **display names only, automatic** (Q2 = A). FR-008.
- **First-slice research scope** → **POI locate + cite only, Overture + OSM; stories deferred to slice 002** (Q3 = A). FR-011.
- **#2 review-data policy** is *not* triggered — reviews are M2+ and out of scope here.
