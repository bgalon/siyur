# AGENTS.md — `planner/`

Nested override for work in this package; extends the root `AGENTS.md` (read that first).

**Scope:** the typed planning pipeline — tool nodes `resolve_area → research → curate/merge →
propose_itinerary → [HITL: approve] → compile` (PydanticAI orchestration + LiteLLM routing over the
`ModelRouter` seam, ADR-0004) with an **owned** Postgres/SQLite checkpoint (one `UPSERT` per step over
`user_plan`), not LangGraph. Read `docs/design/tech-design.md` §5.2 and ADR-0004 first. Scaffolded from
the **fixed** `spike/planner_spike/` adapter (post the Haiku adaptive/effort gate), not the as-written
reference.

**Invariants enforced here:**
- **Seam purity (ADR-0004):** all model calls go through `commons/llm.py`; no `anthropic` / `openai` /
  `litellm` import appears in `planner/`. Enforced by `tests/test_llm_seam.py` (DU-02+).
- **Per-task routing:** Haiku=research, Sonnet=curate, Opus=plan (ratified 2026-07-25). The seam gates
  adaptive-thinking / `output_config.effort` behind `SUPPORTS_ADAPTIVE_EFFORT` — Haiku 4.5 400s on them.
- **Determinism discipline:** the LLM ranks/curates and writes prose; it **never** emits coordinates or
  does spatial/temporal arithmetic — typed tool nodes (PostGIS/DuckDB/shapely, opening_hours.js) do, and
  are unit-tested with a mocked model.
- **HITL:** an explicit persisted pause at itinerary approval; review nodes stay side-effect-free.

**Status (DU-00):** package imports; the pipeline lands in M1 (DU-02+).
