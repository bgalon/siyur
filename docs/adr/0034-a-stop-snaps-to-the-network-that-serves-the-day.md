# 0034 — A stop snaps to the network that serves the day, not to its nearest edge

- Status: proposed
- Decision Maker(s): Ben
- drafted-by: claude-code (Opus 5) · approved-by: _pending_ · Date: 2026-08-14

## Context and Problem Statement

`compiler/routes.py::build_walk_graph` decides whether a compiled bundle's recovery network can serve the planned day. It nodes the linework, finds the connected components, snaps each stop to the network, and declares the graph connected iff every stop snapped **and** they all landed in one component. `connected: false` fails the compile (ADR/T036, plan.md risk 3), on the sound reasoning that shipping islands trades a build failure someone can fix for a traveller being told, offline, to walk through a building.

The snap was `STRtree.query_nearest` — **the single nearest edge**, chosen with no regard for which component it belongs to.

That is the wrong question, and it made the gate unpassable. With the style stage in place, a real compile reached stage four and stopped dead on `DisconnectedWalkGraphError` for **every real area tried**:

```
plan    → route  {"provider":"valhalla","legs":5,"excluded":[]}
compile → routes {"legs":5,"walk_graph_edges":6548,"connected":false}
compile → error  "stops landed on 2 networks that do not join up (components [0, 5])"
```

**Valhalla — a full Greece extract — walked between every consecutive pair of the same five stops.** Both cannot be right about one city, and Valhalla was right: the network was fine and the snap was wrong.

A stop's coordinate is a POI centroid — the middle of a building, a courtyard, a headland — so it is routinely tens of metres from any footway, which is why the tolerance is 100 m. Within that radius a European old town contains many edges, and some of them are *service alleys, courtyards and driveways that touch nothing*. Nearest-edge picks one of those whenever it happens to be a metre closer than the street. The stop lands on a one-edge component, `anchor_components` reads `[0, 5]`, and a day that walks perfectly well is declared unshippable.

Reproduced deterministically in Tier 1: a stop 0.5 m from an isolated 20 m courtyard path, with the block's street 5 m away, yields `connected: false` under nearest-edge.

**This is a correct gate that nothing can pass** — the same shape as the B1 finding, where `approve_plan` required `feasible IS TRUE` and no real day could satisfy it. A gate whose pass rate is zero is not protecting anything; it is only stopping work.

## Considered Options

- **A — Snap to a component that serves every stop.** Collect *all* components reachable within tolerance per stop, intersect across stops, and pick one. Connectivity becomes "is there a single component serving the whole day".
- **B — Snap to the nearest edge of the largest component.** Simple, and fixes the observed case. But "largest" is a proxy for "the one the day uses" and comes apart where a day legitimately sits on a smaller network — a pedestrianised island, a park path system — which is exactly the genericity assumption SC-009 exists to catch.
- **C — Prune to the largest component before snapping.** Same objection as B, and worse: it discards the evidence before the question is asked, so a genuine disconnection becomes indistinguishable from a bad snap.
- **D — Weaken the gate: ship a disconnected graph and let the client fall back to straight lines.** Rejected outright and for the original reason — the fallback is a straight line across whatever lies between, produced at the one moment the product exists for.
- **E — Raise the snap tolerance.** Treats a wrong question as a tuning problem. A larger radius makes it *more* likely a stop finds an irrelevant island.

## Decision Outcome

Chosen: **A — the snap resolves to a component that serves every stop.**

Each stop yields the set of components with an edge within tolerance, and their distances. The serving component is the one present in **every** stop's set, chosen by **least total snap distance** — so the day walks on the network its stops actually sit on rather than whichever component sorts first. Ties break on the larger component, then the lower index, so the choice is deterministic and a rerun of the same compile produces the same bundle, which per-artifact hashing requires.

**The gate is not weakened, and this is the crux.** Three failures remain failures, each with its own test:

- **No component serves all stops** → `connected: false`, and the message names the networks. A day genuinely split across two unconnected networks still refuses.
- **A stop has nothing within tolerance** → unreachable, reported with the true nearest distance so the message says how far off it was.
- **Islands are still islands** — the serving component is pruned to, and everything else is dropped and counted, exactly as before.

What changed is only *which question is asked*: not "what is nearest to each stop" but "is there one network that serves them all". The second is the question the traveller's experience actually depends on; the first was a proxy that happened to agree in synthetic fixtures and disagreed on every real city.

**The frame now carries the evidence, not only the verdict.** `component_count`, `anchor_components` and `dropped_edges` were already on `WalkGraph` and reached nobody: the stream carried `connected` alone, so a failed compile said *that* it failed and nothing about why, and diagnosing it meant reproducing the fetch by hand. That opacity is why this bug needed a live failure and a cross-session investigation to find, rather than being readable off the first failing run. Counts and integers only — no commons-derived text — so this stays inside ADR-0030 A1.

## Consequences

- Real areas compile. This was the last blocker between the pipeline and a real bundle.
- Connectivity is now judged against **the day**, which is the guarantee `require_connected`'s docstring already claimed and the implementation did not deliver.
- A day whose stops sit on a legitimately small network (a pedestrian island, a park) compiles, where option B would have failed it. Genericity (SC-009) is preserved rather than traded for a heuristic.
- The candidate query converts tolerance to degrees using the **longitude** scale at the stop's latitude, so the degree-space box over-covers in latitude and never under-covers; exact haversine filters it back. Doing it in raw degrees would shrink a documented 100 m tolerance by `cos(latitude)` — 25 % at 40° — the same trap `_nearest_edge` already documented.
- Slightly more work per stop: a radius query instead of a single nearest lookup, bounded by the edges within 100 m. Immaterial against fetching and noding the network.
- `contracts/bundles.md`'s `routes` frame gains three keys. Additive; no client reads them yet.

## Confirmation

Satisfied when a stop nearer to an isolated path than to the street that serves the day compiles to a connected graph with the island pruned (`tests/test_walk_graph.py`), while a day split across two genuinely unconnected networks and a stop beyond the snap tolerance both still fail the compile — and when the `routes` frame carries `component_count`, `anchor_components` and `dropped_edges` (`tests/test_compiler_pipeline.py`).
