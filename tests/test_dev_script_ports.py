"""The dev script's port overrides must reach every tier that needs them.

`scripts/dev.sh` is the documented way to run a stack alongside another session
(AGENTS.md, FAIL-011: "take your own ports"). It honoured ``SIYUR_API_PORT`` for the
API itself but never told the **web** tier, whose vite dev proxy targets ``:8000`` by
default (`web/vite.config.ts`) — so the API moved and the proxy did not follow, every
``/areas``, ``/sites`` and ``/plans`` call answered ``502``, and ``dev.sh status``
reported both tiers up throughout.

The symptom is an empty map, which `vite.config.ts`'s own comment warns "reads as *the
backend is broken* when it is not". So the workflow most likely to hit it was the one
the docs recommend, and the failure pointed at the wrong tier.

Asserted by *executing* the script's configuration block rather than grepping it: a
grep passes on a line that is commented out, mistyped, or shadowed later, and this
whole entry exists because a variable was not set.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DEV_SH = REPO / "scripts" / "dev.sh"


def _exported(env: dict[str, str], name: str) -> str:
    """Run dev.sh's configuration prologue and read one exported variable back.

    The prologue is everything before the first function definition — the block that
    computes ports and exports the environment. Sourcing the whole file would run
    nothing (it dispatches on "$1"), but it would also pull in every helper, so the
    slice keeps the assertion pointed at configuration.
    """
    text = DEV_SH.read_text()
    marker = text.index("bold()")
    prologue = text[:marker].replace("set -euo pipefail", "")
    # `cd "$REPO_ROOT"` is in the prologue and must still resolve.
    script = f'BASH_SOURCE=("{DEV_SH}")\n{prologue}\nprintf "%s" "${{{name}}}"\n'
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", **env}
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_the_web_proxy_follows_a_custom_api_port() -> None:
    """`SIYUR_API_PORT=8001` must move the proxy target too, or the app 502s silently."""
    assert _exported({"SIYUR_API_PORT": "8001"}, "SIYUR_API_ORIGIN") == "http://127.0.0.1:8001"


def test_the_default_api_origin_matches_the_default_api_port() -> None:
    """With nothing overridden the two must still agree — 8000 in both places."""
    assert _exported({}, "SIYUR_API_ORIGIN") == "http://127.0.0.1:8000"


def test_an_explicit_api_origin_still_wins() -> None:
    """A caller pointing the web tier at an API somewhere else is not overridden."""
    env = {"SIYUR_API_PORT": "8001", "SIYUR_API_ORIGIN": "http://elsewhere.test:9000"}
    assert _exported(env, "SIYUR_API_ORIGIN") == "http://elsewhere.test:9000"


@pytest.mark.parametrize(
    ("var", "port", "expected_fragment"),
    [("SIYUR_DB_PORT", "5433", ":5433/siyur"), ("SIYUR_DB_PORT", "5432", ":5432/siyur")],
)
def test_the_database_url_follows_its_port_too(var: str, port: str, expected_fragment: str) -> None:
    """The same class of bug, one tier over — asserted so it cannot appear there next."""
    assert expected_fragment in _exported({var: port}, "SIYUR_DATABASE_URL")
