# 2026-08-07 (second session) — Two sessions, one slice; and the canonicalization that would have broken every bundle

**Goal:** pick up Spec 002 as an orchestrator — plan the slices, delegate, review, land through PRs.

## What happened

**Four agents, one wave, four good reports.** `deps` (T001), `services` (T002/T003), `web-e2e`
(T004) and `reconcile` (T007/T007a/T007b), partitioned by file so no two ever collided. The
partition held; nothing raced.

The reports were better than the briefs in three places, and each improvement came from the same
habit — **measuring instead of accepting the premise**:

- `services` corrected the brief: **pedestrian costing is not a container setting.** Valhalla
  serves every costing model from one graph; `costing:"pedestrian"` is a per-request field belonging
  to T016. It also refused to let a comment assert a cost it had not observed — measured 16s for a
  3.3 MB extract, 7s on restart, and recorded that ADR-0020's "1–5 minutes" is the city-sized-PBF
  figure rather than a floor.
- `web-e2e` corrected the brief: a `webServer` running `vite dev` would have **hung T029 forever**,
  because `vite.config.ts` deliberately sets `devOptions.enabled: false`, so there is no service
  worker under the dev server to wait for. It runs `vite build && vite preview` instead. It also
  proved the vitest exclude with a *negative control* — probe collected without the exclude, not
  collected with it — rather than asserting the guard worked.
- `deps` verified import names **by execution** rather than by metadata, and caught that WebFetch's
  summarizer reported `opening-hours-py` as 0.11.1 when the real version is 2.1.4. Same shape as
  yesterday's "tree-shaken" comment: a plausible, conventional, false intermediate.

**Then the review found three defects, and the best one was invisible to every test.** The
manifest-hash rule read "canonical JSON — UTF-8, sorted keys, no insignificant whitespace,
`integrity` omitted", which sounds complete and pins neither string escaping nor number formatting.
The writer is Python, the launch-time verifier is TypeScript:

```
PY : {"attribution":"© OpenStreetMap contributors","bbox":[28.0,36.0,28.5,36.5]}
JS : {"attribution":"© OpenStreetMap contributors","bbox":[28,36,28.5,36.5]}
```

`json.dumps` defaults to `ensure_ascii=True` and escapes the `©` that **every** manifest carries in
its ODbL attribution; Python renders `28.0` where `JSON.stringify` renders `28`, and `bbox` is the
only float array in the document. Both fire on every real bundle. The failure lands on the
traveller's device, offline, as "this bundle is unusable" — the one place with no recourse. The fix
is to name a standard (RFC 8785 / JCS) instead of describing one. **The card had warned about
exactly this risk in its own text and then under-specified the rule anyway.**

Two more in the same pass: `contracts/plans.md` returns `feasibility.checked_at` with no column
behind it, and ADR-0023's "`approved_at` cleared on edit" collides with the
`approved_at`/`approved_by` pairing to make editing an approved plan raise a check violation — the
exact transition its own confirmation (d) asserts must work.

**And then the actual story of the day.** Asked to verify the work was in worktrees, I found it was
not — and, worse, found a **second live session** that had been working the same tasks for two
hours, whose reconciliation had already merged as PR #78 and whose setup was open as PR #79. See
FAIL-008. The short version: both sessions obeyed the isolation rule exactly, and the isolation rule
does not cover what went wrong. Two agents must not share a *file*; nothing said two sessions must
not share a *task*.

The recovery is the part worth keeping. My reconciliation was not rebased onto theirs — the two were
different prose solving the same problem, so a rebase would have produced Frankenstein text. It was
**re-derived** against their merged version, which is slower and correct. Then, having sent them the
three findings, they fixed the canonicalization themselves — with an **implication** rather than my
biconditional for the post-approval `CHECK`, which sidesteps the `'failed'` trap as a *class* rather
than by enumerating one more state. Theirs is better. My branch reduced to what was genuinely
unique: the four ADR ratifications, the guardrail, and this entry.

## Decisions

- **ADRs 0015, 0016, 0017, 0018 ratified.** 0016 gains an owed startup warning, because its revisit
  trigger ("the second process") was checkable but nothing checked it. 0015 keeps policy A (area is
  private) but **persists the resolver's `SourceRef` in T009's migration** — provenance is capturable
  at write time or never, and T009 is the last cheap moment.
- **0018 amended before approval rather than approved as written.** Its `covered = count > 0`
  premise was fixed by PR #67 five days after drafting. Struck in place, not deleted: the reasoning
  for option A is only legible alongside the constraint that applied at the time. Signing it
  unamended would have written a false claim into the governance trail — the `size-override` lesson
  in a different costume.
- **RFC 8785 over a property list**, everywhere a hash crosses the Python/TypeScript boundary.
- **Salvage over discard, and reduction over competition.** When the peer session fixed the same
  defects, the answer was to shrink my PR, not to race it.

## Failures

- **FAIL-008** — two sessions, one slice. Guardrail: `.claude/hooks/concurrent_sessions.py`, warning
  at `SessionStart` when another worktree exists (naming its branch) or when HEAD is on `main`.
  Verified against the live condition.
- **Process, not catalogued:** I never ran `git worktree list` or `ListAgents` at kickoff, and never
  branched off `main` before the first edit. Two commands and roughly ten seconds would have caught
  both. The hook now runs the first one for me; the second is in the same hook's `main` check.

## Cost / turns

One session, four subagents plus a reviewer plus a library survey. ~2h of the four agents' work
duplicated another session's. Net unique output: four ADR ratifications, three blocking defects
found and relayed (all three fixed), two corrections to the peer's setup branch (image digests
pinned, a false `timezonefinder` capability claim), one guardrail, one failure entry.

## Exhibit-tag candidates

- `exhibit/U2-two-sessions-one-task` — **the strongest.** A governance rule followed exactly by both
  parties, which did not cover the failure that happened, because it was written for file races and
  the collision was a task race. Isolation prevents corruption; only visibility prevents duplication.
- `exhibit/U5-the-canonicalization-that-looked-complete` — a spec sentence that names four properties
  and sounds airtight, is silent on the two that matter, and fails on the device rather than in CI.
  Reproducible in two lines, invisible to every test written against either side alone.
- `exhibit/U3-redundancy-found-real-bugs` — the uncomfortable half of FAIL-008: two independent
  passes over one specification found three defects one pass did not. An argument for *deliberate*
  redundancy on high-stakes documents, and not at all an argument for the accidental kind.
- `exhibit/U2-the-summarizer-that-lied` — a tool summary reporting a package version wrong by two
  majors, caught by reading the raw field. The lockfile hash is the authority.
