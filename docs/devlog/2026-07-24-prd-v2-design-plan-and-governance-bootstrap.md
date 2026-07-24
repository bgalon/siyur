# 2026-07-24 — PRD v2.0, the design plan, and the governance bootstrap

**Goal:** ramp up on the seeded Siyur repo, absorb the pivot from a single-user offline tool to a multi-user platform, plan the technical-design work, and stand up the agent governance so the rest of the build is governed and captured.

## What happened

Started from a planning-only repo (PRD v1.0 + methods docs + a ramp-up prompt). Ben reshaped the product mid-session, so the first real work was rewriting the PRD to **v2.0**: a multi-user platform with three online phases (Define area → Research → Plan) plus offline Travel, a **global shared research commons** persisted server-side, Google SSO, multi-language + RTL (English + Hebrew), Plan B/C contingencies, schematic maps + a dynamic timeline, GCP hosting with a required local dev env. Two decisions locked (narration = rich CC BY-SA; global commons); five new open decisions parked in PRD §13. Committed `e10f2c2`.

Wrote a self-contained **Claude-design brief** (for Ben's separate UX track) and a **tech-design v0.1** draft (data spine: `SourcedValue` → `SiteRecordV1` → `ItineraryV1` → `BundleManifestV1`; PostGIS commons; GCP topology; local dev).

Entered plan mode for the technical design. It grew as Ben added three requirements: the project must be a **top-tier exemplar of managing an AI-first project with continuously evolving agents** (guided by the `~/code/siyur-course` repo), the PRD must be **split into deliverable units**, and a **three-tier test strategy** (unit / integration+component / e2e) must sit in every unit and in CI. Pulled two research briefs (agent-ops best practice; the test pyramid for this stack) and read the course repo (8 units U0–U7, one-way exhibits contract). The plan landed as five deliverables (D1 tech-design, D2 delivery-plan, D3 test-strategy, D4 agent-ops, D5 discovery spike). Ben then caught the ordering flaw — governance didn't exist yet — and chose **governance-first**, so the plan gained **D0** ahead of everything.

Executed D0 this session: `AGENTS.md` + `CLAUDE.md`, a strict Ben-approved permission baseline (Checkpoint B), a stdlib-only capture hook (verified writing `logs/events.jsonl` for all six events), the `/adr` `/devlog` `/failure` commands, and the capture dirs. Committed `1590088`.

## Decisions

- Split the ramp-up, governance-first → **ADR-0001**.
- (Earlier, product-level, recorded in PRD v2.0, not as ADRs: narration = rich CC BY-SA; global commons; M1-slice-deep design; pre-ramp-up discovery spike.)

## Failures

- None caught that warranted a FAIL entry. One near-miss avoided by verification: `PostToolUseFailure` is a real hook event (confirmed against current docs) rather than assumed — had it not been, the hook registration would have silently not fired.

## Cost / turns

Long single session (one working day, 2026-07-24). Three background research/reference subagents (two web-research, one claude-code-guide for the hooks/permissions schema). No token accounting captured yet — the hook trail (`logs/events.jsonl`) starts from the next fresh session, since settings load at startup.

## Exhibit-tag candidates

- `exhibit/U1-governance-first-bootstrap` — the D0 commit + this devlog + ADR-0001 as a worked example of "the repo is the agent's brain," including the reasoning for splitting the ramp-up. (proposed)
- `exhibit/U1-permission-baseline-review` — the Checkpoint B settings.json approval moment (what each deny protects). (proposed)
- `exhibit/U2-prd-evolution` — PRD v1.0 → v2.0 diff as a case study in scope change managed via a contract doc + open-decision register. (proposed)
