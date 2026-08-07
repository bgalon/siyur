# 0027 — The Overpass mirror, and a retry policy that can survive a rate limit

- Status: proposed
- Decision Maker(s): Ben
- drafted-by: claude-code (Opus 5) · approved-by: _pending_ · Date: 2026-08-07
- Amends: **ADR-0009** (which chose Overpass as the OSM ingestion mechanism; the *service instance* and the retry policy were never decided)

## Context and Problem Statement

A real research pass over the Rhodes demo area on 2026-08-07 returned:

```
overture  found 508  degraded=false
osm       found 381  degraded=true   reason: "Overpass unavailable (relation: HTTP 429)"
```

Every OSM **relation** — plazas, building complexes, multipolygon sites — was lost, and the pass reported success with a flag. The graceful-degradation machinery worked exactly as ADR-0009 and FR-012 intend; what failed is everything upstream of it.

Two defaults caused it, neither ever decided:

1. **We pointed at `overpass-api.de`**, the main public instance and the most contended one, whose fair-use budget (≈10k queries/day, 2 concurrent) is shared with every other consumer on the internet. `methods-stack-reference.md` §4 had already recorded that the **kumi.systems mirror is more permissive** — the code simply never used it.
2. **We retried a `429` after 0.5 seconds.** A `429` is *"your quota is spent"*, not *"you were unlucky"*. A sub-second wait retries into the same wall and spends the budget faster; `Retry-After`, which is how the server says when to come back, was never read. With `retries = 1` the whole policy amounted to one hopeful immediate re-ask.

This is about to matter far more than it does today. **DU-04's feasibility check is built on `opening_hours`, and that tag comes overwhelmingly from OSM.** Today a dropped relation is thin coverage. From DU-04, under ADR-0022's fail-closed rule, it is `hours_unknown` — which **blocks approval**. A source that silently sheds records under load becomes a source that silently makes days unplannable.

Note what this is *not*: CI never touches Overpass (committed fixtures), so no gate was ever going to catch this. It is a runtime and demo problem, found by running the thing and reading the output.

## Considered Options

- **A — Switch to the kumi.systems mirror and make the retry policy real** (honour `Retry-After`, exponential backoff with jitter, three attempts). Small, contained, strictly better under every future option.
- **B — Source OSM research from the per-area PBF we already download for Valhalla** (ADR-0020). Removes the rate limit entirely and makes research reproducible offline. **The better answer, and deferred on purpose — see ADR-0028.**
- **C — Self-host Overpass.** Full control, an entire additional heavyweight service, and the worst cost/benefit of the three while a PBF is already being fetched for other reasons.
- **D — Drop OSM, use Overture alone.** A non-starter: `opening_hours` and local-script names (`name:el`) are precisely what Overture lacks and what DU-04 and the transliteration sliver depend on.
- **E — Retry harder against the same instance.** More requests into a spent quota is how a fair-use relationship becomes a ban, not how it recovers.

## Decision Outcome

Chosen: **A**, explicitly as a **stopgap** with **B (ADR-0028) as the intended destination after M1 lands**.

**The default endpoint becomes `https://overpass.kumi.systems/api/interpreter`**, overridable per adapter so a self-hosted or private instance is a constructor argument rather than a code change. The honest `User-Agent` the fair-use terms require is unchanged.

**The retry policy becomes:**

| | Before | After |
|---|---|---|
| Attempts | 2 (`retries=1`) | **3** (`retries=2`) |
| Wait | `0.5 × (attempt+1)` — linear | **`Retry-After` when offered, else `0.5 × 2^attempt`** |
| Jitter | none | **full jitter over `[w/2, w]`** |
| Ceiling | none | **8s per sleep** |

**The server's own answer wins.** When Overpass sends `Retry-After` we honour it — in either RFC 9110 form, delta-seconds *or* HTTP-date, because mirrors differ on which they send. A malformed header falls back to exponential backoff rather than being read as "retry now", which is the worst available reading.

**The cap is a deliberate degradation choice, not a timeout.** FR-012 says a slow source yields partial results rather than a hang, so a server asking for two minutes is telling us to flag `degraded` and move on — not to hold the user's SSE stream open. Jitter exists because parallel research passes that fail at the same instant would otherwise retry at the same instant.

**What this does not fix:** we are still a guest on someone else's public infrastructure, still lose records when the mirror is loaded, and still cannot reproduce a research pass offline. That is ADR-0028's job, and the reason this ADR is labelled a stopgap in its own title rather than in a footnote.

### Consequences

- Good: the failure mode that lost every relation is addressed at its actual cause — a shared, contended quota plus a wait too short to clear it.
- Good: `Retry-After` compliance makes us a better-behaved client of a free service we depend on, which is the fair-use relationship working rather than being tested.
- Good: the endpoint is now a parameter, so ADR-0028's alternative — or a self-hosted instance — swaps in without touching the adapter.
- Bad / accepted: **we have moved our dependency from one volunteer-run public instance to another.** kumi.systems is more permissive, not unlimited, and this ADR buys headroom rather than removing the class of failure.
- Bad / accepted: three attempts with backoff means a genuinely unavailable source now takes up to ~15s per element type before degrading, against ~1s before. Slower failure, in exchange for far fewer failures — acceptable while research is an explicit, progress-streamed user action.
- Neutral: no schema, licence or stamping change. Every record is still `kind="osm"`, ODbL, with the OSMF-required attribution.

### Confirmation

`tests/test_sources_osm.py`, seven cases pinning the **policy** rather than the arithmetic:

- `Retry-After: 3` is honoured over our own backoff, and jitter keeps the sleep inside `[1.5, 3.0]` — never longer than asked, never instant.
- Backoff **doubles** (`0.5, 1.0, 2.0`) when no hint is offered. **This test was initially written to assert only that each sleep fell inside its jitter window — which the previous linear policy also satisfies at every attempt, making it unable to fail for the reason it exists (the FAIL-007 shape).** It now pins the jitter and asserts the exact doubling, and was verified against a deliberately reverted linear implementation: it fails, as a guard must be able to.
- `Retry-After: 120` is capped at 8s and the pass degrades rather than stalling (FR-012).
- A malformed `Retry-After` falls back to backoff instead of retrying instantly.
- The HTTP-date form of `Retry-After` parses.
- The default endpoint is the mirror, and is *not* `overpass-api.de`.
- Three attempts by default.

The pre-existing degradation tests (504 → flagged partial, total outage → `degraded` not an exception) are unchanged and still pass — this ADR changes *when* we give up, never *how*.
