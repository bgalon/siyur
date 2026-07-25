# 0006 — Agent-authored CI, gated by per-edit review (narrow the workflow-edit deny)

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: Ben · Date: 2026-07-25

## Context and Problem Statement

DU-00 (the walking skeleton) requires standing up the 7-job CI (`ci.yml` + `eval-quality.yml` + `claude-review.yml`, green on stubs — `test-strategy.md` §, `delivery-plan.md` DU-00). The shared `.claude/settings.json` currently **hard-denies** `Edit(./.github/workflows/**)` — a deliberate guardrail inherited from the methods standard (§5: "restrict agent autonomy on security-critical code"; CI config can disable security gates or exfiltrate secrets, so it is security-critical). Because **`deny` wins across every settings layer**, a personal `settings.local.json` allow cannot override it — so as written, the build agent literally cannot author the CI that DU-00 is defined to produce. Either a human hand-commits every agent-drafted workflow, or the shared policy changes. This ADR resolves how CI gets authored at DU-00 and beyond.

## Considered Options

- **A — Keep the hard deny; Ben hand-commits agent-drafted YAML.** Preserves the guardrail perfectly, but every CI change routes through a manual copy-commit step, and Checkpoint C (human-reviews-first-green-CI) already provides the human gate — so the deny is redundant friction on top of an existing review point.
- **B — Remove the deny; add `Edit(./.github/workflows/**)` to `allow`.** Frictionless authoring, but silently editable CI in *any* session — including future **unattended cloud** runs — which is exactly the autonomy the methods standard warns against for security-critical config.
- **C — Narrow the deny to `ask`.** Move workflow edits from `deny` to the `ask` list: allowed, but **every** workflow edit requires explicit interactive human approval. In a supervised local session (where DU-00 CI is authored) that is one approval per file; in an unattended cloud session `ask` **fails closed** (no human to answer → blocked), preserving the guardrail exactly where it matters most.

## Decision Outcome

Chosen: **C — narrow the deny to `ask`**, because it is the only option that both unblocks agent-authored CI at DU-00 *and* keeps CI config human-gated by default. It removes redundant friction (the human review already happens at merge / Checkpoint C) without granting standing autonomy over security-critical workflow files: the `.env*` and `secrets/**` denies are untouched, and cloud/headless sessions still cannot touch CI because `ask` has no one to approve it. The change is one line moved from `deny` to `ask` in the shared `settings.json`; personal loosening (a local `allow`) remains possible on Ben's own machine via `settings.local.json` if the per-edit prompts become tedious, but the shared default stays safe-for-cloud.

## Consequences

- Good: the build agent authors the 7-job CI directly at DU-00; no manual copy-commit relay. The human gate is preserved (per-edit `ask` locally, merge review at the PR, Checkpoint C on first green CI).
- Good: unattended cloud sessions still cannot modify workflows (`ask` fails closed) — the security intent of the original deny is retained where the risk is highest.
- Good: symmetry with ADR-0005 — CI changes land as reviewable PRs, not direct pushes.
- Bad / accepted cost: one approval prompt per workflow-file edit in local sessions (minor; batchable per PR). If Ben wants zero friction on his own machine he opts in via `settings.local.json`, accepting local-only `allow`.
- Accepted cost: this is a real loosening of a security guardrail, so it is recorded here (not buried in a settings diff) and paired with the compensating controls above.

## Confirmation

The `settings.json` change moves `Edit(./.github/workflows/**)` from `deny` to `ask` in the same PR as this ADR (dogfood: the policy change is itself reviewed). Durable confirmation: the deny→ask narrowing is visible in `.claude/settings.json`; the `.env*`/`secrets/**` denies remain present and are covered by the gitleaks CI job (DU-00 job 6) + the planted-dummy-key test (ramp-up step 24); and the CI-authored-by-agent path is exercised when DU-00's CI PR (unit g) goes green under Checkpoint C. Branch-protection enforcement of these checks is deferred per ADR-0005 (the repo is private on the free tier — protection/rulesets return 403 — so "required status checks" are added when the repo goes public or Pro; until then CI runs on PRs by convention).
