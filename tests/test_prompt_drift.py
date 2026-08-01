"""Unit tests — the prompt registry must not drift from the shipped constant (T067).

`prompts/research.md` §3.2 mirrors `planner.nodes.curate.RANKING_SYSTEM_PROMPT`, and §3.1
says outright that the **constant is authoritative and the document is the bug** on any
disagreement. That rule was unenforced: §5 ("Drift") records the gap and names this test as
the cheap guard. This is that guard.

**What "equal" means here.** The comparison is *paragraph-normalised*, not byte-exact: the
registry hard-wraps the prompt's prose to the file's column limit, so the mirror carries
soft line breaks the shipped string does not. Wrapping is a markdown rendering artefact, not
prompt content. Everything that *is* content — every word, every character of punctuation,
and the paragraph structure (blank lines are preserved as paragraph boundaries) — still has
to match exactly. `test_the_normaliser_does_not_hide_a_real_change` pins that the
normalisation cannot be what makes a genuine edit pass.

**Locating the block** is done by its section (`### 3.2 …` → the next fenced block), never
by a line offset, so ordinary edits above it cannot silently break the guard — and if the
section or its fence disappears, the test fails loudly rather than vacuously passing.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from planner.nodes.curate import RANKING_SYSTEM_PROMPT

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPT_DOC = REPO_ROOT / "prompts/research.md"
#: Where the mirror lives, for the failure messages.
DOC_SECTION = "prompts/research.md §3.2"
CONSTANT = "planner/nodes/curate.py::RANKING_SYSTEM_PROMPT"

#: `### 3.2 …` up to the first fenced block after it. `re.S | re.M` so `.` spans the
#: heading-to-fence gap and `^` anchors to line starts.
_MIRROR_BLOCK = re.compile(r"^###\s+3\.2\b.*?^```[^\n]*\n(?P<body>.*?)^```", re.S | re.M)

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")

DRIFT_MESSAGE = (
    f"prompt drift: the mirrored text in {DOC_SECTION} no longer matches "
    f"{CONSTANT}.\n"
    f"**The constant is authoritative** (prompts/research.md §3.1): whatever the code "
    f"sends is what ships, so the fix is to update the document's fenced block to match "
    f"the constant — not the other way round.\n"
    f"If the *shipped* text changed on purpose, update the block AND bump `version` in "
    f"the doc's front-matter in the same PR (§4.1), which also resets "
    f"`linked_eval_score.score` to null."
)


def normalise(text: str) -> str:
    """Collapse soft wrapping, keep paragraph structure.

    Whitespace runs inside a paragraph become a single space; blank lines stay as
    paragraph boundaries. Two texts compare equal iff they carry the same words, in the
    same order, in the same paragraphs.
    """
    paragraphs = _PARAGRAPH_BREAK.split(text.strip())
    return "\n\n".join(" ".join(paragraph.split()) for paragraph in paragraphs)


def mirrored_prompt() -> str:
    """The fenced block under §3.2, located by its section heading."""
    document = PROMPT_DOC.read_text(encoding="utf-8")
    match = _MIRROR_BLOCK.search(document)
    assert match is not None, (
        f"could not find the mirrored prompt in {DOC_SECTION}: expected a `### 3.2 …` "
        f"heading followed by a fenced block. Either the section was renamed/removed or "
        f"its fence was dropped — this guard cannot silently stop checking, so fix the "
        f"document or this locator."
    )
    return match.group("body")


def test_the_mirror_section_exists_and_is_locatable() -> None:
    """The guard must fail loudly, not vacuously pass, if §3.2 moves or disappears."""
    assert mirrored_prompt().strip(), f"{DOC_SECTION} holds an empty fenced block"


def test_the_registry_mirror_matches_the_shipped_constant() -> None:
    assert normalise(mirrored_prompt()) == normalise(RANKING_SYSTEM_PROMPT), DRIFT_MESSAGE


@pytest.mark.parametrize(
    ("mutation", "what_changed"),
    [
        (lambda text: text.replace("most salient first", "least salient first"), "a word"),
        (lambda text: text.replace("NOTHING", "nothing"), "letter case"),
        (lambda text: text.replace("no coordinates, ", ""), "a dropped constraint"),
        (lambda text: text.replace(" — no prose", ", no prose"), "punctuation"),
        (lambda text: text.replace("\n\n", "\n"), "the paragraph structure"),
    ],
)
def test_the_normaliser_does_not_hide_a_real_change(
    mutation: Callable[[str], str], what_changed: str
) -> None:
    """Guard the guard: re-wrapping is tolerated, editing is not.

    Without this, a normaliser that over-folded (e.g. stripping punctuation, or collapsing
    blank lines too) would make the drift test pass on a genuinely changed prompt — a
    green check asserting nothing.
    """
    mutated = mutation(RANKING_SYSTEM_PROMPT)
    assert mutated != RANKING_SYSTEM_PROMPT, "the mutation did not change the text"
    assert normalise(mutated) != normalise(RANKING_SYSTEM_PROMPT), (
        f"the normaliser erased a change to {what_changed} — it is too lenient to be a drift guard"
    )


def test_rewrapping_the_document_block_is_tolerated() -> None:
    """The mirror is prose in a 100-column file; its line breaks are not prompt content."""
    rewrapped = " ".join(RANKING_SYSTEM_PROMPT.split("\n\n")[0].split()).replace(" ", "\n", 3)
    assert "\n" in rewrapped
    assert normalise(rewrapped) == normalise(RANKING_SYSTEM_PROMPT.split("\n\n")[0])
