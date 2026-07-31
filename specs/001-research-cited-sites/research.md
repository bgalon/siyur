# Phase 0 — Research: Research an area into a cited commons, rendered on the map

**Feature**: `specs/001-research-cited-sites` · **Date**: 2026-07-31

Purpose: resolve every open unknown before design. Most decisions are **already made** in the design docs / ADRs and are recorded here as *reused* (with the citation), so implementation does not re-open them. Two genuinely-new forced choices are settled here and captured as **ADR-0009** and **ADR-0010** (both `proposed`, awaiting Ben).

The spec has **zero `[NEEDS CLARIFICATION]`** (all three resolved A/A/A by Ben on 2026-07-31). No unresolved product clarifications remain; the items below are *technical* unknowns for the plan.

---

## R1 — How does research read Overture places? (NEW → ADR-0009)

- **Decision**: Read the Overture **places** theme with **DuckDB** (`spatial` + `httpfs` extensions) directly over the hosted cloud parquet, filtered to the resolved area bbox/polygon. No bulk download; the query is the ingestion. Each returned place is stamped into `SourcedValue`s at the adapter boundary (`kind="overture"`, `license` read **per-record** — CDLA-Permissive-2.0 or Apache-2.0 within the same theme, never the theme default).
- **Rationale**: tech-design §5.5 already specifies "DuckDB over Overture"; DuckDB is pinned (`~=1.3`). Reading remote parquet keeps the commons the cache (cost mitigation, tech-design §3) rather than mirroring Overture. Per-record license reading is mandated by the spike finding (tech-design §1.1: Meta CDLA vs Foursquare Apache within one theme) and `DATA-LICENSES.md`.
- **Alternatives considered**: (a) download the whole theme to local parquet — heavy, stale, needless at slice scale; (b) Overture's own APIs — not GA / not needed; (c) go through the LLM to "fetch" places — forbidden (model-invented data, FR-002/FR-005).
- **CI note**: never hit live Overture in CI — a **tiny committed Overture parquet fixture** backs the integration test (test-strategy.md).

## R2 — How does research read OSM? (NEW → ADR-0009)

- **Decision**: **Overpass API** over HTTP for the OSM long-tail (POI tags, `name:xx`), filtered to the area. Stamped `kind="osm"`, `license="ODbL-1.0"`, `attribution="© OpenStreetMap contributors"`. Overpass is wrapped by an adapter with **timeout + graceful degradation** (partial results, FR-012) because the spike found Overpass flaky (504s) — the commons cache is a reliability mechanism, not just cost (tech-design §7.1).
- **Rationale**: `DATA-LICENSES.md` lists Overpass as the OSM long-tail source; OSM contributes the local-script (`name:el`) names Overture lacks (spike: Greek names came mainly from OSM). ODbL attribution is required and already in the registry.
- **Alternatives considered**: (a) `osmnx` (pinned `~=2.1`) — excellent for street *graphs* (needed at DU-05 routing), heavier than needed for POI tag reads here; kept available but not the POI path; (b) a full planet/Geofabrik extract — overkill for a bbox; (c) Nominatim for POIs — it is a geocoder (used only for **area** disambiguation, R3), not a POI source.

## R3 — How is the delimited area resolved to a polygon? (reused — tech-design §5.5, DU-01)

- **Decision**: name/drawn-box → polygon via **Overture divisions**; **Nominatim** is the disambiguation fallback (respecting its ≤1 req/s + honest User-Agent terms, `DATA-LICENSES.md`). The polygon drives both the source-adapter spatial filter and the commons coverage query.
- **Rationale**: Straight from tech-design §5.5 and DU-01 scope; no new decision.
- **Genericity**: the area is user-supplied; **nothing is hardcoded to Rhodes** (FR-001). Rhodes is only the demo fixture.

## R4 — Reuse + refresh: how is "already covered" decided? (reused — tech-design §2/§1.2)

- **Decision**: On area define, run the **PostGIS coverage query** `SELECT … FROM site WHERE ST_Within(geom, :area_polygon)` (GiST-indexed `geom`). If it returns records, **show them without a research pass** and offer refresh (US2 / FR-006). Staleness for the refresh prompt is driven by each value's **`observed_at`** (tech-design §1.2 "staleness drives refresh-on-reuse"). Refresh re-runs research and merges; new observation dates + any newly-disagreeing values become `FieldConflict`s (no source lost).
- **Rationale**: Existing data-spine mechanism; the spec asserts the *outcome* (reuse, refresh offered, no source lost), not a new mechanism.
- **Alternatives considered**: bbox-overlap heuristic instead of `ST_Within` — coarser; the point-in-polygon coverage query is already the design. A time-based auto-expire — deferred; refresh is user-offered, not forced (tech-design §1.2 "doesn't block").

## R5 — Merge / dedupe on write into the shared commons (reused — tech-design §1.2, ADR-0008)

- **Decision**: Records write **directly** to the shared, global commons (auth-gated), deduped by the **existing merge**: join on `gers_id` when present, else **fuzzy spatial+name** — PostGIS distance ≤ **ε = 25 m** AND same-language name similarity ≥ **τ = 0.6** (after transliteration); **distance alone never merges** (name signal required). Per-field, union-first; the winning `SourcedValue` is kept, a losing *different* value becomes a `FieldConflict`; **no source ref is ever lost**. Winner = highest `confidence`, tie-break source-trust (Overture/Wikidata > Wikivoyage > OSM tags > open_web) then most recent `observed_at`.
- **Rationale**: ε/τ are **spike-locked** (tech-design §1.2/§6) — not re-derived here. ADR-0008 settles that this is a *direct* shared write (Q1=A); broader governance deferred.
- **Alternatives considered**: staged→promote or private-only — rejected by ADR-0008.
- **Note**: the merge-policy ADR anticipated for DU-03 (`delivery-plan.md`) restates these spike-locked thresholds; this plan does not change them, so no *new* threshold ADR is drafted — ADR-0009 covers the source-adapter/quarantine pattern that DU-02/03 flagged.

## R6 — Greek→Latin display-name transliteration sliver (NEW → ADR-0010)

- **Decision**: **Deterministic, offline, rule-based** Greek→Latin transliteration (ICU `Greek-Latin` transform) applied **only to the display-name field**, producing a new `names["el-Latn"]` `SourcedValue`. The original-script `names["el"]` value **and its attribution are preserved untouched**. The transliterated value **inherits the upstream `SourceRef`** (so ODbL attribution + `bundleable` carry through as a produced work of the same source) with `confidence` reflecting transliteration certainty. Addresses are **excluded** — source address scripts are untrustworthy (FAIL-001). Before transliteration, the value's **script is validated** against its declared BCP-47 language (the FAIL-001 guard); a script mismatch is normalised/flagged, never trusted.
- **Rationale**: FR-008 mandates automatic display-name-only transliteration; SC-004 sets a **≥95%** bar with the original always preserved. A deterministic transform is **testable to a fixed expected output**, **offline**, **free**, and honors the determinism discipline (the LLM never does this arithmetic). Transliteration is a *produced work* of the source data → license inherits (ODbL/CDLA), so it stays bundleable-correct.
- **Alternatives considered**: (a) **LLM transliteration via the seam** — non-deterministic, token cost, hard to gate at ≥95% with a fixed test, and unnecessary for names (reserved for *translation*, an M3 concern); (b) `unidecode`-style ASCII-folding — lossy/inaccurate for Greek diacritics; (c) a first-class `derived`/`transliteration` `SourceRef.kind` — the schema card's enum has none, and inventing one is a `SiteRecordV2` concern, out of scope; inheriting the upstream ref is the minimal provenance-correct choice.
- **Open sub-decision for Ben (in ADR-0010)**: the exact package pin (PyICU vs a pure-python transliteration lib) is **resolve-then-pinned** at implementation (ADR-0007 discipline); the *approach* (deterministic, display-name-only, inherit provenance) is what the ADR fixes.

## R7 — Planner nodes over the seam (reused — ADR-0004)

- **Decision**: `resolve_area → research → curate` run as typed **PydanticAI** nodes over the **`ModelRouter` seam** (`commons/llm.py`); **no langgraph**. Per-task routing: **Haiku 4.5 = research**, **Sonnet 5 = curate** (Opus=plan is DU-04, out of this slice). The seam gates adaptive-thinking / `output_config.effort` behind `SUPPORTS_ADAPTIVE_EFFORT` (Haiku 4.5 400s on them — planner-spike constraint). The **caching lever** is asserted with a realistically-sized prefix (min-prefix precondition).
- **Rationale**: ADR-0004 (accepted) + its satisfied spike. Seam purity is enforced by `tests/test_llm_seam.py` (lands DU-02).
- **Determinism discipline**: the LLM curates/ranks and (later) writes prose; **it never emits or computes coordinates** — DuckDB/PostGIS/shapely do (FR-005). Tool nodes are typed and unit-tested with a **mocked model**.

## R8 — Provenance stamping + quarantine (reused — Constitution V, tech-design §1.0)

- **Decision**: Every value is a `SourcedValue` stamped `source + license + bundleable` **at the adapter boundary** (ingestion). `bundleable=true` only if `source.license ∈` the allowlist {ODbL, CDLA-Permissive-2.0, CC0, CC-BY-4.0, CC-BY-SA-4.0, PD, OFL, LGPL-as-dependency}; `open_web`/`review_provider` are always `false`. Unstamped input is **refused** and **never displayed** (FR-003 / SC-002). The invariant is a **merge-blocking structural test** (`evals/test_structural.py::test_no_unbundleable_in_bundle`).
- **Rationale**: Constitution Article V; `DATA-LICENSES.md` is the registry; verbatim from the schema card.

## R9 — Auth + privacy boundary (reused — tech-design §5.4, ADR-0008, FR-010)

- **Decision**: Google-OIDC via Identity Platform → PWA ID token → `Authorization: Bearer` → FastAPI JWT-verify dependency (issuer/audience + Google public keys) → `user_id`. The **commons (`site*`) is world-readable to any signed-in user**; writes are auth-gated. **User notes/plans/prefs are per-user, row-scoped, never auto-published** to the commons (FR-010). Firebase Auth emulator locally.
- **Rationale**: tech-design §5.4; ADR-0008 (auth-gated shared write); the privacy boundary is Article V + PRD §13 #4.

---

## Resolved unknowns summary

| # | Unknown | Resolution | Source |
|---|---|---|---|
| R1 | Overture read | DuckDB over cloud parquet, per-record license | **ADR-0009 (new)** |
| R2 | OSM read | Overpass adapter, graceful degradation | **ADR-0009 (new)** |
| R3 | Area→polygon | Overture divisions + Nominatim fallback | tech-design §5.5 |
| R4 | Reuse/refresh | `ST_Within` coverage + `observed_at` staleness | tech-design §2/§1.2 |
| R5 | Merge/dedupe | ε=25m, τ=0.6, name-signal, no source lost | tech-design §1.2 · ADR-0008 |
| R6 | Transliteration | deterministic Greek→Latin, display-name only, inherit provenance | **ADR-0010 (new)** |
| R7 | Planner/seam | PydanticAI over `ModelRouter`, Haiku/Sonnet routing | ADR-0004 |
| R8 | Provenance/quarantine | stamp at ingestion; allowlist; merge-blocking test | Constitution V |
| R9 | Auth/privacy | Google-OIDC JWT dep; commons world-read; notes private | tech-design §5.4 · ADR-0008 |

**All unknowns resolved. No `NEEDS CLARIFICATION` remains.** Ready for Phase 1.
