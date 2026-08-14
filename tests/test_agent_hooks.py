"""Guardrails for the ``.claude/hooks/`` scripts the harness runs on every tool call.

Two things are asserted here, and they fail in opposite directions:

1. **Every hook must survive a degenerate payload** (empty, whitespace, malformed
   JSON) with exit 0. This is the regression guard FAIL-002 specified and never
   got: a hook that crashes takes session logging — and, now, the secret guard —
   down silently while CI stays green. It is asserted for *every* file in
   ``.claude/hooks/``, so a new hook inherits the guard automatically.

2. **``guard_secrets.py`` must actually block the Bash-shaped bypass** of the
   ``Read(**/.env)`` / ``Read(./secrets/**)`` deny rules, and must not block the
   ordinary development commands this repo runs all day. A guard that blocks
   ``uv run pytest`` gets switched off within the hour, so the allow cases matter
   as much as the block cases.

**Known limitation, stated rather than hidden.** The harness runs hooks under bare
system ``python3`` — 3.9 on the dev machine, which is why ``pyproject.toml``
excludes ``.claude/`` from ruff and mypy. This test invokes whatever ``python3`` is
on PATH, which in CI is 3.12. It therefore catches crashes and behavioural
regressions but *not* a 3.11+ syntax idiom that only fails on the older
interpreter. Closing that gap needs a 3.9 interpreter in CI, which means editing
``.github/workflows/**`` — review-gated per ADR-0006.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent / ".claude" / "hooks"
GUARD = HOOKS_DIR / "guard_secrets.py"

# Exit 2 is the PreToolUse "block this call" contract; 0 is "allow".
BLOCK = 2
ALLOW = 0


def _run(script: Path, payload: str) -> subprocess.CompletedProcess[str]:
    """Invoke a hook the way the harness does: bare ``python3``, payload on stdin."""
    return subprocess.run(
        ["python3", str(script)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _bash(command: str) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})


def _hook_scripts() -> list[Path]:
    return sorted(p for p in HOOKS_DIR.glob("*.py") if p.is_file())


def test_hooks_directory_is_not_empty() -> None:
    """Guard the guard: a glob that silently matches nothing would vacuously pass."""
    assert _hook_scripts(), f"no hook scripts found in {HOOKS_DIR}"


@pytest.mark.parametrize("script", _hook_scripts(), ids=lambda p: p.name)
@pytest.mark.parametrize(
    "payload",
    ["", "   \n", "{not json", "{}", '{"tool_name": "Bash"}'],
    ids=["empty", "whitespace", "malformed", "empty-object", "no-tool-input"],
)
def test_hook_survives_degenerate_payload(script: Path, payload: str) -> None:
    """FAIL-002's guard: a hook must never crash the tool call that invoked it."""
    assert _run(script, payload).returncode == ALLOW


@pytest.mark.parametrize(
    "command",
    [
        "cat .env",
        "grep SECRET .env",
        "head ./.env.local",
        "cat api/.env",
        "head -5 ./secrets/google.json",
        "uv run python -c \"print(open('.env').read())\"",
        "cat ~/.ssh/id_rsa",
        "cat my-service-account.json",
        "cat credentials.json",
        "cat ~/.aws/credentials",
        "openssl rsa -in server.pem",
        "uv run python -c 'import os; print(os.environ)'",
    ],
)
def test_guard_blocks_secret_access(command: str) -> None:
    """The hole the path-based deny rules cannot close: Bash reading them directly."""
    result = _run(GUARD, _bash(command))
    assert result.returncode == BLOCK, f"should have been blocked: {command}"
    assert "guard_secrets" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        # The documented local dev invocation — sets a credential, does not read one.
        'SIYUR_DATABASE_URL="postgresql+psycopg://siyur:siyur@localhost:5432/siyur"'
        " uv run uvicorn api.app:app",
        "uv run pytest tests/ -q",
        "uv run ruff check .",
        "uv run mypy .",
        "pnpm -C web test",
        "pnpm -C web build",
        "docker compose up -d postgis",
        "git status --short",
        "grep -rn DATABASE_URL api/",
        # Reading one named variable is how the app is meant to get config;
        # only a wholesale dump is a leak.
        "uv run python -c 'import os; print(os.environ[\"SIYUR_DATABASE_URL\"][:12])'",
    ],
)
def test_guard_allows_ordinary_development(command: str) -> None:
    assert _run(GUARD, _bash(command)).returncode == ALLOW, f"false positive: {command}"


def test_guard_ignores_non_bash_tools() -> None:
    """Path-shaped access is settings.json's job; this hook only reads command strings."""
    payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/x/.env"}})
    assert _run(GUARD, payload).returncode == ALLOW


# --- devlog_debt.py: a debt list that reports finished work gets ignored ---------------------


def _devlog_debt_module() -> ModuleType:
    """Import the hook by path — ``.claude/`` is not a package and is off ruff/mypy's map."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("devlog_debt", HOOKS_DIR / "devlog_debt.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_declared_span_covers_the_days_it_names(tmp_path: Path) -> None:
    """``**Covers: A → B**`` discharges every date in the inclusive range.

    A session spanning several days is filed under the day its decisions landed, so the
    earlier days have no file of their own. Before this, they were reported as debt forever:
    on 2026-08-14 a session came within one command of reconstructing 2026-08-09 and 08-10
    from scratch, days after an entry covering both had been merged.
    """
    entry = tmp_path / "2026-08-11-spanning.md"
    entry.write_text(
        "# 2026-08-11 — a session that ran long\n\n**Covers: 2026-08-09 → 2026-08-11.**\n",
        encoding="utf-8",
    )

    covered = _devlog_debt_module()._covered_dates(entry)

    assert covered == {"2026-08-09", "2026-08-10", "2026-08-11"}


def test_an_entry_with_no_declaration_covers_only_itself(tmp_path: Path) -> None:
    """No ``Covers:`` line means no claim, so the filename remains the only coverage."""
    entry = tmp_path / "2026-08-11-ordinary.md"
    entry.write_text(
        "# 2026-08-11 — an ordinary day\n\n**Goal:** ship a thing.\n", encoding="utf-8"
    )

    assert _devlog_debt_module()._covered_dates(entry) == set()


def test_a_date_merely_narrated_does_not_discharge_a_debt(tmp_path: Path) -> None:
    """Prose mentioning a date is not a claim to have covered it.

    The scan is bounded to the head of the file precisely so "the outage on 2026-08-06" deep
    in a narrative cannot silently mark that day as written up.
    """
    entry = tmp_path / "2026-08-11-narrative.md"
    entry.write_text(
        "# 2026-08-11 — a day\n\n**Goal:** x.\n\n## What happened\n\n"
        + "filler\n" * 20
        + "We finally understood the outage on 2026-08-06.\n",
        encoding="utf-8",
    )

    assert _devlog_debt_module()._covered_dates(entry) == set()


def test_a_reversed_or_absurd_span_is_refused_rather_than_trusted(tmp_path: Path) -> None:
    """An unbounded span would discharge every debt at once, so it yields nothing.

    Failing towards *reporting* debt is the safe direction: the cost of a spurious reminder is
    a few seconds, and the cost of a silent one is a day nobody writes up.
    """
    entry = tmp_path / "2026-08-11-absurd.md"
    entry.write_text("# t\n\n**Covers: 1999-01-01 → 2026-08-11.**\n", encoding="utf-8")

    assert _devlog_debt_module()._covered_dates(entry) == set()
