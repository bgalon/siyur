# 2026-07-30 — DU-00 unit d: the Python package skeleton + toolchain foundation

**Goal:** Complete the Python package skeleton + toolchain on top of unit a's empty skeleton — add the planner model seam and eval-tier dependencies, per-package `AGENTS.md` stubs, the geo stale-API tripwire, and eval-tier placeholder gates. Stubs only, no product logic. Scope-fenced OUT: CI, the `web/` Vite scaffold, Google SSO, Spec 001, branch protection, the LLM seam-purity test (all later DU-00 units).

## What happened
Read AGENTS.md (geo pins) → tech-design §4/§5.1 → delivery-plan DU-00 + Amendments → test-strategy tiers → methods §7 (steps 2/16) + stack-reference → ADR-0003/0004. Discovered the skeleton was **further along than the task framing implied**: unit a (commit `a15bca0`) had already committed the empty packages, geo pins, base dev tools, and `test_skeleton.py`, and its `pyproject.toml` explicitly reserved the seam deps for "unit d" and eval deps for "unit e." So unit d's real remaining work was the *additive* layer, not a from-scratch build.

Two stale-instruction traps confirmed and avoided (both flagged in the task): the ramp-up checklist §7 steps 2/16 still name **LangGraph**, superseded by **ADR-0004** (PydanticAI + LiteLLM over the `ModelRouter` seam) — pinned the seam, added no langgraph. ADR-0003 keeps `web/` a separate unit — left it as the existing placeholder README.

**Version pins by resolve-then-pin, not guessing.** The stack reference says the LLM/eval libs are "pinned at scaffolding," giving no exact versions. Added the four deps unbounded (`>=0`), ran `uv lock` to resolve current (`pydantic-ai 2.21.0`, `litellm 1.94.0`, `deepeval 4.1.4`, `agentevals 0.0.9`), then tightened to `~=major.minor`. `uv` resolved 198 packages clean — no conflict between the pinned pydantic 2.11 and pydantic-ai / deepeval's transitive pins, which was the main resolution risk.

**Geo tripwire built empirically.** Before writing `tests/test_geo_api_pins.py`, ran a probe script exercising each entrypoint on the installed libs (shapely `unary_union`/`.geom_type`, h3 `latlng_to_cell`/`cell_to_latlng`/`grid_disk`, geopandas `union_all()`, pyproj `Transformer`) so the assertions match reality and pass on the pins — the test also asserts the v-old idioms (`cascaded_union`, `geo_to_h3`/`h3_to_geo`/`k_ring`, `ox.utils_graph`) are *gone*, so a stale emission fails CI immediately.

**One real failure (FAIL-003).** The `uv run mypy .` DoD gate aborted on `spike/`'s duplicate `run` module — mypy had no exclude, unlike ruff. This is the exact parallel gap to FAIL-002: that fix scoped **ruff** away from `spike`/`.claude` but never propagated the same exclusion to `[tool.mypy]`. Confirmed pre-existing (spike files are untracked, so it reproduces on `main`). Fixed by mirroring the exclude. Otherwise a clean run.

Committed the build, then filed the governance trio. One branch `agent/du00-package-skeleton` → PR to main (ADR-0005).

## Decisions
- Dependency pins + toolchain-foundation choices (seam = pydantic-ai/litellm not langgraph; resolve-then-pin; eval libs now / harness at unit e; anthropic SDK deferred to the seam impl at DU-02; minimal local pre-commit; mypy exclude mirror) → **ADR-0007** (status *proposed*, awaiting Ben's confirm/amend).

## Failures
- `mypy .` aborted on the throwaway `spike/` — no mypy exclude mirroring ruff's → **FAIL-003** (regression guard: the standing `uv run mypy .` gate, now green on 9 source files, self-enforces; becomes CI job 1 at the CI unit). Closed — guardrail present, no stub owed.

## Cost / turns
~1 focused reading pass (7 docs, parallelised) + probe + 8 file writes/edits + 3 governance skills + commits/PR. `uv lock` pulled a large transitive tree (198 locked pkgs) for deepeval/agentevals. Hand-written diff **233 lines** (< 500 guard; `uv.lock` excluded as generated). No tool failures beyond the caught FAIL-003.

## Exhibit-tag candidates
- `exhibit/U1-geo-api-tripwire` — `tests/test_geo_api_pins.py` as the mechanical countermeasure to the #1 geo-agent failure mode (stale-API emission): exercise every entrypoint on the current API *and* assert the v-old names are gone, built by probing the installed libs first. Strong U1 (walking-skeleton / agent-repo-conventions) artifact.
- `exhibit/U1-resolve-then-pin` — pinning LLM/eval deps by resolving-then-tightening rather than guessing versions, when the stack reference deliberately leaves them "pinned at scaffolding." Teaches reproducible dependency hygiene for agent builds.
- `exhibit/U2-stale-checklist-vs-adr` — following ADR-0004 over the ramp-up checklist that predates it (no langgraph), i.e. ADRs as the live source of truth over static process docs. Pairs with FAIL-003 (a config gap left when a prior fix wasn't propagated across parallel tools). Good U2 material.

*(Proposed for Ben to approve.)*
