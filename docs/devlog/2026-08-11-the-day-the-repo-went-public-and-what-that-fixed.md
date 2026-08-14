# 2026-08-11 — The day the repo went public, and the two things that fixed

**Covers: 2026-08-09 → 2026-08-11.** The decisions all landed on the 11th, so this is filed
there; 08-09 and 08-10 are covered by this entry. *(Dates written in full so `devlog_debt.py`
can read this line and stop re-flagging them — the declaration was here from the start, but the
hook matched filenames only, so it kept reporting a gap that had already been filled.)*

**Goal:** deliver Phase 5 (DU-05, the compile pipeline) as orchestrator. Ended up also going
public, licensing the repository, and enabling the branch protection ADR-0005 has wanted since
DU-00.

## The outage that looked like a code failure

Around 19:27 every workflow on every branch started failing — mine and the peer session's, all
jobs, ~2–11 seconds each. Six red checks per PR.

`AGENTS.md` says triage by duration before debugging, and duration said "not the 2026-08-06
platform incident" (those ran 45m–2h30m). But the shape was wrong for a code failure too:
**`runner_name: ""` and `steps: 0`** — the jobs were never assigned a runner. Nothing executed.
Re-running reproduced it exactly, so it was not contention.

Diagnosis: **GitHub Actions minutes exhausted** on a private free-tier repo. I could not confirm
it — the billing API has moved and returns 410 to `gh api`, and the timing endpoint reports
`0` billable ms either way, which is what a job that never ran reports. So the diagnosis went to
Ben as an inference with the evidence, not a conclusion.

It was right, and **going public proved it retroactively**: CI recovered on the next push, with
real durations (unit 1m24s) after ~13 hours of nothing.

The lesson worth keeping is narrower than "check your quota". It is that **the discriminator was
`steps: 0`, not the red X** — six red checks look identical whether the code failed or the runner
never arrived, and the logs are empty in both cases. Duration got us to "not the known incident";
the job metadata got us the rest of the way.

## Public solved a second problem nobody was trying to solve

Actions is free and unlimited for public repos, so the quota question disappeared. But
`AGENTS.md` had also recorded, since DU-00, that **branch protection was unavailable** — "this
private, free-tier, solo-dev repo" — and that ADR-0005's original intent, machine-required
checks 1–7, was blocked on repo tier.

Public unlocks it. So one visibility flip closed a CI outage *and* a governance gap that had been
open for three weeks.

That gap had stopped being theoretical: **the merge gate slipped twice in two days**, once from
each session — my #84 merged with checks pending, the peer's #92 merged with jobs 4 and 7 red.
Both of us wrote the rule down. A rule broken by both of its enforcers inside 24 hours is not a
rule, and the only reason it was self-enforced was that GitHub could not enforce it.

Protection is now live with **`enforce_admins: true`**, deliberately: the escape hatch is to
*disable the rule*, a visible act, rather than to click through a red check. It bit on its first
day — #93 sat `BLOCKED` until its checks finished instead of letting me merge.

Before flipping, a full-history audit: **339 distinct paths ever committed**, nothing
credential-shaped. And a correction to something I had told Ben earlier — job 6's gitleaks does
**not** only cover PR diffs; `fetch-depth: 0` with the comment "gitleaks scans full history" has
been scanning the whole thing on every PR and passing. The evidence for going public was better
than I had represented it.

## One repository, three licences

There was **no `LICENSE` file**. The non-obvious part is that adding one would have been a false
claim: this repo commits real third-party data — ODbL Overpass and Valhalla captures, a CC-BY-SA
Wikivoyage capture, CDLA Overture extracts. Stamping Apache-2.0 across the tree asserts a right
to relicense OpenStreetMap and Wikivoyage. There isn't one.

- **Code → Apache-2.0**, over MIT, for the patent grant — and because the project already reasons
  in Apache-2.0's terms (ADR-0012 allowlisted it; `compiler/attribution.py` implements the §4
  NOTICE mechanism).
- **Docs → CC BY 4.0, deliberately not BY-SA.** The docs are the teaching deliverable and the
  course repo consumes them; share-alike would force every derived slide to carry the same terms.
  Note the asymmetry with the *product's* narration posture, which **is** CC BY-SA because it
  adapts CC BY-SA sources and inherits the obligation. Different question, different answer, and
  `LICENSING.md` says so rather than leaving a reader to infer it.
- **Data → untouched, per file.** `LICENSING.md` states that those obligations **travel with the
  bytes**, and the same warning now opens `tests/fixtures/README.md`, where someone copying a
  fixture will actually meet it.

Both licence texts were fetched from GitHub's licences API rather than typed from memory. A wrong
licence text is worse than none.

## Two red checks, two measurement artifacts, zero overrides

This is the part I am most pleased with, because the tempting shortcut was available twice.

**#102 (licensing) reported 682 lines** — of which 598 were canonical licence text. **#93 (the
peer's ADR) reported 1005 lines against a real diff of 125** — the `BASE_SHA` drift artifact from
2026-08-07, where the guard compares a frozen base sha against a merge ref recomputed against
current `main`.

`size-override` would have been *technically true* on #102 and simply false on #93. AGENTS.md
draws exactly this line: the label records a claim, and clearing a **measurement artifact** with
it "writes 'this PR is >500 lines, override justified' into the governance trail about a PR that
isn't — fix the measurement instead" (`exhibit/U2-override-that-lies`).

So: **#93 was rebased** (the remedy Ben chose for #66), and **#102 got a measurement fix** —
`LICENSE` and `LICENSE-*` added to diff-guard's exclusions, on the guard's own stated rationale
that "anything a reviewer reads at a glance (or not at all) must not consume the budget". The
glob is anchored so it does **not** match `LICENSING.md`: the reasoning about *why* Apache-2.0
over MIT is authored prose and must keep costing budget. Verified by simulation before shipping —
682 → 84, with `LICENSING.md`'s 61 lines still counted.

## Phase 5, and twelve mutations that all landed

Four compiler modules (`quarantine`, `manifest`, `storage`, `attribution`), each with tests, each
in its own PR. **Twelve mutations run across the four, twelve kills, none needing an added case.**

That is a change from 08-08, where a careful mutation *analysis* was wrong on one of four because
a second guard masked the break. The difference was making "**run** the mutation" an explicit
instruction rather than "reason about it" — and asking each agent to report which it actually did.

Three findings worth more than the code:

- **`manifest.py` wrote no canonicalizer.** Three already existed — `commons/models.py`'s seal
  over the pinned `rfc8785`, and `web/src/bundle/manifest.ts` over `canonicalize@3.0.0`. It
  matched them and proved it: 1,607 bytes both sides, byte-identical, same digest. A second
  implementation of one standard is the divergence RFC 8785 was adopted to prevent.
- **The seal covers *which keys exist*.** An M1 manifest carries `"schematic": null`;
  `exclude_none=True` drops it and changes the digest, so the bundle reports itself corrupt on the
  device, offline, with no other symptom. `manifest_json_bytes()` is now the single sanctioned
  path, and a test pins that the tidier alternative fails.
- **`attribution.py` found its own bug on re-read**: credits grouped on the whole `SourceRef`, and
  a stamp's `id` is per record — 780 places rendered **780 near-identical credit lines**, burying
  the obligation text that actually discharges ODbL. Grouped on `(kind, licence, attribution)`
  they render 5.

## Decisions

- **Go public**, after a history audit. Reversible as a setting, one-way for anything copied —
  and with zero forks at the moment of flipping, the exposure window started clean.
- **Apache-2.0 / CC BY 4.0 / third-party data untouched**, with `LICENSING.md` explaining why a
  single licence would have been dishonest.
- **`enforce_admins: true`**, `strict: false`, no required approvals (ADR-0005's solo-dev model).
- **Split Phase 5 by module** into four PRs. It cost four CI matrices where one would have done —
  a real trade against quota — but it also meant **#95 got through the window before CI died**,
  where a combined PR would have stranded all of Phase 5. The trade was not one-directional.
- **Schedule the owed review as a cloud routine** rather than drop it (03:15, one-shot).

## Failures

**Three modules merged unreviewed.** Both `code-reviewer` agents hit a session limit mid-task, and
`manifest.py`, `storage.py` and `attribution.py` merged without the pass CLAUDE.md mandates. Ben
asked for everything merged and I did it — but 08-07's review found a blocking bug fourteen tests
had missed, so this is a real gap, not a formality. Mitigated by scheduling a cloud review; not
closed until that lands.

**Two of my briefs were wrong, and the agents caught both:**

1. I briefed two `code-reviewer` agents to use `git show` and branch names. That agent type has
   **Read/Grep/Glob and no Bash**. Both stalled; one burned its remaining budget probing for
   worktree paths and died without reviewing anything.
2. I told an agent to confirm `git diff <file>` was empty after mutation testing. The file was
   **untracked**, so `git diff` shows nothing regardless of content — my verification would have
   silently accepted a left-behind mutation. It used a `cp`'d backup and told me why.

Both are the same shape: **an instruction that looks like a check but cannot fail.** That is the
thing this project keeps rediscovering, and this time I was the one writing it.

## Cost / turns

One session across three days, ~14 subagents. Nine PRs merged, `main` green at 1135 tests. Repo
public, licensed, protected.

## Exhibit-tag candidates

- `exhibit/U2-the-quota-that-looked-like-a-code-failure` — **the strongest.** Six red checks per
  PR, empty logs, reproducing on rerun. The discriminator was not the logs or the duration but
  `runner_name: ""` / `steps: 0` in the job metadata: the jobs never ran. Teaches reading the
  shape of a failure before its content.
- `exhibit/U2-two-artifacts-zero-overrides` — two red size checks in one day, one technically
  true and one simply false, both cleared by *fixing the measurement* rather than labelling past
  it. The counterpart to `exhibit/U2-override-that-lies`.
- `exhibit/U5-one-repo-three-licences` — why a repository that commits ODbL and CC-BY-SA data
  cannot carry a single licence, and what the honest alternative looks like.
- `exhibit/U2-the-gate-that-finally-bit` — a rule broken by both of the sessions that wrote it,
  made mechanical the same week, blocking a merge on day one.
- `exhibit/U3-the-brief-that-assumed-a-tool` — two instructions that looked like checks and could
  not fail, both caught by the agents receiving them rather than the one writing them.
