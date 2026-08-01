"""Cross-engine string-normalization tripwire (FAIL-005).

`planner/nodes/resolve_area.py` pushes a name prefilter down into DuckDB, and the first
version of it folded the needle in **Python** and the column in **SQL**. Those are not the
same function, so a Greek query matched nothing — silently, and only for non-Latin scripts
(SC-005 is exactly the property that broke). `tests/test_resolve_area.py` pins that one call
site; this file pins the **class**, in three layers:

1. **The oracle** — the repo's Python fold and the SQL arm it generates are measured
   against each other over a non-Latin corpus *and over every codepoint*, and any
   disagreement fails. The pin tables below are now **empty**: since the two arms are
   generated from one definition in `planner/nodes/resolve_area.py`, there is nothing left
   to tolerate. A new disagreement (a new script, a DuckDB upgrade, a change to the repo's
   fold) turns this red instead of turning a user's query into an empty result.
2. **The mechanism** — a real DuckDB table shows what each comparison shape does: the
   shipped shape (fold *both* operands in SQL, hand SQL the raw needle) matches across
   composition, case *and* full-vs-simple folding; the pre-fix shape (fold in Python with
   `casefold`, compare in SQL) still misses, which is why the base is `str.lower()`.
3. **The tripwire** — every SQL literal in the repo is parsed, and any comparison whose
   operands carry *different* normalization wrappers fails the build. That is the shape the
   original bug had (`contains(lower(col), $needle)` with a Python-folded `$needle`), so a
   reintroduction cannot merge. Paired with an AST check that a Python-folded value is not
   handed to a database as a parameter.

**Tier 1 (unit, CI job 2)** — same tier and same spirit as `tests/test_geo_api_pins.py`: an
in-process tripwire over a real library's *current* behaviour, no fixture files, no
containers, no network. DuckDB is imported and queried in memory, which is what the geo-pin
test does with shapely/h3/osmnx; the eval tier is for model-output quality, and nothing here
involves a model.

The general rule this file exists to enforce, stated once: **when two engines must agree on
a string, pin the agreement with a test — never assume it.** Fold both operands of a
comparison inside the *same* engine, and generate that engine's fold from the *same*
definition the Python side uses. `str.casefold()` performs Unicode **full** case folding
(1:N mappings: ``ß`` becomes ``ss``); DuckDB's ``lower()`` performs **simple** lowercasing
(1:1); they disagree on 274 codepoints, including exactly the letters a Greek or Armenian
place name is likely to end in. That is why `resolve_area`'s fold is built on *simple*
lowercasing — the one both engines can compute — with the 1:N recall added back by shared
tables rather than inherited from whichever engine happened to run.
"""

from __future__ import annotations

import ast
import re
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import Final, NamedTuple

import duckdb
import pytest

# The repo's fold — *both* arms — imported rather than re-implemented: this test must fail
# if those change, not if a copy of them does. `_normalize` is the Python arm and
# `_fold_sql` generates the DuckDB arm from the same tables, which is what makes the
# oracle below a comparison of the shipped code rather than of two transcriptions.
from planner.nodes.resolve_area import (
    _DIVISIONS_QUERY,
    _WHITESPACE_MAX,
    _fold_sql,
    _normalize,
)

REPO_ROOT: Final = Path(__file__).resolve().parents[1]

#: Packages whose SQL the tripwire parses.
SCANNED_PACKAGES: Final = ("commons", "planner", "api", "compiler")


# ======================================================================================
# Layer 1 — the oracle: does the repo's Python fold agree with DuckDB's?
# ======================================================================================


class Sample(NamedTuple):
    label: str
    text: str


#: Non-Latin display names of the shape Siyur actually resolves, plus the two Latin controls
#: that make the failure visible when someone only ever tests in English. Greek is M1
#: (Rhodes); Hebrew is the next language per ADR-0010, so it is here in three spellings —
#: bare, pointed, and a Unicode presentation form — because composition is the other axis.
SAMPLES: Final[tuple[Sample, ...]] = (
    Sample("greek precomposed polytonic", "Ἀθῆναι"),
    Sample("greek decomposed polytonic", unicodedata.normalize("NFD", "Ἀθῆναι")),
    Sample("greek perispomeni alone", "ῆτα"),
    Sample("greek monotonic tonos", "Αθήνα"),
    Sample("greek uppercase", "ΠΛΑΤΕΙΑ"),
    Sample("greek plain", "Πλατεία"),
    Sample("greek final sigma", "Ρόδος"),
    Sample("hebrew bare", "סגולה"),
    Sample("hebrew pointed nfc", unicodedata.normalize("NFC", "יְרוּשָׁלַיִם")),
    Sample("hebrew pointed nfd", unicodedata.normalize("NFD", "יְרוּשָׁלַיִם")),
    Sample("hebrew presentation form", "אַ" + "רץ"),  # ALEF WITH PATAH, decomposable
    Sample("cyrillic", "Сгула"),
    Sample("arabic", "القدس"),
    Sample("armenian ligature", "Երևան"),
    Sample("georgian", "თბილისი"),
    Sample("latin control", "São Paulo"),
    Sample("turkish dotted capital i", "İstanbul"),
    Sample("german sharp s", "Straße"),
)

#: The **complete** set of corpus entries on which the two folds disagree, with the reason.
#: **Empty, and that is the point.** It held four entries while the Python arm folded with
#: `str.casefold()` (full, 1:N) and the SQL arm with `lower()` (simple, 1:1): final sigma,
#: the Armenian ew ligature, İ, and ß. `resolve_area` now generates both arms from one
#: definition, so there is nothing left to tolerate — see the resolution note in
#: docs/failures/FAIL-005.md. Kept rather than deleted because the machinery around it is
#: what fails when a *future* divergence appears, and an entry added here must carry a
#: stated reason. Both directions still fail: a new divergence, or a listed one healing.
KNOWN_FULL_FOLD_EXPANSIONS: Final[dict[str, str]] = {}


@pytest.fixture(scope="module")
def con() -> Iterator[duckdb.DuckDBPyConnection]:
    """An in-memory DuckDB — no extension, no file, no network."""
    connection = duckdb.connect()
    try:
        yield connection
    finally:
        connection.close()


#: The shipped SQL arm, generated by the module under test — never transcribed here.
SQL_FOLD: Final = _fold_sql("$v")


def duckdb_fold(con: duckdb.DuckDBPyConnection, text: str) -> str:
    """The fold the shipped prefilter applies **inside SQL** to both of its operands."""
    row = con.execute(f"SELECT {SQL_FOLD}", {"v": text}).fetchone()
    assert row is not None
    return str(row[0])


def duckdb_simple_lower(con: duckdb.DuckDBPyConnection, text: str) -> str:
    """DuckDB's bare ``lower()`` — the *simple* fold the shipped arm is built on top of."""
    row = con.execute("SELECT lower($v)", {"v": text}).fetchone()
    assert row is not None
    return str(row[0])


def test_python_fold_and_duckdb_fold_agree_except_on_the_pinned_expansions(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """The oracle. Unpinned drift in either direction is a build failure."""
    surprises: list[str] = []
    healed: list[str] = []
    for sample in SAMPLES:
        python_side = _normalize(sample.text)
        sql_side = duckdb_fold(con, sample.text)
        agree = python_side == sql_side
        if not agree and sample.label not in KNOWN_FULL_FOLD_EXPANSIONS:
            surprises.append(f"  - {sample.label}: python={python_side!r} duckdb={sql_side!r}")
        if agree and sample.label in KNOWN_FULL_FOLD_EXPANSIONS:
            healed.append(f"  - {sample.label}: both engines now give {python_side!r}")

    assert not surprises, (
        "FAIL-005: Python's fold and DuckDB's fold have drifted apart on a sample that used "
        "to agree.\n"
        + "\n".join(surprises)
        + "\nThe prefilter can therefore drop a row `_match_confidence` would have scored "
        "above zero — a silently empty answer. Both arms are generated from the tables in "
        "planner/nodes/resolve_area.py, so fix them there; only pin a sample in "
        "KNOWN_FULL_FOLD_EXPANSIONS, with its reason, if it genuinely cannot be closed."
    )
    assert not healed, (
        "FAIL-005: a pinned divergence has disappeared — good news, but the pin is now a "
        "lie.\n"
        + "\n".join(healed)
        + "\nRemove the entry from KNOWN_FULL_FOLD_EXPANSIONS (a DuckDB upgrade most "
        "likely changed lower(); check the rest of the corpus in the same breath)."
    )


#: Codepoint range the exhaustive oracle sweeps: every assigned plane-0 and plane-1
#: character, surrogates excluded (they are not text and cannot be bound as a parameter).
_SWEEP_START: Final = 0x20
_SWEEP_STOP: Final = 0x30000
_SURROGATE_LOW: Final = 0xD800
_SURROGATE_HIGH: Final = 0xDFFF


def test_the_two_arms_agree_on_every_codepoint_not_merely_on_the_corpus(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """The oracle, exhaustively: 18 samples prove a fold works, they cannot prove it safe.

    A corpus can only ever pin the scripts someone thought of — and FAIL-005 is a failure
    of *genericity* (SC-005), so the interesting input is by definition the one nobody
    listed. Because both arms are generated from one definition, the far stronger claim is
    available and cheap (~1s): they agree on **every** codepoint. Nothing here is sampled.
    """
    rows = con.execute(
        f"SELECT chr(i::INTEGER), {_fold_sql('chr(i::INTEGER)')} "
        f"FROM range({_SWEEP_START}, {_SWEEP_STOP}) t(i) "
        f"WHERE i NOT BETWEEN {_SURROGATE_LOW} AND {_SURROGATE_HIGH}"
    ).fetchall()
    assert len(rows) > 190_000, f"the sweep only reached {len(rows)} codepoints"
    divergent = [(char, _normalize(char), sql) for char, sql in rows if _normalize(char) != sql]
    assert not divergent, (
        "FAIL-005: the Python arm and the SQL arm of resolve_area's fold disagree on "
        f"{len(divergent)} codepoint(s) — the prefilter can now silently drop rows the "
        "scorer would accept.\n"
        + "\n".join(
            f"  - U+{ord(char):04X} python={python!r} duckdb={sql!r}"
            for char, python, sql in divergent[:20]
        )
    )


def test_python_casefold_is_full_folding_and_duckdb_lower_is_simple(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """The root cause, character by character — why the fold is built the way it is.

    ``str.casefold()`` still expands these and DuckDB's ``lower()`` still does not; that
    has not been fixed and cannot be. What changed is that `resolve_area` no longer *asks*
    the two to agree: it folds on **simple** lowercasing, which both engines compute
    identically, and adds the expansions back from a shared table. If this test ever goes
    red, the premise of that design has moved and the tables must be re-derived.
    """
    expansions = {
        "ς": "σ",  # ς -> σ  (Greek final sigma: every second Greek place name)
        "և": "եւ",  # և -> եւ (Armenian ligature)
        "ß": "ss",  # ß -> ss
    }
    for source, folded in expansions.items():
        assert source.casefold() == folded, "Python no longer does full case folding"
        assert duckdb_simple_lower(con, source) == source, (
            f"DuckDB's lower() now expands {source!r} — it used to be simple (1:1) "
            "lowercasing. Re-derive resolve_area's _FOLD_EXPANSIONS/_FOLD_VARIANTS."
        )
        # ...and the shipped fold closes the gap the two raw functions leave open.
        assert duckdb_fold(con, source) == _normalize(source) == _normalize(folded), (
            f"the shipped fold no longer unifies {source!r} with {folded!r}"
        )


def test_the_whitespace_table_covers_every_codepoint_python_calls_whitespace() -> None:
    """`_FOLD_WHITESPACE` is derived under a bound; the bound itself must stay true.

    The Python arm collapses whitespace with `str.split()`, the SQL arm with a `translate`
    table generated from `range(_WHITESPACE_MAX)`. A codepoint Python splits on above that
    bound would be collapsed by one arm and not the other — the FAIL-005 shape again, in
    whitespace rather than in case.
    """
    above = [code for code in range(_WHITESPACE_MAX, 0x110000) if not chr(code).split()]
    assert not above, (
        "Python treats these codepoints as whitespace but resolve_area's derivation stops "
        f"below them: {[hex(code) for code in above]}. Raise _WHITESPACE_MAX."
    )


#: Corpus entries on which the **SQL** arm is *not* composition invariant. **Empty now.**
#: It held İ while the arm was `nfc_normalize(lower(x))` — folding first and normalizing
#: second, so a character whose simple lowercasing depends on its composition escaped
#: (`lower(U+0130)` drops the dot, `lower('I' + U+0307)` keeps it, and normalizing
#: afterwards cannot undo that). The shipped arm normalizes **first** — and again after,
#: to recompose sequences lowercasing itself produces — so a stored NFC name and an NFD
#: query no longer drift apart. Kept, empty, so a future regression still has to be stated.
KNOWN_COMPOSITION_SENSITIVE: Final[dict[str, str]] = {}


def test_the_python_fold_is_composition_invariant(con: duckdb.DuckDBPyConnection) -> None:
    """NFC and NFD spellings of a name must fold to the same key on the Python side."""
    for sample in SAMPLES:
        nfc = unicodedata.normalize("NFC", sample.text)
        nfd = unicodedata.normalize("NFD", sample.text)
        assert _normalize(nfc) == _normalize(nfd), (
            f"the Python fold became composition-sensitive on {sample.label} — two spellings "
            "of one name no longer compare equal"
        )


def test_duckdb_folds_after_normalizing_only_where_it_is_pinned_to(
    con: duckdb.DuckDBPyConnection,
) -> None:
    """The SQL fold's composition invariance, and the ordering that would restore it."""
    surprises: list[str] = []
    healed: list[str] = []
    for sample in SAMPLES:
        nfc = unicodedata.normalize("NFC", sample.text)
        nfd = unicodedata.normalize("NFD", sample.text)
        invariant = duckdb_fold(con, nfc) == duckdb_fold(con, nfd)
        pinned = sample.label in KNOWN_COMPOSITION_SENSITIVE
        if not invariant and not pinned:
            surprises.append(
                f"  - {sample.label}: nfc={duckdb_fold(con, nfc)!r} nfd={duckdb_fold(con, nfd)!r}"
            )
        if invariant and pinned:
            healed.append(f"  - {sample.label}")
        # The fix that is available today, asserted so the recommendation stays true.
        rows = con.execute(
            "SELECT lower(nfc_normalize($a)) = lower(nfc_normalize($b))", {"a": nfc, "b": nfd}
        ).fetchone()
        assert rows is not None and rows[0], (
            f"normalizing before folding is no longer composition-invariant on {sample.label} — "
            "FAIL-005's recommended ordering needs revisiting"
        )
    assert not surprises, (
        "FAIL-005: DuckDB's nfc_normalize(lower(...)) lost composition invariance on a new "
        "sample.\n" + "\n".join(surprises) + "\nSwap the order to lower(nfc_normalize(...)) "
        "in the query that folds it, or pin the sample in KNOWN_COMPOSITION_SENSITIVE."
    )
    assert not healed, (
        "FAIL-005: a pinned composition sensitivity has healed — remove it from "
        "KNOWN_COMPOSITION_SENSITIVE.\n" + "\n".join(healed)
    )


# ======================================================================================
# Layer 2 — the mechanism: what each comparison shape actually does to a Greek row
# ======================================================================================

#: The three shapes, as SQL. Only the first is safe — and it is the *shipped* one,
#: generated by the module under test rather than transcribed, so this layer cannot end up
#: proving something about a shape the product no longer uses.
_BOTH_SIDES_IN_SQL: Final = (
    f"SELECT count(*) FROM names WHERE contains({_fold_sql('v')}, {_fold_sql('$needle')})"
)
_COLUMN_ONLY_LOWER: Final = "SELECT count(*) FROM names WHERE contains(lower(v), $needle)"
_COLUMN_ONLY_NORMALIZED: Final = (
    "SELECT count(*) FROM names WHERE contains(nfc_normalize(lower(v)), $needle)"
)


@pytest.fixture
def names_table(con: duckdb.DuckDBPyConnection) -> Iterator[duckdb.DuckDBPyConnection]:
    con.execute("CREATE OR REPLACE TABLE names(v VARCHAR)")
    yield con
    con.execute("DROP TABLE IF EXISTS names")


def _hits(con: duckdb.DuckDBPyConnection, sql: str, needle: str) -> int:
    row = con.execute(sql, {"needle": needle}).fetchone()
    assert row is not None
    return int(row[0])


def test_the_shipped_shape_matches_across_case_and_composition(
    names_table: duckdb.DuckDBPyConnection,
) -> None:
    """Fold both operands in SQL, hand SQL the **raw** needle: every spelling matches."""
    stored = "Ἀθῆναι"
    for spelling in (stored, unicodedata.normalize("NFD", stored)):
        names_table.execute("DELETE FROM names")
        names_table.execute("INSERT INTO names VALUES ($v)", {"v": spelling})
        for needle in (stored, unicodedata.normalize("NFD", stored), stored.upper()):
            assert _hits(names_table, _BOTH_SIDES_IN_SQL, needle) == 1, (
                f"the shipped prefilter shape missed {needle!r} against stored {spelling!r}"
            )


def test_a_casefolded_needle_silently_matches_nothing_the_original_fail_005(
    names_table: duckdb.DuckDBPyConnection,
) -> None:
    """The bug as it shipped-and-was-caught: `casefold()` in Python, `lower()` in SQL.

    `casefold()` *decomposes* the precomposed polytonic vowel, `lower()` leaves it composed,
    and the result is zero rows — not an error, not a warning, an empty answer that reads
    like "no such place".
    """
    stored = "Ἀθῆναι"
    names_table.execute("INSERT INTO names VALUES ($v)", {"v": stored})
    assert _hits(names_table, _COLUMN_ONLY_LOWER, stored.casefold()) == 0
    # ...and the reason is composition, not case: the same needle recomposed does match.
    recomposed = unicodedata.normalize("NFC", stored.casefold())
    assert _hits(names_table, _COLUMN_ONLY_LOWER, recomposed) == 1


def test_the_prefilter_no_longer_drops_a_row_the_python_scorer_would_have_accepted(
    names_table: duckdb.DuckDBPyConnection,
) -> None:
    """The residual FAIL-005 gap, now closed — and the reason the needle is still raw.

    Was `test_even_the_repos_python_fold_still_misses_which_is_why_sql_folds_the_needle`,
    which pinned the *open* gap: a user typing ``Ρόδοσ`` (non-final sigma — ordinary on a
    foreign keyboard) was dropped by the DuckDB prefilter although `_match_confidence`
    would have scored the row 1.0. Both arms of the fold now come from one definition, so
    the two agree and the prefilter cannot under-select.

    The needle is nevertheless *still* handed to SQL unfolded. That is deliberate: the
    equality of the two arms is pinned by a test, not guaranteed by DuckDB, and sending a
    Python-folded needle would make it load-bearing at runtime instead.
    """
    stored = "Ρόδος"  # Rhodes — the M1 demo area, and a textbook final sigma
    typed = "Ρόδοσ"  # ...the same name typed with a medial sigma
    names_table.execute("INSERT INTO names VALUES ($v)", {"v": stored})

    assert _normalize(typed) == _normalize(stored) == duckdb_fold(names_table, stored)
    assert _hits(names_table, _BOTH_SIDES_IN_SQL, typed) == 1
    assert _hits(names_table, _BOTH_SIDES_IN_SQL, stored.upper()) == 1

    # ...while a *casefolded* needle compared against a bare `lower()` column still misses,
    # which is why the fold is built on simple lowercasing rather than on `str.casefold()`.
    assert _hits(names_table, _COLUMN_ONLY_NORMALIZED, stored.casefold()) == 0


# ======================================================================================
# Layer 3 — the tripwire: no SQL comparison may normalize its operands differently
# ======================================================================================

#: SQL functions that change a string's comparison form. A comparison whose operands do not
#: carry the *same* chain of these is comparing two different normalizations. ``replace`` /
#: ``translate`` / ``regexp_replace`` / ``trim`` are here because `resolve_area`'s fold is
#: built from them — a fold that the detector did not recognise as folding would make this
#: whole layer silently vacuous, which is a worse failure than a false positive.
FOLDING_SQL_FUNCTIONS: Final[frozenset[str]] = frozenset(
    {
        "lower",
        "upper",
        "lcase",
        "ucase",
        "nfc_normalize",
        "nfd_normalize",
        "normalize",
        "unaccent",
        "replace",
        "translate",
        "regexp_replace",
        "trim",
    }
)

#: Functions whose arguments are compared against each other.
COMPARISON_FUNCTIONS: Final[frozenset[str]] = frozenset(
    {
        "contains",
        "starts_with",
        "ends_with",
        "position",
        "strpos",
        "instr",
        "regexp_matches",
        "regexp_full_match",
        "levenshtein",
        "jaro_similarity",
        "jaro_winkler_similarity",
        "damerau_levenshtein",
    }
)

#: A string literal is in scope exactly when it *folds* something — that is the whole domain
#: of this rule, and it keeps English prose ("please select a where clause") out of the scan.
_FOLD_CALL: Final = re.compile(
    r"\b(" + "|".join(sorted(FOLDING_SQL_FUNCTIONS, key=len, reverse=True)) + r")\s*\(",
    re.IGNORECASE,
)
_CALL: Final = re.compile(r"^\s*([A-Za-z_][A-Za-z_0-9]*)\s*\((.*)\)\s*$", re.DOTALL)
_IDENT_START: Final = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_WORD_OPERATOR: Final = re.compile(r"(?:i?like|similar\s+to)(?![A-Za-z_0-9])", re.IGNORECASE)
#: Characters that can appear in a bare operand token: an identifier, a dotted column, a
#: ``$named`` / ``?`` placeholder, a quoted identifier, a number.
_OPERAND_CHARS: Final = frozenset(
    'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$.?"'
)


class Comparison(NamedTuple):
    """One site where SQL compares two strings."""

    site: str
    operands: tuple[str, ...]


def _balanced(text: str) -> bool:
    depth = 0
    for char in text:
        depth += (char == "(") - (char == ")")
        if depth < 0:
            return False
    return depth == 0


def wrapper_chain(expr: str) -> tuple[str, ...]:
    """The normalization functions wrapping ``expr``, outermost first.

    ``nfc_normalize(lower(names.primary))`` → ``("nfc_normalize", "lower")``; ``$needle`` →
    ``()``. A non-folding call (``length(x)``) ends the chain — it is not a comparison form.

    Multi-argument folding calls are descended into by their **first** argument, so
    ``translate(replace(lower(c), 'a', 'b'), 'x', 'y')`` yields the whole chain rather than
    stopping at the outermost call: a fold expressed with `replace`/`translate` must be as
    visible to this detector as one expressed with `lower`.
    """
    chain: list[str] = []
    current = expr.strip()
    while (match := _CALL.match(current)) and _balanced(match.group(2)):
        name = match.group(1).lower()
        if name not in FOLDING_SQL_FUNCTIONS:
            break
        arguments = _split_top_level(match.group(2))
        if not arguments:
            break
        chain.append(name)
        current = arguments[0]
    return tuple(chain)


def _matching_close(text: str, open_index: int) -> int:
    depth = 0
    for index in range(open_index, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _matching_open(text: str, close_index: int) -> int:
    depth = 0
    for index in range(close_index, -1, -1):
        if text[index] == ")":
            depth += 1
        elif text[index] == "(":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _end_of_string_literal(text: str, start: int) -> int:
    index = start + 1
    while index < len(text):
        if text[index] == "'":
            if text[index + 1 : index + 2] == "'":
                index += 2
                continue
            return index + 1
        index += 1
    return len(text)


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    buffer: list[str] = []
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "'":
            end = _end_of_string_literal(text, index)
            buffer.append(text[index:end])
            index = end
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append("".join(buffer))
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    parts.append("".join(buffer))
    return [part.strip() for part in parts if part.strip()]


def _operand_before(text: str, end: int) -> str:
    index = end - 1
    while index >= 0 and text[index].isspace():
        index -= 1
    if index < 0:
        return ""
    if text[index] == ")":
        open_index = _matching_open(text, index)
        if open_index < 0:
            return ""
        start = open_index - 1
        while start >= 0 and text[start] in _OPERAND_CHARS:
            start -= 1
        return text[start + 1 : index + 1].strip()
    start = index
    while start >= 0 and text[start] in _OPERAND_CHARS:
        start -= 1
    return text[start + 1 : index + 1].strip()


def _operand_after(text: str, start: int) -> str:
    index = start
    while index < len(text) and text[index].isspace():
        index += 1
    if index >= len(text):
        return ""
    if text[index] == "'":
        return text[index : _end_of_string_literal(text, index)]
    end = index
    while end < len(text) and text[end] in _OPERAND_CHARS:
        end += 1
    if end < len(text) and text[end] == "(":
        close = _matching_close(text, end)
        if close > 0:
            return text[index : close + 1].strip()
    return text[index:end].strip()


def _function_comparisons(sql: str) -> list[Comparison]:
    found: list[Comparison] = []
    for match in _IDENT_START.finditer(sql):
        name = match.group(0).lower()
        if name not in COMPARISON_FUNCTIONS:
            continue
        after = match.end()
        while after < len(sql) and sql[after].isspace():
            after += 1
        if after >= len(sql) or sql[after] != "(":
            continue
        close = _matching_close(sql, after)
        if close < 0:
            continue
        arguments = _split_top_level(sql[after + 1 : close])
        if len(arguments) >= 2:
            found.append(Comparison(f"{name}({', '.join(arguments)})", tuple(arguments[:2])))
    return found


def _binary_comparisons(sql: str) -> list[Comparison]:
    found: list[Comparison] = []
    index = 0
    while index < len(sql):
        char = sql[index]
        if char == "'":
            index = _end_of_string_literal(sql, index)
            continue
        operator: str | None = None
        if sql.startswith("<>", index) or sql.startswith("!=", index):
            operator = sql[index : index + 2]
        elif char == "=" and sql[index - 1 : index] not in ("<", ">", "!", "=", ":"):
            if sql[index + 1 : index + 2] != "=":
                operator = "="
        else:
            word = _WORD_OPERATOR.match(sql, index)
            previous = sql[index - 1 : index]
            if word is not None and (not previous or previous not in _OPERAND_CHARS):
                operator = word.group(0)
        if operator is None:
            index += 1
            continue
        left = _operand_before(sql, index)
        right = _operand_after(sql, index + len(operator))
        found.append(Comparison(f"{left} {operator} {right}", (left, right)))
        index += len(operator)
    return found


def comparisons(sql: str) -> list[Comparison]:
    """Every string-comparison site in one SQL statement."""
    return _function_comparisons(sql) + _binary_comparisons(sql)


def _fold_invariant(operand: str) -> bool:
    """True for operands no fold can change: numbers, keywords, lowercase-ASCII literals."""
    text = operand.strip()
    if not text:
        return True
    if text.lower() in {"null", "true", "false"}:
        return True
    if text.replace(".", "", 1).isdigit():
        return True
    if text.startswith("'") and text.endswith("'"):
        body = text[1:-1]
        return body.isascii() and body == body.lower()
    return False


def mismatched_comparisons(sql: str) -> list[str]:
    """Comparisons whose operands are normalized differently — the FAIL-005 shape."""
    problems: list[str] = []
    for comparison in comparisons(sql):
        chains = {operand: wrapper_chain(operand) for operand in comparison.operands}
        if not any(chains.values()):
            continue  # nothing is folded here; not this rule's business
        relevant = {
            operand: chain for operand, chain in chains.items() if not _fold_invariant(operand)
        }
        if len({chain for chain in relevant.values()}) > 1:
            detail = "; ".join(
                f"{operand!r} → {'/'.join(chain) if chain else 'unfolded'}"
                for operand, chain in relevant.items()
            )
            problems.append(f"{comparison.site.strip()}  [{detail}]")
    return problems


def _docstring_nodes(tree: ast.AST) -> set[int]:
    skipped: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                if isinstance(first.value.value, str):
                    skipped.add(id(first.value))
    return skipped


def sql_literals(path: Path) -> list[tuple[int, str]]:
    """Every folding SQL literal in ``path`` — docstrings excluded (prose, not statements)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    skip = _docstring_nodes(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in skip and _FOLD_CALL.search(node.value):
                found.append((node.lineno, node.value))
    return found


def scanned_modules() -> list[Path]:
    return sorted(
        path
        for package in SCANNED_PACKAGES
        for path in (REPO_ROOT / package).rglob("*.py")
        if REPO_ROOT.joinpath(package).is_dir()
    )


def test_no_sql_comparison_normalizes_its_operands_differently() -> None:
    """The reintroduction gate: this is red the moment someone folds only one side."""
    offences: list[str] = []
    for path in scanned_modules():
        for lineno, sql in sql_literals(path):
            for problem in mismatched_comparisons(sql):
                offences.append(f"  - {path.relative_to(REPO_ROOT)}:{lineno} {problem}")
    # The divisions query is *assembled* from `_fold_sql`, so the static literal scan sees
    # only its fragments. Scan the assembled statement too — a generated query must not be
    # a way out of this rule.
    for problem in mismatched_comparisons(_DIVISIONS_QUERY):
        offences.append(f"  - planner/nodes/resolve_area.py::_DIVISIONS_QUERY {problem}")
    assert not offences, (
        "FAIL-005: a SQL comparison normalizes its two operands differently.\n"
        + "\n".join(offences)
        + "\nApply the *same* wrapper chain to both operands inside SQL and pass the user's "
        "string in raw — Python's fold and the database's fold are not the same function "
        "(see planner/nodes/resolve_area.py::_DIVISIONS_QUERY and docs/failures/FAIL-005.md)."
    )


def test_the_shipped_divisions_prefilter_is_what_the_rule_describes() -> None:
    """A tripwire that scans nothing passes forever — assert it really parsed the query."""
    expected = wrapper_chain(_fold_sql("x"))
    # The chain reads outermost-first, so the ordering FAIL-005 recommended — normalize,
    # *then* fold — is the sandwich `nfc_normalize(lower(nfc_normalize(...)))`: an inner
    # normalization the lowercasing sees, and an outer one that recomposes its output.
    sandwich = ("nfc_normalize", "lower", "nfc_normalize")
    windows = [expected[i : i + 3] for i in range(len(expected) - 2)]
    assert sandwich in windows, (
        "the fold no longer normalizes before lowercasing — a stored NFC name and an NFD "
        f"query will drift apart (outermost-first chain {expected})"
    )
    sites = comparisons(_DIVISIONS_QUERY)
    folded = [site for site in sites if any(wrapper_chain(o) for o in site.operands)]
    assert len(folded) >= 4, f"only parsed {len(folded)} folded comparisons out of {len(sites)}"
    for site in folded:
        chains = {wrapper_chain(operand) for operand in site.operands}
        assert chains == {expected}, site
    assert mismatched_comparisons(_DIVISIONS_QUERY) == []


def test_the_scan_reaches_real_repository_sql() -> None:
    modules = scanned_modules()
    assert modules, "no modules scanned — the glob is broken, not the code"
    with_sql = {path for path in modules if sql_literals(path)}
    assert any(path.name == "resolve_area.py" for path in with_sql), (
        "the divisions query is no longer being scanned — did the SQL move or change shape?"
    )


# --- detector self-tests: proof the tripwire detects ------------------------------------


def test_detector_catches_the_original_bug_shape() -> None:
    """The pre-fix prefilter: column folded in SQL, needle folded in Python and passed raw."""
    assert mismatched_comparisons(
        "SELECT id FROM t WHERE contains(lower(names.primary), $needle)"
    ) == [
        "contains(lower(names.primary), $needle)  ['lower(names.primary)' → lower; "
        "'$needle' → unfolded]"
    ]


def test_detector_catches_a_half_normalized_pair_and_an_equality() -> None:
    # nfc_normalize on one side only — the composition half of FAIL-005
    assert mismatched_comparisons(
        "SELECT 1 WHERE contains(nfc_normalize(lower(a)), lower($needle))"
    )
    # the most likely next shape: a plain equality
    assert mismatched_comparisons("SELECT 1 FROM t WHERE lower(name) = $needle")
    # ...and a LIKE
    assert mismatched_comparisons("SELECT 1 FROM t WHERE lower(name) LIKE $pattern")
    # a Postgres unaccent/lower pair applied to one operand only
    assert mismatched_comparisons("SELECT 1 FROM t WHERE unaccent(lower(name)) = lower($needle)")


def test_detector_passes_correctly_normalized_and_unfolded_sql() -> None:
    for innocent in (
        "SELECT 1 WHERE contains(nfc_normalize(lower(a)), nfc_normalize(lower($needle)))",
        "SELECT 1 FROM t WHERE lower(name) = lower($needle)",
        "SELECT 1 FROM t WHERE gers_id = $id AND bbox.xmin <= $maxx AND bbox.xmax >= $minx",
        "SELECT * FROM read_parquet($src, hive_partitioning=1, filename=false) WHERE id = $id",
        "SELECT 1 FROM t WHERE lower(kind) = 'overture'",  # ASCII-lowercase literal is invariant
        "SELECT 1 FROM t WHERE length(trim(n)) > 0",
    ):
        assert mismatched_comparisons(innocent) == [], innocent


def test_detector_ignores_docstrings_and_non_sql_strings(tmp_path: Path) -> None:
    """The grep failure mode: prose and docstrings that *describe* the bug are not the bug."""
    module = tmp_path / "innocent.py"
    module.write_text(
        '"""Explains why contains(lower(x), $needle) is wrong — in prose, not in SQL."""\n'
        'MESSAGE = "please select a where clause"\n'
        "def f() -> str:\n"
        '    """Docstring showing SELECT 1 WHERE lower(a) = $b as an example."""\n'
        "    return MESSAGE\n",
        encoding="utf-8",
    )
    assert sql_literals(module) == []


def test_detector_still_sees_a_real_offending_literal(tmp_path: Path) -> None:
    """...and the same file with a real query in it is scanned and flagged."""
    module = tmp_path / "offender.py"
    module.write_text(
        '"""Innocent docstring."""\n'
        'QUERY = "SELECT id FROM t WHERE contains(lower(name), $needle)"\n',
        encoding="utf-8",
    )
    literals = sql_literals(module)
    assert [lineno for lineno, _ in literals] == [2]
    assert mismatched_comparisons(literals[0][1])


# --- the Python side: a folded value must not reach the database ------------------------

#: Python calls that produce a *folded* string. ``.lower()``/``.upper()`` are here as well as
#: ``.casefold()`` because both diverge from a database's fold on some input.
PYTHON_FOLD_METHODS: Final[frozenset[str]] = frozenset({"casefold", "lower", "upper"})
#: In-repo helpers that wrap those.
PYTHON_FOLD_HELPERS: Final[frozenset[str]] = frozenset({"_normalize", "normalize_name"})
#: Methods that hand their arguments to a database as bound parameters.
DB_PARAMETER_SINKS: Final[frozenset[str]] = frozenset(
    {"execute", "executemany", "exec_driver_sql", "sql", "query"}
)


def _contains_python_fold(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            function = child.func
            if isinstance(function, ast.Attribute) and function.attr in PYTHON_FOLD_METHODS:
                return True
            if isinstance(function, ast.Name) and function.id in PYTHON_FOLD_HELPERS:
                return True
    return False


def _contains_sql_fold(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr in FOLDING_SQL_FUNCTIONS:
                parent = child.func.value
                if isinstance(parent, ast.Name) and parent.id in {"func", "sa", "sqlalchemy"}:
                    return True
    return False


def folded_values_sent_to_sql(path: Path) -> list[str]:
    """Python-folded strings handed to a database, or compared against a SQL-side fold."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offences: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in DB_PARAMETER_SINKS:
                bound: list[ast.expr] = [*node.args[1:], *(k.value for k in node.keywords)]
                if any(_contains_python_fold(argument) for argument in bound):
                    offences.append(f"{path.name}:{node.lineno} folded value bound as a parameter")
        if isinstance(node, ast.Compare):
            sides: list[ast.expr] = [node.left, *node.comparators]
            if any(_contains_sql_fold(side) for side in sides) and any(
                _contains_python_fold(side) for side in sides
            ):
                offences.append(f"{path.name}:{node.lineno} python fold compared to a SQL fold")
    return offences


def test_no_python_folded_value_is_handed_to_a_database() -> None:
    offences = [
        offence for path in scanned_modules() for offence in folded_values_sent_to_sql(path)
    ]
    assert not offences, (
        "FAIL-005: a string folded in Python is being compared by a database engine.\n"
        + "\n".join(f"  - {offence}" for offence in offences)
        + "\nPass the raw string and let SQL fold both operands, or prove the two folds "
        "agree on your inputs by extending SAMPLES in this file."
    )


def test_the_python_side_detector_detects(tmp_path: Path) -> None:
    module = tmp_path / "offender.py"
    module.write_text(
        "def a(con, name):\n"
        "    return con.execute(SQL, {'needle': name.casefold()})\n"
        "def b(con, name):\n"
        "    return con.execute(SQL, {'needle': _normalize(name)})\n"
        "def c(session, name):\n"
        "    return session.query(T).filter(func.lower(T.name) == name.casefold())\n",
        encoding="utf-8",
    )
    assert len(folded_values_sent_to_sql(module)) == 3


def test_the_python_side_detector_allows_the_shipped_call() -> None:
    module = REPO_ROOT / "planner" / "nodes" / "resolve_area.py"
    assert folded_values_sent_to_sql(module) == [], (
        "resolve_area now hands a folded value to DuckDB — that is FAIL-005 all over again."
    )
