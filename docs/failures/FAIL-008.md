# FAIL-008 — Two sessions did the same slice: the isolation rule prevents file races, not task races

- Date: 2026-08-07 · Severity: medium (no defect shipped; ~2h of duplicated agent work, and a
  near-miss on two competing PRs against the same documents)
- Root-cause class: process (a governance rule that was followed exactly, and did not cover the
  failure that happened)

## Symptom

Two Claude sessions worked Spec 002 simultaneously for roughly two hours, without either knowing
the other existed:

| | Session A (`siyur-5f`) | Session B (this one) |
|---|---|---|
| Checkout | `.claude/worktrees/spec-002-plan-compile-offline` | primary checkout, **on `main`** |
| T007/T007a/T007b reconciliation | done → **merged as PR #78** | done by a subagent, never committed |
| T001–T004 setup | done → **merged as PR #79** | done by three subagents, never committed |
| T029/T030 airplane harness | done | deferred to "wave 2" |

Both produced a full reconciliation of the same five documents, in different prose. Only one could
merge. Session B discovered the duplication only when the operator asked whether the work was in a
worktree — roughly two hours in, with 23 files dirty.

## What each session did right, which is the point

Neither session broke a rule it could see.

`CLAUDE.md` is explicit that **teammates inside one session share a working directory**, and that
this is precisely why the no-two-agents-per-file partition is mandatory. Both sessions partitioned
their subagents by folder exactly as required, and **no two agents ever touched the same file**.
The file-race rule worked. `AGENTS.md`'s isolation rule — "each concurrent session works in its own
checkout" — is written for the same hazard: *races on files*.

What actually collided was neither a file nor a branch. It was **a task**. Both sessions read the
same `specs/002-plan-compile-offline/tasks.md`, saw the same unchecked `[ ]` boxes, correctly
identified Phase 1 + T007 as the only unblocked work, and started. Isolation is exactly the wrong
tool for that: putting the second session in its own worktree makes the duplication *cleaner*, not
less likely. It removes the symptom that would have exposed it.

> **The lesson: isolation prevents corruption; only visibility prevents duplication.** A rule that
> says "work somewhere else" cannot tell you that someone is already doing your task.

## Two contributing conditions

1. **Nothing surfaced the peer.** `git worktree list` would have shown a locked worktree on
   `agent/DU-04-setup` in one command, and `ListAgents` would have shown a live peer session in
   another. Session B ran neither at kickoff — not because it decided against them, but because
   nothing in the read-first sequence or the session-start hooks mentions them, and there is no
   habit to fall back on.
2. **Session B never branched.** It began editing on `main` and stayed there for 23 files. That is
   not what caused the duplication, but it is what made the recovery expensive: the corrective work
   had to be re-derived against the *other* session's merged prose rather than rebased, because the
   two reconciliations were different text solving the same problem. Branching costs one command
   before the first edit; not branching cost a salvage.

## What the duplication did NOT cost, stated honestly

No defect shipped and no data was harmed. Both merged PRs are good work. The wasted resource was
agent time and operator attention.

**And the duplication had one genuinely positive effect worth recording**, because it complicates
the lesson: session B's independent review of its *own* reconciliation found three defects that
were also present in session A's merged version — a manifest canonicalization that would have
failed every bundle's launch check on device, a contract field with no column behind it, and a
`CHECK` constraint that made editing an approved plan impossible. Those were relayed to session A,
which verified and fixed them. **Two independent passes over the same specification found bugs one
pass did not.** That is an argument for deliberate redundancy on high-stakes design documents — it
is not an argument for accidental redundancy discovered two hours late, which is what happened
here.

## Fix — the guardrail (this entry's mandatory regression guard)

`.claude/hooks/concurrent_sessions.py`, registered on `SessionStart` alongside `devlog_debt.py`,
which exists for the same reason: a rule that lives only in prose gets skipped under load.

It prints a warning, never blocks, when either condition that produced this entry is true:

1. **Another worktree exists** — naming each one and *the branch it holds*, because "a peer exists"
   is not actionable while "a peer holds `agent/DU-04-setup`" is enough to go ask what it is doing.
2. **HEAD is on `main`** — ADR-0005 is one branch per unit of work, and every violation of it
   starts here.

Silent when clean, silent on any error, stdlib-only against system Python 3.9 — the same contract
`devlog_debt.py` holds. It warns rather than blocks deliberately: a second worktree is frequently
correct, and a hook that punishes the legitimate case gets removed.

**Verified against the condition that produced this entry** — run in the repo with the peer
worktree present, it names all four worktrees and their branches.

## Also owed (not discharged by the hook)

- **`AGENTS.md`**: the isolation bullet should say what to *check*, not only where to work — run
  `git worktree list` and `ListAgents` before picking work off a task list, and claim tasks visibly.
- **A claiming convention.** The hook makes a peer *visible*; it does not make the division of work
  explicit. Ticking a `tasks.md` box to `[~]` on start, or naming held phases in the branch, would
  close the remaining gap. Deferred — it is a process decision for Ben, not a hook.

## Related

- `exhibit/U2-two-sessions-one-task` — candidate: the rule that was followed exactly and did not
  cover the failure.
- ADR-0005 (branch/worktree/PR workflow) — the rule whose scope this entry narrows.
- ADR-0016 — amended in the same pass with a startup warning for its own prose-only trigger, for
  exactly this reason.
