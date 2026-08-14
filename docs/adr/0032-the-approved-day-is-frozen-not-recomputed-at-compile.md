# 0032 — The approved day is frozen at compile, never recomputed

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code (Opus 5) · approved-by: Ben · Date: 2026-08-14 · accepted: 2026-08-14

## Context and Problem Statement

`compiler/routes.py::compile_routes` (T036) does two things: it routes a sequence of waypoints through the routing provider, and it builds the pruned walking network offline recovery runs over. T042 wires the compile pipeline, and the obvious reading of the task list is that the pipeline calls `compile_routes` — it is stage four, it exists, and it produces both artifacts the manifest names.

Doing so would **re-route a day that a human has already approved**, and that turns out to be wrong in a way that is invisible until someone is offline.

The approval gate is a compare-and-set over `itinerary_hash` (ADR-0023). The hash covers the `ItineraryV1`, and `ItineraryV1` **contains its legs** — their geometry, distances and durations. Those are the numbers `planner/feasibility.py` checked against `budgets.walking_m` and `budgets.hours`, and they are the numbers rendered in the plan review surface next to an approve affordance that is unavailable while infeasible. What the human approved is the day *including* those values.

Valhalla is not a pure function of its inputs across time: the graph is rebuilt from OSM, and a leg routed at compile can differ from the same leg routed at planning. So a re-routing pipeline produces a bundle in which:

- `content/itinerary.json` carries the **approved** distances and durations (it is frozen from the row, per ADR-0025 ruling 1), and
- `routing/legs.json` carries **freshly routed** ones.

Two artifacts describing one day, disagreeing, both hashed, both verifying, in a bundle whose reader has no connectivity and no way to establish which is authoritative. `web/src/travel/index.ts` treats `routing/legs.json` as "the standalone freeze of the same objects" — i.e. the client already assumes these two agree. A day could also become infeasible *after* approval by this route, with the traveller holding an approved bundle that breaches the budget they set.

There is a second, more mundane symptom that makes the same point: routing refuses fewer than two waypoints, so a **one-stop day cannot be compiled at all** if compilation must route. A single-museum afternoon is a legitimate plan and it is approvable; it should not become uncompilable because of where the routing call sits.

## Considered Options

- **A — Freeze the approved legs; build only the walk graph at compile.** The pipeline takes `itinerary.legs` through `quarantine_legs` and writes them; the pruned network is built fresh with `build_walk_graph` + `require_connected`.
- **B — Re-route at compile.** Simple, matches the shape of the task list, and gets the freshest geometry. Rejected: it discards the approved numbers, can silently invalidate a feasibility verdict a human relied on, and makes a one-stop day uncompilable.
- **C — Re-route, then compare against the approved legs and fail the compile on divergence.** Honest, and it never ships two disagreeing artifacts. But it makes compilation fail for a reason the user cannot act on ("the road network changed"), turning an approved plan into a dead end that only a fresh proposal clears — and it still needs a divergence threshold, which is a tuning knob with no principled value.
- **D — Re-route and re-run feasibility, superseding the plan when it changes.** The most "correct" and by far the most complex: compile becomes a write path over the HITL state machine, and the human approves once and gets a different day. Explicitly out of scope; if freshness ever becomes a requirement this is where to start.

## Decision Outcome

Chosen: **A — the approved day is frozen; only the walk graph is computed at compile.**

The distinction that carries it: **the legs are part of what was approved; the walk graph is not.** The legs were rendered to a human, checked by the feasibility gate, and sealed into the hash the approval CAS is taken over. The pruned network is a *recovery aid* — it exists so a traveller who wanders off the route can get back onto it, it was never shown to anyone, and nothing about it was approved. Recomputing it at compile is therefore not a change to the day; recomputing the legs is.

Compile is a **publishing** step, not a planning step. Its job is to make the approved day available offline, byte for byte. A pipeline that recomputes any part of what was approved has quietly made compile a second planning pass with no gate in front of it — and the HITL gate exists precisely so that no unreviewed plan reaches a traveller (FR-006 / SC-003).

**Stated precisely, because the loose version is false:**

> **Compile writes plan *status*; it never writes plan *content*.**

Compile demonstrably does write the `user_plan` row — `claim_plan_for_compile` moves `approved → compiling`, `finish_compile` moves `compiling → compiled | failed`, and the `failed → approved` re-arm and the stale-`compiling` reaper are two more status writes. Saying "compile has no write path over the plan state machine" would be contradicted by the very next module to land. The status/content line is the one that actually holds, and it is the more useful rule: expiring a stale claim is a status write and therefore permitted, while recomputing a leg is a content write and therefore not. Anything that would change what the human approved is out of bounds; anything that only changes where the row sits in its lifecycle is in bounds.

*(Sharpened after review — the drafted version said compile "has no write path over the plan state machine", which the re-arm and reaper falsify.)*

**A second, independent reason, which a reviewer can check in ten seconds:** routing refuses fewer than two waypoints, so under the re-routing design a **one-stop day cannot be compiled at all**. A single-museum afternoon is a legitimate plan, it passes feasibility, and it is approvable — so it must be publishable. A design that makes an approvable day uncompilable is wrong on its own terms, whatever one concludes about freshness. This reason stands even for a reader who rejects the whole argument above.

Concretely, `run_compile` therefore takes **no** `RoutingProvider` and no `PedestrianCosting`. That absence is the enforcement: a pipeline with no routing provider cannot re-route by accident, and a future change that wants to must add the parameter and confront this ADR to do it.

## Consequences

- Bundle legs are exactly the approved legs. `content/itinerary.json` and `routing/legs.json` cannot disagree, which is what the travel client already assumes.
- A one-stop day compiles.
- `compiler/routes.py::compile_routes` is currently unused by the pipeline; its routing half is still exercised by T037 and remains the right entry point for any caller that genuinely wants to route. It is deliberately **not** deleted — it is the function a future ADR-D implementation would build on.
- **Bundle legs can go stale relative to the world.** A plan approved and compiled weeks apart carries the older routing. This is the accepted cost, and it is the correct one: a stale leg is a day the human approved, while a fresh one is a day nobody did. If this becomes a problem the answer is to expire approvals, not to silently re-route.
- Quarantine still runs over the frozen legs (`quarantine_legs`), so freezing does not smuggle an unbundleable value past the filter.
- The walk graph is still asserted connected at compile, and `connected: false` still fails the compile.

## Confirmation

Satisfied when `run_compile` has no routing-provider parameter, the bundle's `routing/legs.json` deserialises to the same leg objects as the frozen `content/itinerary.json`, and a one-stop itinerary compiles to a valid bundle.
