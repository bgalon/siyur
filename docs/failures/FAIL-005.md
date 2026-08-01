# FAIL-005 — Two engines, two folds: a Python-folded needle matched nothing in DuckDB

- Date: 2026-08-01 · Severity: med
- Root-cause class: integration (cross-engine assumption — "`lower()` is `casefold()`")

## Symptom

`planner/nodes/resolve_area.py` (T032) resolves a free-text area name by pushing a coarse
containment prefilter down into DuckDB over the Overture **divisions** theme. The first
version folded the user's needle in **Python** (`str.casefold()`) and the division's name in
**SQL** (`lower()`), then compared the two. For a Greek name carrying a precomposed
polytonic vowel this returned **zero rows** — not an error, not a warning, an empty result
that reads exactly like "there is no such place".

```
needle (python)  casefold("Ἀθῆναι") -> 'ἀθῆναι'   U+1F00 U+03B8 U+03B7 U+0342 …   (decomposed)
column (duckdb)  lower("Ἀθῆναι")    -> 'ἀθῆναι'   U+1F00 U+03B8 U+1FC6 …          (composed)
contains(column, needle) -> false -> 0 candidates -> AreaUnresolvable("no area matched …")
```

Two properties make this worse than an ordinary miss:

- **It is silent.** A wrong-but-plausible empty answer, produced by code with no exception
  path, at the very first node of the pipeline — every downstream stage is defined over the
  polygon this node fails to produce.
- **It only bites non-Latin scripts.** ASCII input folds identically in both engines, so an
  English-only test pass is meaningless here. This is precisely **SC-005 (genericity)** and
  precisely the Greek → Hebrew path (ADR-0010) the product is built around.

## Trajectory excerpt

Caught **during implementation of T032, by the implementing session's own test, before the
PR merged** — not in production, and not by a reviewer. The session wrote
`test_overture_divisions_folds_case_script_and_composition`, watched it fail against a real
DuckDB read of the committed divisions fixture, and traced the empty result to the fold
rather than to the fixture or the geometry. Nothing was ever shipped in the broken state.

## Root cause

`str.casefold()` and DuckDB's `lower()` are different functions, along **two independent
axes**, and the code assumed they were one:

1. **Composition.** Python's full case folding *decomposes* some precomposed letters —
   ῆ (U+1FC6) becomes η + U+0342 — where DuckDB's `lower()` leaves the codepoint composed.
   Same word, different bytes, no match.
2. **Fold breadth.** `str.casefold()` implements Unicode **full** case folding, which
   includes 1:N mappings; DuckDB's `lower()` implements **simple** (1:1) lowercasing. They
   still disagree today, on exactly the letters non-Latin place names are made of:

   | input | Python `casefold()` | DuckDB `lower()` |
   |---|---|---|
   | `Ρόδος` (Greek final sigma ς) | `ρόδοσ` | `ρόδος` |
   | `Երևան` (Armenian ligature և) | `երեւան` | `երևան` |
   | `İstanbul` (U+0130) | `i` + U+0307 … | `i` … |
   | `Straße` | `strasse` | `straße` |

The deeper mistake is not the mapping table — nobody memorises Unicode — it is that **an
equality between two engines was assumed rather than pinned**. There was no test anywhere
asserting the two folds agreed, so the assumption was invisible until it produced a wrong
answer.

## Fix

`planner/nodes/resolve_area.py::_DIVISIONS_QUERY` now folds **both operands inside SQL** and
passes `$needle` **raw** (whitespace-collapsed only), with `nfc_normalize` on both sides so
composition cannot differ:

```sql
WHERE contains(nfc_normalize(lower(names.primary)), nfc_normalize(lower($needle)))
   OR contains(nfc_normalize(lower($needle)), nfc_normalize(lower(names.primary)))
```

Python still re-scores every returned row with `_normalize` (`casefold` + NFC) — but on
*both* sides of that comparison, inside Python. Each engine now compares strings it folded
itself; neither is asked to agree with the other. The sibling regression test is
`tests/test_resolve_area.py::test_overture_divisions_folds_case_script_and_composition`.

Note what the fix did **not** do: it did not make the two folds equal (see the table above —
they are still unequal). It removed the *need* for them to be equal. That distinction is the
entry's whole lesson.

## Blast radius

The general rule, which is bigger than this call site:

> **Cross-engine string normalization must be pinned, not assumed.** Any comparison where
> one side is normalized by Python and the other by a different engine — DuckDB, Postgres /
> PostGIS `lower()`/`unaccent`, SQLite, Elasticsearch, a JS `toLowerCase()` in `web/` — is a
> silent-wrong-answer waiting for a non-Latin input. Fold both operands **inside one
> engine**, or add the inputs to the corpus in
> `tests/test_cross_engine_normalization.py` and let the test prove the two folds agree.

Sites audited on 2026-08-01 (`a6f9441`). No second live instance of the bug exists today;
these are the places where it would next appear:

| Site | Status |
|---|---|
| `planner/nodes/resolve_area.py::_DIVISIONS_QUERY` | fixed; two residual gaps below |
| `commons/merge.py::normalize_name` (NFKD → strip marks → NFKC + `casefold`) | **Python-only today, and a third distinct fold.** Safe while merge stays in-memory; the moment name matching is pushed into Postgres (a `lower()`/`unaccent` join, a trigram index) it becomes this bug. |
| `commons/repository.py` | SQLAlchemy expression API, no text folding yet. A name search added here would run Postgres `lower()`, which is **locale-dependent** — a wider divergence than DuckDB's. |
| `commons/sources/overture.py` | bbox numeric predicates only; no folding. |
| `commons/translit.py`, `commons/licenses.py`, `api/config.py` | `.lower()` on BCP-47 tags / license ids / env flags — ASCII-only, never crosses an engine. Not exposed. |
| `web/` | no text comparison against a server-side fold yet; JS `toLocaleLowerCase()` is a *fourth* fold when it arrives. |

## Two residual gaps found while writing the guardrail (not fixed here — file ownership)

Both are pinned by tests in this PR so they cannot rot, and both are for the owner of
`planner/nodes/resolve_area.py` to decide on, not for this session to patch:

1. **The SQL prefilter and the Python re-scorer still disagree on final sigma.** A user who
   types `Ρόδοσ` (non-final sigma — ordinary when typing on a foreign keyboard) is dropped by
   the DuckDB prefilter, although `_match_confidence` would have scored the row `1.0`. The
   prefilter is the gate, so the row is never seen. Same class, same silence, still open.
   Pinned by `test_even_the_repos_python_fold_still_misses_which_is_why_sql_folds_the_needle`.
2. **`nfc_normalize(lower(x))` normalizes in the wrong order.** Folding first and normalizing
   second is not composition-invariant: `lower(U+0130)` drops the dot (`i`) while
   `lower('I' + U+0307)` keeps it, and normalizing afterwards cannot undo the difference — so
   a stored NFC name and an NFD query drift apart. **`lower(nfc_normalize(x))` — normalize
   first — is invariant over the whole corpus**, verified in
   `test_duckdb_folds_after_normalizing_only_where_it_is_pinned_to`.

## Recommendation (not done here)

There are now **three** independent Python folds in the repo — `resolve_area._normalize`,
`merge.normalize_name`, and the SQL expression embedded in `_DIVISIONS_QUERY` — each correct
for its own use and none of them interchangeable. Extract a single documented folding helper
(`commons/text.py`, say) that owns *both* the Python form and the SQL expression that matches
it, so the rule becomes mechanical: **only the helper folds, and its two forms are tested
against each other.** This session deliberately did **not** perform that refactor —
`planner/` and `commons/` are other sessions' files — and it belongs in an ADR-bearing change
rather than a failure-catalog PR.

## Regression eval added

**Entry closed** — `tests/test_cross_engine_normalization.py`, **Tier 1 (unit)**, merge-blocking
via CI job 2. Not the eval tier: evals gate model-output quality, and no model is involved
here; this is a determinism invariant over two libraries, so it belongs beside
`tests/test_geo_api_pins.py` — in-process, no fixtures, no containers, no network.

Three layers, because the sibling's test pins one call site and this pins the class:

1. **The oracle** — 18 samples (Greek precomposed/decomposed/polytonic/final-sigma, Hebrew
   bare/pointed-NFC/pointed-NFD/presentation-form, Cyrillic, Arabic, Armenian, Georgian, plus
   Latin, Turkish and German controls). Every disagreement between the repo's Python fold and
   DuckDB's must be one of four **pinned** entries with a stated reason; an unpinned
   divergence fails, and so does a pinned one that silently heals (a DuckDB upgrade changing
   `lower()` must be noticed, not absorbed).
2. **The mechanism** — a real in-memory DuckDB table demonstrating what each comparison shape
   does: the shipped shape matches across case *and* composition; the pre-fix shape returns
   zero rows; a Python-folded needle still misses today.
3. **The tripwire** — every folding SQL literal under `commons/`, `planner/`, `api/`,
   `compiler/` is parsed (AST for the literals, a small SQL operand parser for the
   comparisons) and **any comparison whose two operands carry different normalization
   wrappers fails the build**. Plus an AST check that a Python-folded value is never bound as
   a database parameter or compared against a `func.lower(...)`. Both detectors carry
   self-tests proving they fire on the original bug's shape and stay quiet on prose,
   docstrings and correctly-folded SQL.

Verified red-on-reintroduction: restoring the asymmetric shape
(`contains(nfc_normalize(lower(names.primary)), $needle)`) in `_DIVISIONS_QUERY` turns layer 3
red with the file, line and offending operand pair — **while all 35 tests in
`tests/test_resolve_area.py` stay green**, which is exactly why the class needed a guardrail
of its own.
