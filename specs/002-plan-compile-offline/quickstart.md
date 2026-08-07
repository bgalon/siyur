# Quickstart — the Spec 002 acceptance walkthrough

**Feature**: `specs/002-plan-compile-offline` · **Written**: 2026-08-07 (plan phase)

> **Status: written against the contracts, not yet against shipped code.** Slice 001's quickstart carries a "re-verified against the shipped code" date because T068 re-ran it after implementation. This one cannot claim that yet — every command below is derived from [`contracts/`](./contracts/) and [`data-model.md`](./data-model.md), and the slice's close-out task re-verifies it against what actually ships and corrects it in place. Treat unverified commands as intent, not evidence.

This walkthrough is the human-runnable form of the four user stories. The automated gates that *prove* them are mapped at the bottom.

## Prerequisites, in one line each

- **Postgres + PostGIS** — `docker compose up -d postgis`, then `export SIYUR_DATABASE_URL="postgresql+psycopg://siyur:siyur@localhost:5432/siyur"`.
- **Valhalla** — `docker compose up -d valhalla` (added by this slice). First start builds the per-area graph from a PBF extract; expect 1–5 minutes before `/status` answers. Without it, `SIYUR_ROUTING_PROVIDER=fixture` runs the recorded fixture instead (Tier 1 default).
- **Object storage** — `docker compose up -d gcs` (`fake-gcs-server`, added by this slice) mirrors GCS for bundle artifacts.
- **Migrations** — `uv run alembic upgrade head` (creates `user_plan`). *Migrations are `ask`-gated; Ben approves.*
- **A researched area** — this slice starts where 001 finished. Run the 001 quickstart first, or `POST /areas` + `POST /areas/{id}/research`, and keep the `area_id`.
- **Auth** — a bearer token from the Firebase Auth emulator, as in the 001 quickstart. Every endpoint below is `401` without it.
- **Web** — `pnpm -C web install && pnpm -C web dev`. In a git worktree `node_modules` is not shared (the ADR-0005 `worktree.symlinkDirectories` config is still unset), so install per worktree.

## US1 — Propose a day, then approve it

```bash
# 1 · propose (SSE: phase status frames, the itinerary, a feasibility verdict, done)
curl -N -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"area_id":"'$AREA'","budgets":{"hours":4.0,"walking_m":4000},
       "start_time":"10:00","interests":"art and coffee"}' \
  http://localhost:8000/plans
# → event: status  {"phase":"propose_itinerary",…}
# → event: itinerary {"id":"…","stops":[…],"legs":[…],"timeline":{…}}
# → event: feasibility {"ok":true,"violations":[]}
# → event: done {"plan_id":"…"}

# 2 · read it back with its approval state
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/plans/$PLAN
# → {"state":"proposed","feasibility":{"ok":true},"itinerary":{…}}

# 3 · approve — the HITL gate
curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8000/plans/$PLAN/approve
# → {"state":"approved","approved_at":"2026-08-07T…Z"}
```

**What to check by eye**, because a green exit code proves none of it:

- Every stop names a place that **exists in the commons** — cross-check a `site_id` against `GET /sites`. The planner selects; it never invents.
- Every leg has a real `geometry` with **more than two vertices**. A two-point line is a straight line pretending to be a route (validation rule 4).
- Total `distance_m` ≤ `budgets.walking_m` and the timeline fits `hours`. **The model did not compute these** — Valhalla and the opening-hours evaluator did.
- Every displayed value carries its inherited source + license stamp.

**The gate itself:** ask for something impossible (`"hours":1.0` over a spread-out area) and confirm `feasibility.ok=false` with named violations, then confirm `POST …/approve` returns **`409` and the plan is still `proposed`**. There is no override.

## US2 — Compile the approved day

```bash
curl -N -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"plan_id":"'$PLAN'"}' http://localhost:8000/bundles
# → event: status {"stage":"tiles"} … {"stage":"routes"} … {"stage":"quarantine"}
#   … {"stage":"content"} … {"stage":"attribution"} … {"stage":"hash"}
# → event: done {"bundle_id":"…"}

curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/bundles/$BUNDLE/manifest
# → BundleManifestV1 incl. size_bytes, per-artifact sha256, integrity.manifest_sha256
```

**What to check by eye:**

- `size_bytes` is present **before** you download anything (FR-014). For the compact demo day expect single-digit MB, far under the 200 MB budget.
- Every path in `tiles`/`routing`/`content`/`attribution` **exists in the archive** (validation rule 7).
- `ATTRIBUTION.md` names "© OpenStreetMap contributors" *and* an individual credit per bundled story.
- **Grep the frozen content for a value you know is `bundleable=false`** and find nothing. This is the quarantine invariant and the single most important check on this page.

**The gate itself:** try to compile a plan that is still `proposed` and confirm **`409`**. That is the HITL gate enforced mechanically rather than by convention.

## US3 — Travel it with the network off

This is the milestone release gate and it is a browser test, not a curl:

```bash
pnpm -C web test:e2e          # Playwright/Chromium, added by this slice
```

The flow it drives (tech-design §5.5 step 7): load online → wait for the service worker **and** the OPFS whole-archive download to finish → `context.setOffline(true)` → **reload** → assert map tiles render from OPFS, itinerary/timeline/narration resolve from the bundle, off-route recovery returns a route, and a request listener saw **zero network calls**.

Two details that are easy to get wrong and are called out in `test-strategy.md`: offline is set **after** first load (WebKit/Firefox error otherwise), and the assertion is on the **rendered canvas/tiles**, not `navigator.onLine` — which lies.

**By hand**, if you want to see it rather than trust it: load the app with a compiled bundle, then kill the dev server entirely (not DevTools throttling — kill it) and reload. Everything the traveller depends on must still be there.

## US4 — Places tell their story offline

With the network still off, open a place that had an available openly-licensed article and confirm a readable account plus **its own article credit**. Then open a place that had none, and confirm it shows its cited facts with **no story and nothing invented** (FR-023).

## Automated gate mapping

| Story | Proven by |
|---|---|
| US1 propose + feasibility | `tests/test_feasibility.py`, `tests/test_planner_propose.py` (mocked model), `evals/test_trajectory.py` (superset incl. `propose_itinerary`) |
| US1 HITL gate | `tests/test_hitl_gate.py` — pause survives restart; approve-twice is idempotent; infeasible ⇒ `409` |
| US2 quarantine + integrity | `evals/test_structural.py::test_no_unbundleable_in_bundle`, `tests/test_compiler_manifest.py` |
| US2 attribution | `tests/test_compiler_attribution.py` — ODbL present, per-article CC BY-SA credit for every story |
| US3 **airplane mode** | `web/test/e2e/airplane.spec.ts` — **CI job 5, the release gate** |
| US3 recovery | `tests/test_walk_graph.py` (noded/connected tripwire) + the e2e recovery assertion |
| US4 narration | `tests/test_sources_wikivoyage.py`, structural eval extended over bundled stories |
| SC-009 genericity | `evals/test_genericity.py` — second area, no place-specific code |

## Known gaps this walkthrough does not paper over

- **Until CI job 5 actually runs Playwright, "gates green" still does not mean airplane mode was verified.** Slice 001's quickstart (§"Job 5 is a green stub") recorded this honestly at T069; it remains true until DU-06 lands, and this walkthrough inherits that caveat rather than quietly dropping it.
- **Valhalla's first run is slow and disk-hungry.** The fixture provider exists so Tier 1 and most local work never pay for it; that also means the fixture path is the one most exercised, and the real container is the one most likely to rot. The Tier 2 integration run is what keeps it honest.
- **Off-route recovery is approximate by design** — a pruned network, no turn restrictions, naive costing (stack reference §5). It is "get me back to the plan", not turn-by-turn navigation.
- **Genericity is evidenced against a second committed fixture area**, so it is rehearsed. The unrehearsed-area bar remains a milestone gate, exactly as slice 001 recorded.
- **`size_bytes` for the demo day will be small enough that the 200 MB budget is untested.** A metro-scale day is the case that would actually exercise it, and this slice does not run one.
