"""Eval-tier placeholder gate (DU-00 unit d).

Stands the eval tier up green *before* the features, so the merge gate exists from
day one (test-strategy.md: structural + trajectory evals are merge-blocking). The
real harness lands at DU-00 unit e:

    evals/
      golden/            versioned dataset (~25 tour-day cases)
      test_structural.py schema / geometry / constraints — merge-blocking
      test_trajectory.py agentevals superset-match on the node sequence
      test_quality.py    DeepEval LLM-judge — nightly, threshold-gated
      history.csv        score trend, appended by CI

This placeholder only asserts the eval libraries resolved into the environment
(via ``find_spec`` — no heavy import, no DeepEval telemetry side effects at
collection time). Delete it when ``test_structural.py`` / ``test_trajectory.py``
arrive.
"""

import importlib.util


def test_eval_libs_installed() -> None:
    for lib in ("deepeval", "agentevals"):
        assert importlib.util.find_spec(lib) is not None, f"eval lib not installed: {lib}"
