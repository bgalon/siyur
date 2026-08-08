# 0020 — Routing engine: self-hosted Valhalla, per-area graph at compile, behind a `RoutingProvider` seam

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code (Opus 5) · approved-by: Ben · Date: 2026-08-07 · accepted: 2026-08-08

## Context and Problem Statement

Spec 002 FR-003 requires every consecutive pair of stops to carry **a real walking leg** — distance, duration and route geometry derived from a walking network — and forbids presenting a straight-line approximation as a route. FR-004 requires feasibility (walking budget, time budget) to be computed by **deterministic machinery, never asserted by the model**, which means an N×N walking time matrix, not a chat answer. `docs/data/route-leg.md` is the field-level authority for the leg record; what was never decided is **which engine, hosted how, and how CI avoids paying for a graph build on every PR**.

`methods-stack-reference.md` §5 ranks the candidates but stops short of a decision, and `delivery-plan.md` DU-05 cannot write `compiler/routes.py` without one. Three constraints shape the answer: routing runs **inside compile**, which may be slow but must not depend on a third party's daily quota; the seam discipline ADR-0004 established for `ModelRouter` — nothing above the seam reaches a vendor directly — applies identically here; and a multi-minute graph build is not a test tier, it is a tax, so Tier 1 must never see one.

## Considered Options

- **A — Self-hosted Valhalla (MIT, official `ghcr.io/valhalla/valhalla-scripted` image), graph built per area at compile time, reached over HTTP behind a `RoutingProvider` protocol in `commons/routing.py`**, with OpenRouteService as a zero-infra dev fallback and a recorded-fixture provider for Tier 1.
- **B — OpenRouteService as the primary engine.** Zero infrastructure, but a GPL-3.0 backend, a free-tier quota (≈2,500 req/day) and a **live external dependency inside the compile path** — fine as the dev fallback, wrong as the engine of record.
- **C — The public OSRM demo server.** Its own policy says not for production, and access is withdrawable at any time.
- **D — GraphHopper hosted.** Free tier ≈500 credits/day — too small.
- **E — Straight-line haversine distances.** FR-003 forbids presenting one as a route.

## Decision Outcome

Chosen: **A — Valhalla, self-hosted, per-area graph built at compile time, pedestrian costing, behind a `RoutingProvider` protocol**, because it is the only option that puts a production-grade pedestrian router **inside our own cost and trust boundary** (MIT, self-hosted, no quota) while serving both primitives feasibility needs — route *and* matrix — from one box. Stack reference §5 already ranks it first for exactly these reasons.

**Exactly two endpoints, both `POST`:**
- **`/route`** with `costing: "pedestrian"` → leg geometry + duration for `RouteLegV1`.
- **`/sources_to_targets`** (the matrix service) → the N×N feasibility time matrix, **one call per feasibility re-check**.

Valhalla also offers optimized ordering from the same box; **M1 does not use it.** The LLM proposes the order and deterministic machinery *validates* it (FR-004) — moving ordering into the engine is an M2 question, not a free win.

**Costing options are fixed and explicit:** `{"pedestrian": {"walking_speed": <pace>, "walkway_factor": 0.9}}`, where `walking_speed` comes from the user's pace preference (**3.5–5.0 km/h**). Valhalla accepts 0.5–25 and **defaults to 5.1 km/h**, which is brisk for a sightseeing day and moves feasibility verdicts more than engine choice does — so the default is never taken implicitly.

**The seam is the decision, not an accessory.** Three implementations sit behind the protocol — `ValhallaProvider` (Tier 2 + production), `OpenRouteServiceProvider` (zero-infra dev fallback, free key) and `FixtureProvider` (recorded responses, Tier 1, no network) — and **nothing above `commons/routing.py` knows which is live**. This is the same discipline as ADR-0004's `ModelRouter`: don't reach a vendor directly. Swapping engines, or replaying a recording, becomes a construction-time choice rather than a rewrite.

**Tile-build trigger:** compile-time, keyed `(area_id, pbf_date)`. The container builds tiles on first start from a mounted per-area PBF extract (~1–5 min); the built tile directory lives in a **named docker volume**, so recompiling the same area is a no-op. Compile blocks on Valhalla's **`/status` readiness, never on a sleep.**

**Licensing, both halves.** Valhalla itself is **MIT** — no obligation beyond the notice. But its graph is built from OSM, so **every leg geometry, distance and duration it emits is a Produced Work of ODbL data**: it carries **ODbL** and renders **"© OpenStreetMap contributors"** (`DATA-LICENSES.md`, Valhalla row; Constitution V). The ATTRIBUTION pipeline already knows how to discharge this. Routing output is not "ours" merely because our container computed it.

**Version discipline:** `ghcr.io/valhalla/valhalla-scripted:latest` is a **floating tag**; a digest is **resolved-then-pinned at implementation** (ADR-0007). No tag or digest is asserted here.

### Consequences

- Good: FR-003 is satisfiable with real network geometry at zero marginal cost and no third-party quota inside compile; FR-004's matrix is one call, cheap enough to re-run after every plan edit.
- Good: the seam keeps CI honest. **Job 1 (Tier 1) never touches Valhalla** — `FixtureProvider` replays committed JSON recorded once against a real container, sitting beside the existing Overture parquet fixture. The real container runs **only in job 3** behind the `integration` marker, against a **tiny committed old-town PBF** (single-digit MB → seconds, not minutes), with the tile directory cached across runs keyed on the PBF hash. **No PR ever builds a metro graph.**
- Good: ORS as the fallback means an early demo needs no docker at all; and if Valhalla ever has to go, it is one implementation of a protocol, not an architecture.
- Bad / accepted cost: **`docker-compose` gains a `valhalla` service**, and **the first run for a new area is slow and disk-hungry** — a per-area PBF plus a 1–5 minute tile build, cached thereafter on a named volume. Local dev acquires a second heavyweight container next to PostGIS.
- Accepted: pinning an image digest means periodic deliberate upgrades, tracked like every other pin (ADR-0007).

### Confirmation

- **`tests/test_routing_provider.py`** (Tier 1, `FixtureProvider`): protocol conformance across the implementations, and an assertion that **no socket is opened** — the fixture path is provably offline.
- **`tests/test_compiler_routes.py`** under `-m integration` (job 3) against the real container over the committed old-town PBF: leg distance and duration agree with the recorded fixture within tolerance, so the fixtures cannot silently drift from the engine.
- **Seam-purity tripwire** mirroring `tests/test_llm_seam.py`: **no HTTP call to a routing host may appear outside `commons/routing.py`**. A direct vendor call fails CI rather than review.
- **Attribution assertion** (`tests/test_compiler_attribution.py`, DU-05): a bundle containing any `RouteLegV1` must carry the ODbL + "© OpenStreetMap contributors" credit — routing output as a Produced Work is enforced, not remembered.
- **TODO (lands with DU-05):** `commons/routing.py`, the two tests above, the recorded fixtures, the committed old-town PBF, the `valhalla` docker-compose service, and the pinned image digest.
