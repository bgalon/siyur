#!/usr/bin/env python3
"""Siyur course-feed capture hook (disler pattern, stdlib-only).

Registered in .claude/settings.json for SessionStart, UserPromptSubmit,
PostToolUse, PostToolUseFailure, PreCompact, SessionEnd. Appends one JSON line
per event to logs/events.jsonl (gitignored) as raw material for /devlog, and on
PreCompact backs up the transcript before context is lost.

Invoked as:  python3 .claude/hooks/log_event.py <EventName>
Reads the hook JSON payload on stdin. Always exits 0 and never blocks — this is
passive capture; it must never disrupt a session.
"""
import sys
import os
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    event = sys.argv[1] if len(sys.argv) > 1 else "Unknown"

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except (ValueError, TypeError):
        payload = {"_unparsed": raw[:2000]}

    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR", "."))
    logs = project_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)

    # Salient, low-volume fields per event — never the full tool payloads.
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "session_id": payload.get("session_id"),
        "cwd": payload.get("cwd"),
    }
    if event in ("PostToolUse", "PostToolUseFailure"):
        record["tool_name"] = payload.get("tool_name")
        if event == "PostToolUseFailure":
            resp = payload.get("tool_response")
            record["error"] = str(resp)[:500] if resp is not None else None
    elif event == "UserPromptSubmit":
        prompt = payload.get("prompt") or ""
        record["prompt_preview"] = prompt[:280]
    elif event == "SessionStart":
        record["source"] = payload.get("source")
    elif event == "SessionEnd":
        record["reason"] = payload.get("reason") or payload.get("source")
    elif event == "PreCompact":
        record["trigger"] = payload.get("trigger")

    with (logs / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    # PreCompact: preserve the transcript before it is compacted away.
    if event == "PreCompact":
        tpath = payload.get("transcript_path")
        if tpath and Path(tpath).is_file():
            backups = logs / "transcripts"
            backups.mkdir(parents=True, exist_ok=True)
            sid = payload.get("session_id") or "unknown"
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            try:
                shutil.copy2(tpath, backups / f"{sid}-{stamp}.jsonl")
            except OSError:
                pass  # never fail the session over a backup

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Passive capture must never disrupt the session.
        sys.exit(0)
