"""Walking-skeleton smoke test (DU-00 unit a).

Proves the toolchain resolves and every Python package imports. It is deliberately
trivial — real unit/structural/trajectory tests land at DU-00 unit e and beyond.
This is one of the "green on stubs" gates that must exist before features do.
"""

import importlib

import pytest


@pytest.mark.parametrize("package", ["commons", "planner", "compiler", "api", "evals"])
def test_package_imports(package: str) -> None:
    assert importlib.import_module(package) is not None
