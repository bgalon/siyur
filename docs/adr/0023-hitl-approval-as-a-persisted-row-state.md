# 0023 — HITL approval is a persisted row state, not a suspended coroutine

- Status: proposed
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: _pending_ · Date: 2026-08-07

## Context and Problem Statement

Spec 002 (`specs/002-plan-compile-offline/`) FR-006 requires that **nothing compiles until the user explicitly approves**, and SC-003 sets the bar at **100%**: the approval must still be there after a process restart. `docs/data/itinerary.md` describes the gate as "an explicit persisted pause (`interrupt()`-equivalent over the owned Postgres checkpoint, ADR-0004)" — the *equivalence* is named, the *mechanism* is not. Planning slice 002 forces it, because the pause is where three edge cases in the spec actually land:

1. the user approves twice (double-click, retried request, two tabs);
2. the user approves **revision 3** while an edit has already produced **revision 4**;
3. the user edits a plan that was already approved.

ADR-0004 already excluded LangGraph's `interrupt()` + checkpointer in favour of an **owned Postgres checkpoint** — one `UPSERT` per step over the plan row. That decision fixes *where* state lives but not *what the pause is*, and the difference is load-bearing: a pause modelled as a waiting coroutine has something to lose on a restart, and a pause modelled as a row does not. The compiler is a separate concern from the API process that took the approval, so "did the user approve?" has to be answerable by anything that can open a database connection, not only by the process that streamed the proposal.

There is also a consistency question. `api/areas.py` already answers a concurrency collision with a **`409`** (ADR-0016, the process-local research guard). If approval invents a second concurrency idiom, the API has two shapes for one class of problem.

## Considered Options

- **A — The pause is a row state on `user_plan`; approval is a compare-and-set on a content hash.** `status` is a small enum, the pause is `status='proposed'`, and the approve transition is a single conditional `UPDATE` predicated on both the current status and the itinerary's content hash. Compile selects only `approved` rows and flips them to `compiling` **in the same transaction**.
- **B — An in-memory `asyncio.Event` (or equivalent) that the request handler awaits.** The natural shape if you think of the pause as a suspended call. A bounce, a deploy, or a Cloud Run scale-to-zero loses the pending decision — precisely what FR-006/SC-003 forbid — and a second instance cannot see the first instance's pause at all.
- **C — LangGraph `interrupt()` + its checkpointer.** The framework's own answer. Excluded by ADR-0004 (we own the checkpoint); re-adopting it here would re-open a settled decision to solve a problem the owned checkpoint already solves.
- **D — An append-only `plan_approval` table, last write wins.** Auditable. But two approvals become two rows, and "which approval is the one compile should use" becomes an application-level reduction — the exact ambiguity that turns one plan into two divergent bundles.
- **E — Optimistic locking on `updated_at` instead of a content hash.** Cheap, but any no-op re-save (a re-serialisation, a metadata touch) bumps the timestamp and falsely invalidates a live approval. The thing the approval is *about* is the itinerary's content, so the predicate should be over the content.

## Decision Outcome

Chosen: **A — the pause is a persisted row state, and approval is a compare-and-set over the itinerary hash**, because it makes the guarantee **structural rather than remembered**: there is nothing in memory to lose, so surviving a restart is not a feature that had to be implemented, it is a property of having never held the state in a process. Compile's precondition then becomes a predicate in the same transaction that consumes it — a database constraint, not an application check someone can forget to write on a new code path.

**The columns.** `user_plan` carries, alongside the serialised `ItineraryV1`:

| Column | Meaning |
|---|---|
| `status` | `proposing` \| `proposed` \| `approved` \| `superseded` \| `compiling` \| `compiled` \| `failed` |
| `revision` | `int`, incremented on every edit |
| `approved_at` | `timestamptz` (UTC), set at approval, cleared on edit |
| `approved_by` | the auth subject that approved (`SessionUser.sub`) |
| `itinerary_hash` | SHA-256 over the canonical `ItineraryV1` JSON — what an approval is *of* |

Every read and write is row-scoped to the auth subject (FR-007); another subject's `plan_id` is a `404`, never a `403`.

**The state machine.**

```
proposing ──(planner writes the proposal)──▶ proposed
proposed  ──approve (feasibility ok)──────▶ approved        # FR-005: infeasible cannot approve
proposed|approved ──edit──▶ proposing @ revision+1          # prior row set `superseded`
approved  ──compile claims it────────────▶ compiling ──▶ compiled | failed
```

**The pause is `status='proposed'`.** It is not a coroutine parked on an event, not a checkpoint blob to be rehydrated, and not a queue entry — it is a row in a state, and any process that can reach the database can see it.

**Compile's gate is a transaction, not a check.** The compiler does not "read status, then start work". It executes a single conditional claim — `UPDATE user_plan SET status='compiling' … WHERE plan_id=:id AND user_id=:sub AND status='approved'` — and proceeds **only if one row was updated**, in that same transaction. A `proposed` plan is not refused by an `if`; it is unclaimable. Two compilers racing on one approved plan produce one claim and one bundle.

**Double approve resolves by compare-and-set.** `UPDATE user_plan SET status='approved', approved_at=now(), approved_by=:sub WHERE plan_id=:id AND status='proposed' AND itinerary_hash=:hash`. The first approve updates one row; the second updates **zero** and the handler returns the **existing** approval — an idempotent `200` carrying the same `approval_id` and the same `approved_at`. Never two approvals, never two bundles, and never an error the user has to interpret as a failure when nothing failed.

**Stale approve is a `409` naming the current revision.** If the user approves revision 3 while revision 4 exists, the hash predicate fails, zero rows update, and the response is `409` with the current `revision` and plan id — deliberately **the same idiom as ADR-0016's process-local `409` research guard**, so the API has one concurrency shape rather than two. The distinction from the idempotent case is exact and is made in the database, not by inspection: the status predicate failing on an already-`approved` row with the *same* hash is idempotency; the *hash* predicate failing is staleness.

**Editing after approval returns the plan to unapproved.** The edit writes a new row at `revision+1` in `proposing`, marks the prior row `superseded`, clears `approved_at`, and re-runs feasibility before approval is offered again — which is the spec's "a plan edited after approval returns to unapproved and re-runs feasibility", expressed as rows rather than as a rule someone has to honour.

**Scope / non-goals**: this fixes the *pause mechanism and its concurrency semantics*. It does not decide the planner graph (ADR-0004), the compile pipeline's stages (tech-design §5.3), or the API's URL shapes (`contracts/plans.md`). Multi-user shared plans, approval delegation, and an approval audit log are out of scope — if a later slice needs approval *history* rather than approval *state*, that is option D re-considered on its own merits, with its own ADR.

### Consequences

- Good: SC-003 ("an approval survives a restart", 100%) is satisfied by construction — there is no in-memory pause to lose. The test that proves it kills the process; it does not mock a restart.
- Good: the no-compile-without-approval gate is enforced by a `WHERE` clause in the claiming transaction, so a future code path that forgets to check cannot bypass it — it simply claims zero rows.
- Good: one concurrency idiom across the API (`409` + the current state), continuous with ADR-0016.
- Good: `itinerary_hash` makes "approved" mean *this exact plan*, so a stale approve can never silently authorise a different day than the one the user read.
- Bad / accepted cost: the status enum has **seven** states, which is more than the two (`proposed` | `approved`) that `specs/002-plan-compile-offline/data-model.md` §6 and the three (`proposed` | `approved` | `superseded`) that `contracts/plans.md` currently name. Those two documents and the `user_plan` `CHECK` constraints must be reconciled to this enum before the migration is written, or the `CHECK (status = 'approved') = (approved_at IS NOT NULL)` rule will reject legitimate `compiling`/`compiled` rows.
- Bad / accepted cost: `revision` + `superseded` means an edited plan leaves rows behind rather than mutating one. That is deliberate — it is what makes a stale approve diagnosable — but it needs a retention answer eventually (not in M1, where a day's plan is small and rare).
- Accepted: the guarantee is the transaction's, not the ORM's, so the tests that matter run against **real Postgres** under `-m integration`. A Tier 1 test with a mocked session can assert the SQL shape; it cannot assert the guarantee.
- Accepted: `approved_by` duplicates `user_id` at M1 (a plan is approved only by its owner). It is recorded now because back-filling *who approved* after the fact is impossible, and M2 shared plans would need it.

### Confirmation

- **`tests/test_hitl_gate.py`** — the four assertions this ADR exists to make true:
  - (a) compile **refuses a `proposed` plan** — the claiming `UPDATE` matches zero rows and no bundle is produced (Tier 1, SQL-level);
  - (b) an approval **survives a process restart** between propose and approve — the process is actually torn down and a new one reads the row (SC-003 = 100%);
  - (c) **concurrent double-approve** yields exactly one `approval_id` and one `approved_at`, with the second call returning `200`, not an error;
  - (d) **editing an approved plan** returns it to `proposing` at `revision+1`, marks the prior row `superseded`, and re-runs feasibility before compile is permitted.
  - (b) and (c) run under **`-m integration`** against real Postgres — the guarantee is the transaction's, not the ORM's.
- **Stale-approve case** (same file): approving `revision` 3 while 4 exists returns **`409`** naming the current revision and leaves `status` untouched — asserted byte-for-byte against ADR-0016's `409` body shape so the two guards cannot drift apart.
- **`evals/test_structural.py`** — no bundle manifest exists for a plan whose row never reached `approved` (the merge-blocking form of FR-006).
- **TODO (lands with DU-04):** `commons/db.py` `user_plan` columns + `CHECK`s, Alembic migration `0005_user_plan` (**`ask`-gated — Ben approves**), `api/plans.py::approve`, and `tests/test_hitl_gate.py`.
