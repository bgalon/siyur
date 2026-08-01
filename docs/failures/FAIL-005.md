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

## Residual defects resolved — 2026-08-01

Both gaps above are closed in `planner/nodes/resolve_area.py`; **nothing is left open.**

The measurement that decided the design: over every codepoint in `0x20–0x30000`, Python's
`NFC(casefold(c))` and DuckDB's `lower(nfc_normalize(c))` disagree on **274** codepoints,
while Python's `NFC(str.lower(c))` and the same DuckDB expression disagree on **one**
(U+0130). Full case folding is not something a database can be talked into; *simple*
lowercasing is something both engines already agree on.

So the fold is now **defined once and emitted twice** — `_normalize` (Python) and
`_fold_sql` (DuckDB), both generated from the same three tables in that module:

```
whitespace-collapse → NFC → simple lowercase → NFC → 1:N expansions → 1:1 variants
```

- **Gap 2 (ordering)** — `nfc_normalize` now runs **before** `lower`, the ordering this
  entry recommended. It also runs again *after*, because lowercasing itself produces
  sequences that want recomposing (Η + U+0342 → η + U+0342 → U+1FC6); normalize-first alone
  would have broken the pre-existing `test_overture_divisions_folds_case_script_and_composition`.
- **Gap 1 (full vs simple folding)** — closed by making the prefilter and the scorer *the
  same function* rather than by reconciling two. `ς`/`σ`, `ß`/`ss`, `և`/`եւ` and the
  U+0130 forms are unified by shared tables applied identically in both arms, so the
  recall full folding used to give is now an explicit product choice in one place.

**Property optimised: the prefilter may over-select, never under-select.** A prefilter is a
gate — a row it drops is a row `_match_confidence` never scores, which is what made this
silent. The two arms being one definition is the strongest available form of that
guarantee, and it is checked exhaustively rather than sampled: `_fold_sql` and `_normalize`
are asserted equal on **every codepoint** (194,528 of them, ~1s), not on the 18-sample
corpus, because SC-005 failures are by definition in the script nobody listed.

The helper was **not** extracted to `commons/text.py` as recommended above: `commons/` is
another session's file and a new cross-package dependency was out of scope. The single
definition therefore lives inside `planner/nodes/resolve_area.py` for now, and
`commons/merge.py::normalize_name` remains a second, independent Python fold — still safe
(in-memory only) and still the next thing to bite when name matching moves into Postgres.
**That extraction is the remaining work, and it is ADR-bearing.**

Guardrail changes, so the record is honest about what was removed:

| Pin | Fate |
|---|---|
| `KNOWN_FULL_FOLD_EXPANSIONS` — final sigma, Armenian ew, İ, ß | all four **removed**; the table is now empty, kept so a future divergence still has to be stated with a reason |
| `KNOWN_COMPOSITION_SENSITIVE` — İ | **removed**; empty, same reason |
| `test_even_the_repos_python_fold_still_misses_…` | renamed to `test_the_prefilter_no_longer_drops_a_row_the_python_scorer_would_have_accepted` and inverted to pin the healed behaviour |
| `test_python_casefold_is_full_folding_and_duckdb_lower_is_simple` | **kept, unchanged in intent** — `casefold()` and `lower()` still differ, and that premise is what the new design is built on |

The oracle was not weakened to make this pass: no sample was removed, no comparison
loosened, and the corpus test was joined by a strictly stronger exhaustive one. Layer 3 now
also recognises `replace`/`translate`/`regexp_replace`/`trim` as folding functions and
descends into their first argument — a fold it did not recognise would have made the whole
tripwire vacuous — and it scans the *assembled* `_DIVISIONS_QUERY`, since a generated query
must not be a way around a static literal scan.

Verified red-before-fix: with the new tests in place and only `planner/nodes/resolve_area.py`
stashed back to `261a380`, 8 of the 9 new `tests/test_resolve_area.py` cases fail — five as
`the DuckDB prefilter dropped 'Δέλτασ Ward'` (and `'ΔΈΛΤΑΣ WARD'`, `'STRASSE Ward'`,
`'İota Ward'` decomposed, `'Iota Ward'`), three as `no attribute '_fold_sql'`.
