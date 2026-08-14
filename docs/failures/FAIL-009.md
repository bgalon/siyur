# FAIL-009 — A PR merged with two red checks, because the command that reports them was truncated

- Date: 2026-08-09 (both instances 2026-08-08 → 2026-08-09) · Severity: high (a red `main` twice
  in two days; no defect reached a user, but the merge gate — the repo's only release control —
  was bypassed by both sessions independently)
- Root-cause class: process + tooling (a rule that both operators knew, defeated by the shape of
  the command used to check it)

## Symptom

**PR #92 was merged while jobs 4 and 7 were failing.** The merging session had checked the gate
first and believed it green:

```
gh pr checks 92 --watch | tail -4
```

`gh pr checks` sorts failures **above** passes. `tail -4` therefore showed four passing rows and
hid both failures. The two red jobs were:

```
4 · deterministic-evals  fail
7 · diff-guard           fail
```

Job 4 was a real defect — `commons/routing.py` had hardcoded a fixture filename containing a place
name, tripping `evals/test_genericity.py`'s `[place-literal]` scan. Job 7 was a missing
`size-override` label on a >500-line PR.

`main` went red. It was fixed forward by PR #94.

**The same week, PR #84 was merged by the other session while its checks were still pending.**
Different session, different command, same outcome — which is what makes this a pattern rather
than a slip.

## Why the rule did not prevent it

`AGENTS.md` is unambiguous, and both sessions could quote it:

> **merge only when CI checks 1–7 are green** … CI runs on every PR and is the real signal —
> treat a red check as a hard block even though GitHub won't stop the merge.

It also explains *why* the rule is only a discipline: this is a **private, free-tier, solo-dev**
repo, so branch protection is unavailable and required status checks cannot be configured. The
merge gate is self-enforced by construction.

Neither failure was ignorance of the rule. Both were **a truthful command producing a misleading
answer**:

- `| tail -4` on output whose failures sort first
- merging on a checks view that had not finished resolving

The rule says "check that it is green". Both sessions checked. The check lied.

## The near-miss that makes the mechanism clear

After #94 restored `main`, the same session ran the same class of command again — and this time
counted the rows instead of reading them:

```
=== any failures? ===   0
pass count: 9
```

Identical intent, different shape, correct answer. The difference was not diligence. It was that
the second form **cannot** hide a failure behind a truncation window.

## Root cause

**A human-readable status command was used as a machine gate.** `gh pr checks` is designed for a
person to read: it sorts by severity, it renders URLs, it streams. Piping it through `tail`, or
reading it before it settles, turns "show me the state" into "show me *some* of the state" — and
the part most likely to be cut is the part that matters, because failures sort to the top and
scroll off first.

Secondary cause: `gh pr checks` **exits non-zero whenever any check is not passing**, which under
`set -e` kills `until` loops. That has already produced 22 recorded tool failures in this repo and
pushes authors toward exactly the pipe-and-truncate idiom that caused this.

## Guardrail (the entry does not close without one)

A checklist item would not have helped — both operators already had one and followed it.

**`scripts/merge-guard.sh <pr>`**: query the check state as **JSON**, count non-passing rows, and
exit non-zero unless the count is `0` and every required job 1–7 is present by name. Presence
matters as much as state: a job that never ran is not a job that passed, and #93's stale-base
failure showed that a *missing* signal reads as innocent.

```bash
gh pr checks "$PR" --json name,state --jq '[.[] | select(.state != "SUCCESS")] | length'
```

No `tail`, no `head`, no `grep` of prose, no eyeballing. The number is the gate.

**Regression check** — `tests/test_merge_guard.py`: feed the guard a captured `gh` JSON payload
containing a failing job and assert it exits non-zero; feed it an all-green payload missing job 5
and assert it *still* exits non-zero. The second case is the one a hand-written guard gets wrong,
and it is the one #93 demonstrated.

## Recurrence — 2026-08-14, by the author of this entry, in a different command

Hours after writing the guardrail, I truncated a status command again and reached a
confidently wrong conclusion about someone else's work.

Checking whether a subagent had begun editing, I ran `git status --short | head -12` and then
`| tail -20`. The list was longer than both windows, so three files it *had* already modified
fell in the **hidden middle**. I concluded it had not started, reset the worktree to a newer
`main`, and destroyed its in-progress edits to `planner/feasibility.py`,
`planner/pipeline.py` and `commons/repository.py`.

**`scripts/merge-guard.sh` did not and could not prevent this.** The guardrail fixed one
*command*; the failure is a *habit*, and it transfers to any tool that prints a list. The rule
in `AGENTS.md` names `gh pr checks` because that is where it was found — but the general form
is:

> **Never pipe a status command into `head`/`tail` and treat the remainder as the answer.**
> If the output is too long to read, that is a reason to count it, filter it by predicate, or
> ask for a machine-readable form — never a reason to look at one end of it.

`git status --porcelain` piped to `wc -l`, or `git diff --name-only <paths>` for a specific
question, would both have been correct here and neither is longer to type.

Recorded because a failure repeated by the person who documented it is the strongest available
evidence that the countermeasure has to be mechanical rather than remembered — and because the
class was wider than the entry originally claimed.

## Related

- `exhibit/U2-override-that-lies` — the neighbouring temptation. #93 later reported 1005 changed
  lines for a 125-line PR because its `BASE_SHA` was stale. `size-override` would have cleared the
  red instantly *and written a false claim into the governance trail*. Fixing the measurement (a
  rebase onto current `main`) cleared it honestly. **A red check you disagree with is a
  measurement to investigate, not a label to apply.**
- FAIL-008 — the other "rule followed exactly, failure not covered by it" entry. Both argue the
  same thing: when a rule depends on a human reading output correctly, it is not yet a guardrail.
