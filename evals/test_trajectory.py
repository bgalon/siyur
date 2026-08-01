"""Trajectory eval — the emitted phase sequence is a superset of the contract (T048).

Constitution Article II: *a right answer reached by a wrong or inefficient path still
fails*. The reference path is :data:`planner.pipeline.PHASES` —
``resolve_area → research → curate`` — and the match mode is **superset**, deliberately:
the pipeline will grow phases (``propose_itinerary``, ``compile``) and that must not
redden this gate, while a **missing** phase must.

`agentevals`' superset matcher answers membership only, so relative **order** is asserted
here on top of it. Both halves are load-bearing: a pipeline that curated before it
researched would satisfy membership and be wrong.

Runs in CI job 4 (`eval-quality.yml` → `deterministic-evals`) with a mocked model, over
the committed Rhodes fixtures: no network, no API key, no database.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Final

import pytest
from agentevals.trajectory.match import create_trajectory_match_evaluator

from planner.pipeline import PHASES, Event
from tests.test_pipeline import drain, phases_of
from tests.test_planner_curate import FakeRouter

#: Superset over tool *names*; arguments are not part of the contract being matched.
_MATCH: Final = create_trajectory_match_evaluator(
    trajectory_match_mode="superset", tool_args_match_mode="ignore"
)


def as_trajectory(phases: Sequence[str]) -> list[dict[str, Any]]:
    """Phases as the OpenAI-shaped tool-call trajectory `agentevals` matches on.

    A pipeline phase *is* the unit `agentevals` calls a tool call — one assistant turn
    whose calls are the phases, in the order they were emitted.
    """
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": phase, "arguments": "{}"}} for phase in phases],
        }
    ]


def is_superset(emitted: Sequence[str], reference: Sequence[str] = PHASES) -> bool:
    """Does ``emitted`` contain every phase of ``reference``? (membership, not order)"""
    result = _MATCH(outputs=as_trajectory(emitted), reference_outputs=as_trajectory(reference))
    return bool(result["score"])


@pytest.fixture(scope="module")
def mocked() -> list[Event]:
    """A pass with the model in the loop — mocked, offline, and asked only to reorder."""
    return drain(router=FakeRouter(reply=json.dumps([])))


def test_the_emitted_phases_are_a_superset_of_the_contract(mocked: list[Event]) -> None:
    emitted = phases_of(mocked)
    assert emitted, "the pass emitted no status frame; there is no trajectory to match"
    assert is_superset(emitted), (
        f"the pass emitted {emitted}, which is missing "
        f"{[p for p in PHASES if p not in emitted]} of the contract {list(PHASES)}"
    )


def test_the_contract_phases_are_emitted_in_order(mocked: list[Event]) -> None:
    """Superset covers membership; this covers the half it cannot see.

    ``research`` fires once per adapter, so the assertion is on spans, not indices: every
    frame of one contract phase precedes the first frame of the next.
    """
    emitted = phases_of(mocked)
    spans = [
        (phase, emitted.index(phase), len(emitted) - 1 - emitted[::-1].index(phase))
        for phase in PHASES
        if phase in emitted
    ]
    assert [phase for phase, _, _ in spans] == list(PHASES), (
        f"a contract phase never fired: emitted {emitted}"
    )
    for (before, _, last), (after, first, _) in zip(spans, spans[1:], strict=False):
        assert last < first, (
            f"{before!r} must be complete before {after!r} begins, but the pass emitted {emitted}"
        )


def test_an_extra_phase_still_matches_but_a_missing_one_does_not(mocked: list[Event]) -> None:
    """The matcher is configured `superset`, not `strict` — the whole point of T048.

    Without this, a future strict-match config change would go unnoticed until the next
    pipeline node was added and every eval reddened at once.
    """
    emitted = phases_of(mocked)
    assert is_superset([*emitted, "propose_itinerary", "compile"]), (
        "extra phases must be allowed: the pipeline grows"
    )
    for dropped in PHASES:
        assert not is_superset([p for p in emitted if p != dropped]), (
            f"dropping {dropped!r} must fail the match, but it passed"
        )


def test_the_model_cannot_change_the_path(mocked: list[Event]) -> None:
    """The trajectory is the pipeline's, not the model's.

    A model that refuses, times out, or tries to hand back a value costs *ordering* at
    most (`planner/nodes/curate.py`); the sequence of phases is unchanged by any of it.
    """
    deterministic = phases_of(drain())
    refusing = phases_of(drain(router=FakeRouter(reply='[{"id": "x", "name": "invented"}]')))
    dead = phases_of(drain(router=FakeRouter(raises=TimeoutError("model unreachable"))))

    assert deterministic == phases_of(mocked) == refusing == dead
    assert is_superset(deterministic)
