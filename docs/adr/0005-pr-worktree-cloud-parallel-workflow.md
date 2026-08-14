# 0005 — PR-based workflow with worktree (local) + cloud parallelism

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: — · Date: 2026-07-25 · accepted: 2026-08-11

## Context and Problem Statement

`AGENTS.md` already declares the intent — "`agent/<ticket>-<short-desc>` for agent work; worktrees for parallel sessions; branch protection added later, at ramp-up" — but it was never operationalized: decision-bearing work committed **directly to `main`**, and parallel sessions shared **one working directory**. This session hit the concrete failure that convention prevents: two spike sessions (Vite config spike + planner validation spike) ran concurrently in the single `main` checkout and (a) repeatedly clobbered each other's writes to `.claude/settings.local.json`, (b) edited tracked files (`methods-stack-reference.md`, `0004-*.md`) simultaneously, and (c) one session moved the shared checkout onto a branch under the others' feet. Now the intent is explicit: work **in parallel on this device (worktrees) and in the cloud at the same time**, which requires isolation and a defined integration protocol.

## Considered Options

- **A — Keep direct-to-`main`, coordinate by hand.** Zero ceremony; but concurrent sessions in one checkout race on files (demonstrated this session), and `main` has no gate. Does not scale past one session.
- **B — Branch-per-unit + PRs, but one shared checkout.** Removes the `main`-gate problem; but concurrent local sessions still share a working directory and still race on files / branch switches.
- **C — Branch-per-unit + PRs + isolation: worktrees locally, separate branches in the cloud.** Each concurrent session gets its **own** working tree (a `git worktree` dir locally; a cloud sandbox remotely), on its own `agent/<ticket>-<slug>` branch, integrating **only via PR to `main`**. Branch protection phases in: require-a-PR now, required CI status checks at DU-00 when CI jobs 1–7 exist.

## Decision Outcome

Chosen: **C — branch-per-unit + PRs with worktree/cloud isolation**, because it is the only option that lets local *and* cloud sessions run at the same time without the shared-working-dir races this session demonstrated, while giving `main` an integration gate. It **operationalizes** the `AGENTS.md` branch/worktree note now and **schedules** the enforced-required-checks half of "branch protection at ramp-up" to DU-00 (when CI exists to gate on). Concretely:

- **One branch per unit of work:** `agent/<ticket>-<slug>` (`<ticket>` = a `DU-NN`, a GitHub issue #, or a short slug). No decision-bearing work on `main` directly.
- **Local parallelism = worktrees:** each concurrent local session runs in its **own** `git worktree` (separate directory) on its own branch — never two sessions in one checkout. Use `EnterWorktree` (harness) or `git worktree add`.
- **Cloud parallelism = separate branches:** cloud sessions branch the same way and integrate via the same PRs; local-worktree and cloud sessions can run concurrently.
- **Integration = PR to `main`** using `.github/PULL_REQUEST_TEMPLATE.md`; conventional-commit title; the DoD + exhibit-tag candidate travel in the PR body.
- **Branch protection, phased:** enable **require-a-PR / no direct push to `main`** once the current in-flight branches land; add **required status checks** (CI jobs 1–7) to the same rule at **DU-00**, per `AGENTS.md`. This ADR itself is delivered **as a PR** (dogfood).

## Consequences

- Good: eliminates the shared-working-dir races demonstrated this session; enables true local(worktree) + cloud parallelism with a clean merge point.
- Good: every unit becomes a reviewable PR — a first-class course artifact (the "how we work is a deliverable" thesis, syllabus U1/U2).
- Bad / accepted cost: more git ceremony (branch → PR → merge per unit); worktrees consume extra disk (mitigate with `worktree.symlinkDirectories` for `node_modules`/caches once `web/` exists).
- Accepted cost: this is a **solo-dev** PR model — PRs are Ben-review + (from DU-00) CI-gated, not multi-human review. Until DU-00 wires CI, protection is "require a PR", not full status-check gating.
- Accepted cost: enabling protection while sessions are mid-flight on `main` would disrupt them — enforcement is deferred until the current in-flight branches land (timing is Ben's call).

## Confirmation

This ADR + the PR template + the `AGENTS.md` workflow block are delivered on branch `agent/introduce-pr-worktree-workflow` and merged **via PR** (the dogfood proof). The protection rule is visible via `gh api repos/:owner/:repo/branches/main/protection`; required status checks (CI jobs 1–7 from `test-strategy.md`) are added to that rule at **DU-00** and tracked in `delivery-plan.md`. TODO: add the "enable branch protection" step to DU-00 and the worktree symlink config to the `web/` scaffolding task on implementation.

## Amendments

### A1 — the enforcement half finally landed (2026-08-14, approved-by Ben)

**The Confirmation above is now discharged, roughly three weeks late.** At DU-00 it turned out that branch protection was **unavailable on a private free-tier repo**, so required status checks could not be configured at all. `AGENTS.md` recorded that as "the merge gate is self-enforced, not machine-enforced", and for the whole of Spec 001 and most of Spec 002 the gate was a discipline binding every session rather than a rule the platform applied.

The repo went **public** on 2026-08-11, and protection was enabled:

```
required checks : 1 · lint  2 · unit  3 · integration  4 · deterministic-evals
                  5 · e2e-airplane  6 · security  7 · diff-guard
strict          : true      # branch must be up to date with main
enforce_admins  : true      # applies to the repo owner
force pushes    : blocked
deletions       : blocked
```

**Why the delay is worth recording rather than quietly closing.** The gap between "we decided to gate merges" and "merges are gated" was filled by discipline, and discipline failed twice in two days — **FAIL-009**: `main` went red because `gh pr checks … | tail -4` hid failures that sort above passes, and both operators had checked before merging. That is the evidence for this ADR's premise: a rule that depends on a human reading output correctly is not yet a gate. It is also why `scripts/merge-guard.sh` still earns its place now that the platform enforces the same thing — it runs *before* you push, and it additionally asserts every required job is **present**, which a stale-base run can silently omit (#93).

**Two things enforcement still does not cover**, so the discipline is narrower rather than gone:

- `required_approving_review_count` is **0**. This remains the solo-dev model the Consequences section accepted: CI-gated, not human-reviewed. Raising it above 0 with a single maintainer would block every PR.
- `strict: true` means a branch behind `main` is refused. That is deliberate — it removes the stale-`BASE_SHA` class of phantom failure — but it makes **rebase-before-push** part of the workflow rather than a courtesy.

The "enforcement is deferred until in-flight branches land" cost in the Consequences is likewise discharged: protection was enabled with zero open PRs.
