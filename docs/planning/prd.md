# PRD — Siyur: The Tour-Day Map Studio
### Plan online with an LLM. Travel offline with a beautiful map.

*v1.0 DRAFT for Ben's approval — 2026-07-24.*
*Grounding: `methods/01-ramp-up-standards.md` (process standards) and `methods/02-siyur-stack-reference.md` (component choices, pinned versions, license register). Approval of this PRD triggers the ramp-up prompt for the build session (§13).*

---

## 1. Vision & problem

The planning half of travel is well served; the traveling half is not. People walk unfamiliar cities with dying batteries, expensive roaming, and dead zones, holding generic map apps that know nothing about *their* day. The beautiful personal map — the illustrated city map people used to buy — doesn't exist in dynamic, personal, offline form. **Siyur** closes that gap: converse with an LLM to plan a day in *any city*, matched to your preferences; receive a custom-designed, dynamic map compiled into a self-contained bundle; travel with zero connectivity and zero LLM.

Siyur is simultaneously the demo project of the GeoAI course: its build (Goal 1) and its architecture (Goal 2) are the curriculum. Meta-requirements that exist only for this reason are marked **[course]**.

## 2. Goals & non-goals

**Product goals.** G1: a user can plan a satisfying, *feasible* tour day for any city in conversation, in ≤ 20 minutes. G2: the compiled bundle delivers the full travel experience — map, itinerary, narration, recovery routing — 100% offline. G3: every map is personal and beautiful: LLM-designed within validated cartographic guardrails. G4: every fact shown has provenance; every license obligation is honored in-product. **[course] G5:** the build produces the course-feed artifacts (§10) as a side effect of working.

**Non-goals (MVP).** Turn-by-turn voice navigation; transit/driving routing (walking only — transit is v2); multi-day trips; collaborative planning; native apps (PWA only); real-time POI data (live hours/prices); monetization features.

## 3. Users & core scenario

**Primary persona:** the deliberate traveler — plans ahead, likes maps, hates roaming charges; comfortable installing a PWA. **Secondary:** the local explorer planning a themed day in their own region.

**Core scenario (generic any-city — Ben's decision, session 1):** the user names *any* city; Siyur resolves its boundary (Overture divisions; Nominatim fallback for disambiguation — "Which Springfield?"), and the agent **researches the city on demand, driven by the user's stated preferences** (interests, pace, budget, constraints like "kids in tow" or "rainy morning"). Nothing is hardcoded per city. Validation target: MVP must be demonstrated on ≥ 3 cities of different characters (e.g., Tel Aviv, Lisbon, a small town) including one never tested before the demo — the unrehearsed-city test is a standing eval.

## 4. User experience — three phases

**Phase A · Plan (online, conversational).** Chat + live map preview side by side. The agent: resolves the city → retrieves candidate POIs (grounded, §6) → proposes a themed day (stops, timings, walking legs, meal anchors) → iterates on feedback. Two **human-approval gates** (LangGraph `interrupt()`): itinerary approval, then style approval. The user sees *why* for every suggestion (source-linked provenance chips). Streaming everywhere; a planning session is resumable across days (checkpointed threads).

**Phase B · Compile (one-shot, observable).** On approval: extract city tiles (PMTiles, tight bbox around the itinerary + buffer), generate the custom map style (Flavor delta → regenerate → validate → contrast-lint → vision check), precompute all route legs + a pruned walking graph, generate narrations with per-claim source IDs, assemble ATTRIBUTION, hash everything into a manifest. The user watches a verification checklist go green — compile is a *product moment*, not a spinner.

**Phase C · Travel (offline, dynamic).** Installed PWA reads the bundle from device storage (OPFS): position-aware (GPS works offline), timeline-aware (now/next, pace tracking vs. plan), stop stories reveal on arrival, off-route recovery via in-bundle path-finding with straight-line fallback. Attribution visible on-map and in a credits screen. No network required for anything; if online, an optional "re-plan the rest of my day" escape hatch calls back to Phase A.

**Embedding patterns (explicit, per the discussion doc §4):** Phase A = conversational surface with HITL gates; Phase B = one-shot generator producing durable artifacts; Phase C = **no LLM by design** — the degraded mode is the product.

## 5. Functional requirements (EARS samples; full set lives in Spec 001)

- WHEN the user names a city and preferences, THE SYSTEM SHALL produce a candidate itinerary whose total walking distance ≤ the user's stated limit and whose timeline respects retrieved opening windows (marked with confidence levels).
- WHEN the user approves an itinerary and style, THE SYSTEM SHALL compile a bundle whose manifest passes all integrity checks and whose total size is reported before download (target ≤ 200 MB for a metro-scale day).
- WHILE the device is offline, THE SYSTEM SHALL render map, itinerary, narrations, and off-route recovery entirely from the bundle.
- WHEN any bundled narration makes a factual claim, THE SYSTEM SHALL hold a source reference (GERS/OSM/Wikivoyage/Wikipedia/Wikidata ID) for it.
- WHEN the map style is generated, THE SYSTEM SHALL reject styles failing schema validation or contrast lint (label/halo ≥ 3:1; water/land ≥ 1.3:1).
- IF the compiled route becomes infeasible after an edit (opening window missed, budget exceeded), THEN THE SYSTEM SHALL flag the conflict before allowing approval.

## 6. Architecture (decisions pre-made by `methods/02`; changes require an ADR)

Two graphs, not one: an interactive checkpointed **planner** (LangGraph 1.x, Postgres checkpointer, FastAPI + SSE) and a batch restartable **compiler**. Key components, pinned: MapLibre GL JS 5.19.x (defer the v6 ESM transition) + PMTiles v3 via `pmtiles extract` from Protomaps daily builds (Planetiler fallback); style = `@protomaps/basemaps` Flavor deltas, validated by `@maplibre/maplibre-gl-style-spec` v25 + homegrown contrast lint + headless vision check; PWA = Vite + Workbox v7 precache for shell, **whole-archive download to OPFS** (never service-worker range-caching) with `navigator.storage.persist()`; curation = DuckDB over Overture **2026-07-22.0** (places CDLA-P, confidence ≥ 0.6; divisions for boundaries) + Overpass long tail + Wikivoyage listings + Wikipedia GeoSearch + Wikidata (CC0) + Commons with per-file license capture; hours = opening_hours.js v3.9 with locale context; routing = **Valhalla per-city Docker build at compile time** (routes, matrices, stop-order optimization; ORS free key as dev-mode fallback), travel-phase = precomputed legs + pruned walk graph + geojson-path-finder recovery; `ItineraryV1` Pydantic model = single source of truth for planner output *and* bundle schema. Geo stack pinned: `shapely~=2.1`, `h3~=4.5`, `osmnx~=2.1`, `geopandas~=1.1`, Python 3.12/uv.

Known architecture risks tracked from day one (from methods/02 §C): iOS storage-eviction ambiguity (design for re-download + launch-time integrity checks), MapLibre v6 migration debt, Overture `categories`→`basic_category` migration (Sept 2026 — write against the new fields now), Protomaps build-URL instability (resolve latest at run time).

## 7. Data & licensing (obligations are requirements, not notes)

ODbL attribution ("© OpenStreetMap contributors") visibly on every rendered map + credits screen; Overture ODbL themes credited, places (CDLA-P) bundled freely; Commons images only from {PD, CC0, CC BY, CC BY-SA} with author/license/URL captured at compile and rendered in credits; fonts OFL with license file in bundle; opening_hours.js used as unmodified LGPL dependency. **License quarantine is mechanical:** every curation-source adapter stamps its output `bundleable: true/false` + license; the narration generator refuses unstamped input; open-web search results are always `bundleable: false` (planning-time ranking signal only). `DATA-LICENSES.md` is the registry; a compile step regenerates `ATTRIBUTION.md` per bundle.

**⚠ Open decision #1 (blocking the narration generator — decide at PRD approval):** narration posture. **(a) Rich:** narrations may adapt Wikivoyage/Wikipedia prose → bundled text is CC BY-SA 4.0 with per-article attribution (open-source-friendly, best content; share-alike applies to the *text* only, not code/style/tiles). **(b) Unencumbered:** narrations generated from CC0/CDLA facts only (Wikidata, Overture, OSM tags) → no share-alike, thinner content. *Recommendation: (a) for MVP — Siyur is open-source-first and the richness matters; revisit only if a future business model demands proprietary text.*

## 8. Quality: evals & definition of done

Eval stack per `methods/01 §3`: `evals/golden/` seeded with ~25 tour-day requests (cities × interests × constraints × edge cases, including deliberately-infeasible requests the agent must refuse); `test_structural.py` (schema, geometry validity, budget/feasibility, bundle integrity, offline render via Playwright with network disabled) — **merge-blocking**; `test_trajectory.py` (agentevals superset match on `resolve_city → curate → route → design → compile`) — merge-blocking; `test_quality.py` (DeepEval LLM-judge on plan quality + style vision-check, pinned judge) — nightly, threshold-gated; `evals/history.csv` appended by CI. Definition of done per task = EARS criteria verified + tests green + evals green + ADR if a decision was made + session logged. The **airplane-mode e2e** (plan → compile → disable network → full travel flow) is the release gate for every milestone.

## 9. Build process standards [course — this *is* Goal 1's curriculum]

The build follows `methods/01` end to end: ramp-up per its 28-step checklist (AGENTS.md ≤200 lines + CLAUDE.md shim; strict shared `.claude/settings.json` safe for unattended cloud runs; disler-pattern logging hooks; Spec Kit ≥0.13 with constitution-first); Spec 001 = "plan one tour day and compile it to an offline bundle" (one vertical slice, interview-produced, EARS criteria); spec-anchored maturity — never spec-as-source; branches `agent/<ticket>-<desc>`, worktrees for parallel sessions, Co-Authored-By trailers, PR evidence sections; CI = lint/typecheck → pytest → structural+trajectory evals → Semgrep + gitleaks + pip-audit → 500-line diff guard, all required; nightly quality evals + budget-capped read-only `claude -p` review job. Constitution articles (v1): offline bundle is the product; deterministic evals gate merges; every decision → ADR; every failure → catalog entry + regression eval; data licenses tracked and attributed.

## 10. The course-feed contract [course]

The agent documents the build via five artifact types — hooks and slash commands make them near-free — each feeding named syllabus units (`course/01-syllabus.md`):

| Artifact | Mechanism | Feeds |
|---|---|---|
| **ADRs** (MADR 4.0 minimal, with Confirmation) | `/adr` command drafts from live session context; human approves | U2, U4 |
| **Devlog** (`docs/devlog/YYYY-MM-DD-*.md`) | hooks capture JSONL → `claude -p` distiller commits summaries: goal, decisions, failures, cost/turns | U2, U7 |
| **Failure catalog** (`docs/failures/FAIL-NNN.md`) | `/failure` command; **every entry must add a golden-set case or guardrail** | U2, U3, U7 |
| **Prompt & eval history** (`prompts/` front-matter versions; `evals/history.csv`) | PR-reviewed prompt files; CI appends scores | U3 |
| **Changelog + exhibits** | conventional commits → git-cliff; tagged "exhibit" commits (style-evolution sequence, tripwire catches) | U1, U5, U6 |

Acceptance: at any milestone, every syllabus unit's "build-artifact feed" line resolves to at least one real artifact. A `docs/course-feed.md` index maps units → artifacts and is checked at each retro.

## 11. Risks

| Risk | L | Impact | Mitigation |
|---|---|---|---|
| Scope creep — Siyur is app + pipeline + agent | H | schedule | Spec 001 is one vertical slice; constitution; 500-line diff guard |
| Any-city variance (thin data in small towns) | M | quality | Confidence thresholds + honest "thin coverage" UX; unrehearsed-city eval keeps us honest |
| iOS storage eviction | M | UX trust | Re-downloadable bundles, launch integrity check, early real-device test |
| Style quality unprovable | M | the "beautiful" promise | Guardrail stack + preset-flavor fallback; vision-check rubric iterated |
| Offline recovery underwhelms | L | travel trust | Precomputed legs carry the experience; recovery is explicitly approximate |
| Course-feed becomes overhead | M | Goal-1 story | Automation-first (hooks/commands); if an artifact type isn't earning its keep by milestone 2, cut it *by ADR* |
| Ecosystem drift (MapLibre v6, Overture schema) | H | maintenance | Pinned versions + tracked migration debt + data-freshness checks — itself course material |

## 12. Milestones

**M0 · Ramp-up (≈1 week):** checklist steps 1–24; repo public; CI green on a walking skeleton. **M1 · Vertical slice:** Spec 001 end-to-end for one city — plan (no style customization) → compile → offline render; airplane-mode eval passes. **M2 · The studio:** style pipeline (Flavor deltas + guardrails), narration generator with provenance + license quarantine, resumable sessions. **M3 · Any-city hardening:** 3-city validation incl. one unrehearsed; recovery routing; iOS device pass. **M4 · Course freeze:** exhibits tagged, course-feed index complete, syllabus validated against artifacts.

## 13. Approval & next step

**Ben approves/amends:** ①/②/③ product scope, embedding decisions, architecture as stated (§2–§6) · ④ narration posture — open decision #1 (§7) · ⑤ the course-feed contract's five artifact types (§10) · ⑥ milestones (§12).
**On approval:** Claude drafts the **ramp-up prompt** — a self-contained bootstrap instruction for the build session (works in both cloud and local Claude Code) that executes the 28-step checklist, wires the course-feed contract, writes constitution v1 and ADR-0001, and runs the Spec 001 interview with Ben as the first working session.
