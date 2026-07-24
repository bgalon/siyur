---
description: Draft a MADR 4.0 minimal ADR from the current session's decision
allowed-tools: Read, Write, Bash(ls:*), Bash(git log:*)
---

Draft an Architecture Decision Record for the decision we just made in this session.

1. Read `docs/adr/README.md` for the exact template and conventions.
2. Determine the next number: the highest existing `docs/adr/NNNN-*.md` + 1 (zero-padded to 4 digits). If none exist, start at `0001`.
3. Reconstruct from THIS session's actual context — do not invent:
   - the problem that forced the decision (link the PRD/spec clause or the FAIL that triggered it),
   - the options actually considered (and any benchmark/result actually observed),
   - the option chosen and the real driver,
   - consequences (good + accepted cost),
   - **Confirmation**: the concrete eval id / CI job / test path that will verify it holds. If none exists yet, write "TODO: add on implementation" and note it.
4. Set `drafted-by: claude-code`, today's date, Status `proposed`, Decision Maker(s): Ben.
5. Write `docs/adr/NNNN-<kebab-title>.md`. Then show Ben a 3-line summary and ask him to confirm/amend before it's considered `accepted`.

Topic (optional): $ARGUMENTS
