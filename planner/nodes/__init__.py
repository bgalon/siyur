"""Typed tool nodes of the planner pipeline (`planner/AGENTS.md`).

One module per node — ``resolve_area → research → curate → propose_itinerary`` — each a
plain function over commons types. Nodes are pure with respect to the pipeline: they take
their inputs, return a frozen result, and never persist. Orchestration and the checkpoint
live in ``planner/pipeline.py``.
"""
