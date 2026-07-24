# 0001 — Adopt the ramp-up standard, executed governance-first

- Status: proposed
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: — · Date: 2026-07-24

## Context and Problem Statement

The project adopts the 28-step ramp-up standard in `docs/planning/methods-ramp-up-standards.md` §7 as its bootstrap process. But the project also grew (PRD v2.0) into a multi-user platform whose design is non-trivial, and it must itself be a teaching exemplar of AI-first project management where "the repo is the agent's brain" and "documentation-as-you-build is a first-class deliverable" (syllabus U1/U2).

Running the ramp-up as originally written would produce all agent instructions, hooks, constitution, schema cards, and CI in one pass *after* the technical-design work — meaning the design docs and the discovery spike would be written with **no governance and no capture machinery in place**, throwing away exactly the course material (AGENTS.md git history, first devlog entries, tripwire catches) the project exists to harvest.

## Considered Options

- **A — Design-first, full ramp-up after.** All design docs + spike, then the whole 28-step ramp-up at once. Simplest ordering; loses capture of the design phase; not governed.
- **B — Full 28-step ramp-up now, before design.** Everything up front; but the constitution and schema cards depend on the design (data model, agent-ops/test-strategy articles), so they'd be placeholders needing rework.
- **C — Split the ramp-up: governance-first.** Bring the design-*independent* subset forward (AGENTS.md, CLAUDE.md, strict `.claude/settings.json`, logging hooks, `/adr` `/devlog` `/failure`, capture dirs) as "D0"; keep the design-*dependent* remainder (constitution, schema cards, CI wired to real test tiers, Spec Kit, evals, branch protection) for after the design docs + spike.

## Decision Outcome

Chosen: **C — split the ramp-up, governance-first**, because it is the only ordering that both (a) puts every subsequent action under permission governance and course-capture, and (b) avoids rework by not authoring design-dependent artifacts before the design exists. It is also the most faithful demonstration of the project's own U1 thesis.

### Consequences

- Good: design docs D1–D4 and the discovery spike D5 are captured (hooks → `logs/`, distilled by `/devlog`) and governed (permission baseline) from the first action; the bootstrap session is itself the first course exhibit.
- Good: the constitution can ratify the test-strategy (D3) and agent-ops (D4) rules with real content instead of placeholders.
- Accepted cost: the ramp-up is no longer a single linear session; the methods doc's "ADR-0001 = adopt the ramp-up standard" is fulfilled here in a split form, and its remaining steps are tracked to run after the design. A follow-up ADR may record the constitution reframe (PRD §13 #1).

### Confirmation

D0 acceptance checks, all met at commit `1590088`: the capture hook writes `logs/events.jsonl` for every event and exits 0 (verified by simulation); `AGENTS.md` ≤ 200 lines (72); `.claude/settings.json` is valid JSON with a Ben-approved allow/ask/deny baseline (Checkpoint B); `logs/` and `settings.local.json` are gitignored. The design-dependent remainder is tracked in the approved plan and will be confirmed by the later CI (required checks 1–5, per `docs/design/test-strategy.md`).
