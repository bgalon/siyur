# Prompt registry

Constitution **Article VII** — *"Prompts live in `prompts/` with front-matter (version, model,
date, linked eval score) and move to `production` by label, independently of app deploys."*
(`.specify/memory/constitution.md`). This directory is that registry.

## Index

| File | Covers | Version | Status |
|---|---|---|---|
| [`research.md`](research.md) | slice 001 `research` (no prompt — deterministic) + `curate` ranking | 1 | `production` |

`prompts/planner.md` arrives with DU-04 (`docs/design/delivery-plan.md`).

## Conventions

- **Front-matter is the four Article VII fields** — `version`, `model`, `date`,
  `linked_eval_score` — plus a `status` label for promotion. Do not drop a field because it has
  no value: record the absence (`null`) and say why. An invented model date or a carried-over
  eval score is worse than an honest gap.
- **Model pins mirror `commons.llm.ROUTING_TABLE`; they do not define it.** Undated pins carry
  the `undated_reason` that `ModelPin.__post_init__` already enforces (ADR-0013).
- **The code is authoritative for prompt text** while the registry mirrors it. Any mirrored
  block says so at the point of quotation, and names the constant it mirrors. A second copy that
  claims authority is how prompts drift.
- **A prompt change is a code change:** same PR, same CI checks 1–7, `version` bumped in that PR,
  `linked_eval_score.score` reset to `null` until re-scored.
- **Scores live in `evals/history.csv`**, appended by CI (`eval-quality.yml` job 8). That file
  lands with the judge harness at DU-04; until then registry scores are honestly `null`.
- **Migrations follow the Article VII playbook** — offline trace-replay → shadow → canary, then
  strip the scaffolding the newer model no longer needs. Vibes do not migrate models.
