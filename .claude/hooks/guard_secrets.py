#!/usr/bin/env python3
"""PreToolUse guard: stop Bash from reading what the permission rules only deny to Read.

WHY THIS EXISTS. `.claude/settings.json` denies `Read(**/.env)`, `Read(./secrets/**)`
and friends. Those rules bind the *Read tool*. They do nothing about
`grep KEY .env`, `head ./secrets/sa.json`, or
`uv run python -c "print(open('.env').read())"` — and `Bash(grep:*)`,
`Bash(python3:*)` and `Bash(uv:*)` are all allowed, because narrowing them would
flood every session with prompts. A path-based deny list cannot close a hole that
lives in an arbitrary command string; a hook can, because it sees the string.

This is DEFENCE IN DEPTH, not the primary gate. The deny rules in settings.json
remain the real boundary. This catches the Bash-shaped bypass of them.

FAILS OPEN, DELIBERATELY. This hook runs on every single Bash call. FAIL-002 was
exactly this class of bug: a `.claude/` hook that crashed under the harness's bare
system python3 (3.9) and silently took session logging with it. A guard that
bricks every tool call in the session is a worse outcome than a guard that misses
a case, so ANY internal error exits 0 and lets the call through. Only a positive,
confident match blocks.

PYTHON 3.9 ONLY. The harness runs `.claude/hooks/*` under bare system python3,
which is 3.9.6 on this machine — not the project's 3.12. No `X | Y` runtime
annotations, no `datetime.UTC`, no `match`. `.claude/` is excluded from ruff and
mypy in pyproject.toml precisely so neither rewrites this file to 3.11+ idioms.

Contract: reads the PreToolUse JSON payload on stdin. Exit 0 = allow, exit 2 =
block with the stderr message handed back to the model.
"""

import json
import re
import sys

# Each entry: (compiled pattern, what it protects). Patterns are deliberately
# narrow — a false negative costs one missed bypass, a false positive costs a
# blocked command and an irritated operator, and this file is defence in depth.
#
# Known false positive, accepted: searching *for* the literal string (e.g.
# `grep -rn "\.env" docs/`) trips the first rule. Rephrase the search, or read
# the file with the Read tool, which is where the real policy lives.
_RULES = (
    # `.env`, `./.env`, `.env.local`, `api/.env` — but NOT `SIYUR_DB=x` inline
    # assignments, and not the bare word "environment".
    (re.compile(r"(?:^|[\s'\"=/])\.env(?:\.[\w.-]+)?(?:$|[\s'\";,)&|])"), ".env files"),
    (re.compile(r"(?:^|[\s'\"=/])secrets/"), "the secrets/ directory"),
    (re.compile(r"\.ssh/"), "SSH keys"),
    (re.compile(r"\bid_(?:rsa|ed25519|ecdsa|dsa)\b"), "SSH private keys"),
    (re.compile(r"[\w.-]*service-account[\w.-]*\.json"), "service-account keys"),
    (re.compile(r"\bcredentials(?:\.\w+)?\b"), "credential files"),
    (re.compile(r"\.(?:pem|p12|pfx|key)(?:$|[\s'\";,)&|])"), "private key material"),
    (re.compile(r"\.aws/"), "AWS credentials"),
    (re.compile(r"\.config/gcloud"), "gcloud credentials"),
    # Wholesale environment dumps: the process env carries SIYUR_DATABASE_URL and
    # the Google OIDC client secret. `env`/`printenv` are already denied in
    # settings.json; these are the routes around that deny.
    # A dump (`print(os.environ)`) is a leak; a single lookup
    # (`os.environ["SIYUR_DATABASE_URL"]`, `.get(...)`) is how the app is meant
    # to read config, so it stays allowed.
    (re.compile(r"os\.environ\b(?!\s*(?:\[|\.get))"), "the process environment"),
    (re.compile(r"\bexport\s+-p\b"), "the process environment"),
)


def _commands(payload):
    """Every shell string worth inspecting in this payload.

    Only Bash carries an arbitrary command string; the file tools are already
    governed by the path rules in settings.json.
    """
    if payload.get("tool_name") != "Bash":
        return []
    command = payload.get("tool_input", {}).get("command")
    return [command] if isinstance(command, str) else []


def main():
    raw = sys.stdin.read()
    if not raw.strip():
        return 0  # no payload (e.g. the CI smoke test) — nothing to judge

    payload = json.loads(raw)
    for command in _commands(payload):
        for pattern, protects in _RULES:
            if pattern.search(command):
                sys.stderr.write(
                    "Blocked by .claude/hooks/guard_secrets.py: this command "
                    "references {0}.\n\n"
                    "AGENTS.md: never read or write .env* or secrets/. Credentials "
                    "come from the process environment only, and must not be echoed "
                    "into a transcript.\n\n"
                    "If you need a config VALUE, read the variable name from the "
                    "code that consumes it (e.g. api/config.py) and ask Ben for the "
                    "value — do not read the file.\n".format(protects)
                )
                return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — see the module docstring: fail open, always.
        sys.exit(0)
