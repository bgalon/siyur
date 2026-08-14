#!/usr/bin/env python3
"""Surface unwritten devlog entries at session start (stdlib-only, non-blocking).

Registered in .claude/settings.json for SessionStart. Prints a short reminder
naming any recent date that has real commits but no `docs/devlog/<date>-*.md`.
SessionStart stdout is added to the session context, so the next session is told
about the gap instead of discovering it weeks later.

## Why this exists

`AGENTS.md` says "decision-bearing sessions end with /devlog". On 2026-08-06
nine concurrent sessions opened eleven PRs, made four ADR-worthy decisions, and
wrote no entry. The day was reconstructed on 2026-08-07 from `logs/events.jsonl`
— but only the *what* survived; the *why* did not, and the why is the part the
course needs (`exhibit/U2-the-log-that-outlived-the-story`).

A rule that is only prose gets skipped under load. This is the cheapest possible
mechanism: it notices, it says so once, and it never blocks anything.

## Deliberate choices

* **Today is excluded.** You cannot have written today's entry before doing
  today's work, so flagging it would train the reader to ignore this.
* **Merge commits do not count.** A day whose only commits are merges did no
  authoring worth distilling.
* **Bounded lookback** (`_DAYS`). Old gaps are either accepted or already
  reconstructed; an unbounded list would be noise, and noise gets ignored.
* **Silent when clean**, and silent on any error. This is a nudge, not a gate:
  it must never disrupt a session, so every failure path exits 0 quietly. A
  missing git binary, a shallow clone, or a detached HEAD all mean "say nothing".
"""

import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# `timezone.utc`, NOT `datetime.UTC` — hooks run under whatever bare `python3`
# resolves to (macOS system Python 3.9 here), not the repo's uv-managed 3.12.
# `datetime.UTC` is 3.11+, and the ImportError would fire at module import,
# outside main()'s guard, so the hook would crash instead of failing quietly.
# `log_event.py` targets the same interpreter for the same reason. Keep this
# file to the 3.9 standard library.

#: How far back to look. Long enough to catch a gap while it is still
#: reconstructable from `logs/events.jsonl`, short enough that the list stays
#: readable and therefore gets read.
_DAYS = 14

#: Never print more than this many dates — a long list reads as noise.
_MAX_REPORTED = 5


def _commit_dates(root: Path, since: date) -> set[str]:
    """UTC dates (YYYY-MM-DD) with at least one non-merge commit since ``since``."""
    result = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "log",
            "--no-merges",
            f"--since={since.isoformat()}",
            "--date=format-local:%Y-%m-%d",
            "--format=%cd",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        env={"TZ": "UTC", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
    )
    if result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


#: How far into an entry to look for its `**Covers: ... → ...**` line. It belongs directly
#: under the title; scanning the whole file would let a date mentioned in prose ("the outage
#: on 2026-08-06") silently discharge a debt it only narrates.
_COVERS_SCAN_LINES = 8

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


def _covered_dates(entry: Path) -> set:
    """Dates an entry *declares* it covers, via `**Covers: <start> → <end>**` near the top.

    A session that spans days is filed under the date its decisions landed, so the earlier
    days have no file of their own and were reported as debt forever. That is the failure
    mode this function exists to close: on 2026-08-14 a session came within one command of
    reconstructing 08-09 and 08-10 from scratch, days after an entry covering both had been
    merged. **A debt list that reports work already done gets ignored**, and this hook is
    worth nothing the moment it is ignored.

    Two dates are read as an inclusive range; a single date covers itself; more than two are
    taken as an explicit list rather than a range, because a range is only unambiguous with
    exactly two endpoints. Anything unparseable yields nothing and the date stays flagged —
    failing towards *reporting* debt is the safe direction for a nudge.
    """
    try:
        with entry.open(encoding="utf-8") as handle:
            head = [next(handle, "") for _ in range(_COVERS_SCAN_LINES)]
    except OSError:
        return set()

    for line in head:
        if "covers:" not in line.lower():
            continue
        found = _ISO.findall(line)
        if len(found) == 2:
            start, end = sorted(found)
            first = date.fromisoformat(start)
            last = date.fromisoformat(end)
            span = (last - first).days
            # A malformed or reversed span could otherwise cover an unbounded stretch.
            if 0 <= span <= _DAYS * 2:
                return {
                    (first + timedelta(days=offset)).isoformat() for offset in range(span + 1)
                }
            return set()
        return set(found)
    return set()


def main() -> int:
    try:
        root = Path(__file__).resolve().parents[2]
        devlog_dir = root / "docs" / "devlog"
        if not devlog_dir.is_dir():
            return 0

        today = datetime.now(timezone.utc).date()
        worked = _commit_dates(root, today - timedelta(days=_DAYS))
        # A date is covered by any entry whose filename starts with it (the
        # `YYYY-MM-DD-<slug>.md` convention `docs/devlog/README.md` sets), OR by any
        # entry that *declares* it covers that date. A session spanning several days
        # is filed under the day its decisions landed, so the earlier days have no
        # file of their own and were reported as debt indefinitely.
        entries = sorted(devlog_dir.glob("????-??-??-*.md"))
        logged = {p.name[:10] for p in entries}
        for entry in entries:
            logged |= _covered_dates(entry)

        missing = sorted(d for d in worked - logged if d != today.isoformat())
        if not missing:
            return 0

        shown = missing[-_MAX_REPORTED:]
        more = len(missing) - len(shown)
        print(
            "Devlog debt: these dates have commits but no docs/devlog entry — "
            + ", ".join(shown)
            + (f" (+{more} older)" if more else "")
            + ". Reconstruct with /devlog while logs/events.jsonl still covers them, "
            "and mark any reconstruction as such."
        )
    except Exception:  # noqa: BLE001 — a nudge must never disrupt a session
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
