# Delivery Plan — Siyur

*v1.1 — 2026-07-25. Splits the PRD into **deliverable units (DU)** — vertical, demoable increments so progress is visible and each increment produces course material. Companion to `tech-design.md` (what to build), `test-strategy.md` (how each DU is tested), `agent-ops.md` (how the agents evolve), and `~/code/siyur-course/syllabus.md` (units U0–U7). The course repo *observes* one-way: each DU emits exhibit-tag candidates + `course-wishlist` issues; we never edit the course repo.*

*Changelog v1.1: reconciled with the two executed D5 spikes (ADR-0003 Vite config spike, ADR-0004 planner validation spike — both now run, Confirmations satisfied) and the ADR-0005 PR/worktree workflow. No milestone re-sequencing; DU scopes threaded with the spike-proven constraints below.*

## Principle

Walking skeleton first, then thin end-to-end slices. Every DU is **demoable**, produces at least one course-feed artifact, and carries a Definition of Done. No DU merges without its DoD (per `test-strategy.md` gates 1–7).

**DoD template (every DU):** EARS criteria (PRD §5) verified · the named test tiers green · trajectory/structural evals green · an ADR if a decision was made · a devlog entry · exhibit-tag candidate proposed.

## Amendments (design review 2026-07-25)

Four ADRs from the design review (+ ADR-0005 workflow) adjust this plan — no milestone re-sequencing:

- **ADR-0002 — online-first on the bundle read model.** The client reads the compiled bundle from day one; in M1 it is served over HTTP, offline/OPFS is a later transport swap (Chromium-first). **DU-05** compiles the bundle served over HTTP; **DU-06** offline render becomes that transport swap, not a rebuild. New M1 tripwire: a scoped airplane-mode e2e asserting the client reads itinerary/sites/map **only** from bundle endpoints (never a live commons/API read) — it becomes the DU-06 gate unchanged when transport swaps to OPFS.
- **ADR-0003 — Vite pinned.** The **Vite config spike is executed (2026-07-25, Confirmation satisfied)** — module worker + Workbox precache + WASM build static together, and a server-killed offline reload served the shell from precache + read the 5 MiB archive from OPFS with **zero network bytes** (`spike/vite_spike/FINDINGS.md`). Version pins (`vite@8.1.5` / `vite-plugin-pwa@1.3.0` / `workbox@7.4.1`) are in the stack reference. Config invariants it proved thread into **DU-00** (web scaffold) and **DU-06** (airplane-mode gate) below.
- **ADR-0004 — planner = PydanticAI + LiteLLM over a `ModelRouter` seam.** Changes **DU-04** (below). The **planner validation spike is executed (2026-07-25, Confirmation satisfied)** — routing (Haiku/Sonnet/Opus per task) and a prompt-cache hit on repeated same-area research measured against the live API (`spike/planner_spike/FINDINGS.md`); `planner/` is scaffolded from the **fixed** adapter. The **seam-purity test** (`tests/test_llm_seam.py`: no provider SDK imported above `commons/llm.py`) is added at DU-02 and enforced through DU-04. The spike surfaced **two build-time constraints**, now placed on their DUs below: (1) the caching **min-prefix precondition** (`cache_control` no-ops below 2,048 tok Sonnet 5 / 4,096 tok Haiku 4.5 → the caching eval must use a realistically-sized prefix) → **DU-02**; (2) the **per-tier capability constraint** (Haiku 4.5 400s on adaptive-thinking / `output_config.effort` → the seam must gate them behind a `SUPPORTS_ADAPTIVE_EFFORT` set) → **DU-02/DU-04**. Per-task model routing lands with DU-04.
- **ADR-0005 — PR/worktree workflow.** No milestone change; two operational steps land at **DU-00**: (a) enable **branch protection** (require-a-PR now; required CI status checks jobs 1–7 added at DU-00 when CI exists); (b) the **web/ scaffolding task** gets the worktree `node_modules`/cache symlink config (`worktree.symlinkDirectories`) once `web/` exists.
- **Flagged for Ben, not scheduled:** iPhone/WebKit offline scope (future ADR before the offline-runtime milestone); cross-provider/non-Anthropic support (future ADR before any second model adapter; also a standing-decision change).
- **Ratified 2026-07-25 (Ben):** the research-tier routing sub-decision the planner spike reopened → **option 1: keep Haiku 4.5 for research with adaptive/effort gated off**; the DU-04 routing table (Haiku=research, Sonnet=curate, Opus=plan) stands unchanged (ADR-0004).

## Sequence at a glance

| DU | Increment | Milestone | Feeds | Exhibit-tag candidate |
|---|---|---|---|---|
| DU-00 | Walking skeleton (ramp-up + CI + SSO + empty map) | M0 | U1, U2 | `U1-walking-skeleton`, `U2-constitution` |
| DU-01 | Define area | M1 | U4 | `U4-area-resolution` |
| DU-02 | Research (1 source) | M1 | U3, U4 | `U4-duckdb-overture`, `U3-grounding` |
| DU-03 | Merge (2–3 sources) | M1 | U3, U4 | `U3-merge-provenance` |
| DU-04 | Plan (no variants) | M1 | U3, U5 | `U5-hitl-gate` |
| DU-05 | Compile → bundle | M1 | U4, U5 | `U5-compile-moment`, `U4-valhalla` |
| DU-06a | Rendered-viewport gate (**lands red**) | M1 | U2 | `U2-green-tests-blind-to-pixels` |
| DU-06b | Usable on a phone | M1 | U0, U5 | `U2-the-assertion-that-was-three-lines` |
| DU-06 | Offline render (airplane-mode) | M1 | U0, U5 | `U0-airplane-mode` |
| DU-07+ | schematic map · Plan B/C · narration+quarantine · dynamic timeline · resumable · commons-at-scale | M2 | U3, U5 | *(sketch)* |
| … | i18n + RTL (Hebrew) · 3-area validation (incl. unrehearsed + non-Latin) · recovery · iOS · GCP deploy | M3 | U0, U5 | *(sketch)* |
| … | exhibits tagged · course-feed index complete · syllabus validated | M4 | U6, U7 | *(sketch)* |

The discovery spike (`tech-design.md` §7) precedes DU-00 and hardens the schema + merge thresholds.

---

## M0 — Ramp-up

### DU-00 · Walking skeleton
- **Scope:** the design-dependent remainder of the ramp-up — constitution (ratifying the D3/D4 ⟐ rules + the PRD §13 #1 reframe), Spec 001, ADRs 0002+ for forced choices, the five schema cards (`docs/data/`), `DATA-LICENSES.md`, the package skeleton (`commons/planner/compiler/api/web`), full CI (jobs 1–7) green on stubs, branch protection. Google SSO login works; an empty MapLibre map renders.
  - **`web/` scaffold carries the ADR-0003 spike-proven config** (`spike/vite_spike/` is the reference): `vite@8.1.5` + `vite-plugin-pwa@1.3.0` + `workbox@7.4.1`; `worker.format:'es'` (the OPFS reader is a *module* worker); the PMTiles archive is runtime-fetched into OPFS — **never `import`ed, never in `public/`** (which copies verbatim into `dist/`); `workbox.globPatterns` excludes the archive with a small `maximumFileSizeToCacheInBytes` as a leak tripwire; `base:'/'` unless a CDN sub-path forces `base` + SW-scope alignment. Add the worktree `node_modules`/cache symlink config (`worktree.symlinkDirectories`, ADR-0005) to this scaffold task.
  - **Branch protection (ADR-0005) — deferred, blocked on repo tier.** GitHub branch protection / required status checks are unavailable on this private free-tier repo, so checks 1–7 cannot be made machine-required at DU-00. The PR-and-green-CI merge gate is instead a **self-enforced discipline** binding all sessions (AGENTS.md §Conventions); enable protection to lock it in if the repo goes Pro or public.
- **Demo:** sign in with Google → see an empty map; the PR shows all 7 required checks green.
- **Tests:** every tier stood up green with stubs so the gates exist before the features; `tests/test_geo_api_pins.py` tripwire live; a skeletal airplane-mode e2e (empty map renders offline).
- **Artifacts:** constitution, Spec 001, ADR chain, schema cards, DATA-LICENSES.md, first green CI run. **DoD:** checks 1–7 green · SSO works · Spec 001 zero `[NEEDS CLARIFICATION]` · devlog.

---

## M1 — Vertical slice (the airplane-mode promise, thin)

### DU-01 · Define area
- **Scope:** draw/name an area → resolve polygon (Overture divisions; Nominatim fallback for disambiguation) → commons coverage query (`ST_Within`).
- **Demo:** draw a box, get its boundary + "N sites already known here."
- **EARS:** "delimits an area already covered → reuse existing cited data + offer refresh."
- **Tests:** T1 polygon/bbox geometry + resolve logic (mocked); T2 component `POST /areas` over PostGIS coverage query. **Artifacts:** tile-source schema card, devlog, `exhibit/U4-area-resolution`.

> **i18n sliver in M1 (accepted 2026-07-24):** DU-02/DU-03 include transliteration of the display **name/address** to the presentation language (source scripts are untrustworthy — FAIL-001). Full multi-language + RTL stays M3; exact extent pinned in Spec 001.

### DU-02 · Research (one source)
- **Scope:** DuckDB over Overture → `SiteRecordV1`s stamped with provenance, persisted (single source, no merge yet).
- **Demo:** research the area → cited sites appear on the map, each with a source chip.
- **EARS:** "every bundled claim holds a source reference; unstamped input refused."
- **Tests:** T1 `SourcedValue` stamping + schema + quarantine invariant; T2 integration DuckDB fixture → persist → read back; deterministic eval: research-node schema-valid output. **Planner-spike regression evals (ADR-0004, land with the research node):** (a) **caching-regression** — `cache_read > 0` on repeated same-area research, asserted against a **realistically-sized (> min-prefix) cached prefix**, not a stub, so `cache_read=0` can't be a false pass on an uncacheable prefix; (b) **seam-capability** — the seam must **not** send adaptive-thinking / `output_config.effort` to a model that 400s on them (Haiku 4.5), i.e. verify the `SUPPORTS_ADAPTIVE_EFFORT` gate in `commons/llm.py` (complements the seam-purity test). **Artifacts:** `prompts/research.md` v1, POI/site schema card, DATA-LICENSES Overture rows, a curation-source-adapter skill (agent-ops D4 #2), ADR (adapter + quarantine pattern), `exhibit/U4-duckdb-overture`, `exhibit/U3-grounding`.

### DU-03 · Merge (2–3 sources)
- **Scope:** add Overpass/Wikivoyage/OSM → per-field merge + conflict flags. **ε/τ from the spike.**
- **Demo:** one site enriched from three sources; conflicting hours flagged, not silently overwritten.
- **EARS:** "merge multiple sources into one record, retain a source ref per field, flag conflicts."
- **Tests:** T1 merge logic (no source lost, conflict creation, winner policy); T2 multi-source integration; eval: merge-correctness golden cases. **Artifacts:** ADR (merge policy + ε/τ), any FAIL entries + regression evals, `exhibit/U3-merge-provenance`.

### DU-04 · Plan (no variants)
- **Scope:** planner (PydanticAI + LiteLLM over the `ModelRouter` seam, ADR-0004) → `ItineraryV1` (no Plan B/C) + HITL approval (explicit persisted pause); per-task model routing (Haiku=research, Sonnet=curate, Opus=plan). Scaffolded from the **fixed** `spike/planner_spike/` adapter (post the Haiku adaptive/effort gate), not the as-written reference. Research-tier routing **ratified 2026-07-25**: Haiku 4.5 stays for research with adaptive/effort gated off (ADR-0004 option 1) — routing table unchanged, no longer a gate.
- **Demo:** chat "half-day, art + coffee" → itinerary with provenance chips → approve.
- **EARS:** "candidate itinerary whose walking ≤ stated limit and whose timeline respects opening windows."
- **Tests:** T1 planner node (mocked model, schema-valid itinerary, feasibility) + **seam-purity test** (no provider SDK above `commons/llm.py`); T2 pipeline run w/ own Postgres/SQLite checkpoint + explicit HITL pause; **trajectory eval** superset match on `resolve_area→research→curate→propose_itinerary`. **Artifacts:** `prompts/planner.md`, ItineraryV1 schema card, ADR (HITL gate), `exhibit/U5-hitl-gate`, `exhibit/U3-structured-output`.

### DU-05 · Compile → bundle
- **Scope:** `pmtiles extract` + Valhalla legs + quarantine filter + `BundleManifestV1` + download to OPFS.
- **Demo:** approve → compile verification checklist goes green → bundle downloads.
- **EARS:** "compile a bundle whose manifest passes integrity checks; report size before download."
- **Tests:** T1 manifest hash + quarantine filter; T2 compiler contract test (rebuild, verify hashes, assert no `bundleable=false`) + Valhalla/fake-gcs integration. **Artifacts:** route-leg + bundle-manifest schema cards, ADRs (routing engine = Valhalla; tile source = Protomaps), ATTRIBUTION pipeline, `exhibit/U5-compile-moment`, `exhibit/U4-valhalla`.

### DU-06a · Rendered-viewport gate — **lands red** *(added 2026-08-15)*
- **Scope:** a Playwright assertion suite over **real layout** at 375×667 / 390×844 / 430×932 (plus 1440×900 for the attribution): reachability via `elementFromPoint`, tap targets ≥ 44 px, type floor ≥ 14 px (chips ≥ 11 px, allowlisted explicitly), no horizontal overflow, ODbL attribution unoccluded. Plus a source scan holding the stylesheets to logical direction properties.
- **Demo:** the suite runs on `main` and **names the defects** — `Use this view` painted under `Plan a day`, 11 of 13 controls under the tap floor, five clipped out of the plan panel's own scroller, the ODbL credit under the sheet at every width including desktop.
- **EARS:** "WHERE the viewport is 375–430 px, every interactive element SHALL be reachable, ≥ 44 px, and legible."
- **Why it is its own DU, before the fixes:** every DU-06b fix is verified by this suite, so landing it afterwards would make it a suite nobody has ever seen fail. The known failures ship as `test.fail()` — CI green on a red gate — and each turns **red the moment a fix lands without deleting its marker**. This is FAIL-014's lesson applied in advance: *a check nobody has watched fail is not yet a control.* **Artifacts:** `web/test/e2e/viewport.spec.ts`, `web/test/css-logical.test.ts`, CI job 5 promoted from an `echo` stub to a real gate, FAIL-012's guardrail, `exhibit/U2-green-tests-blind-to-pixels`.

### DU-06b · Usable on a phone *(added 2026-08-15)*
- **Scope:** the phone pass over DU-01…DU-05 (F-01…F-07) — the flow column below 760 px (ADR-0035), tap targets and type floor, the sheet off the attribution, search-glyph contrast, the coverage card reading `known_site_count` in both branches, honest pending/empty states, a visible signed-out state.
- **Demo:** **a human completes delimit → research → read a site → plan a day → approve at 390 px**, recorded as a GIF. Not "the suite is green" — that phrase is what produced the audit.
- **EARS:** "WHILE on a 390 px viewport, a user SHALL complete the research-and-plan journey without a desktop, devtools, or a remembered UUID."
- **Tests:** each task deletes a named `test.fail()` from DU-06a; nothing is "done" on description. **Artifacts:** ADR-0035, `exhibit/U2-the-assertion-that-was-three-lines`.

### DU-06 · Offline render — **M1 done**
- **Scope:** PWA reads the bundle from OPFS; the full offline experience (map, itinerary, timeline, narration, off-route recovery).
- **Demo:** disable network → walk the plan → everything works.
- **EARS:** "WHILE offline, render map, itinerary, narrations, and off-route recovery from the bundle."
- **Tests:** **T3 airplane-mode e2e is THE release gate** (network off, tiles from OPFS, zero network requests, recovery works) + OPFS-load integration. This is the **same module-worker + OPFS-sync-read + Workbox-precache path the ADR-0003 Vite spike already proved** under a server-killed reload (zero network bytes) — DU-06 productionizes that spike path behind the ADR-0002 transport swap, it does not re-derive it. **Artifacts:** the compiled example bundle, the airplane-mode e2e as the standing gate, `v0.x` milestone tag, `exhibit/U0-airplane-mode`, `exhibit/U5-offline-bundle`.

---

## M2–M4 (sketched — detailed when M1 lands)

- **M2 · The studio:** schematic-map render pass + dynamic timeline (PRD §13 #5), narration generator with per-claim provenance + license quarantine, Plan B/C variants (+ their routing, bundle-size strategy ❓), resumable checkpointed sessions, commons merge-at-scale, Cloud Run Jobs compiler, OTel/Phoenix tracing (agent-ops D4 #3). *Feeds U3, U5.*
- **M3 · Reach & hardening:** multi-language + RTL (Hebrew) end-to-end, 3-area validation incl. one unrehearsed + a non-Latin-script name, recovery routing depth, iOS device pass (storage-eviction), first GCP deploy. *Feeds U0, U5.*
- **M4 · Course freeze:** exhibits tagged across U0–U7, `docs/course-feed.md` index resolves every unit, syllabus validated against real artifacts, `v1.0`. *Feeds U6, U7.*

## Course-feed index

`docs/course-feed.md` (created at ramp-up) maps each syllabus unit → the artifacts that fill it, and is checked at each retro. Acceptance: at any milestone every unit's "build-artifact feed" resolves to ≥1 real artifact.
