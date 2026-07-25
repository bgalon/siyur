# Ramp-Up Standards for an AI-First Geospatial Project (Siyur)

**State of the art as of July 2026.**
Scope: repo bootstrap through first working slice, for a solo experienced developer building an
LLM-embedded tour-day map app ("Siyur": plan online with an LLM, compile to an offline map bundle)
with Claude Code (cloud + local sessions), where the build itself is documented to feed a training course.

## Table of Contents

1. **[Repo bootstrap standards for agentic development](#1-repo-bootstrap-standards-for-agentic-development)** — AGENTS.md/CLAUDE.md layout, `.claude/` conventions, permissions & sandbox for mixed cloud+local, git/worktree/PR conventions.
2. **[Spec-driven ramp-up](#2-spec-driven-ramp-up)** — GitHub Spec Kit 0.13.x, constitution.md, interview-to-spec, first-spec structure, right-sized ceremony for solo+agent.
3. **[Eval harness from day one](#3-eval-harness-from-day-one)** — post-promptfoo tool landscape, pytest+DeepEval+agentevals, golden-dataset bootstrapping, LangGraph 1.2.x structure and checkpointing.
4. **[Documentation-as-you-build with agents](#4-documentation-as-you-build-with-agents)** — MADR 4.0 ADRs, hook-driven session logs / dev diary, changelog automation, failure catalog, prompt versioning. (Course-critical.)
5. **[CI/CD for agentic repos](#5-cicd-for-agentic-repos)** — headless `claude -p` in Actions, eval gating, SAST/secrets hygiene for agent-generated code (the 45% problem).
6. **[Geo-specific ramp-up items](#6-geo-specific-ramp-up-items)** — version pinning vs stale-API generation, data dictionary/schema cards, data-license registry (ODbL!).
7. **[Consolidated ramp-up checklist](#7-consolidated-ramp-up-checklist)** — 28 ordered steps, `git init` → first eval passing in CI.

---

## 1. Repo bootstrap standards for agentic development

### Leading references
- **"AGENTS.md vs CLAUDE.md: The Definitive Guide"** — Blink, blog, May 8 2026 — https://blink.new/blog/agents-md-vs-claude-md
- **"AGENTS.md Spec (2026): Recommended Sections + AGENTS.md vs CLAUDE.md"** — MorphLLM, guide, 2026 — https://www.morphllm.com/agents-md-guide
- **"Claude Code Permissions: Allow Lists, Deny Rules, and Sandboxing Explained"** — Claude Directory, guide, May 15 2026 — https://www.claudedirectory.org/blog/claude-code-permissions-guide
- **"Git Workflow for AI-Assisted Development 2026"** — BuildMVPFast, blog, Jun 9 2026 — https://www.buildmvpfast.com/blog/git-workflow-ai-assisted-development-agent-commits-2026
- **"Best Claude Code Templates and Starter Kits (2026)"** — Iwo Szapar, roundup, Jun 1 2026 (updated Jul 21 2026) — https://www.iwoszapar.com/p/best-claude-code-templates
- **claude-code-hooks-mastery** — disler, GitHub template repo (all 13 hook events, UV single-file hook scripts) — https://github.com/disler/claude-code-hooks-mastery
- **"Agent Skills: From Claude to Open Standard to Your Daily Coding Workflow"** — Laurent Kempé, blog, Jan 27 2026 — https://laurentkempe.com/2026/01/27/Agent-Skills-From-Claude-to-Open-Standard/
- **my-claude-code-setup** — centminmod, GitHub starter template (CLAUDE.md memory-bank system) — https://github.com/centminmod/my-claude-code-setup

### Takeaways

**Instruction files — the 2026 consensus is settled:**
- `AGENTS.md` is the primary, tool-neutral instruction file; `CLAUDE.md` is a one-line shim containing `@AGENTS.md` (Claude Code's import syntax) plus any Claude-specific rules.
- Symlinking works on Unix, but the import shim is the pattern Anthropic's own docs recommend, and it survives Windows and cloud checkouts.
- Keep the combined file under ~200 lines; link out to deeper docs (progressive disclosure) instead of inlining architecture essays.
- Recommended sections: project map, build/test/run commands, conventions, architecture pointers, "read these docs first" links.
- Monorepo nesting: a nested `AGENTS.md` per package (e.g. `packages/compiler/AGENTS.md`) overrides/extends the root for work in that subtree. For Siyur (planner service, bundle compiler, mobile shell) that means one root file + one per package as they appear.
- Personal vs shared: `CLAUDE.local.md` still works but the current preference is a home-directory import (`@~/.claude/siyur-personal.md`) because repo-local personal files do not follow git worktrees.

**`.claude/` directory convention (2026):**
- `settings.json` — shared permissions + hooks config, committed.
- `settings.local.json` — personal overrides, gitignored.
- `commands/` — slash commands (markdown, `allowed-tools` frontmatter grants narrow per-command permissions).
- `skills/<name>/SKILL.md` — Agent Skills; now an open standard adopted by 16+ tools, so skills written for the course are portable beyond Claude Code.
- `agents/` — subagent definitions with restricted tool sets.
- `hooks/` — lifecycle scripts (see §4 for the logging stack).
- Template repos worth mining: disler/claude-code-hooks-mastery (hooks/logging), Anthropic's official examples repo, centminmod's memory-bank setup. Commercial "mega-templates" (400+ plugins) are overkill for a solo greenfield build.

**Permissions & sandbox for a mixed cloud+local workflow (Claude Directory guide):**
- Structure: `allow` / `ask` / `deny` arrays + `defaultMode`. Rule syntax `Tool(matcher)`.
- Gotcha: `Bash(git status)` allows only the bare command — you almost always want `Bash(git status:*)`.
- Layers merge in priority order (enterprise → user `~/.claude/settings.json` → project `.claude/settings.json` → local); **deny always wins** across layers.
- Baseline worth committing:

```jsonc
{
  "permissions": {
    "allow": [
      "Read(./**)", "Edit(./src/**)", "Edit(./tests/**)", "Edit(./evals/**)", "Edit(./docs/**)",
      "Bash(git add:*)", "Bash(git commit:*)", "Bash(git diff:*)", "Bash(git status:*)", "Bash(git log:*)",
      "Bash(uv run:*)", "Bash(pytest:*)", "Bash(ruff:*)", "Bash(mypy:*)"
    ],
    "ask": ["Bash(git push:*)", "Bash(uv add:*)", "WebFetch"],
    "deny": [
      "Bash(sudo:*)", "Bash(rm -rf:*)", "Bash(curl:*)", "Bash(wget:*)",
      "Read(./.env*)", "Read(./secrets/**)", "Edit(./.github/workflows/**)"
    ],
    "defaultMode": "default"
  }
}
```

- macOS adds OS-level bash sandboxing (workspace-scoped writes, outbound network blocked unless approved); Linux/cloud rely on the allow/deny rules plus the cloud sandbox.
- Mixed cloud+local rule of thumb: the *shared* settings must be strict enough to run unattended in a cloud session; loosen only in `settings.local.json` on your own machine.
- `PreToolUse` hooks give dynamic policy: exit code 0 = allow, 2 = block — use for "block commits containing TODO", "block edits to generated files".

**Git conventions for agent work (BuildMVPFast + worktree guides):**
- Branch per agent task: `agent/<ticket>-<short-desc>` — tells the reviewer it is machine-generated before opening the diff, and enables targeted branch-protection rules.
- `git worktree` is the 2026 standard for parallel agent sessions: one worktree per concurrent Claude session, no branch collisions or file locks.
- Commit hygiene: human-readable subject describing the change + `Co-Authored-By:` trailer naming the model — keeps the log clean and provenance machine-greppable (`git log --grep="Co-Authored-By"`).
- CI-enforced max diff size (~500 changed lines) forces the agent to scope changes tightly.
- Review discipline: treat the agent "like an untrusted junior" — review deletions first, verify new imports exist, run locally before approving; cross-model review (one model writes, another reviews) catches self-consistent errors.
- PR template must require **evidence**: command transcript of tests/evals passing, screenshots or artifacts, link to the session log (§4), linked ADRs.

### RECOMMENDED STANDARD for Siyur
Bootstrap with: root `AGENTS.md` (≤200 lines: project map, commands, conventions, geo-version cheat-sheet from §6) + `CLAUDE.md` shim containing `@AGENTS.md`; per-package `AGENTS.md` as packages appear.
Commit `.claude/settings.json` with the strict baseline above (safe for unattended cloud runs); keep personal loosening in gitignored `settings.local.json`.
Adopt `.claude/{commands,skills,agents,hooks}` from day one, starting with the §4 logging hooks, cloning patterns from disler/claude-code-hooks-mastery rather than a heavyweight commercial template.
Git: `agent/<ticket>-<desc>` branches, worktrees for parallel sessions, Co-Authored-By trailers, PR template with an "Evidence" section.
Rationale: maximal portability and minimal lock-in; every piece is itself teachable course material; the strict-shared/loose-local split is the only configuration that is simultaneously safe in cloud and ergonomic locally.

---

## 2. Spec-driven ramp-up

### Leading references
- **GitHub Spec Kit** — GitHub (OSS), toolkit — **v0.13.0, Jul 17 2026**; 195 releases; 30+ agent integrations incl. Claude Code — https://github.com/github/spec-kit
- **"Spec-Driven Development in 2026: What It Is, the Tooling, and How Teams Actually Use It"** — krlz, DEV Community, Jun 19 2026 — https://dev.to/krlz/spec-driven-development-in-2026-what-it-is-the-tooling-and-how-teams-actually-use-it-2fk2
- **"Diving Into Spec-Driven Development With GitHub Spec Kit"** — Microsoft for Developers, blog — https://developer.microsoft.com/blog/spec-driven-development-spec-kit/
- **"Spec-Driven Development with Coding Agents"** — DeepLearning.AI (with GitHub), course — https://learn.deeplearning.ai/courses/spec-driven-development-with-coding-agents/
- **"constitution.md in Spec-Driven Development"** — Quality With Millan, blog — https://qualitywithmillan.github.io/blog/ai/constitution-md-spec-driven-development.html
- **"9 Best AI Tools for Spec-Driven Development in 2026: Kiro, BMAD, GSD…"** — MarkTechPost, roundup, May 8 2026 — https://www.marktechpost.com/2026/05/08/9-best-ai-tools-for-spec-driven-development-in-2026-kiro-bmad-gsd-and-more-compare/

### Takeaways

**Spec Kit is the de-facto open reference implementation:**
- `specify init` scaffolds `.specify/` (with `memory/constitution.md`, templates, project-local overrides) and installs the slash-command set.
- Core pipeline: `/speckit.constitution` → `/speckit.specify` → `/speckit.clarify` → `/speckit.plan` → `/speckit.tasks` → `/speckit.implement`.
- Quality extras: `/speckit.analyze` (cross-artifact consistency), `/speckit.checklist`, `/speckit.taskstoissues` (tasks → GitHub issues), and `/speckit.converge` (assess an existing codebase against its specs — new in 2026, useful once Siyur has drifted).
- Specs live in numbered feature dirs (`specs/001-<feature>/spec.md|plan.md|tasks.md`), giving the course a clean exhibit trail.

**The constitution is the highest-leverage first artifact:**
- ~9 short immutable articles of project principle, checked by every downstream generation step.
- Siyur candidates: "offline bundle is the product — every feature must work in airplane mode"; "deterministic evals gate merges"; "every decision produces an ADR"; "every failure produces a catalog entry + regression eval"; "data licenses are tracked and attributed".

**Maturity: target "spec-anchored", not "spec-as-source" (2026 consensus):**
- Spec-first (specs seed generation, then drift) — fine for prototypes.
- **Spec-anchored (specs and code evolve together, tests enforce alignment) — "the sweet spot for most production systems".**
- Spec-as-source (humans edit only specs) — still aspirational; Thoughtworks keeps SDD in "Assess", warning code remains the source of truth.
- Anti-patterns called out: over-specification ("when specs become pseudo-code, you've written the program twice") and false confidence (a wrong spec, perfectly matched, satisfies nothing).

**Interview-to-spec is the standard greenfield entry move:**
- You do not write the first spec — the agent interviews you. `/speckit.specify` + `/speckit.clarify` run structured questioning, mark `[NEEDS CLARIFICATION]`, and iterate until none remain.
- Acceptance criteria in **EARS syntax** (Easy Approach to Requirements Syntax) — unambiguous to humans and models alike. Siyur examples:
  - "WHEN the user requests a tour day for a city, THE SYSTEM SHALL return an itinerary whose total walking distance is ≤ the user's stated limit."
  - "WHILE the device is offline, THE SYSTEM SHALL render the full day plan, map tiles, and POI details from the local bundle."
- First spec = one end-to-end **vertical slice**, never the whole product.
- Tasks cite their spec clauses for traceability; never skip from spec to code — review plan, then tasks, then implement.

**Right-sized ceremony for solo dev + agent:**
- Full SDD is recommended precisely for AI-assisted, integration-heavy work (Siyur qualifies); skip it only for throwaway prototypes.
- Solo trim: constitution once + spec/plan/tasks per feature-sized unit; defer `/speckit.checklist` and `/speckit.taskstoissues` until collaborators exist.
- Lightweight alternative for indie devs: OpenSpec. Guiding quote: "the value is the thinking you do while writing the spec, not the tooling around it" (Brandon Kindred, 2026).
- **Definition of done lives in the spec.** Per task: EARS criteria verified + tests green + eval suite green + ADR written if a decision was made + session logged.

### RECOMMENDED STANDARD for Siyur
Run `specify init` (Spec Kit ≥0.13.0, Claude Code agent) on day one.
Write the constitution first (~1 hour, human-authored, agent-polished) with the five Siyur articles above.
First spec = **Spec 001: "Plan one tour day and compile it to an offline bundle"** — produced by an interview session, criteria in EARS, DoD including "structural eval suite passes in CI".
Use spec→plan→tasks→implement with hand review between each step; stay at spec-anchored maturity.
Rationale: Spec Kit is agent-portable, actively released (Jul 2026), and its numbered artifacts double as course exhibits; spec-anchored discipline gives an agent-verifiable contract without waterfall weight.

---

## 3. Eval harness from day one

### Leading references
- **"Top 5 AI Agent Eval Tools After Promptfoo's Exit"** — The Daily Agent, DEV Community, Mar 15 2026 — https://dev.to/thedailyagent/top-5-ai-agent-eval-tools-after-promptfoos-exit-576i
- **"OpenAI acquires Promptfoo to secure its AI agents"** — TechCrunch, news, Mar 9 2026 — https://www.techcrunch.com/2026/03/09/openai-acquires-promptfoo-to-secure-its-ai-agents/
- **agentevals** — LangChain AI, OSS library (trajectory evaluators) — https://github.com/langchain-ai/agentevals ; companion **openevals** — https://pypi.org/project/openevals/
- **"How to evaluate your agent with trajectory evaluations"** — LangChain docs — https://docs.langchain.com/langsmith/trajectory-evals
- **"Golden dataset evaluation: build and maintain LLM test sets"** — Langfuse, engineering guide — https://langfuse.com/resources/engineering/golden-dataset-evaluation
- **LangGraph** — LangChain AI, framework — **v1.2.4 (Jun 2 2026); 1.0 GA Oct 17 2025** — https://pypi.org/project/langgraph/ ; scaffold: **new-langgraph-project** template — https://deepwiki.com/langchain-ai/new-langgraph-project
- **"LLM Eval Tools Compared 2026"** — benchmarkingagents.com, comparison — https://benchmarkingagents.com/tools-compared/

### Takeaways

**The post-promptfoo tool landscape (for a solo dev):**
- Promptfoo was acquired by OpenAI on Mar 9 2026 for $86M; the OSS repo lives on, but vendor-neutrality is in doubt and new-project momentum has moved to pytest-based stacks.
- **DeepEval** — best for individual developers: pytest-native ("you write eval tests exactly like unit tests"), 50+ metrics incl. 6 agent-specific, free/OSS.
- **Braintrust** — best CI/CD quality gates that block deploys on eval thresholds; $249/mo Pro — overkill solo.
- **Arize Phoenix** — best self-hosted/offline tracing+eval; OTel-based, free, no feature restrictions.
- **LangSmith** — $39/seat; best-in-class step-level scoring *only if* you're on LangGraph and want SaaS.
- **Opik** — budget OSS alternative (Apache 2.0).

**Deterministic validators first, LLM-judges second:**
- Siyur's outputs are *structured* (itinerary JSON, GeoJSON, bundle manifest) — most day-one evals are plain asserts: pydantic schema validation; `shapely.is_valid` on geometries; all POIs within the city bounding box; route time budget ≤ day length; opening-hours conflicts = 0; bundle renders with network disabled.
- These run offline in CI at zero token cost and are merge-blocking from the first slice.
- Reserve LLM-as-judge for plan *quality* (sensible ordering, interest match, pacing); pin the judge model version and prompt in the repo.

**Golden-dataset bootstrapping (Langfuse guide):**
- Start with 20–50 hand-curated input→expected pairs covering the core flow + known edge cases; version them *in the repo* (`evals/golden/*.json`).
- Grow by failure-driven accretion: every real failure (§4 catalog) adds a case; add synthetic variations around each failure.
- Review quarterly for drift; expected values are structural properties, not exact text.
- Siyur seed set: ~25 tour-day requests across cities × interests × constraints ("no walking >6 km", "museum closed Mondays", "kids in tow", "rainy day").

**Trajectory checks for LangGraph agents:**
- `agentevals` ships ready-made trajectory-match evaluators — `strict` / `unordered` / `subset` / `superset` — plus a trajectory LLM-judge.
- Assert the planner calls `geocode → find_pois → optimize_route → compile_bundle` in an acceptable order without hard-coding exact sequences; `superset` match is the pragmatic default.
- Runs inside pytest; no SaaS dependency.

**LangGraph 1.2.x project structure:**
- Scaffold from `new-langgraph-project` (or `langgraph new`): `langgraph.json` at repo root (graph entrypoints, env, deps), `src/agent/graph.py`, tests; `langgraph dev` gives the local Studio loop.
- Checkpointing via `langgraph-checkpoint` savers: `InMemorySaver` in tests → `SqliteSaver` local dev → `PostgresSaver` production.
- Thread-scoped checkpoints give resumable planning sessions and double as replay/debug records for evals and the devlog.
- Pin `langgraph~=1.2` (note: 1.2.3 and 1.1.7 were yanked for regressions — pin to a known-good patch).

### RECOMMENDED STANDARD for Siyur
**pytest + DeepEval + agentevals, all offline/CI-runnable; self-hosted Phoenix for traces when needed; no eval SaaS.**
Layout:
```
evals/
  golden/            # versioned dataset, start ~25 cases
  test_structural.py # deterministic: schema, geometry, constraints — merge-blocking
  test_trajectory.py # agentevals superset-match on tool sequence — merge-blocking
  test_quality.py    # DeepEval LLM-judge — nightly, threshold-gated
  history.csv        # score trend, appended by CI
```
Rationale: pytest-native evals are reviewable by the same agent workflow as product code, cost nothing deterministic in CI, avoid betting on promptfoo's post-acquisition roadmap, and the harness itself becomes a course module.

---

## 4. Documentation-as-you-build with agents

*(Course-critical: the build's documentation is a primary deliverable, not a by-product.)*

### Leading references
- **MADR** — adr.github.io, standard — **v4.0.0, Sep 17 2025** (adds `Confirmation` section, renames to `Decision Maker(s)`, adds minimal/bare variants) — https://adr.github.io/madr/ ; releases: https://github.com/adr/madr/releases
- **claude-code-hooks-mastery** — disler, GitHub repo — all 13 hook events, JSON logging of every tool call, `PreCompact` transcript backup, per-session metadata in `.claude/data/sessions/` — https://github.com/disler/claude-code-hooks-mastery
- **"Automate Your AI Workflows with Claude Code Hooks"** — GitButler, blog — https://blog.gitbutler.com/automate-your-ai-workflows-with-claude-code-hooks
- **git-cliff** — orhun, OSS changelog generator (conventional commits → CHANGELOG) — https://git-cliff.org/
- **"The AI Agent Postmortem Template I Use"** — Dhiraj Das, blog — https://www.dhirajdas.dev/blog/ai-agent-postmortem-template
- **"How Coding Agents Fail Their Users: A Large-Scale Analysis of Developer-Agent Misalignment in 20,574 Real-World Sessions"** — arXiv, paper, 2026 — https://arxiv.org/html/2605.29442v1
- **"9 Critical Failure Patterns of Coding Agents"** — Columbia DAPLab, article, Jan 8 2026 — https://daplab.cs.columbia.edu/general/2026/01/08/9-critical-failure-patterns-of-coding-agents.html
- **"API Docs for AI Agents: llms.txt Guide"** — Fern, guide, May 2026 — https://buildwithfern.com/post/optimizing-api-docs-ai-agents-llms-txt-guide

### Takeaways

**ADRs — MADR 4.0 is the current standard:**
- `docs/adr/NNNN-title.md`, minimal variant skeleton:
```markdown
# NNNN — <decision title>
Status: accepted | Decision Maker(s): Ben | drafted-by: claude-code
## Context and Problem Statement
## Considered Options
## Decision Outcome
### Consequences
### Confirmation   <!-- how we verify the decision is implemented/holding -->
```
- The new **Confirmation** section is exactly the evidence hook a course needs ("confirmed by eval X / CI job Y").
- **Agent-generated ADRs are the emerging practice**: a `/adr` slash command drafts the ADR from the live session context — the options actually considered, the benchmarks actually run — and the human edits and commits.
- Trigger rule: any session where the agent chose between libraries, schemas, or architectures ends with "draft an ADR for the decision we just made." Mark provenance (`drafted-by` / `approved-by`).

**Session logging → dev diary (the least-standardized area; assembling it is itself novel course content):**
- Claude Code exposes 13 hook events; the disler repo demonstrates the full logging stack:

| Hook | Documentation use |
|---|---|
| `SessionStart` / `SessionEnd` | bracket each session; log source (startup/resume/clear) and end reason |
| `PostToolUse` / `PostToolUseFailure` | append JSON records of every tool call and error to `logs/` |
| `PreCompact` | back up the full transcript before context compaction (otherwise raw history is lost) |
| `Stop` / `SubagentStop` | trigger end-of-turn summarization |
| `UserPromptSubmit` | log prompt history per session (`.claude/data/sessions/<id>.json`) |

- Best available pipeline: hooks capture raw JSONL (gitignored) → a `SessionEnd`-triggered or nightly `claude -p` job distills each session into a committed `docs/devlog/YYYY-MM-DD-<slug>.md`: goal, what happened, decisions (→ ADR links), failures (→ catalog links), cost/turns.
- Public examples are hook demos, not end-to-end diary workflows — no one has packaged "build in public with an agent" documentation; Siyur doing so is differentiated course material.

**Changelog automation:**
- Conventional commits from day one (commitlint pre-commit hook, or a `PreToolUse` hook validating `git commit` messages).
- **git-cliff** generates `CHANGELOG.md` from commit history via `cliff.toml`; agent-friendly, no npm/release-please machinery needed for a solo Python project; output in Keep-a-Changelog format.

**Failure catalog / postmortem-as-you-go:**
- Research base now exists — the 20,574-session misalignment study and DAPLab's 9 failure patterns (ignoring constraints, hallucinated APIs, test-gaming, etc.) — but repo-level practice is DIY.
- Adopt `docs/failures/FAIL-NNN.md` using a trimmed agent-postmortem template:
  symptom → trajectory excerpt → root cause (prompt gap / stale-API knowledge / spec ambiguity / tool gap) → fix → **regression eval added: link**.
- Rule: every entry must add a golden-dataset case or a guardrail — closing the loop between §4 and §3.

**Prompts, eval history, and agent-readable docs are versioned artifacts:**
- `prompts/` — one file per production prompt with front-matter (version, model, date, linked eval score); prompt changes go through PRs like code.
- `evals/history.csv` — CI appends eval-run summaries so score trends are diffable.
- Root **`llms.txt`** (2026 docs-as-code staple) indexes the docs an agent should read first — yours and your students'.

### RECOMMENDED STANDARD for Siyur
Adopt a **four-artifact documentation contract**, enforced by the constitution and mostly agent-authored:
1. **ADRs** — MADR 4.0 minimal, drafted by a `/adr` command at the end of any decision-bearing session.
2. **Devlog** — hook-captured JSONL (SessionStart / PostToolUse / PostToolUseFailure / PreCompact / SessionEnd, per disler) distilled by a `claude -p` summarizer into committed `docs/devlog/` entries.
3. **Failure catalog** — `docs/failures/FAIL-NNN.md`, each entry required to add a regression eval.
4. **Changelog** — conventional commits + git-cliff.
Plus versioned `prompts/` and a root `llms.txt`.
Rationale: this captures the exact raw material of the course — decisions, narrative, failures, releases — as a side effect of building rather than as separate writing work; and because nobody has published this pipeline end-to-end, it is the course's most differentiated content.

---

## 5. CI/CD for agentic repos

### Leading references
- **"Claude Code in CI/CD and Headless Automation"** — hidekazu-konishi.com, guide, Jun 7 2026 — https://hidekazu-konishi.com/entry/claude_code_cicd_and_headless_automation.html
- **claude-code-action@v1** — Anthropic, GitHub Action — https://github.com/anthropics/claude-code-action
- **"Vibe Coding's Security Debt: The AI-Generated CVE Surge"** — Cloud Security Alliance AI Safety Initiative, research note, Apr 4 2026 — https://labs.cloudsecurityalliance.org/research/csa-research-note-ai-generated-code-vulnerability-surge-2026/
- **"Spring 2026 GenAI Code Security Update"** — Veracode, report, 2026 — https://www.veracode.com/blog/spring-2026-genai-code-security/
- **"2025 GenAI Code Security Report"** — Veracode, report (Jul 2025) — https://www.veracode.com/resources/analyst-reports/2025-genai-code-security-report/ (the BusinessWire press-release mirror is bot-gated; link the primary report)

### Takeaways

**Headless `claude -p` pattern:**
- `claude -p "<task>" --output-format json --max-turns N --max-budget-usd X --allowedTools "Read,Grep,Glob" --permission-mode dontAsk`
- Read-only tool surface for review/triage jobs; JSON (or `stream-json`) output parsed by the pipeline; hard caps on turns, budget, and wall-clock.
- Core rule: *"the agent either runs the action automatically or is blocked — decide in advance which."* Humans gate irreversible actions (push, deploy); deterministic checks validate agent output before merge.
- Use `anthropics/claude-code-action@v1` for PR-review and issue-triage jobs; route mechanical tasks to cheaper models (`--model sonnet`/haiku-class).

**Eval gating in CI:**
- Structural + trajectory evals (deterministic, token-free) run as **required status checks** on every PR.
- LLM-judge quality evals run nightly and on `release/*`, gated on threshold (mean quality ≥ baseline − ε), scores appended to `evals/history.csv`.
- Cache LLM calls; fail the job loudly on budget overrun rather than silently truncating.

**The security numbers make SAST non-negotiable (CSA research note, Apr 2026):**
- 45% of AI-generated samples introduce OWASP Top-10 vulnerabilities (Veracode, 100+ models — pass rates *flat* across 2025–26 despite vendor claims).
- 86% of samples vulnerable to XSS; 88% to log injection.
- AI-assisted developers commit 3–4× faster but introduce security findings at ~10× the rate; privilege-escalation paths +322%.
- ~20% of generated samples reference non-existent packages ("slopsquatting"); 74 confirmed agent-attributable CVEs tracked by Georgia Tech's Vibe Security Radar by Mar 2026.
- CSA mitigations: SAST + dependency scanning + secret detection in CI; restrict agent autonomy on security-critical code (authn, crypto, input validation); SBOM with AI provenance.

**Secrets hygiene with agents:**
- Agents read everything they are allowed to: deny-list `.env*` / `secrets/**` in `.claude/settings.json` (§1).
- Run **gitleaks** in CI *and* as a pre-commit hook; verify with a planted dummy key.
- Real keys live only in GitHub Environments / local keychain; CI agent jobs get a scoped, low-privilege API key.

### RECOMMENDED STANDARD for Siyur
One `ci.yml` with ordered jobs:

| Job | Contents | Gate |
|---|---|---|
| lint+typecheck | ruff, mypy/pyright | required |
| test | pytest unit/integration | required |
| eval-structural | deterministic evals + agentevals trajectory | **required** |
| security | Semgrep (SAST) + gitleaks + pip-audit | **required** |
| diff-guard | fail >500 changed lines without `size-override` label | required |

Plus `eval-quality.yml` (nightly DeepEval judges, threshold-gated, appends history) and `claude-review.yml` (claude-code-action@v1, read-only tools, `--max-turns 12`, budget-capped) posting a PR review comment.
Rationale: every merge is provably lint-clean, security-scanned, and eval-green with zero human vigilance — the precondition for letting an agent do most of the committing; given the 45%/10× findings, shipping agent code without SAST is professional malpractice in 2026.

---

## 6. Geo-specific ramp-up items

### Leading references
- **Shapely** — PyPI — **2.1.2** (Sep 24 2025; Python ≥3.10, GEOS ≥3.9) — https://pypi.org/project/shapely/ ; 2.x migration: https://shapely.readthedocs.io/en/latest/release/2.x.html
- **h3-py** — Uber, PyPI — **4.5.0** (May 30 2026; Python ≥3.10) — https://pypi.org/project/h3/
- **OSMnx** — Geoff Boeing, PyPI — **2.1.0** (Feb 16 2026; Python ≥3.11) — https://pypi.org/project/osmnx/
- **GeoPandas** — changelog — **1.1.x** (1.1.4 current stable line) — https://geopandas.org/en/stable/docs/changelog.html
- **REUSE Specification v3.3** — FSFE, licensing standard (`LICENSES/` dir + SPDX headers / `REUSE.toml`) — https://reuse.software/spec-3.3/
- **"Reuser's Guide to Open Data Licensing"** — Open Data Institute, guide — https://theodi.org/insights/guides/reusers-guide-to-open-data-licensing/

### Takeaways

**Stale-API generation is the #1 geo-specific agent failure mode:**
- All four core libraries crossed breaking major versions recently, and models still emit the old APIs from training data:

| Library | Now | Trap the LLM falls into |
|---|---|---|
| Shapely | 2.1.2 | 1.x idioms: mutability, `object.type`, `cascaded_union`; 2.x is vectorized, use `unary_union`, `.geom_type` |
| h3-py | 4.5.0 | **entire API renamed in v4**: `geo_to_h3`→`latlng_to_cell`, `h3_to_geo`→`cell_to_latlng`, `k_ring`→`grid_disk` |
| OSMnx | 2.1.0 | 2.0 (Nov 2024) was a breaking rewrite; 1.x module paths/kwargs (`ox.graph_from_place` args, `utils_graph`) are stale |
| GeoPandas | 1.1.4 | 0.x-era `.unary_union`, deprecated I/O engines; 1.x defaults to pyogrio/shapely-2 |

- Countermeasures: (a) **pin** in `pyproject.toml` (`shapely~=2.1`, `h3~=4.5`, `osmnx~=2.1`, `geopandas~=1.1`, plus `pyproj`, map renderer); (b) a **"current geo APIs — read before writing geo code" block in AGENTS.md** listing versions + the rename traps above; (c) a deterministic test importing/exercising every geo entrypoint used (`tests/test_geo_api_pins.py`) so any stale call fails CI immediately; (d) `llms.txt` links to current library docs.

**Data dictionary / schema cards (agent-repo convention):**
- One markdown card per dataset/table the agent touches: `docs/data/<dataset>.md`.
- Card contents: fields, types, units, **CRS (EPSG code)**, geometry type, timezone handling for spatio-temporal fields, provenance, update cadence, license pointer, 3 example rows.
- Referenced from AGENTS.md so the agent never guesses schemas.
- Siyur cards: POI, itinerary schema, route legs, offline bundle manifest, tile sources.

**Data licensing is a real compliance surface for a map product:**
- OSM data is **ODbL**: attribution required, and share-alike applies to derived *databases* — an offline bundle compiled from OSM plausibly is one; design attribution into the app from Spec 001.
- Overture Maps is CDLA-Permissive-2.0; Wikidata is CC0; commercial tile providers carry their own attribution terms.
- Standard practice: REUSE v3.3 for *code* licensing (`LICENSES/` dir, SPDX headers, `reuse lint` in CI) + a hand-maintained **`DATA-LICENSES.md`** registry for *data*:
  `source | license | attribution string required in-app | share-alike implications | date checked`.

### RECOMMENDED STANDARD for Siyur
In the bootstrap commit: pin the geo stack (`shapely~=2.1`, `h3~=4.5`, `osmnx~=2.1`, `geopandas~=1.1`; Python 3.12, uv-managed);
add the geo-version cheat-sheet + rename-trap table to AGENTS.md;
add `tests/test_geo_api_pins.py` as a stale-API tripwire;
write the five `docs/data/` schema cards before the first slice;
create `DATA-LICENSES.md` with OSM/ODbL as row one and wire in-app attribution into Spec 001's acceptance criteria.
Rationale: four small artifacts eliminate the dominant geo-agent failure mode (stale APIs), give the agent authoritative schema ground truth, and turn license compliance into a checked deliverable instead of a launch-week surprise.

---

## 7. Consolidated ramp-up checklist

Ordered, `git init` → first eval passing in CI. Roughly one focused week, solo + agent.

1. `git init siyur && cd siyur`; default branch `main` (protection added at step 22).
2. `uv init`, Python 3.12. Pin geo stack: `shapely~=2.1`, `h3~=4.5`, `osmnx~=2.1`, `geopandas~=1.1`, `pyproj`. App: `langgraph~=1.2`, `langgraph-checkpoint-sqlite`, `pydantic`. Dev: `pytest`, `deepeval`, `agentevals`, `ruff`, `mypy`, `pre-commit`; tools: gitleaks, `reuse`, git-cliff.
3. Write root `AGENTS.md` (≤200 lines): project map, commands, conventions, **geo API cheat-sheet + rename traps**.
4. Create `CLAUDE.md` containing `@AGENTS.md` (+ Claude-only notes if any).
5. Commit `.claude/settings.json` with the §1 allow/ask/deny baseline; gitignore `.claude/settings.local.json`, `logs/`, `.env*`.
6. Install logging hooks in `.claude/hooks/` (disler patterns): `SessionStart`, `PostToolUse`, `PostToolUseFailure`, `PreCompact` transcript backup, `SessionEnd` devlog distiller.
7. Add `.claude/commands/`: `/adr` (draft MADR 4.0 minimal from session context), `/devlog` (distill now), `/failure` (new FAIL-NNN + eval stub).
8. `uvx --from git+https://github.com/github/spec-kit.git specify init .` — Spec Kit ≥0.13.0, Claude Code agent.
9. `/speckit.constitution` — Siyur's five articles: offline-first; deterministic evals gate merges; every decision → ADR; every failure → catalog entry + regression eval; data licenses tracked and attributed.
10. Scaffold docs tree: `docs/adr/` (write ADR 0001 "Adopt this ramp-up standard" — meta, and a course exhibit), `docs/devlog/`, `docs/failures/`, `docs/data/`, `prompts/`, root `llms.txt`.
11. Write the five `docs/data/` schema cards: POI, itinerary, route-leg, bundle-manifest, tile-source (fields, types, EPSG, timezone rules, license pointer, sample rows).
12. Create `DATA-LICENSES.md` (OSM/ODbL row first, attribution strings); run `reuse lint` baseline for code licensing.
13. `/speckit.specify` interview → **Spec 001: plan one tour day, compile to offline bundle**; criteria in EARS; `/speckit.clarify` until no `[NEEDS CLARIFICATION]` remains.
14. `/speckit.plan` → hand-review; capture the 2–3 forced architecture choices as ADRs 0002+.
15. `/speckit.tasks` → tasks citing spec clauses; per-task DoD = EARS criteria + tests + evals + doc artifacts.
16. Scaffold the planner (**PydanticAI + LiteLLM over the `ModelRouter` seam**, ADR-0004): `commons/llm.py` seam + Anthropic-native adapter, `planner/` typed pipeline, own Postgres/SQLite checkpoint (in-memory in tests), and the seam-purity test. Reference: `spike/planner_spike/`. *(Was: scaffold LangGraph from `new-langgraph-project` — superseded by ADR-0004.)*
17. Seed `evals/golden/` with ~25 hand-written tour-day cases (cities × interests × constraints × edge cases).
18. Write `evals/test_structural.py` (schema, geometry validity, constraint checks, offline-render check) and `tests/test_geo_api_pins.py` (stale-API tripwire).
19. Write `evals/test_trajectory.py` — agentevals `superset` trajectory match on `geocode → find_pois → optimize_route → compile_bundle`.
20. Set up conventional commits (commitlint pre-commit) + `cliff.toml` for git-cliff.
21. Add PR template with **Evidence** section (test/eval transcript, screenshots, session-log link, ADR links); agent branches `agent/<ticket>-<desc>`; Co-Authored-By trailers.
22. Create `ci.yml`: lint+typecheck → pytest → eval-structural+trajectory (required) → Semgrep + gitleaks + pip-audit (required) → 500-line diff guard. Protect `main` on these checks.
23. Add `eval-quality.yml` (nightly DeepEval judges, threshold vs baseline, append `evals/history.csv`) and `claude-review.yml` (claude-code-action@v1, read-only tools, `--max-turns 12`, budget cap).
24. Keys only in GitHub Environments + local keychain; scoped low-privilege key for CI agent jobs; plant a dummy secret and confirm gitleaks catches it.
25. `/speckit.implement` task 1 on branch `agent/001-first-slice` in a dedicated worktree; commit granularly.
26. Open the first PR; confirm all required checks green — **first eval passing in CI** — merge; git-cliff writes the first changelog entry.
27. Verify the documentation loop fired end-to-end: devlog entry committed, ADRs 0001–000x present, any failure filed as FAIL-001 with its regression eval added to the golden set.
28. Retro (30 min, with the agent): fold lessons into AGENTS.md and the constitution; tag `v0.1.0`. Ramp-up complete — the steady-state loop (spec → implement → eval → document) is now self-sustaining.
