# PRD — Siyur: The Tour-Day Map Studio

### Research a place with an LLM. Plan a day. Travel it offline, guided.

*v2.0 DRAFT for Ben's approval — 2026-07-24. Supersedes v1.0 (2026-07-24, single-user offline-bundle tool).*
*Grounding: `methods-ramp-up-standards.md` (process standards) and `methods-stack-reference.md` (component choices, pinned versions, license register). Approval of this PRD triggers the ramp-up prompt for the build session (§14).*

**What changed from v1.0 (read this first):** Siyur is now a **multi-user platform**, not a single-user tool. Four material additions: (1) a **persistent, shared research commons** on our servers — one user's researched site benefits everyone; (2) **Google SSO** and multi-tenancy; (3) **multi-language + RTL** (English + Hebrew first, translate to the user's language); (4) **GCP hosting** with a required local dev environment. The flow is re-framed into three online phases (**Define area → Research → Plan**) plus an **offline Travel** payoff, with maps & visualization — including **schematic maps** and a **dynamic timeline** — in every phase, and non-linear "go back and gather more" at any point. The offline bundle is no longer "the product"; it is the **travel guarantee** of a larger online product. Several v1.0 "settled" items are re-opened as **§13 open decisions**.

---

## 1. Vision & problem

The planning half of travel is well served; the *researching* and *traveling* halves are not. People piece a place together from a dozen tabs — reviews on one site, hours on another, a story on a third — then walk the city with a dying battery, roaming charges, and a generic map that knows nothing about *their* day. And every traveler re-does that research from scratch. **Siyur** closes both gaps: research any area *with an LLM* into a durable, cited knowledge base that **the next traveler inherits**; plan a personal day from it, with fallbacks for when things go sideways; then compile it into a self-contained bundle and **travel with zero connectivity and zero LLM — guided by a rich digital tour guide**.

Siyur is simultaneously the demo project of the GeoAI course: its build (Goal 1) and its architecture (Goal 2) are the curriculum. Meta-requirements that exist only for this reason are marked **[course]**.

## 2. Goals & non-goals

**Product goals.**
- **G1 · Research** — a user delimits any area and, in conversation with the agent, produces a **cited, structured knowledge base** of its sites (practical facts + stories), persisted to a **shared commons** other users reuse.
- **G2 · Plan** — from that knowledge base, produce a satisfying, *feasible* tour day in ≤ 20 min of conversation, **including Plan B / Plan C** contingencies (closure, weather, running late).
- **G3 · Travel offline** — the compiled bundle delivers the full experience — map, itinerary, narration, practical info, off-route recovery — **100 % offline**, acting as a digital tour guide.
- **G4 · Beautiful & personal** — every phase is map- and visualization-driven; the system produces **schematic (illustrated) maps** and a **dynamic timeline**, LLM-designed within validated cartographic guardrails.
- **G5 · Trustworthy & multilingual** — every fact shown has provenance; every license obligation is honored in-product; the UI works in **English and Hebrew (RTL) at launch**, translating to the user's language.
- **G6 · Multi-user** — Google SSO; the commons compounds in value as users research more places.
- **[course] G7** — the build produces the course-feed artifacts (§11) as a side effect of working.

**Non-goals (MVP).** Turn-by-turn voice navigation; transit/driving routing (walking-first — transit is v2); multi-day trips (single tour day; the *area* may be revisited across days); collaborative/real-time co-planning; native apps (PWA only); real-time POI data (live prices/availability); monetization; user-generated content beyond a user's own free-text notes; becoming a review platform (we *link and summarize*, we do not host reviews).

## 3. Users & core scenario

**Primary persona:** the deliberate traveler — researches ahead, likes maps, hates roaming charges, comfortable installing a PWA. **Secondary:** the local explorer / guide building a themed day in their own region, who *contributes* rich research back to the commons.

**Core scenario (generic any-area — nothing hardcoded per place):** the user signs in with Google, **draws or names an area** (Overture divisions; Nominatim fallback for disambiguation — "Which Springfield?"). If the commons already covers it, research is *inherited and refreshed*; otherwise the agent **researches on demand, driven by the user's stated preferences** (interests, pace, budget, constraints — "kids in tow", "rainy morning") and **persists results with references**. Genericity is a standing eval: MVP must be demonstrated on ≥ 3 areas of different character (dense metro, small town, and a **non-Latin-script name** for the disambiguation/RTL path) including one never tested before the demo.

## 4. User experience — three online phases + offline travel

Phases are a **guided loop, not a strict pipeline**: from any phase the user can go **back to gather more info** about a site or widen the area. **Maps & visualization are present in every phase.** Two **human-approval gates** (LangGraph `interrupt()`) remain: itinerary approval, then style/compile approval.

**Phase A · Define the area (online).** Map-first: the user draws a boundary or names a place; the system resolves it to a polygon, shows coverage already in the commons, and estimates research effort. Output: a scoped area + preference brief.

**Phase B · Research & collect (online, persistent, shared).** The agent researches the area into the **commons**: for each site it gathers **practical facts** (address, phone, opening hours, prices/tickets, accessibility, website/booking link), **free-text notes**, **links to tourism sites**, a **cross-platform traveler-review summary** (e.g. "Google 5★ / Komoot 4.5★" — link-and-summarize, license permitting; see §7), and **stories** (CC BY-SA prose, per §7). Every field carries a **source reference**. Data from **many sites is merged** into one record per place with conflict flags. The phase is visualized as a **schematic map** of discovered sites and a **dynamic timeline** of coverage; the user can send the agent back for deeper research on any site. Results persist server-side so the next user inherits them.

**Phase C · Plan the tour (online, conversational).** From the commons, the agent proposes a themed day (stops, timings, walking legs, meal anchors) and iterates on feedback, showing *why* for every suggestion (source-linked provenance chips). It produces **Plan B and Plan C** — precomputed contingencies for a closed site, rain, or falling behind pace — surfaced on the schematic map and dynamic timeline. On approval, **Compile** (one-shot, observable): extract PMTiles for the itinerary bbox + buffer, generate the map style + schematic map, precompute all route legs + a pruned walking graph (incl. B/C branches), generate multilingual narrations with per-claim source IDs, assemble ATTRIBUTION, hash everything into a manifest, and **download the bundle to the device**. Compile is a *product moment* — a verification checklist goes green.

**Travel (offline, on device).** Installed PWA reads the bundle from device storage (OPFS): position-aware (GPS works offline), timeline-aware (now/next, pace vs. plan), stop stories reveal on arrival, **rich tour-guide panel** per place (facts + story + links, links noted as needing connectivity), off-route recovery via in-bundle path-finding, and **one-tap switch to Plan B/C**. Attribution visible on-map and in a credits screen. **No network required for anything.** If online, an optional "re-research / re-plan the rest of my day" escape hatch calls back to Phases B/C.

**Embedding patterns (explicit):** Phase A = map-driven scoping with light LLM assist; Phase B = research/generation producing durable, cited, *shared* artifacts; Phase C = conversational planning with HITL gates + one-shot compile; Travel = **no LLM by design** — the degraded, offline mode is the product.

## 5. Functional requirements (EARS samples; full set lives in Spec 001)

- WHEN a user authenticates, THE SYSTEM SHALL use Google SSO and scope all personal data (plans, notes, preferences) to that user, while the researched-site commons remains shared.
- WHEN the user delimits an area already covered in the commons, THE SYSTEM SHALL reuse existing cited data and offer to refresh stale records rather than re-researching from scratch.
- WHEN the agent researches a site from multiple sources, THE SYSTEM SHALL merge them into one record, retain a source reference per field, and flag conflicting values.
- WHEN any bundled narration or fact makes a claim, THE SYSTEM SHALL hold a source reference (GERS/OSM/Wikivoyage/Wikipedia/Wikidata/URL) for it, and SHALL NOT bundle content whose license forbids redistribution (proprietary review text → link only).
- WHEN the user approves an itinerary and style, THE SYSTEM SHALL compile a bundle — map tiles, schematic map, itinerary, Plan B/C branches, narrations, attribution — whose manifest passes all integrity checks and whose total size is reported before download (target ≤ 200 MB for a metro-scale day).
- WHILE the device is offline, THE SYSTEM SHALL render map, schematic map, itinerary, timeline, narrations, per-place tour-guide info, and off-route recovery entirely from the bundle, and SHALL let the user switch to Plan B/C.
- WHERE the user's language is not English, THE SYSTEM SHALL present UI and translatable narration in that language; WHERE the language is RTL (e.g. Hebrew), THE SYSTEM SHALL render a correct RTL layout.
- WHEN the map or schematic map is generated, THE SYSTEM SHALL reject outputs failing schema validation or contrast lint (label/halo ≥ 3:1; water/land ≥ 1.3:1).
- IF a planned or contingency route becomes infeasible after an edit (opening window missed, budget exceeded), THEN THE SYSTEM SHALL flag the conflict before allowing approval.

## 6. Architecture (decisions grounded in `methods-stack-reference.md`; deviations & new surfaces require ADRs)

**Shape:** a **GCP-hosted multi-tenant web platform** with an offline PWA client. Open-source-first for the *application* stack; GCP is the deployment substrate and Google SSO the one sanctioned hosted identity dependency.

- **Client** — PWA (Vite + Workbox v7 shell precache), MapLibre GL JS 5.19.x + PMTiles v3 (defer MapLibre v6 ESM transition), **whole-archive download to OPFS** (never SW range-caching) with `navigator.storage.persist()`. **i18n** (message catalogs, `dir=rtl` + CSS logical properties for Hebrew); schematic-map + timeline components.
- **Identity & tenancy** — Google SSO (OAuth 2.0 / OIDC) via GCP Identity Platform (or Firebase Auth); per-user rows for plans/notes/preferences; shared rows for the commons. Auth is security-critical — agent autonomy restricted here per methods §5.
- **API & planner** — FastAPI + SSE on **Cloud Run**; interactive checkpointed **planner** (LangGraph 1.x, **Cloud SQL Postgres** checkpointer). Two graphs, not one: interactive planner + **batch restartable compiler** (Cloud Run job).
- **The research commons** — **Cloud SQL Postgres + PostGIS** for sites/POIs with per-field provenance and merge/conflict metadata; **GCS** for tiles, bundles, media, and glyphs. `ItineraryV1` (Pydantic) plus a new `SiteRecordV1` are the single sources of truth for planner output *and* bundle schema.
- **Curation sources** — DuckDB over Overture **2026-07-22.0** (places CDLA-P, confidence ≥ 0.6; divisions for boundaries) + Overpass long tail + Wikivoyage listings + Wikipedia GeoSearch + Wikidata (CC0) + Commons (per-file license capture); hours via opening_hours.js v3.9; **review summaries via provider APIs under their terms (link-first, §7)**.
- **Style & tiles** — `@protomaps/basemaps` Flavor deltas validated by `@maplibre/maplibre-gl-style-spec` v25 + contrast lint + headless vision check; PMTiles via `pmtiles extract` from Protomaps daily builds (Planetiler fallback); **schematic map** = separate stylized/illustrated render pass over the itinerary graph.
- **Routing** — **Valhalla per-area Docker build at compile time** (routes, matrices, stop-order optimization incl. B/C branches; ORS free key as dev fallback); travel-phase = precomputed legs + pruned walk graph + geojson-path-finder recovery.
- **Multi-language** — content stored canonically (source language + English); **LLM translation at plan/compile time**, cached in the commons; UI strings in message catalogs. Translated CC BY-SA narration retains attribution (§7).
- **Local dev env (required)** — docker-compose mirroring GCP: Postgres+PostGIS, Valhalla, a GCS emulator, and an auth emulator; `uv`-managed Python 3.12. Geo stack pinned: `shapely~=2.1`, `h3~=4.5`, `osmnx~=2.1`, `geopandas~=1.1`.

**Known risks tracked from day one:** iOS storage eviction (re-download + launch-time integrity checks); MapLibre v6 migration debt; Overture `categories`→`basic_category` migration (Sept 2026 — write to new fields now); Protomaps build-URL instability (resolve latest at run time); **new:** commons data staleness/merge conflicts, proprietary-review ToS limits, GCP cost/scaling, auth/PII surface, translation quality & RTL correctness.

## 7. Data, licensing & privacy (obligations are requirements, not notes)

ODbL attribution ("© OpenStreetMap contributors") visibly on every rendered map + credits screen; Overture ODbL themes credited, places (CDLA-P) bundled freely; Commons images only from {PD, CC0, CC BY, CC BY-SA} with author/license/URL captured at compile; fonts OFL bundled; opening_hours.js used unmodified (LGPL). **License quarantine is mechanical:** every curation adapter stamps output `bundleable: true/false` + license + source ref; the narration/bundle step refuses unstamped input; **open-web and proprietary-review results are always `bundleable: false`** (planning-time signal + link only).

- **Narration posture — DECIDED (v1 open decision #1): (a) Rich.** Narrations may adapt Wikivoyage/Wikipedia prose → bundled text is **CC BY-SA 4.0 with per-article attribution** (share-alike applies to the *text* only, not code/style/tiles). Translations of that text inherit CC BY-SA + attribution.
- **Reviews (new):** we **link-and-summarize**, we do not host reviews. Numeric rating summaries and links are planning-time signals; whether any rating/summary may be *cached or shown* depends on each provider's API terms — **§13 open decision #2**. Default until decided: **links only in-bundle**, live summaries online only.
- **Commons & privacy (new):** the researched-site commons is a **global shared resource**; a user's **personal** data (plans, free-text notes, preferences, auth identity) is private and per-user. Google SSO introduces PII — minimal scope, documented retention, deletion path; no secrets or PII ever bundled. `DATA-LICENSES.md` is the registry; a compile step regenerates `ATTRIBUTION.md` per bundle.

## 8. Quality: evals & definition of done

Eval stack per methods §3: `evals/golden/` seeded with ~25 tour-day requests (areas × interests × constraints × edge cases, incl. deliberately-infeasible ones the agent must refuse, and **non-Latin-script + RTL** cases); `test_structural.py` (schema incl. `SiteRecordV1` provenance, geometry validity, budget/feasibility, **Plan B/C feasibility**, bundle integrity, offline render via Playwright with network disabled) — **merge-blocking**; `test_trajectory.py` (agentevals superset match on `resolve_area → research → curate → route → design → compile`) — merge-blocking; `test_quality.py` (DeepEval LLM-judge on plan quality, story quality, style vision-check, **translation adequacy**; pinned judge) — nightly, threshold-gated; `evals/history.csv` appended by CI. **New structural checks:** commons merge correctness (no source ref lost on merge), license-quarantine (no `bundleable:false` field in a bundle), RTL layout smoke test. The **airplane-mode e2e** (research → plan → compile → disable network → full guided travel incl. a Plan-B switch) is the release gate for every milestone. DoD per task = EARS criteria verified + tests green + evals green + ADR if a decision was made + session logged.

## 9. Build process standards [course — this *is* Goal 1's curriculum]

The build follows methods §7 end to end: ramp-up per the 28-step checklist (AGENTS.md ≤200 lines + CLAUDE.md shim; strict shared `.claude/settings.json` safe for unattended cloud runs; disler-pattern logging hooks; Spec Kit ≥0.13 constitution-first); Spec 001 = "**research an area and plan one tour day, compiled to an offline bundle**" (one vertical slice, interview-produced, EARS criteria); spec-anchored maturity; branches `agent/<ticket>-<desc>`, worktrees for parallel sessions, Co-Authored-By trailers, PR evidence sections; CI = lint/typecheck → pytest → structural+trajectory evals → Semgrep + gitleaks + pip-audit → 500-line diff guard, all required; nightly quality evals + budget-capped read-only `claude -p` review. **Scope note [course]:** GCP hosting + auth + multi-tenancy expand the course beyond the methods docs' original "laptop-scale, not production-deployment" framing — cloud deploy, IaC, and auth-security now become course material (see §13 decision #3). Constitution articles (v1, to be written at ramp-up — **now reframed**): *the guided experience is the product and its travel mode must work in airplane mode*; deterministic evals gate merges; every decision → ADR; every failure → catalog entry + regression eval; data licenses & user privacy tracked and honored.

## 10. Architecture risks

| Risk | L | Impact | Mitigation |
|---|---|---|---|
| Scope creep — platform + pipeline + agent + commons | H | schedule | Spec 001 is one vertical slice; constitution; 500-line diff guard; commons ships thin |
| Commons data quality (stale, conflicting, thin in small towns) | H | trust | Per-field provenance + conflict flags + staleness dates + honest "thin coverage" UX; refresh-on-reuse; unrehearsed-area eval |
| Proprietary-review ToS | M | legal | Link-first; bundle nothing proprietary; decision #2 resolves caching limits before shipping summaries |
| GCP cost & scaling (LLM research/translation × many users) | M | viability | Cache research & translations in the commons; per-user quotas; batch/cheap models for mechanical steps |
| Auth / PII surface | M | trust/legal | Minimal scope, restrict agent autonomy on auth code, SAST-gated, documented retention + deletion |
| iOS storage eviction | M | UX trust | Re-downloadable bundles, launch integrity check, early real-device test |
| Style/schematic quality unprovable | M | the "beautiful" promise | Guardrail stack + preset fallback; vision-check rubric |
| i18n / RTL correctness | M | reach | RTL smoke evals; Hebrew as a first-class launch language, not an afterthought |
| Ecosystem drift (MapLibre v6, Overture schema) | H | maintenance | Pinned versions + tracked migration debt + freshness checks — itself course material |
| Course-feed becomes overhead | M | Goal-1 story | Automation-first; cut an artifact type *by ADR* if it isn't earning its keep by M2 |

## 11. The course-feed contract [course]

The agent documents the build via five artifact types — hooks and slash commands make them near-free — each feeding named syllabus units (`course/01-syllabus.md`):

| Artifact | Mechanism | Feeds |
|---|---|---|
| **ADRs** (MADR 4.0 minimal, with Confirmation) | `/adr` drafts from live session; human approves | U2, U4 |
| **Devlog** (`docs/devlog/YYYY-MM-DD-*.md`) | hooks capture JSONL → `claude -p` distiller commits summaries | U2, U7 |
| **Failure catalog** (`docs/failures/FAIL-NNN.md`) | `/failure`; **every entry adds a golden-set case or guardrail** | U2, U3, U7 |
| **Prompt & eval history** (`prompts/` front-matter; `evals/history.csv`) | PR-reviewed prompts; CI appends scores | U3 |
| **Changelog + exhibits** | conventional commits → git-cliff; `exhibit/<unit>-<slug>` tags | U1, U5, U6 |

Acceptance: at any milestone, every syllabus unit's "build-artifact feed" line resolves to ≥ 1 real artifact. `docs/course-feed.md` maps units → artifacts, checked at each retro.

## 12. Milestones

**M0 · Ramp-up (≈1 week):** checklist steps 1–24; repo public; CI green on a walking skeleton; **local dev env (docker-compose) stands up**. **M1 · Vertical slice:** Spec 001 end-to-end for one area — auth (SSO) → research (persist a few cited sites) → plan (no style customization, no B/C yet) → compile → offline render; airplane-mode eval passes. **M2 · The studio:** style + schematic-map pipeline, narration generator with provenance + license quarantine, Plan B/C, dynamic timeline, resumable sessions, commons merge. **M3 · Reach & hardening:** multi-language + RTL (Hebrew), 3-area validation incl. one unrehearsed + a non-Latin-script name; recovery routing; iOS device pass; GCP deploy. **M4 · Course freeze:** exhibits tagged, course-feed index complete, syllabus validated against artifacts.

## 13. Open decisions (for Ben — resolve at/around approval)

1. **Constitution reframe.** Confirm v1 article #1 changes from "offline bundle is the product" to "**the guided experience is the product; its travel mode must work in airplane mode**." (Recommended: yes — the platform is now the product, offline is a guarantee within it.)
2. **Review data (blocking the review feature).** Which is acceptable: **(a)** links only, live summaries fetched online, nothing cached/bundled (safest, default); **(b)** cache numeric ratings under provider API terms, still never bundle text; **(c)** defer reviews entirely to v2. *Recommendation: (a) for MVP, revisit per-provider terms before (b).*
3. **Course scope expansion.** Accept that GCP deploy + auth + multi-tenancy are now course material (contradicts methods docs' "laptop-scale, not production-deployment"). If yes, the methods/stack docs get an addendum. *Recommendation: accept — it's honest to the real build and valuable to students; keep it methods-first, not ops-heavy.*
4. **Commons write policy.** Confirm **global commons** (decided) with: is *any* signed-in user's research auto-published to the commons, or does raw source-derived data auto-publish while a user's edits/notes stay private? (leans hybrid on privacy grounds). *Recommendation: auto-publish source-derived cited data; keep personal notes private.*
5. **Schematic map & timeline scope for MVP.** Are these M2 (studio) as staged here, or must a basic version land in M1? *Recommendation: M2; M1 uses the standard MapLibre render + a simple list timeline.*

## 14. Approval & next step

**Ben approves/amends:** ①–③ product scope, embedding decisions, architecture (§2–§6) · ④ §13 open decisions #1–#5 · ⑤ the course-feed contract (§11) · ⑥ milestones (§12).
**On approval:** Ben takes UX/visual design to a separate "Claude design" track; in this repo we proceed to **technical design** (data model `SiteRecordV1`/`ItineraryV1`, commons schema, GCP topology, local dev env, auth flow) and then the ramp-up prompt executes the 28-step checklist and runs the Spec 001 interview.
