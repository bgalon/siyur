# Delivery Plan — Siyur

*v1.0 — 2026-07-24. Splits the PRD into **deliverable units (DU)** — vertical, demoable increments so progress is visible and each increment produces course material. Companion to `tech-design.md` (what to build), `test-strategy.md` (how each DU is tested), `agent-ops.md` (how the agents evolve), and `~/code/siyur-course/syllabus.md` (units U0–U7). The course repo *observes* one-way: each DU emits exhibit-tag candidates + `course-wishlist` issues; we never edit the course repo.*

## Principle

Walking skeleton first, then thin end-to-end slices. Every DU is **demoable**, produces at least one course-feed artifact, and carries a Definition of Done. No DU merges without its DoD (per `test-strategy.md` gates 1–7).

**DoD template (every DU):** EARS criteria (PRD §5) verified · the named test tiers green · trajectory/structural evals green · an ADR if a decision was made · a devlog entry · exhibit-tag candidate proposed.

## Sequence at a glance

| DU | Increment | Milestone | Feeds | Exhibit-tag candidate |
|---|---|---|---|---|
| DU-00 | Walking skeleton (ramp-up + CI + SSO + empty map) | M0 | U1, U2 | `U1-walking-skeleton`, `U2-constitution` |
| DU-01 | Define area | M1 | U4 | `U4-area-resolution` |
| DU-02 | Research (1 source) | M1 | U3, U4 | `U4-duckdb-overture`, `U3-grounding` |
| DU-03 | Merge (2–3 sources) | M1 | U3, U4 | `U3-merge-provenance` |
| DU-04 | Plan (no variants) | M1 | U3, U5 | `U5-hitl-gate` |
| DU-05 | Compile → bundle | M1 | U4, U5 | `U5-compile-moment`, `U4-valhalla` |
| DU-06 | Offline render (airplane-mode) | M1 | U0, U5 | `U0-airplane-mode` |
| DU-07+ | schematic map · Plan B/C · narration+quarantine · dynamic timeline · resumable · commons-at-scale | M2 | U3, U5 | *(sketch)* |
| … | i18n + RTL (Hebrew) · 3-area validation (incl. unrehearsed + non-Latin) · recovery · iOS · GCP deploy | M3 | U0, U5 | *(sketch)* |
| … | exhibits tagged · course-feed index complete · syllabus validated | M4 | U6, U7 | *(sketch)* |

The discovery spike (`tech-design.md` §7) precedes DU-00 and hardens the schema + merge thresholds.

---

## M0 — Ramp-up

### DU-00 · Walking skeleton
- **Scope:** the design-dependent remainder of the ramp-up — constitution (ratifying the D3/D4 ⟐ rules + the PRD §13 #1 reframe), Spec 001, ADRs 0002+ for forced choices, the five schema cards (`docs/data/`), `DATA-LICENSES.md`, the package skeleton (`commons/planner/compiler/api/web`), full CI (jobs 1–7) green on stubs, branch protection. Google SSO login works; an empty MapLibre map renders.
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
- **Tests:** T1 `SourcedValue` stamping + schema + quarantine invariant; T2 integration DuckDB fixture → persist → read back; deterministic eval: research-node schema-valid output. **Artifacts:** `prompts/research.md` v1, POI/site schema card, DATA-LICENSES Overture rows, a curation-source-adapter skill (agent-ops D4 #2), ADR (adapter + quarantine pattern), `exhibit/U4-duckdb-overture`, `exhibit/U3-grounding`.

### DU-03 · Merge (2–3 sources)
- **Scope:** add Overpass/Wikivoyage/OSM → per-field merge + conflict flags. **ε/τ from the spike.**
- **Demo:** one site enriched from three sources; conflicting hours flagged, not silently overwritten.
- **EARS:** "merge multiple sources into one record, retain a source ref per field, flag conflicts."
- **Tests:** T1 merge logic (no source lost, conflict creation, winner policy); T2 multi-source integration; eval: merge-correctness golden cases. **Artifacts:** ADR (merge policy + ε/τ), any FAIL entries + regression evals, `exhibit/U3-merge-provenance`.

### DU-04 · Plan (no variants)
- **Scope:** LangGraph planner → `ItineraryV1` (no Plan B/C) + HITL approval (`interrupt()`).
- **Demo:** chat "half-day, art + coffee" → itinerary with provenance chips → approve.
- **EARS:** "candidate itinerary whose walking ≤ stated limit and whose timeline respects opening windows."
- **Tests:** T1 planner node (mocked model, schema-valid itinerary, feasibility); T2 graph run w/ SQLite checkpointer + HITL interrupt; **trajectory eval** superset match on `resolve_area→research→curate→propose_itinerary`. **Artifacts:** `prompts/planner.md`, ItineraryV1 schema card, ADR (HITL gate), `exhibit/U5-hitl-gate`, `exhibit/U3-structured-output`.

### DU-05 · Compile → bundle
- **Scope:** `pmtiles extract` + Valhalla legs + quarantine filter + `BundleManifestV1` + download to OPFS.
- **Demo:** approve → compile verification checklist goes green → bundle downloads.
- **EARS:** "compile a bundle whose manifest passes integrity checks; report size before download."
- **Tests:** T1 manifest hash + quarantine filter; T2 compiler contract test (rebuild, verify hashes, assert no `bundleable=false`) + Valhalla/fake-gcs integration. **Artifacts:** route-leg + bundle-manifest schema cards, ADRs (routing engine = Valhalla; tile source = Protomaps), ATTRIBUTION pipeline, `exhibit/U5-compile-moment`, `exhibit/U4-valhalla`.

### DU-06 · Offline render — **M1 done**
- **Scope:** PWA reads the bundle from OPFS; the full offline experience (map, itinerary, timeline, narration, off-route recovery).
- **Demo:** disable network → walk the plan → everything works.
- **EARS:** "WHILE offline, render map, itinerary, narrations, and off-route recovery from the bundle."
- **Tests:** **T3 airplane-mode e2e is THE release gate** (network off, tiles from OPFS, zero network requests, recovery works) + OPFS-load integration. **Artifacts:** the compiled example bundle, the airplane-mode e2e as the standing gate, `v0.x` milestone tag, `exhibit/U0-airplane-mode`, `exhibit/U5-offline-bundle`.

---

## M2–M4 (sketched — detailed when M1 lands)

- **M2 · The studio:** schematic-map render pass + dynamic timeline (PRD §13 #5), narration generator with per-claim provenance + license quarantine, Plan B/C variants (+ their routing, bundle-size strategy ❓), resumable checkpointed sessions, commons merge-at-scale, Cloud Run Jobs compiler, OTel/Phoenix tracing (agent-ops D4 #3). *Feeds U3, U5.*
- **M3 · Reach & hardening:** multi-language + RTL (Hebrew) end-to-end, 3-area validation incl. one unrehearsed + a non-Latin-script name, recovery routing depth, iOS device pass (storage-eviction), first GCP deploy. *Feeds U0, U5.*
- **M4 · Course freeze:** exhibits tagged across U0–U7, `docs/course-feed.md` index resolves every unit, syllabus validated against real artifacts, `v1.0`. *Feeds U6, U7.*

## Course-feed index

`docs/course-feed.md` (created at ramp-up) maps each syllabus unit → the artifacts that fill it, and is checked at each retro. Acceptance: at any milestone every unit's "build-artifact feed" resolves to ≥1 real artifact.
