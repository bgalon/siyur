---
description: Distill the current session into a committed devlog entry
allowed-tools: Read, Write, Bash(ls:*), Bash(cat:*), Bash(git log:*)
---

Write a devlog entry for this session.

1. Read `docs/devlog/README.md` for the entry shape.
2. Reconstruct the session from what actually happened (you have the live context; `logs/events.jsonl` holds the captured event trail if you need timing/tool counts). Do not fabricate.
3. Produce `docs/devlog/YYYY-MM-DD-<slug>.md` (today's date; slug from the session's theme) with: **Goal**, **What happened** (the real path, dead ends, surprises), **Decisions** (→ ADR-NNNN links), **Failures** (→ FAIL-NNN links + their regression eval), **Cost / turns** (rough), and **Exhibit-tag candidates** (teachable moments worth an `exhibit/<unit>-<slug>` tag — proposed, for Ben to approve; units are U0–U7 from the course syllabus).
4. Show Ben the entry and the exhibit-tag candidates before committing.

Optional focus/slug: $ARGUMENTS
