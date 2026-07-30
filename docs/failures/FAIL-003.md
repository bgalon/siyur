# FAIL-003 — `uv run mypy .` aborts on the throwaway `spike/` (mypy exclude never mirrored ruff's)

- Date: 2026-07-30 · Severity: low
- Root-cause class: tool-gap

## Symptom

Running the DoD typecheck gate during DU-00 unit d, `uv run mypy .` failed before checking any app source:

```
spike/run.py: error: Duplicate module named "run" (also at "./spike/planner_spike/run.py")
Found 1 error in 1 file (errors prevented further checking)
```

The `lint+typecheck` gate (CI job 1, per methods-ramp-up-standards §7 step 22) was therefore **not actually clean** — a latent failure that would break CI the moment the CI unit lands, and that masks *all* real type errors in the packages because mypy aborts on the duplicate-module error before reaching them.

## Trajectory excerpt

- `[tool.mypy]` in `pyproject.toml` had `python_version`, `strict`, and `ignore_missing_imports` — but **no `exclude`**.
- `spike/` (the gitignored, never-merged discovery/planner spike, tech-design §7) contains both `spike/run.py` and `spike/planner_spike/run.py` — two modules named `run` with no package `__init__.py` between them.
- `mypy .` walks the whole tree, hits the duplicate `run` module, and stops (`errors prevented further checking`).
- Confirmed pre-existing: `spike/*.py` are untracked (present regardless of branch), so the abort reproduces on `main`, independent of unit d's changes.

## Root cause

FAIL-002 fixed the parallel problem for **ruff** — it added `[tool.ruff] extend-exclude = ["spike", ".claude"]` so the py312 linter would not touch throwaway spike code or the 3.9 governance hooks. That reasoning ("spike/ and .claude/ are not py312 app source") applies identically to **mypy**, but the exclusion was never propagated to `[tool.mypy]`. A tool-config gap: two tools scan the same tree, one was scoped and the other was left global. `spike/` being gitignored made it invisible in diffs, so the gap survived until a full-tree `mypy .` run surfaced it.

## Fix

Added the mirror exclusion to `pyproject.toml`:

```toml
[tool.mypy]
exclude = ['^spike/', '^\.claude/']
```

`uv run mypy .` now reports `Success: no issues found in 9 source files`. (`.claude/` is also excluded for the same reason FAIL-002 excluded it from ruff — hooks run under system python 3.9, not the py312 venv.)

## Regression eval added

**Guardrail in place (self-enforcing):** the `[tool.mypy] exclude` makes `uv run mypy .` — the standing typecheck gate — pass. This gate *is* the regression check: if the exclude is ever removed, `mypy .` fails loudly again (locally and, once it lands, as required CI job 1 `lint+typecheck`, unit g). Unlike FAIL-002 (whose hook-crash needed a *new* CI smoke step because the normal lint gate does not execute hooks), this failure is fully covered by the existing `mypy .` gate — no separate stub is required. Entry **closed**: guardrail present and verified (`mypy .` green, 9 source files).
