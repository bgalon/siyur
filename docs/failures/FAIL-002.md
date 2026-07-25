# FAIL-002 — `ruff --fix` broke a governance hook by rewriting it to a newer-Python idiom

- Date: 2026-07-25 · Severity: low
- Root-cause class: tool-assisted-change / cross-interpreter assumption

## Symptom

While standing up the toolchain (DU-00 unit a), `ruff check --fix` on `.claude/hooks/log_event.py` applied rule **UP017**, rewriting `from datetime import datetime, timezone` + `datetime.now(timezone.utc)` → `from datetime import UTC, datetime` + `datetime.now(UTC)`. `datetime.UTC` exists only in **Python 3.11+**. The harness runs hooks via bare **system `python3` (3.9.6)**, not the uv-managed 3.12 venv, so the rewritten hook raised `ImportError: cannot import name 'UTC' from 'datetime'` and exited non-zero — i.e. **every SessionStart/PostToolUse/etc. hook would have crashed**, silently killing the session-logging + transcript-backup pipeline the course depends on.

## Trajectory excerpt

DU-00 unit a: `pyproject.toml` set `[tool.ruff] target-version = "py312"`; `uv run ruff check .` flagged the D0 hook; `ruff check .claude/hooks/log_event.py --fix` "fixed" it. Verification `echo '{}' | python3 .claude/hooks/log_event.py SessionStart` → `ImportError`. Caught **pre-commit**; the autofix was never committed.

## Root cause

The lint config's `target-version` describes the **application** interpreter (3.12), but `.claude/hooks/*.py` are **harness scripts executed by whatever `python3` the environment provides** — here 3.9. Applying py312-targeted autofixes to code that must run on an older, uncontrolled interpreter introduced a version-incompatible idiom. Generalizes the AGENTS.md stale-API concern in the other direction: not "too old an API", but "too *new* an API for the actual runtime."

## Fix

- Revert the hook to its D0 form (kept 3.9-safe `timezone.utc`).
- Exclude `.claude/` from the py312 ruff scope: `[tool.ruff] extend-exclude = ["spike", ".claude"]` — hooks are not py312 app source and must not be linted/fixed against py312 assumptions.
- Hooks stay written to a 3.9-compatible floor until/unless the harness is guaranteed a newer interpreter.

## Regression eval added

**Guardrail in place:** the `extend-exclude = [".claude", ...]` in `pyproject.toml` prevents ruff (locally and in CI job 1 `lint+typecheck`) from ever again rewriting a hook to a py312-only idiom. **STUB (entry stays OPEN until filled):** a CI smoke step that runs each `.claude/hooks/*.py` under the *system* `python3` with an empty JSON payload and asserts exit 0 — to be added when `ci.yml` lands (DU-00 unit g), so a hook that breaks under the harness interpreter fails CI. Owner: build agent. Tracked so unit g cannot close without it.
