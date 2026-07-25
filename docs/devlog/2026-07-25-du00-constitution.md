# 2026-07-25 — DU-00 unit b: Spec Kit init + constitution ratified (Checkpoint A)

**Goal:** the second DU-00 unit — initialize GitHub Spec Kit and draft Siyur's constitution, stopping at **Checkpoint A** for Ben to approve (and to resolve the one open decision the constitution touches, PRD §13 #1) before anything is committed.

## What happened

**Synced first.** PR #8 (kickoff devlog + FAIL-002) had already merged to `main`; fast-forwarded and branched `agent/du00-constitution` off it. Read the authoritative set for this unit: delivery-plan DU-00, methods §2 + §7 steps 8–9, PRD §9 (the five articles) + §13 #1 (the reframe), and agent-ops' four ⟐ rules.

**Spec Kit init was clean.** `uvx --from git+…/spec-kit specify init --here --force --integration claude --script sh` scaffolded `.specify/` (constitution template, plan/spec/tasks/checklist templates, bash scripts, workflow registry) and installed ten `/speckit-*` skills into `.claude/skills/`. Resolved version is **0.14.3.dev0** (≥0.13 ✓). One naming surprise worth noting for later units: this build uses **hyphen** command names (`/speckit-constitution`, `invoke_separator: "-"`), not the dotted `/speckit.*` the methods doc and RAMP-UP prompt wrote — the skills are the same, the separator differs. No secrets written; `.gitignore` untouched; ignored Spec Kit's "add `.claude/` to gitignore" suggestion because we deliberately commit `.claude/` (settings, commands, skills) and none of it holds credentials.

**Drafted the constitution by following the `speckit-constitution` skill's own flow** (placeholder-fill → consistency propagation → Sync Impact Report). Landed on **7 principle articles** rather than the template's 5, so the four agent-ops ⟐ rules get ratified as real articles instead of footnotes: PRD's five (airplane-mode product / evals gate merges / decisions→ADR / failures→regression eval / mechanical provenance) plus ⟐#1 (self-improving instructions) and ⟐#4 (prompt & model lifecycle); ⟐#3 folded into Article II and ⟐#5 into Article V. Added three sections (Delivery Discipline, Development Workflow & Quality Gates, Governance).

**Checkpoint A — held the line on §13 #1.** Article I is the exact wording PRD §13 #1 reserves to Ben ("offline bundle is the product" → "the guided experience is the product; travel works in airplane mode"). Drafted it in the recommended reframed form but marked it `TODO(ARTICLE_I_REFRAME)` and did **not** self-resolve — presented both framings to Ben. Ben **ratified the reframe** and approved the draft for commit. Cleared the TODO, stamped ratification 2026-07-25, committed constitution + `.specify/` + speckit skills in one commit (28 files). Propagation check: the Spec Kit templates reference the constitution only through a generic "`[Gates determined based on constitution file]`" placeholder — nothing hardcoded to sync, so no template edits.

## Decisions

- **Constitution v1.0.0 ratified** (Ben, Checkpoint A). Recorded in the constitution's Sync Impact Report; no separate ADR — the ratified document is itself the artifact, and ADR-0001's Confirmation only anticipated a reframe ADR as optional ("may").
- **PRD §13 #1 (constitution reframe) resolved: adopt the reframe** — "the guided experience is the product; its travel mode must work in airplane mode." Ben's call, made at Checkpoint A, not by the agent.
- **7 articles, not 5** — ratify the agent-ops ⟐ rules as first-class articles so the evolution discipline is constitutionally binding, not advisory.

## Failures

- None this session. (No `/failure` filed.)

## Cost / turns

Short, single local session. ~1 spec-kit build via uvx, 4 doc reads, 1 draft + 3 small edits, 1 commit. One human checkpoint (A) with a 2-question approval. No product runtime touched; nothing to test beyond the doc/scaffold landing.

## Exhibit-tag candidates

- `exhibit/U2-constitution` — the ratified constitution as the worked artifact the delivery-plan named for this unit: how a project's *build* principles (not product scope) get written, and how the four agent-ops evolution rules become binding articles. (proposed)
- `exhibit/U2-checkpoint-a-open-decision` — the agent drafting a principle in its recommended form **but refusing to resolve the owner's open decision**, flagging it with a TODO and a two-option checkpoint instead. A clean teachable instance of "flag, don't decide." (proposed)
