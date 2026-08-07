#!/usr/bin/env python3
"""Surface concurrent checkouts and an unbranched start (stdlib-only, non-blocking).

Registered in .claude/settings.json for SessionStart. Prints a short warning when
either of the two conditions that produced FAIL-008 is true at session start:

1. **Another worktree exists.** `AGENTS.md` requires each concurrent session to
   work in its own checkout. That rule keeps two sessions from racing on files —
   but nothing tells a starting session that another one is *already running*,
   so both can pick the same task off the same `tasks.md` and do it twice.
2. **HEAD is on `main`.** ADR-0005 is one branch per unit of work. Sitting on
   `main` is not yet a violation, but every violation starts there, and the cost
   of noticing at the first edit is zero while the cost of noticing after twenty
   files are dirty is a salvage.

SessionStart stdout is added to the session context, so the session is told
before it does the work rather than after.

## Why this exists

On 2026-08-07 two sessions independently ran Spec 002 Phase 1 and the T007
reconciliation — same tasks, same files, ~2h of duplicated agent work, and only
one could merge. Both had followed the rules they could see: teammates inside
each session were partitioned by folder exactly as CLAUDE.md requires. What
neither session did was look for the *other session*, because no rule said to and
nothing surfaced it. `git worktree list` would have shown it in one command.

The lesson FAIL-008 records is that the isolation rule was written for file
races, and the failure that actually happened was a *task* race, which isolation
does not prevent and visibility does.

## Deliberate choices

* **Warns, never blocks.** A second worktree is normal and often correct; the
  hook's job is to make it *visible*, not to adjudicate it. Blocking would make
  the legitimate case painful and the hook would be removed.
* **Names the branch of each other worktree**, because "another session exists"
  is not actionable while "another session holds `agent/DU-04-setup`" is — that
  is enough to ask it what it is holding before duplicating it.
* **Silent when clean, silent on error.** Same contract as `devlog_debt.py`: a
  missing git binary, a shallow clone or a detached HEAD all mean "say nothing".
* **`main` check is separate from the worktree check**, because they fail
  independently: a solo session on `main` still owes a branch, and a properly
  branched session still needs to know a peer exists.
"""

import subprocess
import sys
from pathlib import Path

# `timezone.utc` style constraints apply here too: hooks run under whatever bare
# `python3` resolves to (macOS system Python 3.9), never the repo's uv-managed
# 3.12. Keep this file to the 3.9 standard library — see devlog_debt.py.

#: Branches that mean "you have not branched yet".
_TRUNK = ("main", "master")

#: Never name more than this many peer worktrees; a long list reads as noise.
_MAX_REPORTED = 4


def _git(root, args):
    """Run a git command, returning stdout or None. Never raises."""
    result = subprocess.run(
        ["git", "-C", str(root)] + args,
        capture_output=True,
        text=True,
        timeout=10,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
    )
    if result.returncode != 0:
        return None
    return result.stdout


def _worktrees(root):
    """[(path, branch)] for every worktree, in `git worktree list` order."""
    out = _git(root, ["worktree", "list", "--porcelain"])
    if not out:
        return []
    entries = []
    path = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = line[len("worktree ") :].strip()
        elif line.startswith("branch ") and path is not None:
            branch = line[len("branch ") :].strip()
            entries.append((path, branch.replace("refs/heads/", "")))
            path = None
        elif line.strip() == "" and path is not None:
            entries.append((path, "(detached)"))
            path = None
    return entries


def main():
    try:
        root = Path(__file__).resolve().parents[2]
        if not (root / ".git").exists():
            return 0

        messages = []

        here = _git(root, ["rev-parse", "--show-toplevel"])
        here = here.strip() if here else str(root)

        others = [(p, b) for p, b in _worktrees(root) if p != here]
        if others:
            shown = others[:_MAX_REPORTED]
            more = len(others) - len(shown)
            named = "; ".join("{} on {}".format(Path(p).name, b) for p, b in shown)
            messages.append(
                "Concurrent checkouts: {} other worktree(s) exist — {}{}. "
                "Another session may be live and working the same tasks. Before "
                "picking up a task list, check what they hold (git log on their "
                "branch, gh pr list, or message the session) — FAIL-008 is two "
                "sessions doing Spec 002 Phase 1 twice.".format(
                    len(others), named, " (+{} more)".format(more) if more else ""
                )
            )

        branch = _git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
        branch = branch.strip() if branch else ""
        if branch in _TRUNK:
            messages.append(
                "You are on '{}'. ADR-0005 is one branch per unit of work: create "
                "agent/<ticket>-<slug> BEFORE the first edit, not after. Branching "
                "later means salvaging a dirty tree.".format(branch)
            )

        if messages:
            print("\n".join(messages))
    except Exception:  # noqa: BLE001 — a nudge must never disrupt a session
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
