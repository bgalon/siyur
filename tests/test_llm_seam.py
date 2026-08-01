"""Seam-purity tripwire (T020, ADR-0004 Confirmation).

ADR-0004's whole "replace or extend the provider later is a localized change" property
rests on one invariant: **no provider SDK is imported anywhere above ``commons/llm.py``**.
That is a mechanical rule, so it gets a mechanical hook rather than reviewer vigilance
(Constitution Article VI). This test parses every module under ``commons/``, ``planner/``,
``api/`` and ``compiler/`` with :mod:`ast` and fails on any ``anthropic`` / ``openai`` /
``litellm`` import outside the seam.

Why AST and not grep: grep matches the word inside a docstring, a comment, or a string
literal — this file's own module docstring names all three SDKs — and grep misses nothing
*and* flags everything. The AST sees only real ``Import`` / ``ImportFrom`` nodes, at any
nesting depth (module level, inside a function, inside a ``try``), which is exactly the
set of statements that can bind a provider package.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Packages whose modules must stay provider-free.
SCANNED_PACKAGES = ("commons", "planner", "api", "compiler")

#: Provider SDKs. Importing any of these binds provider types into the caller.
PROVIDER_SDKS = frozenset({"anthropic", "openai", "litellm"})

#: The one module ADR-0004 exempts — the seam itself.
SEAM_MODULE = REPO_ROOT / "commons" / "llm.py"


class Offence(NamedTuple):
    path: Path
    lineno: int
    imported: str

    def describe(self) -> str:
        rel = self.path.relative_to(REPO_ROOT)
        return f"{rel}:{self.lineno} imports provider SDK {self.imported!r}"


def _root_package(dotted: str) -> str:
    return dotted.split(".", 1)[0]


def provider_imports(path: Path) -> list[Offence]:
    """Every provider-SDK import in ``path``, found by parsing — not by matching text."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[Offence] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            # `import litellm`, `import anthropic as sdk`, `import openai.types`
            for alias in node.names:
                if _root_package(alias.name) in PROVIDER_SDKS:
                    found.append(Offence(path, node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            # `from litellm import completion`. A relative import (level > 0) has no
            # provider root by construction — `node.module` is repo-internal.
            if node.level == 0 and node.module is not None:
                if _root_package(node.module) in PROVIDER_SDKS:
                    found.append(Offence(path, node.lineno, node.module))
    return found


def scanned_modules() -> list[Path]:
    return sorted(
        path for package in SCANNED_PACKAGES for path in (REPO_ROOT / package).rglob("*.py")
    )


def test_no_provider_sdk_imported_outside_the_seam() -> None:
    offences = [
        offence
        for path in scanned_modules()
        if path != SEAM_MODULE
        for offence in provider_imports(path)
    ]
    assert not offences, (
        "ADR-0004 seam breach — a provider SDK is imported outside commons/llm.py.\n"
        + "\n".join(f"  - {o.describe()}" for o in offences)
        + "\nRoute the call through commons.llm.ModelRouter instead; only the seam may "
        "import anthropic / openai / litellm."
    )


def test_scan_covers_every_package() -> None:
    """A tripwire that scans nothing passes forever — assert it found real modules."""
    scanned = scanned_modules()
    assert SEAM_MODULE in scanned
    for package in SCANNED_PACKAGES:
        assert any(p.is_relative_to(REPO_ROOT / package) for p in scanned), (
            f"no modules scanned under {package}/ — the glob is broken, not the code"
        )


def test_detector_sees_the_seams_own_import() -> None:
    """Proof the detector works: the seam *does* import a provider SDK, lazily."""
    imported = {offence.imported for offence in provider_imports(SEAM_MODULE)}
    assert "litellm" in imported, (
        "commons/llm.py no longer imports a provider SDK — either the adapter moved "
        "(update SEAM_MODULE) or this detector has stopped detecting."
    )


def test_detector_catches_both_import_forms(tmp_path: Path) -> None:
    """`import x`, `from x import y`, aliases and function-local imports all count."""
    module = tmp_path / "offender.py"
    module.write_text(
        "import os\n"
        "import anthropic\n"
        "import openai.types as t\n"
        "from litellm import completion\n"
        "from . import sibling\n"
        "def late():\n"
        "    import litellm\n"
        "    return litellm, completion, anthropic, t, sibling, os\n",
        encoding="utf-8",
    )
    offences = provider_imports(module)
    assert [(o.lineno, o.imported) for o in offences] == [
        (2, "anthropic"),
        (3, "openai.types"),
        (4, "litellm"),
        (7, "litellm"),
    ]


def test_detector_ignores_mentions_that_are_not_imports(tmp_path: Path) -> None:
    """The grep failure mode: docstrings and strings naming an SDK are not imports."""
    module = tmp_path / "innocent.py"
    module.write_text(
        '"""Talks about anthropic, openai and litellm without importing them."""\n'
        'PROVIDERS = ("anthropic", "openai", "litellm")\n'
        "# import litellm\n",
        encoding="utf-8",
    )
    assert provider_imports(module) == []
