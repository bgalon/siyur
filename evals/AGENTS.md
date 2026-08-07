# AGENTS.md — `evals/`

Nested override for work in this package; extends the root `AGENTS.md` (read that first).

**Scope:** the quality axis, kept in `eval-quality.yml` so a non-deterministic judge can never
block a product hotfix. **Job 4 · deterministic-evals** — schema/structural + trajectory
(`superset` match) on fixed traces with a **mocked LLM**; fast, no API key, **PR-gating**.
**Job 8 · llm-judge-evals** — pinned judge model + prompt, non-blocking on PRs, blocking on
`main` and nightly. Run locally with `uv run pytest evals/ -q`.

**Invariants enforced here:**
- **The deterministic tier stays deterministic.** No live model call, no network, no clock or
  RNG dependence. If an eval can flake, it belongs in job 8, not job 4.
- **An eval must be able to fail.** Prove it by inducing the specific break it claims to catch
  and recording the observed red. An eval that cannot fail is documentation.
- **Provider figures are read, not remembered.** Prompt-cache minimums differ per tier and are
  **not monotonic across generations** (FAIL-006: a cached prefix under the minimum caches
  nothing and raises no error). Load the `claude-api` skill rather than writing numbers from
  memory — that is exactly how the wrong figures in `tasks.md` were caught.
- **Aim an eval at the node that can satisfy it.** T065's caching eval was pointed at
  `research`, which makes no model call (ADR-0014), so it could only fail forever or be
  "fixed" by adding a pointless call. Re-aimed at `curate`.
- **Gate on significance, not raw deltas** — paired-t ±95% CI vs. baseline (agent-ops D4).

**Status:** `evals/history.csv` and the pinned-judge harness **land at DU-04**; job 8 is a
documented green stub until then. Do not create the CSV early — wire it with the harness.
