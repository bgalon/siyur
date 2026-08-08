# 0017 — Cross-engine string folding: one definition, two emitted arms — and the `commons/text.py` extraction it owes

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: Ben · Date: 2026-08-01 · accepted: 2026-08-07

## Context and Problem Statement

FAIL-005: `planner/nodes/resolve_area.py` folded the user's needle in **Python**
(`str.casefold()`) and the division's name in **DuckDB SQL** (`lower()`), then compared them. For
a Greek name with a precomposed polytonic vowel this returned **zero rows** — not an error, an
empty result that reads exactly like "there is no such place", at the first node of the pipeline,
on precisely the non-Latin input SC-005 (genericity) is the product claim about.

The first fix removed the *need* for the two folds to be equal (fold both operands in SQL, pass
the needle raw). Building the guardrail then found that this had bought silence for a different
silent drop: the SQL prefilter and the Python re-scorer still disagreed on final sigma, so a user
typing `Ρόδοσ` was dropped by the **gate** even though `_match_confidence` would have scored the
row `1.0`. A prefilter may over-select; it may never under-select.

So the question this ADR settles is not "which fold is correct" — both were individually correct
— but **how a string comparison that spans two engines is defined at all**, given that Python's
`str.casefold()` is Unicode *full* (1:N) case folding and DuckDB's `lower()` is *simple* (1:1),
and that a 1:N mapping is **not expressible** as a SQL `lower()` at any price. This is the
decision that binds every future engine boundary in the repo, which is why it is worth an ADR
rather than a diff.

**The measurement that decided it** (recorded in FAIL-005, over every codepoint in
`0x20–0x30000`):

| Python arm | vs DuckDB `lower(nfc_normalize(c))` | divergent codepoints |
|---|---|---|
| `NFC(casefold(c))` — full folding | | **274** |
| `NFC(str.lower(c))` — simple folding | | **1** (U+0130 İ) |

Full case folding is not something a database can be talked into. Simple lowercasing is something
both engines already agree on, to within one codepoint.

## Considered Options

**A — Keep two folds; remove the need for them to be equal.** Fold both operands inside SQL, pass
the needle raw (the original FAIL-005 fix). Cheap, and it fixed the reported bug. **Measured
cost:** the prefilter under-selects relative to the Python scorer (the final-sigma case), so the
same class of silent drop survives one layer down.

**B — Make DuckDB reproduce `casefold()`.** Keep Python as written and emit SQL that matches it.
**Not available:** 274 divergences, and the 1:N subset (`ß`→`ss`, `և`→`եւ`, the ligatures) cannot
be produced by `lower()` at all. Reconciling by hand is exactly the "assume an equality" mistake
in a more laborious costume.

**C — One definition, two emitted arms, built on *simple* lowercase (chosen).** Define the fold
once as three tables and generate both arms from them: `_normalize` (Python) and `_fold_sql`
(DuckDB expression), pipeline
`whitespace-collapse → NFC → simple lowercase → NFC → 1:N expansions → 1:1 variants`. The recall
that full folding used to give (`ς`≡`σ`, `ß`≡`ss`, `և`≡`եւ`) is bought back by shared expansion
and variant tables applied identically in both arms — i.e. it becomes an explicit **product
choice stated in one place** instead of an accident of which engine ran.

**D — Fold once in Python and stop pushing the prefilter down.** Trivially consistent — one
engine, one fold. **Rejected on cost:** it means reading the whole divisions theme per query. The
name path is *already* the slow path even with the prefilter (`docs/TRY-IT.md`: a hosted-parquet
divisions scan with no bbox pushdown can hang for minutes and froze a browser tab). D makes the
thing that is already too slow to ship strictly slower.

**E — Precompute a folded column at ingest.** The standard answer, and **not available here**:
the divisions theme is read live from hosted Overture parquet. We own no ingest step for it to
be a column of.

## Decision Outcome

Chosen: **C — one definition, two emitted arms, on simple lowercase.**

The driver is a property, not a preference: **the prefilter can never drop a row the Python
scorer would have accepted, because the prefilter and the scorer are the same function.** A and B
try to make two functions agree; C removes the second function. That is the strongest available
form of the guarantee, and it is checked exhaustively rather than sampled — the two arms are
asserted equal over **194,528 codepoints** (`0x20–0x30000` minus the 2,048 surrogates), because
an SC-005 failure is by definition in the script nobody put in the corpus.

Two supporting choices worth stating, since both are counter-intuitive:

- **Simple lowercase is the base precisely because it is the weaker function.** The base has to
  be something a database can reproduce; breadth is then added back in tables both engines read.
  Choosing the *more* capable Python primitive would have left 274 divergences open.
- **NFC runs on both ends.** Normalizing *first* is what makes the fold composition-invariant
  (`lower(U+0130)` and `lower('I'+U+0307)` differ, and normalizing afterwards cannot undo it);
  normalizing *again* recomposes sequences lowercasing itself produced (Η + U+0342 → η + U+0342 →
  U+1FC6).

### The extraction to `commons/text.py` — a consequence of this decision, not a follow-up chore

FAIL-005 and the implementing session both flagged it, and it is the reason this ADR is
load-bearing. **`commons/merge.py::normalize_name` is a second, independent Python fold**, and it
sits directly in front of the τ=0.6 name-similarity join.

**Correcting a natural misreading of that sentence:** the two folds are not the same function
implemented twice, and the extraction is therefore **not** "make merge call `_normalize`".
`normalize_name` is `NFKD → drop combining marks (Mn) → P/S/C/Z → space → NFKC → casefold` — it
**strips diacritics and punctuation**, which `resolve_area._normalize` deliberately does not do
(folding accents away would over-match division names, and `_normalize` is documented as
composition-insensitive, not diacritic-insensitive). Naively unifying them would silently change
τ scoring in `merge` or recall in `resolve_area`. So:

> The extraction is **one module owning a small named family of folds — each with the SQL twin it
> needs, and a rule that no fold is written outside it** — not one fold to rule them all.

**And it is cheaper than FAIL-005 estimates.** That entry cites "a new cross-package dependency
was out of scope" among its reasons for deferring; `planner/nodes/resolve_area.py` already
imports `commons.geo`, `commons.licenses`, `commons.models` and `commons.sources.*`. The
dependency exists. **File ownership, not architecture, was the real blocker.**

`normalize_name` is **safe today** — merge runs entirely in memory, so both sides of every
comparison are folded by the same Python function. It becomes this exact bug the moment name
matching moves into Postgres (a `lower()`/`unaccent` join, a `pg_trgm` index), and Postgres
`lower()` is **locale-dependent**, a wider divergence than DuckDB's. A third arm is not a copy of
the second.

### Consequences

- Good: the prefilter/scorer under-selection class is closed by construction, not by agreement;
  the two arms cannot be edited apart because one generates the other.
- Good: the recall decisions (`ς`≡`σ`, `ß`≡`ss`, `և`≡`եւ`) are now a product statement in one
  table rather than a side effect of `casefold`'s Unicode tables.
- Good: verified exhaustively (every codepoint), not on an 18-sample corpus — the sample would
  necessarily omit the script that breaks it.
- Bad / accepted cost: **the fold's single definition lives in `planner/nodes/resolve_area.py`,
  a node module** — the wrong home for a repo-wide primitive, and the reason the extraction is
  owed rather than optional. Until it moves, "only the helper folds" is a convention, not a
  location.
- Bad / accepted cost: the guardrail's AST tripwire scans `("commons", "planner", "api",
  "compiler")` — **`web/` is not scanned.** A JS `toLocaleLowerCase()` compared against a
  server-produced fold would be a **fourth** fold arriving unguarded. (Today `web/` only lowercases
  ASCII BCP-47 subtags in `sites.ts`, which never crosses an engine.)
- Accepted: the shared expansion/variant tables are a *deliberate* recall widening. `Ρόδοσ` and
  `Ρόδος` now match. That is wanted for place names; it is a choice, and a future consumer
  wanting exact identity must not reuse this fold for it.

### Confirmation

- **`tests/test_cross_engine_normalization.py::test_the_two_arms_agree_on_every_codepoint_not_merely_on_the_corpus`**
  — `_normalize` (Python) vs `_fold_sql` (real DuckDB) over the whole sweep; any divergence fails.
  This is the enforcement; it fails closed.
- **The layer-3 AST tripwire** in the same file — every folding SQL literal under the scanned
  packages is parsed and **any comparison whose two operands carry different normalization
  wrappers fails the build**, including inside the *assembled* `_DIVISIONS_QUERY` (a generated
  query must not be a way around a static literal scan). Proven to bite: reintroducing the
  asymmetric shape turns it red **while all 35 behavioural tests in `tests/test_resolve_area.py`
  stay green** — which is why the class needed a guardrail rather than a case.
- **`test_the_whitespace_table_covers_every_codepoint_python_calls_whitespace`** — the derived
  whitespace table cannot silently fall behind Python's own definition.
- **Owed, and not discharged by this ADR:** `commons/text.py` does not exist. Its landing change
  owes (a) both existing folds re-homed under distinct names with their intent documented, (b)
  the two-arm equality test re-pointed at the new home, (c) the AST tripwire's `SCANNED_PACKAGES`
  extended to `web/` if a JS fold has arrived by then.

### Revisit trigger — the second engine, not a date

This ADR's rule (*fold both operands in one engine, or derive both arms from one definition and
assert them equal exhaustively*) is not up for revisiting. What is triggered is **the extraction**,
and the trigger is the arrival of a third arm:

1. **The first Postgres-evaluated name predicate** — any `lower()`, `unaccent`, `pg_trgm` or
   `ILIKE` over a name in `commons/repository.py` or a migration. This is the likely one, and it
   is where `merge.normalize_name` turns from safe into FAIL-005.
2. **The first client-side comparison against a server-produced fold** in `web/`.
3. **A DuckDB or CPython upgrade that moves a divergence** — the exhaustive test will announce
   it; the response is to re-derive the tables, never to relax the assertion.

`commons/text.py` must land **in the same change as** whichever of 1 or 2 arrives first, not
after it. Adding a third fold and extracting later is the exact ordering that produced FAIL-005:
each fold correct on its own, the equality between them assumed rather than pinned.
