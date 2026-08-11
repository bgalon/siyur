"""The FAIL-009 regression check — `scripts/merge-guard.sh` must refuse a red PR.

FAIL-009: `main` went red twice in two days because PRs merged with failing checks. Neither
operator was ignorant of the rule — both checked, and the check lied, because
`gh pr checks | tail -4` hides failures that sort to the top.

Article IV: a failure closes only with a regression guard. These are that guard's guards.

**Every case here is one that actually happened**, which is why the file is short and why
each test names its incident. The second case is the one a hand-rolled guard gets wrong: an
all-green payload with a job *missing* is not a pass, and PR #93 demonstrated that a missing
signal reads as innocent.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD = REPO_ROOT / "scripts/merge-guard.sh"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="merge-guard.sh needs jq")


def _named(prefix: str, state: str = "SUCCESS") -> dict[str, str]:
    return {"name": f"{prefix} · some description", "state": state}


def all_seven(state: str = "SUCCESS") -> list[dict[str, str]]:
    """A payload shaped like `gh pr checks --json name,state` on a healthy PR."""
    return [_named(str(n), state) for n in range(1, 8)] + [
        {"name": "8 · llm-judge-evals", "state": "SUCCESS"},
        {"name": "claude-review (advisory)", "state": "SUCCESS"},
    ]


def run(payload: list[dict[str, str]], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    f = tmp_path / "checks.json"
    f.write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.run(
        ["bash", str(GUARD), "--json", str(f)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_an_all_green_pr_is_allowed(tmp_path: Path) -> None:
    result = run(all_seven(), tmp_path)
    assert result.returncode == 0, result.stderr
    assert "0 failing" in result.stdout


def test_a_failing_job_blocks_the_merge(tmp_path: Path) -> None:
    """PR #92, exactly: jobs 4 and 7 red, everything else green.

    The merging session saw four green rows through `tail -4` and merged. The guard must
    reach the opposite conclusion from the same facts.
    """
    payload = all_seven()
    payload[3]["state"] = "FAILURE"  # 4 · deterministic-evals
    payload[6]["state"] = "FAILURE"  # 7 · diff-guard
    result = run(payload, tmp_path)
    assert result.returncode != 0
    assert "2 non-passing" in result.stderr


def test_a_missing_required_job_blocks_even_when_nothing_is_failing(tmp_path: Path) -> None:
    """The case a hand-written guard gets wrong.

    Every reported check passes — there is simply no job 5. Counting failures returns 0 and
    a naive guard says yes. `5 · e2e-airplane` is the **merge gate** (Constitution Article
    I); merging without it is merging without the release control, not with a green one.
    """
    payload = [c for c in all_seven() if not c["name"].startswith("5 ·")]
    assert all(c["state"] == "SUCCESS" for c in payload), "precondition: nothing is failing"
    result = run(payload, tmp_path)
    assert result.returncode != 0
    assert "missing required job" in result.stderr
    assert "5 ·" in result.stderr


def test_a_pending_job_is_not_a_passing_one(tmp_path: Path) -> None:
    """PR #84 was merged while its checks were still resolving."""
    payload = all_seven()
    payload[1]["state"] = "PENDING"
    result = run(payload, tmp_path)
    assert result.returncode != 0


def test_no_checks_at_all_is_not_a_pass(tmp_path: Path) -> None:
    """An empty payload is the most dangerous input: it fails no check because it has none."""
    result = run([], tmp_path)
    assert result.returncode != 0
    assert "No checks is not the same as passing" in result.stderr
