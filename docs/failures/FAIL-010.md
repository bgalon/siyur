# FAIL-010 — `Up (healthy)` measured that the router runs, not that it holds the map we need

- Date: 2026-08-14 · Severity: medium (no defect shipped; a wrong blocker reported to the
  operator, and a mystery `400` waiting for whoever first routed a real leg)
- Root-cause class: environment + observability (every health signal in the stack was green
  and every one of them was measuring the wrong proposition)

## Symptom

`siyur-valhalla-1` reported `Up (healthy)` with six days of uptime. `GET :8002/status` returned
`200` with a populated `tileset_last_modified`. Every signal available said the routing engine
was fine.

Routing a pedestrian leg between two points in the area our fixtures use:

```
POST :8002/route  (36.4425, 28.2205) -> (36.4470, 28.2280)
  {"error_code":171,"error":"No suitable edges near location","status_code":400}
```

I reported this to the operator as **"Valhalla has an empty graph"**. That was wrong. A second
pair of coordinates settled it:

```
POST :8002/route  (42.5063, 1.5218) -> (42.5100, 1.5350)
  {"trip":{"legs":[{"maneuvers":[{"type":1,"instruction":"Wal…      ← routes fine
```

Those are in **Andorra**. The container was serving the upstream image's default demo extract.
The build had run; it had run against the wrong region.

## Why every signal was green

Each check was truthful about the thing it measures, and none of them measures coverage:

| Signal | What it actually asserts |
|---|---|
| `docker compose ps` → `healthy` | the process is up and its healthcheck command exits 0 |
| `GET /status` → `200` | the HTTP service is answering |
| `tileset_last_modified` populated | *a* tileset was loaded — not *which* |

"The routing service is running" and "the routing service can route where we are going" are
different propositions, and **the whole stack reports the first while every caller assumes the
second**. A service with the wrong map is not degraded; it is perfectly healthy and useless.

## Why no test caught it

No test in the suite routes over live Valhalla at all — confirmed by the DU-05 session on its
own branch. `tests/test_routing.py` exercises `select_provider` against literal env dicts (pure,
no I/O), every other routing test replays committed captures through `FixtureProvider`, and the
only live-service integration tests hit fake-gcs. So a Tier 2 run of 68 passes was honest as a
count and said nothing about routing — not because it would have failed, but because it never
reached the service.

Same shape as FAIL-009's neighbouring lesson and as `test_a_proposed_plan_is_unclaimable`: green,
and green for a reason other than the one a reader would assume.

## Why the imprecision mattered

"Empty graph" and "Andorra graph" imply different repairs:

- *empty* → build a graph
- *wrong region* → **the build already ran**; find what fed it the wrong extract, or it comes
  back on the next `docker compose up`

The first fix looks successful and does not survive a restart. The distinction cost two `curl`s
and was only found by routing a second coordinate pair *outside* our own area — which is not an
obvious thing to try, because it means testing somewhere you do not care about.

## Blast radius, as of this entry

**The planner, not the compiler.** `planner/nodes/propose_itinerary.py` routes every consecutive
pair of stops, so a real multi-stop day cannot be planned on this machine. The compile path is
structurally immune: ADR-0032 froze the approved day's legs and removed the routing call from
compile entirely, so `compiler/pipeline.py` names no `RoutingProvider` — a decision taken for
unrelated reasons that happens to close this blast radius.

## Guardrail

`tests/test_routing_coverage.py::test_the_live_router_covers_the_fixture_area` — when
`SIYUR_ROUTING_PROVIDER=valhalla` names a live endpoint, route a short leg **inside the
fixtures' own bbox** and fail with a message naming *coverage* rather than surfacing a bare
`171`. It asserts the proposition that matters instead of the one that is easy to measure.

Deliberately **opt-in**: it skips when no live endpoint is configured, so it cannot redden CI,
which runs against fixtures by design (ADR-0020 — CI must not pay for a graph build per PR). A
guardrail that forces every contributor to run a regional extract would be traded away within a
week; one that fires for whoever actually points at a live router is the one that survives.

It derives the probe coordinates **from the fixture bbox**, never from a literal — a hardcoded
pair would put a place literal in the tree (`evals/test_genericity.py`) and would silently stop
testing the right region the moment the fixtures moved.

## Related

- FAIL-009 — the other "a truthful command answering a different question" entry. Both reduce to:
  *a signal that is easy to read is not the same as the signal you need*.
- ADR-0020 — the `RoutingProvider` seam. It is why this was an inconvenience rather than an
  outage: `FixtureProvider` keeps the whole suite deterministic and offline, which is also
  precisely why nothing noticed the live service was wrong.
