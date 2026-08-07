# 2026-08-06 — Eleven PRs across eight worktrees, cut in half by an Actions incident

> **Reconstructed, not remembered.** This entry was rebuilt on 2026-08-07 from
> `logs/events.jsonl` (1,738 captured events for this date), git history, and PR
> metadata — the session that lived it did not run `/devlog`. Prompts, tool
> counts, commands and timings below are quoted from the event trail and are
> exact. **Anything about intent or reasoning is inference and is marked as
> such.** The gap is itself the lesson: the trail survived, the narrative did not,
> and the narrative is the part the course needs.

**Goal (inferred from the prompt trail):** clear the DU-03 review backlog into
`main`, then put the project on a footing for autonomous operation.

## What happened

**Scale.** 12:51–19:56 UTC (~7h). **9 sessions across 8 git worktrees**, running
concurrently under `.claude/worktrees/`. 1,738 hook events: 1,029 `Bash`, 275
`Edit`, 165 `Read`, 64 `Write`, 10 `Agent` dispatches, 8 `Monitor`, 6 `TaskStop`,
plus 112 browser-automation calls (`javascript_tool`, `computer`, `navigate`,
`read_console_messages`) — the map was verified in a real browser, not only in
tests. **Zero errors captured in the trail.**

This is the isolation rule working at full stretch: eight concurrent checkouts,
never two sessions in one directory (`AGENTS.md`).

**Eleven PRs, #61 through #71.** Thirteen non-merge commits. The spread of work:

- `#61` quickstart re-aimed at acceptance + T069 gate verification recorded
- `#62` cryptography ≥50.0.0 for PYSEC-2026-3552 — a CVE that reddened job 6
  *after* the gate check had already passed
- `#63` ledger idempotence enforced in the database rather than in Python
  (+ `#63`'s follow-up: static dedupe SQL in `0003`, no dynamic construction)
- `#64` dense site markers become dots, attribution peeks on demand — the change
  that later required **ADR-0019**
- `#65` divisions name lookup stops reading the whole global theme
- `#66` ADR-0019 · `#67` coverage semantics · `#68` marker a11y
- `#69` the `scripts/dev.sh` one-command dev stack
- `#70` auto mode + Agent Teams + three subagents · `#71` agent-ops hardening

**The incident cut the day in half, and the cut is visible in the data.** The
GitHub Actions incident opened at **15:22:49Z**. Everything before it landed:
`#61`, `#62`, `#63`, `#64`, `#65` and `#70` all merged on 08-06. Everything after
it stranded: **`#66`, `#67`, `#68`, `#69` and `#71` were created on 08-06 and did
not merge until 08-07.** No decision caused that split; infrastructure did. It
was triaged and cleared the following day (see the 2026-08-07 entry).

**A governance thread ran alongside the product work.** The 18:03 prompt asked to
"set up this project for autonomous work with auto mode and Agent Teams… step by
step", which became `#70` (auto mode, Agent Teams, the three subagents now in
`.claude/agents/`) and then `#71` (closing a Bash secret-read hole, gating agent
self-edits). The 18:09 prompt — *"can we run these changes while there is a
session running?"* — is the right question and the reason the two were split.

**`#71` was closed and later reopened.** `gh pr close 71` appears in the trail,
and at 19:43 the prompt reads *"look for the last session that created pr 71,
let resume this session, explain me why we can't merge pr 71"*, followed at 19:51
by wanting to merge it first "and then in a new session after getting claude
setting in main". **Inference:** `#71` changes `.claude/settings.json`, i.e. the
permission surface the running sessions were themselves operating under — a
chicken-and-egg where the change must land before a session can be started that
inherits it. It merged on 08-07.

**Two ratifications visible in the prompts**, both terse and both load-bearing:
*"fix the coverage bug too"* (13:57) became `#67`, and *"merge them when CI goes
green"* (17:59) is the merge-gate discipline stated as an instruction rather than
assumed.

## Decisions

Decisions this day produced ADRs or PRs; none is re-derived here, and none of the
ADRs below has been approved.

- **Attribution is co-present with its value, not permanently visible** →
  **ADR-0019** (`#66`, drafted 08-06, merged 08-07, `approved-by: _pending_`).
  Forced by `#64`: ~780 markers with permanent name+chip labels are illegible, so
  the label moved behind hover/focus. That reinterprets FR-004, which is
  merge-blocking, so it was owed a record rather than a code comment.
- **`covered` means "researched", not "contains a site"** (`#67`) — the ADR-0018
  defect made concrete, plus migration `0004_area_researched_at`.
- **Ledger idempotence belongs in the database, not in Python** (`#63`).
- **Auto mode + Agent Teams adopted**, then immediately hardened (`#70`, `#71`).

## Failures

No `FAIL-NNN` entry was filed on this date, and the event trail records **zero
tool errors**. Two things are worth noting as process:

- **A CVE reddened job 6 *after* the T069 gate check had passed** (`#62`,
  documented in `649625b`). Nothing was wrong with the code; the world moved.
  Worth remembering when a gate is treated as a durable result rather than a
  point-in-time one.
- **This entry's own absence.** Nine sessions, eleven PRs, four decisions, no
  `/devlog`. Recoverable only because the hooks captured the trail — the
  reasoning had to be inferred and some of it is now unrecoverable.

## Cost / turns

9 sessions, ~7 hours wall-clock, 1,738 captured events, 11 PRs, 13 commits.
Tool profile dominated by `Bash` (1,029) — largely `gh pr checks` / `gh api`
polling against CI, much of it during the incident window and therefore wasted.

## Exhibit-tag candidates

- `exhibit/U2-eight-worktrees` — nine concurrent sessions, eight isolated
  checkouts, eleven PRs, zero cross-session file races. The isolation rule at
  full stretch, with the event trail as evidence.
- `exhibit/U2-the-log-that-outlived-the-story` — **the strongest teaching moment
  here.** The session skipped `/devlog`; the hook trail survived and this entry
  was rebuilt from it a day later — but only the *what*, not the *why*. Argues
  the case for logging-as-you-go better than any exhortation could.
- `exhibit/U2-permission-chicken-and-egg` — `#71` changes the permission surface
  the running sessions operate under, so it must land before a session can
  inherit it.
- `exhibit/U5-gate-that-went-stale` — a passing T069 gate, then a CVE reddens
  job 6 the same day. A gate is a timestamp, not a property.
