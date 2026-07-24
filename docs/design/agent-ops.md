# Agent-Ops — Running Siyur as a Continuously-Evolving Agent Project

*v1.0 — 2026-07-24. Companion to `methods-ramp-up-standards.md` (the baseline) and the course syllabus. Defines how the agents, prompts, subagents, and context **keep improving** over the project's life — the discipline that makes Siyur a teaching exemplar (Goal 1). Rules marked **⟐ constitution** are proposed as constitution articles / ADRs at ramp-up.*

## Baseline (already standard — do not re-derive)

In place from D0 + the ramp-up: `AGENTS.md` (+ `CLAUDE.md` shim), strict `.claude/settings.json` permissions, disler-pattern logging hooks → `logs/events.jsonl`, `/adr` (MADR 4.0), the failure catalog with a mandatory regression eval per entry, git-cliff changelog, Spec Kit constitution-first, pytest + DeepEval + agentevals, SAST-gated CI, the 500-line diff guard, and a budget-capped `claude -p` review job. This doc is what a top-tier 2026 setup **adds** on top, focused on *evolution*.

## The five additions

### 1. Self-improvement loop (the Cherny loop) — ⟐ constitution

When a correction recurs, the rule is written **into the instructions themselves**, not just applied once.
- **Mechanism:** a `/reflect` command + a `Stop`/`SubagentStop` hook that queues recurring correction phrases from `logs/` for human-approved folding into `AGENTS.md` or a scoped `.claude/rules/<topic>.md`. Weekly retro (transition-plan rhythm) reviews the queue.
- **Split:** hard "must always" rules → **deterministic hooks** (context-length-proof); evolving conventions → `AGENTS.md`. A rule that can be mechanically checked should be a hook, not a sentence.
- *Why it matters:* converts one-off corrections into permanent behavior; the `AGENTS.md` git history ("every correction = a commit") is itself course material. **Feeds U1, U7.**

### 2. Skills & subagents as versioned, evaluated artifacts

- **Mechanism:** each Skill is `SKILL.md` (open standard, progressive disclosure) + **semver + inline CHANGELOG** + an `evals.json` (10–20 prompts: deterministic checks + negative controls). Siyur's own skills (curation-source adapter, style-guide, quarantine-stamp) follow this.
- **Regression discipline:** run a skill's evals with the skill **unloaded** to detect rotted or model-absorbed capability, and prune it. Skill/prompt changes go through PRs like code.
- **When to split a subagent:** by **context boundary** (isolation, parallel independent work, 15–20+ tool specialization), *not* by problem type — multi-agent costs ~3–10× tokens; justify it.
- *Why it matters:* skills drift silently as the base model improves; versioning + unloaded-evals keep them honest and portable. **Feeds U4.**

### 3. Agent tracing + tiered eval gates — ⟐ constitution (evals gate merges)

- **Tracing:** OpenTelemetry GenAI conventions (`gen_ai.*` spans) via self-hosted **Phoenix** (or Langfuse) capturing nested LLM/tool/subagent spans — you cannot debug a non-deterministic run from its final output alone.
- **Trajectory evals:** `agentevals` superset match on the planner node sequence (see `tech-design.md` §5.2) — a right answer via a wrong/inefficient path still fails.
- **Tiered gates with statistical significance:** smoke (20–50 cases) per PR / regression (200–500) on merge / benchmark (1000+) on release; gate on a **paired-t ±95% CI vs. baseline**, not raw score deltas. Scores → `evals/history.csv`.
- *Why it matters:* extends the harness methods §3 already mandates into an evolution-safe gate. **Feeds U3.**

### 4. Prompt & model lifecycle — ⟐ constitution (pin snapshots + judge)

- **Prompt registry:** `prompts/` with front-matter (version, model, date, linked eval score) and **label-based promotion** (`production` tag moves independently of app deploys); prompt changes are PR-reviewed.
- **Pinning:** pin **dated model snapshots** (not floating aliases) for both the app and the **judge** model + judge prompt; re-validate the judge against human labels whenever it changes.
- **Model-migration playbook:** offline **trace replay → shadow → canary %** with rollback; on a newer model, re-tune prompts and **strip stale verification/negative-example scaffolding** the new model no longer needs. Track active → retired with notice.
- *Why it matters:* concrete *now* — the session that moved to Opus 4.8 is the first real migration; it should run this playbook, not vibes. **Feeds U2, U3.**

### 5. Supply-chain & provenance governance — ⟐ constitution (data licenses + deps)

- **Slopsquatting gate:** ~20% of AI-generated code cites non-existent packages, many pre-registered by attackers. Beyond `pip-audit`: **allowlist agent-initiated installs** (`uv add` is already `ask` in D0's permissions), verify lockfile + hashes in CI, and check publisher + registration date for any new dependency. **⟐** No dependency enters `uv.lock` without this check.
- **Provenance:** SLSA Source-track signals via the `Co-Authored-By:` model trailer (machine-greppable authorship) + signed commits where available; agent branches `agent/<ticket>`.
- **Review discipline:** treat agent output as an **untrusted junior** — autonomy proportional to demonstrated reliability; review deletions first, verify new imports exist, run locally before approving (ThoughtWorks "AI-code complacency" antipattern; DORA "AI as amplifier": throughput *and* instability rise together, so process quality is the counterweight).
- *Why it matters:* the security numbers (methods §5: 45% OWASP-vulnerable samples, ~10× finding rate) make this non-negotiable. **Feeds U1, U6.**

## How this shows up in the repo

| Addition | Concrete artifact(s) | When |
|---|---|---|
| 1 Cherny loop | `.claude/commands/reflect.md`, `.claude/rules/*.md`, retro devlog entries | ramp-up + ongoing |
| 2 Versioned skills | `.claude/skills/<name>/{SKILL.md,evals.json,CHANGELOG}` | as skills appear (M1+) |
| 3 Tracing + gates | Phoenix/OTel wiring, `evals/history.csv`, significance check in `eval-quality.yml` | ramp-up (harness), M2 (tracing) |
| 4 Prompt/model lifecycle | `prompts/*.md` front-matter, `docs/adr/*-model-migration.md`, pinned judge | ramp-up + each migration |
| 5 Supply-chain | slopsquatting CI step, dep-review checklist, signed trailers | ramp-up (CI) |

## Cut rule

Any of these that isn't earning its keep by M2 is cut **by ADR**, with the reasoning captured (that reasoning is itself course material — PRD risk row "course-feed becomes overhead"). Automation-first: if an artifact isn't near-free via a hook or command, it doesn't survive.
