# Transition Plan — From Documents to a Code Project
### The concrete path from where we are today to Siyur's first merged PR

*v1.0 — 2026-07-24. Companion to `project/02-prd-siyur.md` (§13) and `methods/01-ramp-up-standards.md` (§7, the 28-step checklist this plan schedules).*

## The shape of the transition

Three homes, three jobs — confusion between them is the main transition risk, so we fix the map first:

| Home | Job | Source of truth for |
|---|---|---|
| **GitHub repo `siyur`** (to be created) | The build: code, specs, evals, and ALL build documentation (ADRs, devlog, failure catalog) | The product and the Goal-1 process record |
| **This Claude project** ("GeoAI project - howto") | Course production: syllabus, PRD lineage, methods references, cross-session memory | The course and the decisions that precede the repo |
| **Ben's machine** | Local Claude Code sessions, Docker (Valhalla, Postgres), real-device testing (iOS PWA) | Nothing — it holds working copies only |

Rule of thumb after day one: **if it's about building Siyur, it lives in the repo; if it's about teaching from Siyur, it lives here.** Devlog highlights and tagged exhibits sync from repo → project at each milestone, not continuously.

## Step 0 — Approve the PRD (you, now)

Work through `02-prd-siyur.md` §13: amend or approve items ①–⑥, and decide **open decision #1** (narration posture — my recommendation is (a) CC BY-SA rich). Nothing below starts until this lands, because the constitution and Spec 001 both inherit from it.

## Step 1 — I write the ramp-up prompt (Claude, ~same day)

On approval I produce `project/04-ramp-up-prompt.md`: a self-contained bootstrap instruction that works pasted into a fresh Claude Code session, cloud or local. It will contain: the project one-paragraph context; the 28-step checklist inlined with Siyur-specific values (constitution articles, geo pins, permission baseline, hooks list, course-feed artifact set); explicit human checkpoints (constitution review, settings review, CI-green confirmation); and the Spec 001 interview as its final act. It deliberately does *not* contain product design freedom — the PRD is attached context, not re-negotiable by the build agent.

## Step 2 — Create the repo (you, 10 minutes)

GitHub, name `siyur` (or your chosen name), **public recommended** — the repo is course material and "build in public with an agent" is part of its value; private-then-publish is the fallback if you prefer. Initialize empty (no README template — the agent writes everything per the checklist). Add branch protection *later* (checklist step 22, after CI exists). Prepare three secrets you'll need during ramp-up, none earlier: an Anthropic API key (scoped, for the devlog distiller + CI review job), an OpenRouteService free key (dev-mode routing), and nothing else — every other data source in the MVP is keyless by design.

## Step 3 — Seed the planning docs (you + me, 15 minutes)

First commit is human: copy into `docs/planning/` the PRD, the project definition, both methods docs, and the reference index. This makes the repo self-contained — a build session never needs to reach back into this Claude project for context. I'll prepare the exact file set as a zip when the ramp-up prompt is ready.

## Step 4 — The ramp-up session (you + agent, ~1 focused week for M0)

Start **local** for the first session (hooks, pre-commit, and gitleaks verification behave more predictably where you can watch them), paste the ramp-up prompt, and let the agent execute checklist steps 1–24 with you reviewing at the three checkpoints. Cloud sessions join from step 25 onward (implementation tasks in worktrees). The session ends with the Spec 001 interview — budget an unhurried hour for it; the interview quality sets the slice quality.

## Step 5 — Steady-state rhythm (from M1)

**Session division of labor:** local for anything touching Docker (Valhalla builds, Postgres), device testing, and hook debugging; cloud for bounded parallel implementation tasks, research spikes, and the nightly devlog distillation. Worktree per concurrent session; `agent/<ticket>` branches; every PR carries its Evidence section and you review the *plan* before the diff on multi-file work. **Weekly 30-minute retro with the agent:** fold corrections into AGENTS.md (the Cherny loop), check the course-feed index (`docs/course-feed.md`) still resolves every syllabus unit, and sync highlights to this project.

## Step 6 — Course production track (me, in parallel from M1)

While you build, this project's sessions: validate syllabus units against arriving artifacts, refine unit hands-ons into exercise branches, refresh the reference library at milestones (per `references/00-INDEX.md` §7), and draft slides once M2's exhibits exist. The syllabus freeze is M4, matching the PRD.

## Anti-patterns to refuse at the transition (the ones that quietly kill this)

Writing code before the constitution exists ("we'll add process later" — later never comes, and the course loses its ramp-up chapter). Skipping evals until there's "something to test" — the golden set and structural evals are checklist steps 17–19, *before* the first slice, deliberately. Letting a cloud session run with loosened permissions because a deny rule was inconvenient — loosen only in `settings.local.json`, locally, consciously. Treating the course-feed artifacts as paperwork to batch up later — they are only cheap when produced by the hooks in the moment. And hand-editing generated files (styles, changelogs, bundles) instead of fixing the generator — the agent will faithfully learn the wrong lesson.

## Ready-to-code checklist

☐ PRD approved incl. decision #1 · ☐ ramp-up prompt delivered (`project/04`) · ☐ repo created, visibility decided · ☐ planning docs seeded to `docs/planning/` · ☐ Anthropic + ORS keys in hand (GitHub Environments + local keychain only) · ☐ Docker running locally · ☐ first local session scheduled with an unhurried hour for the Spec 001 interview. When all boxes tick, you are one pasted prompt away from a code project.
