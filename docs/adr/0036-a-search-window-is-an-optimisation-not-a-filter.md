# 0036 — A search window is an optimisation, so it may never turn a hit into a miss

- Status: proposed
- Decision Maker(s): Ben
- drafted-by: claude-code (Opus 5) · approved-by: — · Date: 2026-08-15

## Context and Problem Statement

Resolving an area by name against the Overture **divisions** theme is the slowest interactive
path in the product. Measured end to end against the hosted release, same machine and link
(`planner/nodes/resolve_area.py`, the block comment above `_DIVISIONS_QUERY`):

```
212 s  before — one statement, name predicate + ST_AsWKB(geometry)
 73 s  after, no window — two passes, narrow projection, 32 read threads
 18 s  after, with a caller-supplied window — and one precise candidate, not 20
```

The UX audit (`docs/design/ux-audit-2026-08-15.md`) turned this from a performance note into a
blocking usability problem. At 375 and 390 px the `Use this view` control — the 0.18 s delimit
path — is painted under the plan panel and unreachable, so **the search pill is the only way
into the product**, and it takes **61.6 s** to answer. DU-06a's gate now holds that finding as a
standing assertion, and DU-06b's T-10 will make `Use this view` reachable again — but the name
search remains the only route for any area not already on screen, and 61.6 s is not a route.

`window` is the one lever that makes the lookup interactive, because `bbox` is the only column
the theme's row groups index (median span ≈ 4.8° lon × 3.0° lat). **But it is applied as a `WHERE`
clause**, not as a ranking input:

```sql
WHERE bbox.xmin <= $max_lon AND bbox.xmax >= $min_lon
  AND bbox.ymin <= $max_lat AND bbox.ymax >= $min_lat
  AND (<name match>)
```

So sending the map viewport unconditionally means **searching "Paris" while looking at Rhodes
returns nothing** — and "nothing" is indistinguishable from "no such area". That is the worst
answer this endpoint can produce, and the module is explicit that it must not: a slow or
unreachable source raises rather than returning a false negative (`AreaLookupTimeout` — *"This
says nothing about whether the area exists"*). A silent windowed miss would reintroduce, in the
client, exactly the failure the resolver refuses to make in the server.

`window` is currently **not plumbed through `api/areas.py` at all**, so this decision is about
what to build, not what to change.

## Considered Options

- **(a) Always window to the viewport** — 18 s. Silently cannot find anywhere off-screen.
- **(b) Windowed first, fall back to unwindowed when the windowed pass returns empty** — 18 s
  typical, ~91 s worst case (18 + 73), never a false negative. Client-side only.
- **(c) An explicit control — *this view* / *everywhere*** — 18 s or 73 s, the user's choice.
  Honest and predictable, but adds a control to a screen that has no room at 390 px.
- **(d) Make `window` a ranking hint server-side rather than a filter** — 18 s flat with no
  false negatives. Best end state; a `planner/` change and its own ADR.

## Decision Outcome

Chosen: **(b), windowed first with an unwindowed fallback on empty**, because it is the only
option that buys the 4× latency win **without ever converting a hit into a miss**. The *retry
rule* lives in the client — the server plumbs the parameter through and never retries — for
one reason: only the client can render "widening the search…", and a silent server-side retry
would hide the extra ~73 s rather than explain it.

**A windowed empty must not reach the Nominatim fallback.** This is the subtlest part of the
decision and it was nearly shipped wrong. `resolve_area` consults the geocoder when divisions
returns nothing, on the premise that *nothing* means *divisions has nothing*. Under a window
that premise is false, and the consequence is not a slow path but a **wrong answer**: viewport
over Rhodes, user types "Paris", the windowed divisions pass finds nothing, and an unwindowed
Nominatim answers with the OSM relation at full confidence. The caller gets a confident `200`
carrying a geocoder ring instead of the Overture division that exists — and never sees the
empty result this record requires it to re-ask on, so the widening pass never happens and
nothing on screen ever says so. **The bug hides because it succeeds.** So a window suppresses
the geocoder; on the unwindowed re-ask, an empty divisions result really is silence, and the
fallback behaves exactly as it always did.

Option (a) is explicitly rejected, not merely unchosen: it trades a correctness property for
latency, and the failure it introduces is invisible to the person it fails.

Option (d) is recorded as the **right long-term shape** and is what should happen when the
cached divisions extract lands (the same block comment argues that an extract keeping
`id`/`names`/`sources`/`bbox` turns every later lookup into a local scan, at which point the
window stops being load-bearing at all).

### Consequences

- **Good:** the common case — searching for somewhere you are looking at — drops from 61.6 s to
  ~18 s, and returns one precise candidate rather than 20 to disambiguate between.
- **Good:** searching for somewhere off-screen still works. It is slower than today by the cost
  of the first pass, and it answers.
- **Bad / accepted cost:** the **worst case gets worse** — ~91 s versus 73 s today — and it lands
  on exactly the query a user is least sure about. This is only acceptable because the fallback
  is **visible**: the client must render a distinct "widening the search…" state when the first
  pass comes back empty, so the extra wait is explained rather than merely endured. A silent
  fallback would be a worse defect than the one being fixed (FAIL-013: *the app knew something
  and did not say it*).
- **Bad / accepted cost:** two round trips on the miss path, and the client now owns a retry
  rule that properly belongs in the resolver. Option (d) reclaims it.
- **Bad / accepted cost:** the client rule cannot key on an empty result alone. A viewport
  that **crosses the antimeridian** cannot be expressed as one `[minLon, minLat, maxLon,
  maxLat]` box — MapLibre reports either a decreasing pair (`west=178, east=-178`) or an
  unwrapped `east=181`, and both are refused with a `422`. So the client must widen on **a
  `422` naming `window` as well as on an empty result**, or a user searching from Fiji or the
  Aleutians sees a flat rejection of a perfectly good name. Splitting the box server-side is
  the better long-term answer and is deliberately not done here: inventing a wrap silently
  would make `window` mean something this record does not say.
- The window remains **caller-supplied and never a constant** — FR-001/SC-005 mean the resolver
  knows no coordinates of its own, and this decision does not change that.

### Confirmation

**Landed with this ADR (server half, Tier 1, no network and no database):**

0. `tests/test_resolve_area.py`, each **mutation-proved** rather than merely written:
   - `test_a_windowed_miss_never_consults_the_geocoder` — the load-bearing one. Deleting the
     `and window is None` guard turns it red. Paired with
     `test_an_unwindowed_miss_still_consults_the_geocoder` so the guard cannot be "fixed" by
     disabling the fallback outright.
   - `test_this_node_never_retries_an_empty_windowed_lookup` — **fails if the resolver ever
     grows a helpful second pass**, which would kill the visible-state requirement silently.
     Mirrored at the endpoint by
     `test_a_windowed_miss_is_a_plain_404_that_the_client_must_not_trust`.
   - `test_an_absent_window_is_still_passed_explicitly_as_none` — asserted on captured
     `**kwargs`, because `search(name)` and `search(name, window=None)` record the same value
     and are very different calls. Dropping the keyword turns it red.
   - malformed windows refused; the antimeridian cases refused with the `422` the consequence
     above depends on.
   
   Two limitations are asserted rather than left to be found: a lat/lon transposition that
   stays in range is a legitimate box elsewhere on Earth and **no validator can reject it**,
   and an explicit `bbox`/`polygon` returns before the window is validated at all, so a bad
   window is dropped on that path. Both are second, independent reasons the unwindowed
   fallback is mandatory rather than an optimisation of an optimisation.

   There is deliberately **no instance-level window** on `OvertureDivisions` any more: a
   construction-time default could not be turned off by a caller passing `window=None`, so the
   mandatory re-ask would have stayed windowed against a pre-configured lookup — the user
   would watch "widening the search…", wait the extra ~73 s, and still get a false "no such
   area". One way to say it, and `None` means none.

**Still to land (client half — the part that makes this a user-visible win):**

1. **`web/test/areas.test.ts`** — given a windowed `POST /areas` that resolves to no candidates,
   the client must issue a second, unwindowed request and surface its result. Assert on the
   second request being made and on the final candidate list, not on a status code. The
   companion negative case: a windowed pass that *does* resolve must issue exactly one request.
2. **The "widening the search…" state is asserted, not assumed** — via FAIL-013's guardrail (2)
   on DU-06a's viewport harness (`web/test/e2e/viewport.spec.ts`): after activating a control
   that issues a request, the DOM must reach a state that is not the pre-request state within a
   bounded time. The fallback path is the longest-running case in the product and therefore the
   one that assertion exists for.
3. **No unconditional window reaches the resolver** — a test asserting that the client's
   first-pass-empty path re-issues without `window`, so option (a) cannot be reintroduced by
   deleting a branch.

TODO on implementation: (1) and (3) land with **T-15**; (2) lands with **T-16**, which is what
makes the pending state exist at all. The entry does not become `accepted` until Ben confirms
the shape.

**Until the client half lands, nothing sends a `window`, so no behaviour changes.** The server
accepts the parameter and nobody passes it — deliberately, because a client that sent one
*without* the fallback would be shipping option (a), the one this record rejects.
