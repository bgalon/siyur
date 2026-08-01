#!/usr/bin/env python3
"""Report which tool calls prompted for approval, and propose rules to stop them.

    python3 scripts/permission_report.py                # whole log
    python3 scripts/permission_report.py --since 2026-08-01
    python3 scripts/permission_report.py --rules        # just the paste-able rules

Reads `logs/events.jsonl` (gitignored, written by .claude/hooks/log_event.py) and
classifies every captured call against the allow rules in `.claude/settings.json`
plus `.claude/settings.local.json`:

  ALLOWED   a rule matched -> ran silently
  PROMPTED  ran, but no rule matched -> the harness must have asked
  BLOCKED   attempted (PreToolUse) with no PostToolUse -> denied or rejected

"PROMPTED" is the interesting bucket: those are the approvals you granted by hand
and could have granted once, as a rule. Stdlib only; never writes anything.

Caveat: only calls made *after* the PreToolUse hook was registered (2026-08-01)
carry a command string. Older rows log `tool_name` alone and are reported as
UNKNOWN rather than silently counted as allowed.
"""

from __future__ import annotations

import argparse
import collections
import fnmatch
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVENTS = REPO / "logs" / "events.jsonl"
SETTINGS = [REPO / ".claude" / "settings.json", REPO / ".claude" / "settings.local.json"]

# `Bash(git status:*)` -> ("Bash", "git status:*");  `Edit(./docs/**)` -> ("Edit", "./docs/**")
_RULE = re.compile(r"^(\w+)\((.*)\)$")


def load_rules(section: str) -> list[tuple[str, str]]:
    """Every (tool, pattern) pair in the named permissions section, both files."""
    rules: list[tuple[str, str]] = []
    for path in SETTINGS:
        if not path.is_file():
            continue
        try:
            perms = json.loads(path.read_text(encoding="utf-8")).get("permissions", {})
        except ValueError:
            continue
        for entry in perms.get(section, []):
            match = _RULE.match(entry)
            rules.append(match.groups() if match else (entry, "*"))
    return rules


def matches(rule_pattern: str, target: str) -> bool:
    """Claude Code rule semantics, approximated closely enough to tune rules.

    `cmd:*` is a prefix match on the command; a bare rule is an exact match;
    path rules are globs. This is a reporting heuristic, not the harness's own
    matcher — treat a surprising result as a prompt to check the real config.
    """
    if rule_pattern in ("*", ""):
        return True
    if rule_pattern.endswith(":*"):
        return target.startswith(rule_pattern[:-2])
    if any(ch in rule_pattern for ch in "*?["):
        candidates = {target}
        if target.startswith(str(REPO)):
            rel = "./" + str(Path(target).relative_to(REPO))
            candidates |= {rel, rel[2:]}
        return any(fnmatch.fnmatch(c, rule_pattern) for c in candidates)
    return target == rule_pattern


def target_of(row: dict) -> str | None:
    for key in ("command", "file_path", "notebook_path", "pattern", "url", "skill"):
        if row.get(key):
            return str(row[key])
    return None


def classify(row: dict, allow, deny, ask) -> str:
    tool, target = row.get("tool_name"), target_of(row)
    if target is None:
        return "UNKNOWN"
    for label, rules in (("DENY", deny), ("ASK", ask), ("ALLOWED", allow)):
        for rule_tool, pattern in rules:
            if rule_tool == tool and matches(pattern, target):
                return "ALLOWED" if label == "ALLOWED" else label
    return "PROMPTED"


_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def suggest(command: str) -> str:
    """The narrowest rule that would have covered this command.

    Leading `VAR=value` assignments are stripped before choosing the head. They
    are why a command can be *prompted* despite an apparently matching rule:
    `Bash(uv run:*)` is a prefix match, so `FOO=bar uv run ...` does not match
    it. Suggesting a rule keyed on the assignment would be useless (and would
    embed a redacted secret), so we key on the real executable instead.
    """
    words = command.strip().split()
    while words and _ENV_ASSIGN.match(words[0]):
        words = words[1:]
    if not words:
        return "Bash(*)"
    head = words[0]
    # Two-word heads are the useful granularity for multi-verb CLIs.
    if head in {"git", "gh", "uv", "docker", "pnpm", "npm", "cargo", "kubectl"} and len(words) > 1:
        return f"Bash({head} {words[1]}:*)"
    return f"Bash({head}:*)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", help="ISO date prefix, e.g. 2026-08-01")
    parser.add_argument("--rules", action="store_true", help="print only suggested rules")
    parser.add_argument("--top", type=int, default=25)
    args = parser.parse_args()

    if not EVENTS.is_file():
        print(f"no event log at {EVENTS} — nothing to report", file=sys.stderr)
        return 1

    rows = []
    for line in EVENTS.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if args.since and not str(row.get("ts", "")).startswith(args.since):
            if args.since > str(row.get("ts", "")):
                continue
        rows.append(row)

    allow, deny, ask = load_rules("allow"), load_rules("deny"), load_rules("ask")

    pre = {r.get("tool_use_id"): r for r in rows if r.get("event") == "PreToolUse"}
    post_ids = {r.get("tool_use_id") for r in rows if r.get("event") == "PostToolUse"}
    blocked = [r for tid, r in pre.items() if tid is not None and tid not in post_ids]
    # The newest Pre with no Post is almost always this very invocation: its own
    # PostToolUse has not been written yet. Reporting it as blocked is a false
    # positive, so drop the in-flight tail.
    if blocked and rows and blocked[-1].get("ts") == max(r.get("ts", "") for r in rows):
        blocked.pop()

    buckets: collections.Counter[str] = collections.Counter()
    prompted: collections.Counter[str] = collections.Counter()
    for row in rows:
        if row.get("event") != "PostToolUse":
            continue
        verdict = classify(row, allow, deny, ask)
        buckets[verdict] += 1
        if verdict in ("PROMPTED", "ASK"):
            target = target_of(row) or ""
            if row.get("tool_name") == "Bash":
                rule = suggest(target)
            else:
                # Suggest the containing directory, not the individual file.
                parent = str(Path(target).parent)
                if parent.startswith(str(REPO)):
                    rel = Path(parent).relative_to(REPO)
                    parent = f"./{rel}" if str(rel) != "." else "."
                rule = f"{row['tool_name']}({parent}/**)"
            existing = {f"{t}({p})" for t, p in allow}
            if rule in existing:
                # The rule is present but did not fire. Almost always a leading
                # `VAR=value` assignment (rules prefix-match the raw command) or
                # a compound `a && b`. Worth surfacing — adding it again is a
                # no-op, so a naive suggestion would be actively misleading.
                rule += "   [RULE EXISTS — did not match: env-var prefix or compound command]"
            prompted[rule] += 1

    if not args.rules:
        print("=" * 66)
        print(f"Permission report — {len(rows)} events, {len(allow)} allow rules")
        print("=" * 66)
        for name in ("ALLOWED", "PROMPTED", "ASK", "DENY", "UNKNOWN"):
            if buckets[name]:
                note = {
                    "UNKNOWN": "  (logged before command capture; re-run after more usage)",
                    "PROMPTED": "  <- approvals you granted by hand",
                }.get(name, "")
                print(f"  {name:<9} {buckets[name]:>5}{note}")
        if blocked:
            print(f"\n  BLOCKED   {len(blocked):>5}  (attempted, never ran — denied or rejected)")
            for row in blocked[: args.top]:
                print(f"      {row.get('tool_name')}: {target_of(row) or '<no command captured>'}")
        print()

    if prompted:
        print("Rules that would have prevented the hand-approvals, most valuable first:")
        for rule, count in prompted.most_common(args.top):
            print(f'  {count:>4}x   "{rule}",')
    else:
        print("No prompted calls found in range — either fully covered, or the log")
        print("predates command capture. Use the session normally and re-run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
