# 2026-07-25 — Planner spike execution: measuring the ADR-0004 stack

**Goal:** run the not-yet-executed planner validation spike (`spike/planner_spike/`) against the live Anthropic API and get real numbers for ADR-0004's Confirmation — output tokens per completed plan, per-task model routing, and a prompt-cache hit on repeated same-area research. Measure, don't assume.

## What happened

The reference spike from the prior design-review session had never been run (no key, no env then). This session stood up a throwaway `uv --with anthropic` env, obtained a credential, and executed it. Running it — rather than trusting the "verified against the reference" header comment — is what earned the findings: **two defects surfaced that a careful read had missed, both of which would have made the spike fail.**

1. **Stale-API defect (would 400 the research step).** The adapter sent `thinking={"type":"adaptive"}` + `output_config={"effort":…}` for *every* task, but those are 4.6+ features and **Haiku 4.5 — the research tier — rejects both with a 400.** The research step is the first call *and* the one we cache, so the whole run would have crashed before producing a token. Exactly the stale-API trap AGENTS.md warns about. Fixed by gating adaptive/effort behind a `SUPPORTS_ADAPTIVE_EFFORT` model set (ADR-0004 option 1: keep Haiku for research, drop the params it can't take — preserves the routing decision).

2. **The cache confirmation was unreachable as written.** With the Haiku fix in, the pipeline ran, but `cache_read` stayed 0 across repeats. Cause: the `cache_prefix=True` breakpoints sat on ~30-token stub system prompts, far below the minimum cacheable prefix (2,048 tokens Sonnet 5 / 4,096 Haiku 4.5) — below that, `cache_control` silently no-ops. The spike literally could not demonstrate the lever it exists to prove. Gave the research step a realistically-sized stable grounding prefix (~15k tokens — production's system + commons source rows), and the lever fired: `cache_write=14,993` on the first run, `cache_read=14,993` on the repeat.

Numbers captured (4 runs, area "Rhodes old town"): output tokens per completed plan ≈ **1,900–3,700 (mean ~2,850)**; routing confirmed Haiku/Sonnet/Opus per task; cache read >0 on repeat. Full detail in `spike/planner_spike/FINDINGS.md`.

## Decisions

- ADR-0004 **Confirmation marked satisfied** (numbers + routing + cache all measured), with two new build-time constraints recorded in the ADR: the caching **min-prefix precondition**, and the **per-tier adaptive/effort capability constraint**.
- Spike fix taken as **option 1** (keep Haiku for research, gate off unsupported params) — preserves the `Haiku→research` routing. **Flagged for Ben, not decided:** option 2 (re-point research at a 4.6+ small model for uniform levers) would change the routing table and is a real reopen of the routing sub-decision.
- `planner/` to be scaffolded from the **fixed** adapter, not the as-written reference.

## Failures

- None filed as FAIL-NNN yet — the two defects live in throwaway `spike/` code, not product code. Recorded as **regression evals to file via `/failure` when the code lands** at ramp-up: (a) the seam must not send adaptive/effort to a model that 400s on them; (b) the caching-regression eval (`cache_read > 0` on repeated same-area research, above the min-prefix threshold).

## Cost / turns

One working session, ~a dozen user turns. Six live pipeline runs total (≈18 model calls: Haiku+Sonnet+Opus × 6) — two pre-fix (crash-then-diagnose is inaccurate; they ran but showed cache_read=0), four post-fix including the two-run cache proof. Throwaway key, revoked after. Spike remains gitignored — not merged.

## Exhibit-tag candidates

- `exhibit/D5-run-it-to-find-the-bug` — a "verified against the reference" reference impl that a careful read passed but a single execution broke twice (Haiku 400; sub-minimum cache prefix). The clearest evidence yet for the discovery-spike ethos: running beats reasoning about the API. (proposed)
- `exhibit/U6-caching-min-prefix-precondition` — why `cache_read=0` is ambiguous (broken vs. nothing-cacheable), and the min-prefix threshold that decides which. A reusable gotcha for anyone leaning on Anthropic prompt caching. (proposed)
