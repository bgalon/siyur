# 2026-07-31 — CI gate stood up, Spec 001 clarified, and DU-00 runtime slices built in parallel cloud sessions

**Goal:** Pick up a mid-stopped session (CI never existed), stand up the required merge gate, then move DU-00 forward: author Spec 001, and build the walking-skeleton runtime (empty map + SSO) — using cloud/parallel sessions for the first time.

## What happened

**The CI gap.** "Review the CI status" surfaced that there was **no CI at all** — `.github/` held only the PR template, no `workflows/`. The mid-session stop had landed the *substrate* (tripwire tests, evals seam, toolchain in PR #11) but never the workflow YAML that DU-00's DoD requires. ADR-0006 had already narrowed the workflow-edit deny to `ask` precisely to unblock this.

**Stood up the 7-job gate, green on stubs** (test-strategy §"CI gating"): `ci.yml` (1 lint+type · 2 unit · 3 integration · 5 e2e-airplane · 6 security · 7 diff-guard), `eval-quality.yml` (4 deterministic-evals · 8 llm-judge, split out so a judge flake can't block a hotfix), `claude-review.yml` (advisory). Jobs without product code yet (3, 5) are documented green placeholders; the rest run for real. Verified every gate locally before wiring, then two first-run bugs fixed: gitleaks needed `pull-requests: read`; diff-guard read the `size-override` label from a frozen event payload (→ read live PR state, re-run on labeled/unlabeled). Also excluded `docs/`+`specs/` from ruff so hand-aligned markdown code snippets don't trip the format gate.

**Security gate caught two real advisories** on first run (pip-audit, zero ignores): `pytest 8.4.2` (PYSEC-2026-1845) and transitive `langchain 1.3.2` (PYSEC-2026-2192), both dev/eval-only. Ben chose **fix, not ignore** → `pytest ~=8.3→~=9.0` (forced `pytest-asyncio ~=0.24→~=1.0`) + a `langchain>=1.3.9` uv constraint. Suite re-verified green under pytest 9.

**Branch-protection reality.** Confirmed GitHub branch protection is a 403 on this private free-tier repo, so checks 1–7 can't be machine-required. Recorded in AGENTS.md (always-loaded, binds local + cloud sessions) and the delivery plan that the merge gate is a **self-enforced discipline** — merge only on green.

**Spec 001, authored here then clarified.** Ran `speckit-specify` → the first vertical slice (research an area → cited sites on the map, Rhodes demo). Three decisions **reserved to Ben** were surfaced as questions rather than defaulted (the spec's whole point is capturing *his* calls) and resolved A/A/A: commons write policy (§13 #4) → direct auth-gated shared write (ADR-0008); transliteration extent → display-names-only; first-slice scope → POI locate+cite only, stories → slice 002.

**First parallel cloud sessions.** With Spec 001 on `main`, launched **three** background Opus-4.8 agents on disjoint file territories: the `/speckit-plan` run (specs/ + ADRs), the `web/` empty-map scaffold, and the `api/` Google-SSO scaffold. All three opened green PRs (#14/#16/#15), zero conflicts (disjoint files). The publish-for-review pipeline (own branch → PR → CI → review) that ADR-0005 + this CI make real held up under genuine parallel load. Coordination guard that mattered: only the plan agent wrote ADR files (0009/0010) — the others flagged decisions in their PR bodies to avoid an ADR-number collision, formalized afterward.

**The empty map renders.** Verified the `web/` output live: a MapLibre canvas with the ODbL attribution control, no tiles, no network — the DU-00 "empty map renders offline" target, and Ben's first visible output.

## Decisions
- Commons write policy (§13 #4, to the extent slice 001 needs) → **ADR-0008** (accepted).
- Research source-adapter pattern (Overture/DuckDB + OSM/Overpass, boundary stamping) → **ADR-0009** (accepted).
- Greek→Latin transliteration = deterministic/offline ICU, display-name only, provenance-inheriting → **ADR-0010** (accepted).
- Google SSO = Authlib + server-side OIDC Authorization-Code flow, server-side canonical for M1, Bearer (tech-design §5.4) deferred/swappable at the `require_user` seam → **ADR-0011** (accepted).
- Web toolchain deviations accepted: `maplibre-gl ~5.19` (follows stack-reference pin, not v6) and `vitest ~4.1` (forced by vite 8 type-compat); recorded here rather than a full ADR.
- Security pin bumps (pytest 9 / pytest-asyncio 1 / langchain constraint) → **ADR-0007 amendment**.

## Failures
- None new. The pip-audit advisories were the gate working as designed (fixed via pin bumps), not a process failure; the two CI first-run bugs (gitleaks perms, diff-guard stale label) were fixed same-session before any green claim.

## Cost / turns
Long interactive session + 3 background cloud agents (~80k/96k/133k subagent tokens). Seven PRs merged (#11–#16 + this governance PR). Every merge on green CI; diff-guard `size-override` used twice for legitimately large skeleton/plan PRs (docs + generated lockfiles excluded from the human-churn count).

## Exhibit-tag candidates
- `exhibit/U0-airplane-mode` — the empty map that renders offline (shell precache, no tile network), the first standing proof of the airplane-mode guarantee.
- `exhibit/U1-walking-skeleton` — the full DU-00 slice: CI gate + SSO + empty map, built across parallel cloud sessions with a self-enforced (not machine-enforced) merge gate.
- `exhibit/U2-spec-reserved-decisions` — a spec that *flags and surfaces* the decisions reserved to the human (§13 #4, transliteration extent, slice scope) instead of silently defaulting them, then records the resolution as ADRs. Constitution Article III in practice.
- `exhibit/U3-parallel-cloud-fanout` — three headless agents on disjoint territories opening green PRs concurrently; the ADR-number-collision guard as the coordination lesson.

*(Proposed for Ben to approve.)*
