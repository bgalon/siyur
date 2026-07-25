"""Siyur planner — the typed planning pipeline.

Tool nodes (research / curate / propose) over the model seam (PydanticAI + LiteLLM,
ADR-0004) with an owned Postgres/SQLite checkpoint. Per-task routing: Haiku=research,
Sonnet=curate, Opus=plan (ADR-0004, ratified 2026-07-25).

Skeleton stage (DU-00): package exists and imports; pipeline lands in M1 (DU-02+).
"""
