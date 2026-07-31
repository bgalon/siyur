# 0007 — DU-00 unit d: planner-seam + eval dependency pins and toolchain-foundation completion

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: Ben · Date: 2026-07-30

## Context and Problem Statement

DU-00 unit a stood up the empty package skeleton and the geo/base-tool pins, but deliberately deferred the planner-seam and eval-tier dependencies (a note in `pyproject.toml` reserved them for "unit d / unit e"). Unit d completes the Python package skeleton + toolchain foundation (delivery-plan DU-00; tech-design §4, §5.1), which forces a cluster of build/tooling choices. None are product decisions (PRD §13 open items untouched) — but per AGENTS.md a session that picks libraries/versions ends in an ADR.

Two of the ramp-up checklist steps this unit executes (`methods-ramp-up-standards.md` §7 steps 2, 16) **predate ADR-0004** and still name LangGraph; the stack reference likewise says the LLM/eval libs are "pinned at scaffolding," not in advance. So the concrete pins and the langgraph-vs-seam choice had to be settled here against the ADRs, not copied from the stale checklist.

## Considered Options

- **Planner deps — LangGraph (checklist §7 step 2) vs. the ADR-0004 seam.** LangGraph + `langgraph-checkpoint-*` is what the checklist literally lists; ADR-0004 supersedes it with PydanticAI orchestration + LiteLLM routing over an owned `ModelRouter` seam and a single owned Postgres/SQLite checkpoint row.
- **Version-pin source — guess from the stack reference vs. resolve-then-pin.** The stack reference gives no exact versions for these libs (it says "pinned at scaffolding"). Option A: hand-write plausible pins. Option B: add the deps unbounded, let `uv` resolve current, then tighten to `~=major.minor` of what actually resolved.
- **Eval libs — now (unit d) vs. deferred to unit e.** Unit a's `pyproject.toml` note deferred `deepeval`/`agentevals` to unit e (the harness unit); this unit's task scope pulls them forward so the eval *toolchain* resolves before the harness is written.
- **Anthropic SDK — add now vs. defer to the seam.** ADR-0004's M1 adapter is Anthropic-native (direct `anthropic` SDK). Add it in the skeleton, or defer until `commons/llm.py` actually exists.
- **Pre-commit scope — none vs. minimal-local vs. full (commitlint + gitleaks).** `pre-commit` is a listed dev dep; the config could be absent, minimal (lint/type/hygiene), or the full conventional-commit + secret-scan stack.

## Decision Outcome

Chosen, because the driver throughout is *follow the ADRs over the stale checklist, and pin to observed ground truth, not guesses*:

1. **Planner seam = `pydantic-ai~=2.21` + `litellm~=1.94`** over the `ModelRouter` seam (ADR-0004). **No langgraph** — the checklist reference is superseded.
2. **Resolve-then-pin:** deps added unbounded → `uv lock` → tightened to the resolved `~=major.minor`: `pydantic-ai 2.21.0`, `litellm 1.94.0`, `deepeval 4.1.4`, `agentevals 0.0.9`. Geo/spine pins from unit a unchanged.
3. **Eval libs land now** (`deepeval~=4.1`, `agentevals~=0.0.9`, dev group) so the eval gate resolves; a placeholder `evals/test_evals_tier.py` asserts they installed. The *harness* (golden set, structural/trajectory/quality tests) still lands at unit e.
4. **`anthropic` SDK deferred** to the seam implementation (`commons/llm.py`, DU-02) — unit d is stubs only, no product logic; adding an unused SDK now would be dead weight.
5. **Skeleton completed** with per-package `AGENTS.md` nested-override stubs (commons/planner/compiler/api) carrying each package's invariants (seam-purity, licence quarantine, auth row-scoping).
6. **Minimal local `.pre-commit-config.yaml`** (ruff lint+format, `pre-commit-hooks` hygiene, local `uv run mypy`). Conventional-commit linting, gitleaks, and the eval/security gates are CI concerns (checklist §7 steps 20/22) → the CI unit, not here.
7. **`[tool.mypy] exclude` mirrors ruff's** `spike`/`.claude` exclusion (FAIL-003).

## Consequences

- Good: the toolchain foundation is ADR-consistent (no langgraph debt to unwind at DU-04) and the pins reflect what actually resolves on PyPI today, so `uv sync` is reproducible.
- Good: eval + lint/type gates exist green *before* the features they will guard (test-strategy tiers), and the skeleton is fully importable with per-package guidance in place.
- Bad / accepted cost: `deepeval` + `agentevals` pull a large transitive tree (198 locked packages total) that is unused until unit e — accepted so the gate exists early; `agentevals 0.0.9` is a `0.0.x` lib (`~=0.0.9` = `>=0.0.9,<0.1`) and may move fast. Pins are `~=major.minor`, so patch/minor security bumps need a deliberate `uv lock --upgrade-package`.
- Accepted: pulling the eval libs forward diverges from unit a's "unit e" note; superseded by this unit's task scope (libs now, harness at e).

### Confirmation

- **Now (this PR):** `uv sync` resolves (198 packages); `uv run ruff check .` and `uv run mypy .` clean; `uv run pytest` = 13 passed, including `tests/test_geo_api_pins.py` (the stale-API tripwire) and `evals/test_evals_tier.py`; all five packages import.
- **Standing guard for the langgraph-exclusion + seam confinement:** `tests/test_llm_seam.py` (added at DU-02, enforced through DU-04) asserts no `anthropic`/`openai`/`litellm` import appears in `planner/` or `commons/` above `commons/llm.py` — the tripwire that keeps `pydantic-ai`/`litellm` behind the seam. **TODO: lands at DU-02** with the seam implementation.

## Amendment — 2026-07-31 (security-gate-driven pin bumps)

*drafted-by: claude-code · approved-by: Ben (this session)*

Standing up the CI security gate (job 6, `pip-audit`) — the very step decision #6
deferred to "the CI unit" — surfaced two dependency advisories in the **dev/eval
toolchain only** (never in the product runtime):

- `pytest 8.4.2` → **PYSEC-2026-1845**, fixed in `9.0.3`
- `langchain 1.3.2` (transitive via `agentevals → openevals`) → **PYSEC-2026-2192**, fixed in `1.3.9`

Per the decision-#6 mandate to keep the gate honest, and consistent with the
consequences note that "security bumps need a deliberate `uv lock`," Ben chose to
**bump both to the fixed versions rather than ignore the advisories** (pip-audit
therefore runs with **zero `--ignore-vuln` flags** — it reddens only on genuinely
new CVEs). This adjusts decision #2's pins:

- `pytest ~=8.3` → **`~=9.0`** (resolved `9.1.1`; full suite re-verified green under 9).
- `pytest-asyncio ~=0.24` → **`~=1.0`** (resolved `1.4.0`) — forced by pytest 9 (`~=0.24` caps pytest `<9`).
- Added `[tool.uv] constraint-dependencies = ["langchain>=1.3.9"]` to lift the transitive `langchain` to the fixed release (resolved `1.3.9`; also bumps `langgraph-sdk 0.3.15→0.4.2`).

Confirmation: `uv lock --locked` consistent; `pip-audit` → **no known vulnerabilities**;
`ruff`/`mypy`/`pytest` (13 passed) all green under the new pins.
