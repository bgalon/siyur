@AGENTS.md

<!--
Claude Code-specific notes only; all substantive, tool-neutral guidance lives in AGENTS.md.
- Personal, machine-local overrides go in .claude/settings.local.json (gitignored), never here.
- Permission MODE (defaultMode) lives in ~/.claude/settings.json, never in .claude/settings.json —
  project settings outrank user settings, so a mode pinned here would impose one operator's
  autonomy level on every clone, collaborator and cloud runner. The repo carries only the rule
  surface (allow/ask/deny); see the _comment block in .claude/settings.json.
- Slash commands: /adr, /devlog, /failure (see .claude/commands/). Run /adr at the end of any
  decision-bearing session and /devlog before ending it.
- Hooks in .claude/settings.json capture the session to logs/ (gitignored) as course-feed raw material.
-->

## Commands

Python 3.12 via `uv`; the `web/` PWA via **pnpm** (not npm). These mirror CI jobs 1–4 — green locally should mean green in CI.

```bash
# Test
uv run pytest tests/ -q          # Tier 1 — unit, pure, fast
uv run pytest -q -m integration  # Tier 2 — needs PostGIS (docker compose up -d postgis
                                 #   + export SIYUR_DATABASE_URL=...); exit code 5 = none collected = OK
uv run pytest evals/ -q          # deterministic evals (CI job 4), mocked LLM
pnpm -C web test                 # vitest

# Lint / typecheck
uv run ruff check .
uv run ruff format --check .
uv run mypy .                    # strict
pnpm -C web typecheck            # tsc --noEmit

# Build / install
pnpm -C web build                # vite build
uv sync --locked                 # install from the lockfile
```

## Working efficiently in this repo

Measured over 7,029 captured hook events, two habits dominate the waste:

- **Read and search with the dedicated tools, not the shell.** 426 of 3,262 Bash calls were
  `grep -n` / `grep -rn` / `sed -n` doing what Read, Grep and Glob do better and cheaper.
- **Don't re-`cd` on every call.** The Bash working directory **persists between calls**, yet
  246 commands opened with `cd /Users/beng/code/siyur`. A `cd` inside a compound command can
  also trigger a permission prompt that the bare command would not.

## Delegation

- For any **multi-file task, spawn teammates and partition the work by folder/module** so that no two agents ever edit the same file. The package boundaries are the natural seams: `commons/`, `planner/`, `compiler/`, `api/`, `web/`, `tests/`, `docs/`. Name the owned paths explicitly when dispatching.
- **Always run the `code-reviewer` agent on the changes before finishing.** It is read-only; apply its blocking findings yourself.
- **Team size: 4 maximum.**
- Available subagents (`.claude/agents/`): `code-reviewer` (read-only review), `test-runner` (runs and repairs the suite), `implementer` (feature work inside one package).
- Parallel *sessions* still need their own checkout — a `git worktree` locally, a separate branch/sandbox in the cloud. Teammates inside one session share a working directory, which is exactly why the no-two-agents-per-file partition is not optional.

## Always ask Ben first

Never run these unattended, even in auto mode — they are `ask`-gated in `.claude/settings.json` and that gate is deliberate:

- **`git push`** — and PR creation/merge (`gh pr create`, `gh pr merge`). ADR-0005: one branch per unit of work, integrate via PR, never straight to `main`.
- **Database migrations** — `alembic upgrade`, `alembic revision`, `alembic stamp`, and edits to `alembic/versions/**`. `alembic downgrade` is denied outright.
- **Publishing and deploys** — `gcloud`, `gsutil`, `terraform`, `docker push`, any `publish`.
- **CI config** — `.github/workflows/**` (ADR-0006: security-critical, human-approved per edit).
