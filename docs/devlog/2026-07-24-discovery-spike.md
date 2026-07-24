# 2026-07-24 — Discovery spike: three scripts, three findings that changed the design

**Goal:** before hardening the `SiteRecordV1` schema and the merge model, pressure-test them against real data from three deliberately diverse areas — Rhodes/Ρόδος (Greek), Jaffa/יפו (Hebrew+Arabic, RTL), Takayama/高山 (CJK) — and derive the merge thresholds from data instead of guessing. Throwaway code in `spike/` (gitignored).

## What happened

Confirmed the data path first (DuckDB → Overture on S3 works; the pinned 2026-07-22.0 release exists; the spatial extension auto-decodes GeoParquet so `ST_X(geometry)` works directly). Then hit two instructive snags: my plan to derive thresholds from Overture→OSM *source links* found **zero** links — because Overture places are **Meta/Foursquare-sourced, not OSM** (0/2175 in Rhodes), with **per-record licenses** (CDLA-P + Apache-2.0). And Jaffa's first Overpass call was a **504** (public API overload), so the first run captured *no* Hebrew names — added retries + mirror fallback to fix it.

Pivoted the threshold derivation to the fuzzy-match distribution: measure coordinate offset for name-agreeing pairs (→ ε) and name-similarity for spatially-close pairs (→ τ). Results were consistent across all three areas.

## Findings (folded into tech-design §1.1/§1.2/§6)

- **ε = 25 m, τ = 0.6 on same-language names, name-signal-required** — derived. Distance alone is a terrible matcher (median name-sim within 20 m ≈ 0.1: dense old towns pack different POIs together).
- **Overture ≠ OSM**, ~27–40 % overlap → merge is **enrichment-first, union-first**, dedup second; per-source license stamping required.
- **i18n is harder and earlier than the PRD assumed:** local-script names are sparse in sources and need transliteration/translation; BCP-47 subtags (`ja-Hira`/`ja-Latn`); **source scripts are untrustworthy** → FAIL-001.
- **Overpass is flaky** → the commons cache is a *reliability* mechanism, not just cost.

## Decisions

- Lock ε/τ + union-first merge (tech-design §6); **Spec 001 demo area = Rhodes** (richest, compact, non-Latin without RTL); Jaffa → M3 RTL validation; Takayama → unrehearsed-city eval.
- **Scope flag raised for Ben:** a name/address transliteration sliver moves from M3 into M1.

## Failures

- **FAIL-001** — source script contamination (Hebrew Jaffa address `Сгула 13` stored in Cyrillic). Guardrail stub filed (open until the eval harness exists).

## Cost / turns

One focused sub-session; ~3 spike runs (probe → full → robust re-run) + diagnostics. DuckDB/S3 scans of the Overture places theme dominated wall-clock (~1–3 min per area). No token accounting yet.

## Exhibit-tag candidates

- `exhibit/U3-derive-thresholds-from-data` — deriving ε/τ empirically from a spike rather than guessing (with the "distance-alone-fails" and "cross-script sim≈0" plots). (proposed)
- `exhibit/U4-overture-is-not-osm` — the surprise that Overture places is commercial-POI-sourced with per-record licenses, and what that does to joins + quarantine. (proposed)
- `exhibit/U0-cyrillic-hebrew-address` — FAIL-001 as a vivid "don't trust source scripts" teaching moment for the any-city genericity thesis. (proposed)
