---
description: File a failure-catalog entry plus a mandatory regression eval stub
allowed-tools: Read, Write, Bash(ls:*)
---

File a failure-catalog entry for the failure we just hit.

1. Read `docs/failures/README.md` for the entry shape and the closing rule.
2. Determine the next number: highest existing `docs/failures/FAIL-*.md` + 1 (zero-padded 3 digits; start `001`).
3. Write `docs/failures/FAIL-NNN.md` capturing: **Symptom**, **Trajectory excerpt** (the offending steps/output), **Root cause** + its class (prompt-gap | stale-API | spec-ambiguity | tool-gap | data-quality | other), and **Fix**.
4. **Mandatory:** create a regression eval/guardrail stub so this can't silently recur, and link it in the entry's "Regression eval added" field. If the eval harness doesn't exist yet, create a clearly-marked stub file (e.g. under `evals/` or `tests/`) with a `TODO` and a failing/skipped placeholder, and note that the entry stays **open** until the stub is filled. An entry with no regression eval is not done.
5. Summarize for Ben.

What failed (optional): $ARGUMENTS
