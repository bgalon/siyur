<!--
SYNC IMPACT REPORT — Siyur Constitution
Version change: (template) → 1.0.0  (initial ratification)
Bump rationale: first concrete constitution; MAJOR baseline per semver for a new governing document.
Ratified-by: Ben at Checkpoint A (2026-07-25, DU-00 unit b) — Article I reframe (PRD §13 #1) approved.

Principles (7):
  I.   The guided experience is the product — travel works in airplane mode   [PRD §9(a); ratifies §13 #1 reframe — FLAGGED for Ben]
  II.  Deterministic evals gate merges                                        [PRD §9(b) + agent-ops ⟐#3 tracing/tiered gates]
  III. Every decision is an ADR                                              [PRD §9(c)]
  IV.  Every failure earns a regression eval                                 [PRD §9(d)]
  V.   Provenance is mechanical — data licenses and supply chain             [PRD §9(e) + agent-ops ⟐#5]
  VI.  The instructions improve themselves                                   [agent-ops ⟐#1 Cherny loop]
  VII. Prompts and models have a governed lifecycle                          [agent-ops ⟐#4]

Added sections: Delivery Discipline; Development Workflow & Quality Gates; Governance.

Templates status:
  ✅ .specify/templates/plan-template.md   — Constitution Check is a generic gate placeholder; no hardcoded principles to sync.
  ✅ .specify/templates/spec-template.md   — no constitution-derived mandatory sections to add for v1.0.0.
  ✅ .specify/templates/tasks-template.md  — task categories already cover test/eval/doc artifact types the principles require.
  ✅ .claude/skills/speckit-*/SKILL.md     — generic agent-neutral references; nothing to rename.

Resolved / Deferred:
  - Article I framing (PRD §13 #1 constitution reframe) — RESOLVED: Ben ratified the reframe at Checkpoint A
    (2026-07-25). No open TODO remains on Article I.
  - Branch-protection mechanical ENFORCEMENT is deferred (private free-tier repo → rulesets 403, ADR-0005);
    the PR-integration RULE holds now, enforcement lands when the platform allows.
-->

# Siyur Constitution

*Siyur (סיור — "a tour"): research any area with an LLM into a shared cited commons → plan a day tour with
Plan B/C → compile a self-contained offline bundle → travel guided with zero connectivity. This repo is also the
dogfooded case study for a GeoAI course; the way we build is a first-class deliverable.*

This constitution states the non-negotiable principles that every spec, plan, task, and PR is checked against.
It supersedes convenience. Product scope lives in `docs/planning/prd.md` (v2.0, approved) and is not re-litigated
here; this document governs *how we build*, not *what we build*.

## Core Principles

### I. The guided experience is the product — travel works in airplane mode

The product is the guided tour-day experience across its online phases (Define → Research → Plan) and its offline
Travel payoff. The offline bundle is not a lesser mode: it is the **travel guarantee** within the product, and it is
non-negotiable. Every feature that reaches the traveller MUST function with **zero connectivity and zero LLM** —
map, schematic map, itinerary, timeline, narration, per-place info, and off-route recovery all render from the
bundle alone. The **airplane-mode end-to-end eval is the release gate for every milestone**: if the bundle needs the
network for anything the traveller depends on, the milestone is not done.

> This wording ratifies the PRD §13 #1 "constitution reframe" — moving article I from v1.0's *"the offline bundle is
> the product"* to *"the guided experience is the product; its travel mode must work in airplane mode."* That
> decision was **Ben's** (PRD §13) and he **ratified the reframe at Checkpoint A (2026-07-25, DU-00 unit b)**. The
> airplane-mode release gate is unchanged by the reframe.

### II. Deterministic evals gate merges

Correctness is proven by evals, not asserted. Structural and trajectory evals are **deterministic, offline, and
merge-blocking** from the first slice: schema validity (incl. per-field provenance), geometry validity, feasibility
and budget checks, Plan B/C feasibility, license-quarantine, and bundle integrity. A right answer reached by a
wrong or inefficient path still fails (trajectory `superset` match). Gates are **tiered** — smoke per PR, regression
on merge, benchmark on release — and quality judgements gate on a **paired-t ±95% CI vs. baseline**, not raw score
deltas. Non-deterministic runs are **traced** (OpenTelemetry GenAI spans → self-hosted Phoenix) because a run cannot
be debugged from its final output alone. Scores append to `evals/history.csv`.

### III. Every decision is an ADR

Any session that chooses between libraries, schemas, or architectures ends with a MADR 4.0 minimal ADR in
`docs/adr/`, drafted by `/adr`, marking `drafted-by` / `approved-by`, and carrying a **Confirmation** clause naming
the eval or CI check that proves the decision holds. Standing decisions (AGENTS.md) and the PRD are not re-opened
without a superseding ADR. Decisions are not folded silently into code.

### IV. Every failure earns a regression eval

Every real failure becomes a `docs/failures/FAIL-NNN.md` entry via `/failure`, and **does not close until it has
added a golden-set case or a guardrail** that would catch its recurrence. No exceptions. This closes the loop
between the failure catalog and the eval harness: the test suite grows by failure-driven accretion.

### V. Provenance is mechanical — data licenses and supply chain

Provenance is enforced by machinery, not vigilance — on both data and dependencies.
- **Data:** every curated value is stamped at ingestion with `source + license + bundleable` flag; the narration and
  bundle steps **refuse unstamped input** and **refuse anything `bundleable=false`**. ODbL attribution
  ("© OpenStreetMap contributors") renders on every map and credits screen; `DATA-LICENSES.md` is the registry and
  `ATTRIBUTION.md` is regenerated per bundle. Personal data (plans, notes, preferences, identity) is per-user and
  private and is **never bundled**; secrets and `.env*` are never read or written.
- **Dependencies:** no dependency enters `uv.lock` without a slopsquatting check (publisher + registration date +
  lockfile hashes); agent-initiated installs stay on the `ask` list. Agent output is reviewed as an **untrusted
  junior** — deletions first, new imports verified to exist, run locally before approving. Autonomy on
  security-critical code (auth, crypto, input validation) is restricted.

### VI. The instructions improve themselves

When a correction recurs, the rule is written **into the instructions themselves**, not merely applied once. A
mechanically-checkable rule becomes a **deterministic hook** (context-length-proof); an evolving convention becomes a
sentence in `AGENTS.md` or a scoped `.claude/rules/<topic>.md`. The `AGENTS.md` git history — every correction a
commit — is a deliverable. Skills and prompts are versioned, evaluated artifacts and change through PRs like code.

### VII. Prompts and models have a governed lifecycle

Prompts live in `prompts/` with front-matter (version, model, date, linked eval score) and move to `production` by
label, independently of app deploys. The **app model and the judge model are pinned to dated snapshots** (never
floating aliases); the judge is re-validated against human labels whenever it changes. Model migrations follow a
playbook — offline trace-replay → shadow → canary — and strip stale scaffolding the newer model no longer needs,
tracking active → retired. Vibes do not migrate models.

## Delivery Discipline

- **Walking skeleton first, then thin vertical slices.** Every deliverable unit (DU) is demoable, carries a
  Definition of Done, and produces ≥1 course-feed artifact. Scope creep is fought structurally: Spec 001 is one
  vertical slice; the commons ships thin.
- **Genericity is a standing eval.** Nothing is hardcoded per place; MVP is demonstrated on ≥3 areas of different
  character (dense metro, small town, non-Latin-script name) including one never tested before the demo.
- **Integration is via PR to `main`** (ADR-0005): one `agent/<ticket>-<slug>` branch per unit of work, Co-Authored-By
  trailers, PR template with an Evidence section; parallel sessions use isolated worktrees, never one directory.
  Mechanical branch protection is enabled when the hosting platform allows it (deferred while the repo is
  private free-tier — ADR-0005); the PR-integration rule holds regardless.

## Development Workflow & Quality Gates

- **CI is the merge contract.** The required check set (lint+typecheck → pytest → structural+trajectory evals →
  Semgrep + gitleaks + pip-audit → ≤500-line diff guard) is merge-blocking; quality LLM-judge evals run nightly,
  threshold-gated. Given the security numbers (≈45% of AI-generated samples carry an OWASP Top-10 flaw), shipping
  agent code without SAST is not permitted.
- **Definition of Done (every unit):** EARS acceptance criteria verified · the named test tiers green ·
  structural/trajectory evals green · an ADR if a decision was made · a devlog entry · an exhibit-tag candidate
  proposed. A unit does not merge without its DoD.
- **Maturity target is spec-anchored:** specs and code evolve together and tests enforce alignment — not
  spec-as-source, not spec-then-drift. Over-specification (specs as pseudo-code) and false confidence (a wrong spec
  perfectly matched) are named anti-patterns.

## Governance

This constitution supersedes other practices where they conflict. It governs process; the PRD governs product.

- **Amendment procedure:** changes are proposed by PR, recorded as (or alongside) an ADR, and — for principle
  additions, removals, or redefinitions — approved by Ben. The Sync Impact Report at the top of this file is updated
  on every amendment, and dependent templates are re-checked for alignment.
- **Versioning (semver):** MAJOR = backward-incompatible governance/principle removal or redefinition;
  MINOR = a new principle/section or materially expanded guidance; PATCH = clarifications and wording.
- **Compliance review:** every PR and review verifies compliance; complexity must be justified against these
  principles. Runtime build guidance lives in `AGENTS.md` (+ `CLAUDE.md` shim).
- **Reserved to Ben (not decided here):** the PRD §13 open decisions (#1 constitution reframe — see Article I;
  #2 review-data policy; #3 course-scope/GCP; #4 commons write policy; #5 schematic/timeline milestone). Agents
  flag these; they do not resolve them.

**Version**: 1.0.0 | **Ratified**: 2026-07-25 | **Last Amended**: 2026-07-25
