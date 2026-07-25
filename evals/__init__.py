"""Siyur evals — the three-tier eval harness (test-strategy.md).

Structural (schema / geometry / constraints, merge-blocking), trajectory
(agentevals superset match, merge-blocking), and quality (DeepEval LLM-judge,
nightly). Golden set in ``evals/golden/``; score trend in ``evals/history.csv``.

Skeleton stage (DU-00): package exists; harness + golden set land at DU-00 unit e.
"""
