# Devlog — the build's dev diary

One entry per decision-bearing session: `YYYY-MM-DD-<slug>.md`. Produced by `/devlog`, which distills the session (hook-captured JSONL in `logs/`, gitignored) into a committed summary. The course harvests these (syllabus U2, U7).

## Entry shape

```markdown
# YYYY-MM-DD — <title>

**Covers: YYYY-MM-DD → YYYY-MM-DD**   <!-- optional; only when the session spans days -->

**Goal:** <what this session set out to do>

## What happened
<narrative: the path, the dead ends, the surprises>

## Decisions
- <decision> → ADR-NNNN

## Failures
- <failure> → FAIL-NNN (regression eval: <path>)

## Cost / turns
<rough tokens, turns, wall-clock if known>

## Exhibit-tag candidates
<teachable moments worth an `exhibit/<unit>-<slug>` tag — proposed, for Ben to approve>
```

## A session that spans days

File the entry under the date the decisions landed, and declare the span on a **`**Covers: YYYY-MM-DD → YYYY-MM-DD**`** line near the top. Write both dates in full ISO form: `.claude/hooks/devlog_debt.py` reads that line, so a declared span stops the hook reporting the earlier days as unwritten.

This is not bookkeeping. The hook matched filenames only until 2026-08-14, so 08-09 and 08-10 were flagged as debt on every session start for days after an entry covering them had been merged — and a session nearly reconstructed both from scratch before checking the content of the entry that already covered them. **A debt list that reports work already done gets ignored, which is the one thing this hook must not become.**
