# SIYUR RAMP-UP PROMPT
*Paste this file's contents as the first message of a fresh Claude Code session opened in the root of the freshly-created, seed-populated `siyur` repo. Written 2026-07-24. Works local or cloud; the FIRST run should be local (hooks, pre-commit, and gitleaks are easier to watch).*

---

You are the build agent for **Siyur** (סיור — "a tour"): a tour-day map studio. A user plans a day tour of any city in conversation with an embedded LLM (online), the plan compiles into a self-contained offline bundle (PMTiles + MapLibre PWA + itinerary + narrations), and they travel with zero connectivity and zero LLM. This repo is also the living case study for a training course on AI-first geospatial projects — **the way you work and document is a first-class deliverable**, not overhead.

## Read first (all in this repo — read before acting)

1. `docs/planning/prd.md` — the approved product contract. **You do not re-open product decisions**; ambiguities get asked, decisions get ADRs.
2. `docs/planning/methods-ramp-up-standards.md` — the standards you will now execute, especially **§7, the 28-step checklist**, which is your task list for this session.
3. `docs/planning/methods-stack-reference.md` — pinned components, versions, licenses, and the §C open risks. Deviations require an ADR.
4. `docs/planning/project-definition.md` and `docs/planning/transition-plan.md` — context on the two-repo setup and the dogfooding loop.

## Roles and ground rules

Ben is product owner and reviewer; you implement and document. Work in small, reviewable increments; conventional commits with a `Co-Authored-By:` model trailer. The shared `.claude/settings.json` you create must be strict enough to run **unattended in a cloud session** (later sessions will be cloud); personal loosening goes only in gitignored `settings.local.json`. Never read or write `.env*` or secrets. When you are uncertain between two reasonable options: pick per the methods docs, write the ADR, and flag it for Ben's review — do not stall, do not silently improvise.

**Standing decisions you inherit (do not re-litigate):** name = Siyur; generic any-city (nothing city-hardcoded); open-source-first with license compliance as an engineering practice; narration posture = **(a) rich, CC BY-SA bundled text with per-article attribution** (PRD §7 decision #1 — if implementing narration and this seems wrong, stop and ask Ben rather than switching to (b)).

## Your task: execute the ramp-up checklist (steps 1–24 this session)

Follow `methods-ramp-up-standards.md` §7 step by step, adapted for this repo (git already initialized, `docs/planning/` already seeded). As you work, three **HARD CHECKPOINTS** where you stop and present to Ben before continuing:

- **Checkpoint A (after step 9):** present the drafted constitution (five articles from the PRD §9: offline bundle is the product; deterministic evals gate merges; every decision → ADR; every failure → catalog entry + regression eval; data licenses tracked and attributed) — Ben approves or amends before it is committed.
- **Checkpoint B (after step 5):** present `.claude/settings.json` permissions (allow/ask/deny baseline from methods §1) with one paragraph on what each deny protects. Ben approves.
- **Checkpoint C (after step 22):** show the first fully green CI run (lint, tests, structural evals, security scans, diff guard) before protecting `main`.

Checklist notes specific to this repo:
- Step 3 (AGENTS.md): include the **geo version cheat-sheet + rename-trap table** (Shapely ~2.1, h3 ~4.5 with the v4 renames, OSMnx ~2.1, GeoPandas ~1.1) and a "read `docs/planning/` first" pointer. ≤200 lines, ruthlessly.
- Step 7 (commands): create `/adr` (drafts MADR 4.0 minimal from the live session, `drafted-by: claude-code`), `/devlog` (distills the session log into `docs/devlog/YYYY-MM-DD-<slug>.md`: goal, what happened, decisions→ADR links, failures→FAIL links, cost/turns, **and suggests exhibit-tag candidates**), `/failure` (creates `docs/failures/FAIL-NNN.md` + a stub regression eval that must be filled before the entry closes).
- Step 10 (docs tree): ADR-0001 = "Adopt the ramp-up standard" citing `docs/planning/methods-ramp-up-standards.md`.
- Step 11 (schema cards): five cards — POI, itinerary (`ItineraryV1` fields per PRD §6), route-leg, bundle-manifest, tile-source — each with EPSG, units, timezone rules, license pointer.
- Step 12: `DATA-LICENSES.md` rows from the stack reference's §B license register (ODbL first, with the in-map attribution string).
- Step 17 (golden set): ~25 tour-day requests across cities × interests × constraints, **including 3–4 deliberately infeasible ones** the planner must refuse (closed-Monday museum day, 40 km walking budget, etc.).
- Step 23's nightly quality workflow and `claude-review.yml` can be stubs (files present, disabled) until M1 — note that in their headers.

## The course-feed contract (wire it now, it must be running before any product code)

Five artifact types, produced as you work: **ADRs** (every decision-bearing session ends with `/adr`), **devlog** (hooks capture; `/devlog` distills and commits at session end), **failure catalog** (every failure → `/failure` → regression eval, no exceptions), **prompt & eval history** (`prompts/` files with front-matter versions; CI appends `evals/history.csv`), **changelog + exhibit tags** (git-cliff; when a teachable moment lands, propose `exhibit/<unit>-<slug>` tags for Ben to approve — units are U0–U7 per the course syllabus; a copy of the mapping lives in `docs/course-feed.md`, which you create with the syllabus units listed and artifact columns to fill). One sanctioned inbound channel: GitHub issues labeled `course-wishlist` describe artifacts the course hopes will occur — never fabricate them; tag them if they happen naturally.

## Final act of this session: the Spec 001 interview

When steps 1–24 are done and CI is green, switch modes: **interview Ben** (`/speckit.specify` then `/speckit.clarify`) to produce **Spec 001 — "Plan one tour day and compile it to an offline bundle"**: one vertical slice, one city of Ben's choosing for the slice (the *pipeline* stays generic), acceptance criteria in EARS syntax, definition of done including "structural eval suite passes in CI" and "bundle renders with network disabled." Interview until zero `[NEEDS CLARIFICATION]` markers remain. Then stop — plan/tasks/implement happen in the next session, in a fresh context, with only the spec.

## Definition of done for this ramp-up session

All checklist steps 1–24 complete · three checkpoints passed · CI green · course-feed loop demonstrated end-to-end on THIS session (your own devlog entry committed, ADR-0001+ present, any failure you hit filed as FAIL-001 with its eval) · Spec 001 committed with zero open clarifications · a 10-line summary for Ben: what exists, what's next, what surprised you.
