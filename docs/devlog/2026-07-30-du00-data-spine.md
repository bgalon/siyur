# 2026-07-30 — DU-00 unit c: the data-spine schema cards

**Goal:** Write the five `docs/data/` schema cards (POI/site, itinerary, route-leg, bundle-manifest, tile-source) and the root `DATA-LICENSES.md` registry — the field-level ground truth for the data spine and the machinery behind Constitution Article V. Docs only; no product code, CI, or Spec 001 (later DU-00 units).

## What happened
Read the sources in order: AGENTS.md → tech-design §1 (the data spine: `SourcedValue`/`SourceRef` primitive, `SiteRecordV1`, `ItineraryV1`, `BundleManifestV1`, merge model with ε=25 m/τ=0.6) → delivery-plan DU-00 → constitution Article V → methods §6 (schema-card shape: fields, types, units, CRS, timezone, provenance, license pointer, 3 example rows) → stack-reference §4–5 + Appendix B (the license obligations register).

Four of the five schemas were fully specified in tech-design §1 — transcription plus the methods §6 card frame (units, timezone rules, worked example rows). The one genuine authoring gap was **route-leg**: tech-design only references `RouteLeg` inside `ItineraryV1.legs` and `BundleManifestV1.routing` without spelling out its fields. Modelled `RouteLegV1` conservatively from context (Valhalla pedestrian legs → LineString EPSG:4326 geometry, `distance_m`/`duration_s`, ODbL-derived provenance) and flagged that the routing-engine choice is an ADR at DU-05 — did not invent a decision.

`DATA-LICENSES.md` is a near-mechanical fold of stack-reference Appendix B into the methods §6 registry columns (`source | license | attribution | share-alike | bundleable | date checked`), ODbL as row one, plus the bundleable quarantine allow-set from tech-design §1.0 and the always-`false` sources (open_web, review_provider). No AGENTS.md edit needed — it already points at `docs/data/*` cards.

Clean run: no dead ends, no failures, no re-litigated product decisions. One branch `agent/du00-data-spine` → PR #10 to main (ADR-0005).

## Decisions
- None. The cards transcribe the approved tech-design §1 and methods §6; no library/schema/architecture choice was made. Referenced existing ADR-0002 (online-first bundle read model), ADR-0004 (planner seam), ADR-0005 (PR workflow). `RouteLegV1`'s routing-engine ADR remains owed at DU-05, as tech-design already schedules.

## Failures
- None this session.

## Cost / turns
~1 focused turn of reading (5 source docs, parallelised) + 6 file writes + commit/PR. Small: ~35 KB of docs authored. No tool failures.

## Exhibit-tag candidates
- `exhibit/U2-schema-cards` — schema cards as the anti-"guess the schema" discipline: authoritative per-dataset ground truth referenced from AGENTS.md, with worked example rows. Teachable for the agent-repo-conventions unit.
- `exhibit/U2-data-licenses` — `DATA-LICENSES.md` + the `bundleable` quarantine allow-set as *provenance-as-machinery* (Constitution Article V): license compliance turned into a checked, stamped invariant rather than launch-week vigilance. A strong U2/U3 teaching artifact.

*(Proposed for Ben to approve.)*
