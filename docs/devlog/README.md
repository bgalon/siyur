# Devlog — the build's dev diary

One entry per decision-bearing session: `YYYY-MM-DD-<slug>.md`. Produced by `/devlog`, which distills the session (hook-captured JSONL in `logs/`, gitignored) into a committed summary. The course harvests these (syllabus U2, U7).

## Entry shape

```markdown
# YYYY-MM-DD — <title>

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
