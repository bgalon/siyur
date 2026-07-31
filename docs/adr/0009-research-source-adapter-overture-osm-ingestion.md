# 0009 — Research source-adapter pattern: Overture (DuckDB parquet) + OSM (Overpass) ingestion for slice 001

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: Ben · Date: 2026-07-31

## Context and Problem Statement

Spec 001 (`specs/001-research-cited-sites/`) FR-002/FR-011 require research to locate + cite real-world places from **Overture + OpenStreetMap**, each value provenance-stamped (`SourcedValue`, Constitution V). `tech-design.md` §5.5 names "DuckDB over Overture (+ Overpass…)" and `delivery-plan.md` DU-02 explicitly reserves an **"ADR (adapter + quarantine pattern)"** — the concrete *how* was left to build time. Planning slice 001 forces it now: how does research physically read each source, and how is the per-source provenance/quarantine stamp applied so that FR-003 (refuse unstamped; 100% stamped) and the `bundleable` quarantine hold from ingestion?

Two source-specific facts from the discovery spike (tech-design §1.1/§7.1) shape the choice: (a) Overture places carry **per-record** licenses that differ *within* one theme (Meta CDLA-Permissive-2.0 vs Foursquare Apache-2.0) — the theme default must never be assumed; (b) **Overpass is flaky (504s)** — ingestion must degrade gracefully (FR-012), and the commons cache is itself the reliability mechanism.

This ADR is scoped to slice 001 (Overture + OSM). Wikivoyage/Wikipedia/Wikidata/Commons adapters (stories, extra facts) are slice 002+ and out of scope; they will reuse this pattern.

## Considered Options

- **A — A `SourceAdapter` protocol; Overture via DuckDB-over-cloud-parquet, OSM via Overpass; stamp at the adapter boundary.** One small protocol (`base.py`) that every source implements, yielding already-`SourcedValue`-stamped candidates; `overture.py` runs DuckDB (`spatial`+`httpfs`) against the hosted Overture parquet filtered to the area, reading the license **per record**; `osm.py` calls Overpass with a timeout and returns partial results on failure. The stamp (source+license+`bundleable`) is applied **once, at the boundary**, so nothing unstamped can flow downstream.
- **B — Bulk-mirror sources into local storage first, then ingest.** Download the Overture theme / an OSM extract, ingest from local. Reproducible offline, but heavy, quickly stale, and needless at slice scale; still needs the same stamping logic.
- **C — No shared protocol; bespoke per-source ingestion inline in the research node.** Fastest to write one source; but stamping/quarantine logic scatters and drifts per source (a provenance-completeness risk), and adding slice-002 sources means re-deriving the boundary each time.

## Decision Outcome

Chosen: **A — a `SourceAdapter` protocol with boundary stamping; Overture via DuckDB parquet, OSM via Overpass**, because it makes provenance **mechanical and single-sited** (Constitution V): every source funnels through one stamp point, so "refuse unstamped input" and the `bundleable` quarantine are enforced by construction, not by per-source vigilance. It matches the pinned toolchain (`duckdb~=1.3`) and the tech-design without mirroring cost (B), and it keeps slice-002 sources additive (one new adapter) rather than a re-architecture (C). Per-record Overture license reading and Overpass graceful-degradation are properties of the two adapters, not the caller.

**Scope / non-goals**: this fixes the *ingestion + stamping pattern* for Overture + OSM only. The **merge thresholds** (ε=25 m, τ=0.6, name-signal-required) are **already spike-locked** (tech-design §1.2/§6) and are *not* re-decided here; the DU-03 "merge policy" ADR line in the delivery plan is satisfied by that existing lock plus this pattern. `osmnx` remains the tool for **street-graph** reads at DU-05 routing, not the POI path.

### Consequences

- Good: provenance is enforced at one boundary → FR-003 / SC-002 (100% stamped) is structural, not aspirational; the quarantine test guards it.
- Good: no Overture mirror to keep fresh; the commons is the cache (tech-design §3 cost posture); Overpass flakiness degrades to partial results (FR-012), not a hang.
- Good: slice-002 sources (Wikivoyage/Wikidata/…) are one adapter each behind the same protocol.
- Bad / accepted cost: DuckDB-over-remote-parquet depends on Overture's hosted layout/URL — pinned + fixture-backed, but an upstream layout change needs an adapter bump. Reading license **per record** is mandatory boilerplate (never trust the theme default) — accepted, it is the spike's explicit lesson.
- Accepted: live Overture/Overpass are **never** hit in CI — a tiny committed Overture parquet + a small Overpass JSON fixture back the tests (test-strategy.md "never hit live … in CI").

### Confirmation

- **Provenance-completeness eval** (deterministic, PR-gating): every value produced by any adapter is a `SourcedValue` with a non-null `source`; an adapter emitting a bare value fails. Complements `evals/test_structural.py::test_no_unbundleable_in_bundle` (quarantine, merge-blocking).
- **Per-record license test** (`tests/test_sources_overture.py`): a fixture mixing CDLA-Permissive-2.0 and Apache-2.0 rows within one theme yields correct per-row `license` + `bundleable` stamps (not the theme default).
- **Graceful-degradation test** (`tests/test_sources_osm.py`): a simulated Overpass 504 yields partial results + `degraded=true` in the research `summary`, never an exception to the user (FR-012).
- **Trajectory eval**: the research phase appears in the `resolve_area → research → curate` superset (mocked LLM).
- **TODO (lands with DU-02/DU-03 implementation):** the three tests above + the committed Overture/Overpass fixtures.
