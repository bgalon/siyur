# Failure catalog

Every real failure — a wrong turn, a stale-API emission, a spec ambiguity the agent guessed wrong, a tool gap — gets a `/failure` entry: `FAIL-NNN.md`. **An entry does not close until it has added a regression eval or a guardrail** (this is the loop that keeps the golden set growing). The course harvests these honestly, including cost (syllabus U2, U3, U7).

## Entry shape

```markdown
# FAIL-NNN — <short symptom>

- Date: YYYY-MM-DD · Severity: low | med | high
- Root-cause class: prompt-gap | stale-API | spec-ambiguity | tool-gap | data-quality | other

## Symptom
<what went wrong, observably>

## Trajectory excerpt
<the relevant steps / the offending output>

## Root cause
<why it happened>

## Fix
<what changed — code / prompt / AGENTS.md rule / hook>

## Regression eval added
<REQUIRED before closing: golden-set case id or guardrail/test path>
```
