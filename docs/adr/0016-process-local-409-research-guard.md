# 0016 — The `409` "research already running" guard stays process-local for slice 001

- Status: proposed
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: _pending_ · Date: 2026-08-01

## Context and Problem Statement

`specs/001-research-cited-sites/contracts/research.md` promises: *"`409` research already
running for this area (idempotency guard)."* `api/areas.py` implements it with a module-level
`_RUNNING: set[UUID]` behind a `threading.Lock`.

That is **correct within one process and blind across processes.** One uvicorn worker sees its
own in-flight passes; a second worker, a second container, or a Cloud Run instance scaled to two
does not. The implementing session wrote this into the module docstring rather than dressing it
up, and flagged the fix as schema-shaped and therefore ADR-bearing.

**What is and is not at risk — the part worth being accurate about.** The underlying write is
safe either way. `commons/repository.py::upsert_sites` upserts, so a doubled pass **enriches the
same rows rather than forking them**; `api/areas.py` commits only after the stream completes and
rolls back otherwise, so a doubled pass cannot leave a half-written area either. **There is no
corruption scenario here.** What a missed `409` actually costs is:

1. **Wasted work** — two full adapter fan-outs (Overture over hosted parquet, Overpass) and two
   `curate` model calls for one area. Real money and real third-party load; the Overpass usage
   policy is a courtesy we are meant to keep.
2. **A dishonest contract** — the API advertises an idempotency guard it can only keep when
   deployed as a single process. That is the more serious of the two, because it is a promise
   rather than an inefficiency.
3. **A doubled provenance trail** — `site_source` is append-only by design, so the second pass's
   observations are recorded permanently alongside the first's. The merged `site` row is right;
   the audit trail gains duplicate observations that nothing removes.

Also worth recording so the guard's real reach is not overstated: the reuse path
(`known.covered and not force_refresh`) returns **before** `_claim` is ever called, so the guard
only ever governs passes that are actually going to do work.

## Considered Options

**A — Leave it process-local, documented (shipped).** Zero machinery. Honest in the docstring,
optimistic in the contract.

**B — A Postgres advisory lock.** `pg_advisory_lock(hashtext(area_id))` taken on the session the
stream already holds (`api/areas.py` opens a `session = factory()` for the pass and closes it in
`_research_frames`'s `finally`), released when that connection is returned. **No schema change
at all** — worth naming separately from "a lock row", because it is materially cheaper than the
option as usually framed, and Postgres releases it automatically if the backend dies.

**C — A claim row / `research_started_at` column with a staleness timeout.** A durable
`research_started_at` on `area` (or a `research_claim` table). Cross-process by construction and
inspectable, but needs a migration, and needs a timeout constant that is a *guess*: too short and
a slow-but-live pass gets a second one racing it; too long and a crashed process wedges the area
for that long.

**D — Drop the `409` from the contract** and let concurrent passes run. Honest, and cheap, but it
gives up a guard that is genuinely useful in the single-process case for no gain.

## Decision Outcome

Chosen: **A — leave it process-local for slice 001**, with the limitation stated at the call site
(it already is) and with the deployment condition that makes it wrong written down here as the
revisit trigger.

Two drivers.

**The deployment this guard would be wrong for does not exist.** Slice 001 runs one uvicorn
process. Paying a migration (C) or a lock protocol (B) against a topology nobody has configured
is speculative work, and the failure mode it guards is cost, not correctness (above).

**A fails open; B and C fail closed — and for a guard whose only job is to save duplicate work,
failing open is the better direction.** If the process dies mid-pass, `_RUNNING` dies with it and
the area is immediately researchable again. A durable claim row (C) survives the process that
died holding it, which is exactly why C has to invent a timeout — the timeout is not incidental
complexity, it is C's *repair* for the failure mode C introduces. B sits between the two:
Postgres drops the advisory lock when the connection goes, so it fails open like A, at the cost
of pinning the guard's lifetime to a connection held for the whole SSE stream.

**D was rejected** because the single-process guard is doing real work today (a double-click on
"Start researching" is the common case it catches), and removing a promise is worse than scoping
one.

When the trigger below fires, **B is the recommended landing point, not C.** It needs no
migration, it inherits the fail-open property A already has, and the connection it needs is
already open for the duration of the stream. C only wins if the claim must be *observable* —
e.g. if the UI ever needs to show "someone else is researching this right now", which is a
product feature and would come with its own requirement.

### Consequences

- Good: no migration, no lock protocol, no timeout constant to guess wrong; the guard fails open,
  so an area is never wedged.
- Good: the limitation is written where someone deploying will meet it (`api/areas.py` module
  docstring), not only here.
- Bad / accepted cost: at more than one process the contract's `409` becomes a claim the API
  cannot keep. Two passes can run, burning two adapter fan-outs and two `curate` calls, and
  appending duplicate `site_source` observations. **No data is corrupted and no rows fork.**
- Accepted: `contracts/research.md` states the `409` unconditionally. It is not wrong per
  deployment-as-shipped, but it is unqualified; if this ADR is accepted, the contract deserves a
  one-line scope note — **not this session's file.**

### Confirmation

- **`tests/test_api_research.py::test_a_pass_already_running_for_the_area_is_409`** — pins the
  behaviour that *does* hold: within one process, a second pass over the same area is refused.
  It is deliberately not evidence of a cross-process guarantee, and should not be read as such.
- No test asserts the cross-process property, because the shipped code does not have it. When the
  trigger below fires, the landing change owes a test that *does* — two sessions/connections,
  one area, one `409`.

### Revisit trigger — the second process, not a date

Revisit the moment the API is deployed as **more than one process**, by any of these routes:

- `uvicorn --workers > 1` (or gunicorn with multiple workers);
- a Cloud Run service with `max-instances > 1` (or any autoscaling replica count above one);
- any second container/host serving the same database.

That is a configuration change, so it is checkable at deploy time rather than by vigilance: **if
the process count is about to exceed one, this ADR must be revisited in the same change.** Until
then the guard is exactly as strong as the deployment needs it to be, and no stronger.

A second, weaker trigger: if the UI ever needs to *display* an in-flight pass to another user,
the claim must become observable and C wins over B on that requirement alone.
