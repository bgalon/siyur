# 0002 — Online-first delivery on an offline-ready data contract

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: Ben · Date: 2026-07-25

## Context and Problem Statement

The PRD's signature guarantee is "travel guided with **zero connectivity**" (airplane-mode), and the tech design builds the whole data spine around it (`SourcedValue.bundleable` §1.0, `BundleManifestV1` §1.4, the airplane-mode e2e §5.5). But the offline **runtime** — OPFS storage, the PWA service worker, whole-archive download, `navigator.storage.persist()`, and iOS/WebKit eviction handling — is the largest and most fragile surface in M1, and its worst failure modes are Safari/WebKit-specific (7-day eviction, patchy `createWritable`). Building it inside the M1 vertical slice risks sinking the slice into browser-storage edge cases before the core research→plan→compile loop is even proven.

The question raised this session: can M1 ship **fully online** and add offline later without a full refactor? The refactor risk is real and specific — if the client reads data live (calls the API / queries the commons directly), then adding offline later flips the client's entire read path from "call the server" to "read a local file," which touches every read site. Deferring offline naively **is** a rewrite.

Related but **separately decided** (future ADRs, flagged not resolved here): whether iPhone/WebKit is in scope for the offline runtime, and whether LangGraph is the right planner framework (see the lock in `tech-design.md` §6).

## Considered Options

- **A — Fully online, offline bolted on at the end (naive).** Client reads live from the API/commons in M1; add OPFS/PWA later. Fastest to first demo, but adding offline later rewrites the client read path — the refactor we're trying to avoid.
- **B — Chrome/Chromium-first offline from the start (Android + desktop).** Build the offline runtime in M1 but only for the Chromium engine, dodging the WebKit pain. Preserves the zero-connectivity promise early, but loads M1 with the storage/PWA surface up front and defers no risk.
- **C — Online-first delivery on an offline-ready *data contract*.** Defer the offline **runtime**; keep the offline **data contract** from day one. The client always reads from a compiled, hashed **bundle** (`BundleManifestV1`) behind a read-abstraction seam — online, that bundle is served over HTTP from GCS; offline (added later), the same bundle is read from OPFS. `SourcedValue`/`bundleable`, the three versioned schemas, the in-process compile step (§5.3), and the license-quarantine merge-blocking test all land in M1. Only the storage layer (OPFS, PWA, persistence, eviction handling) is deferred — and when it lands, it goes Chromium-first per Option B's rationale.

## Decision Outcome

Chosen: **C — online-first on an offline-ready data contract**, because it is the only option that buys M1 velocity *without* poisoning the architecture. The load-bearing commitment is **the bundle is the client's read model from day one** (promoting `tech-design.md` §1.4 to a standing rule): adding offline later becomes a *transport swap* (HTTP→OPFS behind one interface), not a read-path rewrite. The genuinely expensive-to-retrofit pieces — per-field provenance (`SourcedValue.bundleable`) and the quarantine that keeps `bundleable=false` out of the commons — are built now, exactly when they're cheap; the genuinely deferrable pieces (browser storage) are pushed to where their WebKit risk can be handled deliberately (Chromium-first).

## Consequences

- Good: M1 proves research→plan→compile→render without sinking into OPFS/PWA/eviction edge cases; the offline runtime becomes purely additive work behind the read seam.
- Good: `SourcedValue`, the versioned schemas, the compile step, and the license-quarantine test are all in M1 — nothing that is expensive to retrofit is deferred; the commons never accumulates unstamped data.
- Good: when offline lands it targets Chromium (Android + desktop) where OPFS is robust and there is no 7-day eviction — the clean offline story, with iPhone/WebKit handled as its own scoped decision.
- Bad / accepted cost: M1 does **not** demonstrate the zero-connectivity guarantee end-to-end — it demonstrates an offline-*ready* contract served online. This re-scopes the §5.5 release gate for M1 (it becomes a "client reads only from the bundle" tripwire, not a network-off render) and must be reconciled with the PRD's airplane-mode headline — flagged for Ben as PRD-adjacent.
- Accepted cost: the client read-abstraction seam and the MapLibre/PMTiles protocol adapter carry one implementation (HTTP) in M1 for the sake of a second (OPFS) later — a small up-front tax to avoid the refactor.

### Confirmation

An early, scoped version of the §5.5 e2e (added in M1, CI-required): assert the client reads itinerary/sites/map **only** from bundle endpoints — never a direct commons/API read. It passes online today (bundle over HTTP) and **becomes** the real airplane-mode gate unchanged when the transport swaps to OPFS; a failure means a live read path was introduced (the drift that would force a refactor). Backed by the existing `test_no_unbundleable_in_bundle` structural test (quarantine) and the presence of `SourcedValue.bundleable` stamps on all M1-populated fields. Offline-runtime confirmation (network-off render, `navigator.storage.persist()`, Chromium matrix) is tracked to the offline-runtime milestone. TODO: add the scoped e2e test path once `web/` and `tests/` exist at ramp-up.
