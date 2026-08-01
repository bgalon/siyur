# FAIL-004 — Cross-source merge silently impossible: `und` never matched a tagged BCP-47 name key

- Date: 2026-08-01 · Severity: high
- Root-cause class: integration (two independently-correct modules, wrong together)

## Symptom

Running the Overture and OSM adapters together for the first time, the Rhodes fixture's **deliberately planted cross-source anchor did not merge**. The same real place — the Gate of the Arsenal — seen by both sources, with a byte-identical Greek name, 3.44 m apart:

```
overture: {'und': 'Πύλη Ταρσανά'}
osm:      {'el': 'Πύλη Ταρσανά', 'en': 'Gate of the Arsenal'}
distance_m: 3.44                (well inside ε=25 m)
name_similarity: 0.00       ->  NOT MERGED
```

Every module's own test suite was green. `commons/merge.py` had 54 passing tests including ε/τ boundary cases; both adapters had passing per-source tests. Nothing was red.

This is DU-03's entire value proposition ("one site enriched from several sources") failing silently — and it would have failed *quietly in production*, producing a commons full of near-duplicate records rather than an error.

## Trajectory excerpt

Discovered while verifying PR #31/#32 (source adapters) against `main`, which already carried `commons/merge.py`. Driving both adapters over the committed fixtures and calling `merge_records` on the anchor pair:

```
decide_match: matched=False rule=None
reason='distance 3.4 m ≤ ε but the name signal is missing (0.00 < τ=0.6) — distance alone never merges'
```

The merge module was behaving exactly as specified. The specification was incomplete.

## Root cause

`commons/merge.py`'s τ gate implemented "same language, post-transliteration" as **exact BCP-47 key equality**, comparing only keys present on *both* records.

Sources that publish a display name **without declaring its language** — Overture `names.primary`, a bare OSM `name` tag — are keyed **`und`** ("undetermined") by the adapters, which is correct: guessing a language would be inventing provenance. But `und` and `el` are not equal, so the two records shared no comparable key and scored 0.00.

Neither module was wrong in isolation. The adapters were right not to guess a language; the merge rule was right to require same-language comparison. The defect lived in the seam between them, and only appeared when real data from both flowed through the same call — which no unit test did.

**Why the file-ownership boundary mattered:** the adapter session predicted this from the key mismatch and **flagged it rather than editing `commons/merge.py`**, which another session owned. Had it "helpfully" relaxed the merge rule to make its own fixture pass, the fix would have landed unreviewed and possibly weakened ε/τ.

## Fix

`commons/merge.py::name_similarity_by_language` — an `und` value on one side is now compared against **every** key on the other side, with the score filed under the *determined* tag (the informative one), so `decide_match` still reports a meaningful `language` for audit.

This does **not** weaken the join rule:

- ε **and** τ must still both hold. This changes which names are *comparable*, not the thresholds.
- `und` is the *absence* of a language claim, so treating it as comparable contradicts nothing. Matching two **differently-tagged** languages (`el` ↔ `fr`) remains forbidden.

Verified against real fixture data after the fix:

```
decide_match: matched=True rule=spatial_name sim=1.0 lang=el
merge_records -> 1 record, both source refs retained
full corpus:  225 raw -> 223 merged
```

## Regression eval added

Two tests in `tests/test_merge.py`, both passing and merge-blocking via CI job 2:

- **`test_und_name_matches_a_tagged_name_in_another_language`** — reproduces the exact anchor (`und` vs `el`, 3.4 m apart), asserts `best_name_similarity` returns `("el", 1.0)` filed under the determined tag, and asserts no source ref is lost through the merge.
- **`test_und_still_requires_the_name_signal_distance_alone_never_merges`** — the guard against over-correcting: an `und` name that genuinely *disagrees* with the tagged name at 3.4 m must **not** merge. Pins that making `und` comparable did not turn the rule into a distance-only join.

`scripts/try_it.py` also exercises the anchor end-to-end over real fixtures, so the behaviour is visible without reading tests.

## Standing lesson

Per-module green is not integration green. Two correct modules can be wrong together, and the failure mode is silence rather than an exception. **When two independently-built modules first meet, run them against each other on real data before trusting either one's test suite** — the fixture's planted cross-source anchor existed precisely so this check had something to catch, and it earned its keep.
