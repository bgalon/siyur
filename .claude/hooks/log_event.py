#!/usr/bin/env python3
"""Siyur course-feed capture hook (disler pattern, stdlib-only).

Registered in .claude/settings.json for SessionStart, UserPromptSubmit,
PreToolUse, PostToolUse, PostToolUseFailure, PreCompact, SessionEnd. Appends one
JSON line per event to logs/events.jsonl (gitignored) as raw material for
/devlog, and on PreCompact backs up the transcript before context is lost.

Invoked as:  python3 .claude/hooks/log_event.py <EventName>
Reads the hook JSON payload on stdin. Always exits 0 and never blocks — this is
passive capture; it must never disrupt a session.

## Permission telemetry (added 2026-08-01)

The log previously recorded only `tool_name`, which made it impossible to answer
"which calls prompted for approval?" — the question that drives permission
tuning. Two additions fix that:

* **`PreToolUse` is now registered.** It fires on every *attempted* call;
  `PostToolUse` fires only on calls that actually ran. So the set difference
  (`PreToolUse` minus `PostToolUse`, keyed on `tool_use_id`) is exactly the set
  of **denied or user-rejected** calls.
* **The command / target is captured** — `command` for Bash, `file_path` for
  file tools. With that plus the allow-rules in settings.json, a call that ran
  but matches no allow rule must have *prompted*. `scripts/permission_report.py`
  does that classification.

**Redaction:** command strings can carry secrets (`FOO_SECRET=… cmd`). Anything
resembling a credential assignment is masked before it is written, and `logs/`
is gitignored, so this never reaches the repo. Commands are truncated to
`_MAX_CMD` characters — this is telemetry for rule-tuning, not a shell history.
"""
import sys
import os
import re
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

_MAX_CMD = 400

#: KEY=value pairs whose key looks credential-ish, in a command string. The value
#: is replaced wholesale — we only ever need the *shape* of a command to tune a
#: permission rule, never the secret it carries.
_SECRET_ASSIGN = re.compile(
    r"\b([A-Za-z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|APIKEY|API_KEY|CREDENTIAL)"
    r"[A-Za-z0-9_]*)\s*=\s*(\S+)",
    re.IGNORECASE,
)
#: Bearer tokens and common provider key prefixes appearing anywhere in a command.
_SECRET_LITERAL = re.compile(
    r"\b(?:Bearer\s+\S+|sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|whsec_[A-Za-z0-9]{8,})"
)


def _redact(text: str) -> str:
    """Mask credential-shaped substrings, then truncate. Never raises."""
    try:
        text = _SECRET_ASSIGN.sub(lambda m: f"{m.group(1)}=<redacted>", text)
        text = _SECRET_LITERAL.sub("<redacted>", text)
        if len(text) > _MAX_CMD:
            text = text[:_MAX_CMD] + f"...(+{len(text) - _MAX_CMD} chars)"
        return text
    except Exception:
        return "<redaction-failed>"


def _target(tool_name, tool_input):
    """The one field identifying what a call acted on, per tool family."""
    if not isinstance(tool_input, dict):
        return {}
    out = {}
    if tool_name == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str):
            out["command"] = _redact(command)
    else:
        # Edit / Write / Read / NotebookEdit key on file_path; Glob/Grep on pattern.
        for key in ("file_path", "notebook_path", "pattern", "url", "skill"):
            value = tool_input.get(key)
            if isinstance(value, str):
                out[key] = _redact(value)
                break
    return out


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
    if event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        record["tool_name"] = payload.get("tool_name")
        # Correlates a PreToolUse with its PostToolUse. A Pre with no matching
        # Post is a call that was denied or rejected — the signal this hook
        # exists to capture.
        record["tool_use_id"] = payload.get("tool_use_id")
        record.update(_target(payload.get("tool_name"), payload.get("tool_input")))
        # Present when the harness supplies it; absent is normal, not an error.
        for key in ("permission_mode", "permissionMode", "permission_decision"):
            if payload.get(key) is not None:
                record["permission_mode"] = payload[key]
                break
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
