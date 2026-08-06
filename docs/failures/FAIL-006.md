# FAIL-006 — The prompt cache was switched on and cached nothing: a prefix under the provider minimum

- Date: 2026-08-01 · Severity: med
- Root-cause class: other (provider-precondition — a per-tier minimum that fails **silently**)

## Symptom

`planner/nodes/curate.py` asks for prompt caching **correctly**. It passes `cache_prefix=True`;
`commons/llm.py::_system_message` puts the `cache_control` breakpoint on the stable system turn
and nowhere near the per-request record list; the tier is `TaskTier.CURATE`, the one node that
crosses the seam. Every part of the mechanism is right.

It cached nothing, and would have gone on caching nothing for ever:

```
RANKING_SYSTEM_PROMPT           535 UTF-8 bytes  (~133 tokens)
claude-sonnet-5 minimum prefix  1024 tokens
                                ──────────────
                                short by ≥ 891 tokens

usage.cache_creation_input_tokens = 0     ← nothing was ever written
usage.cache_read_input_tokens     = 0     ← so nothing could ever be read
HTTP status                       = 200   ← no error, no warning, no field to check
```

**Anthropic caches nothing below the model's minimum cacheable prefix and raises no error.**
The request is valid, the response is normal, and the two usage counters that would tell you sit
at 0 — which is also what they read when the cache simply missed. The lever was off while
looking on: a cost regression with no exception, no log line and no failing call.

## Trajectory excerpt

Caught by an eval written **for this purpose, before any production traffic** — not by a bill,
not by a monitor, and not by a reviewer reading the diff. That ordering is the honest and
slightly uncomfortable part of this entry: the defect was found because a sibling session sat
down to write `evals/test_caching.py` and asked the question "is the prefix big enough?", which
nobody had asked while writing, reviewing or merging the caching code itself.

The eval landed **red on purpose**, marked `xfail(strict=True)` because fixing it meant editing
files that session did not own:

```python
@pytest.mark.xfail(
    strict=True,
    reason=(
        "LIVE DEFECT — planner/nodes/curate.py::RANKING_SYSTEM_PROMPT is 535 UTF-8 bytes "
        "(~133 tokens), far under Sonnet 5's 1024-token minimum cacheable prefix. …"
    ),
)
def test_the_cached_prefix_is_large_enough_to_be_cacheable() -> None: ...
```

`strict=True` is what made the hand-off safe: an XPASS reddens CI, so the moment the prompt grew
the marker had to be removed in the same PR — the eval could not rot into a permanently-yellow
"known issue". It is removed here.

There was one near-miss earlier: `commons/llm.py::_system_message` already carries a docstring
saying *"the breakpoint silently no-ops below the model's minimum cacheable prefix … callers keep
the system prefix realistically sized"*. The precondition was **written down at the seam and not
checked anywhere**, and the one caller did not meet it. A documented invariant with no check is
how this class of defect survives review.

## Root cause

**A provider precondition that is per-model, non-obvious, and fails silently rather than
erroring.** Three properties compound:

1. **It is a floor, not a scale.** Caching is not "less effective" below the minimum — it is
   entirely absent. There is no partial credit and no gradient to notice.
2. **It is silent by design.** A too-short prefix is not a malformed request, so there is
   nothing for the API to reject. The only evidence is a counter that stays 0, and 0 is
   indistinguishable from an ordinary miss.
3. **Nobody sizes a prompt in tokens while writing it.** The prompt was written to be *correct*
   — short, precise, enforced after the fact anyway (Article VI). Concision is a virtue
   everywhere else in this repo, and here it silently disabled the feature the next line of code
   asked for.

The deeper mistake is the same one FAIL-005 records in a different costume: **an external
precondition was assumed rather than pinned.** Nothing in the repo compared the prefix to the
minimum, so the gap was invisible until something measured it.

## Fix

`RANKING_SYSTEM_PROMPT` was rewritten — **535 → 5,755 UTF-8 bytes**, an upper bound of 5,755
tokens and realistically ~1,150–1,440, clearing the 1,024-token floor with headroom on any
plausible tokenization.

**The size is a consequence, not the goal.** Padding a prompt to satisfy a size check would be a
worse defect than the one it hides: it would cost tokens on every call for ever and dilute the
instructions that matter. The v1 prompt was genuinely thin — it named the output format and
nothing else — so v2 adds the material the ranking task actually needs and lets the size follow:

| Added | Why it earns its place |
|---|---|
| What "salient to a traveller" means, plus a six-band category ladder (landmarks → museums → settings → markets/quarters → hospitality → infrastructure) | v1 said "traveller salience" and left the model to invent the rubric. This is the ranking task; it was the one thing the prompt did not say. |
| Tie-breaking within a band | Removes deliberation over orderings that carry no information, and says outright that either order is correct. |
| How to read a generic vs. a category-bearing name | The observed failure mode of a names-only ranking: treating an uninformative name as a signal in one direction or the other. |
| Names in a source script | ADR-0010 / SC-005 territory. A ranking that quietly demotes what it cannot read is a genericity bug, invisible in an English-only eval. |
| Ids only — restated, with the reason | `_apply_ranking()` already refuses a value, a field or a coordinate (FR-003 / FR-005). Saying it in the prompt makes the model *comply* rather than be corrected — the enforcement stays the guarantee, but a refusal costs the reply its ordering, so compliance is worth buying. |
| Near-duplicates the merge left distinct | `merge_records` is deliberately conservative (ε=25 m ∧ τ=0.6). What survives is a real, recurring input shape the prompt said nothing about; "rank adjacently, keep both, never fold" is the answer the caller needs. |
| Two worked examples, one with a refused reply shape | Cheaper to demonstrate the contract than to describe it, and the negative example names the exact shape (`[{"id": …, "score": …}]`) the code rejects on sight. |

Mechanical consequences, all enforced by tests kept green in this PR:

- `prompts/research.md` §3.2 (the mirror `tests/test_prompt_drift.py` guards) updated; **`version`
  bumped 1 → 2** per Article VII. `linked_eval_score.score` was already `null`, so the reset the
  bump implies is a no-op here rather than a discarded number.
- New §3.5 in the registry records the floor, the per-tier table, and rule 1 above — so the next
  person to edit this prompt learns the constraint from the document rather than from a bill.
- The `xfail(strict=True)` marker is **removed**, with no assertion weakened.
- `evals/test_caching.py::test_the_cache_simulation_distinguishes_a_write_a_read_and_a_silent_no_op`
  used the shipped prompt as its *under-minimum* example. It now uses a deliberately small
  prefix, guarded by an `undersized_reason(...) is not None` assertion so the branch cannot decay
  into a second copy of the passing case.

The fix does **not** touch `commons/llm.py`, `_system_message`, or the breakpoint placement:
nothing there was wrong. It also does not add a runtime size check at the seam — see below.

## Blast radius

**Every future cached prefix in this repo**, because the bar is per-tier and moves:

| Tier | Pin | Minimum cacheable prefix |
|---|---|---|
| `plan` | `claude-opus-5` | **512** tokens |
| `curate` | `claude-sonnet-5` | **1,024** tokens |
| `research` | `claude-haiku-4-5` | **4,096** tokens |

Read from the `claude-api` skill's prompt-caching reference on 2026-08-01, never from model
memory — the same discipline `commons.llm.ROUTING_TABLE` applies to model ids.

Three consequences worth stating plainly:

1. **The minimum is not monotonic across generations.** Opus 5 (newest, most capable) has the
   *lowest* floor at 512; Haiku 4.5 has the highest at 4,096 — 8× more. Intuition about newer or
   larger models is no guide at all.
2. **A prompt cacheable at one tier is silently uncacheable at another.** Today's 5,755-byte
   ranking prompt clears `curate` and `plan` comfortably and would still be a coin-flip at a
   Haiku tier's 4,096-token floor. Moving a prompt between tiers is not a neutral edit.
3. **Re-pinning a model can move the bar underneath a prompt that was fine yesterday.** No code
   changes, no prompt changes, no error — the pin moves and caching stops. `MIN_CACHEABLE_PREFIX_TOKENS`
   in `evals/test_caching.py` **raises rather than defaults** on a model with no recorded minimum,
   so a re-pin fails the eval instead of silently asserting against a guess.

Sites audited on 2026-08-01. `curate` is the only caller passing `cache_prefix=True` today —
`research` makes no model call at all (ADR-0014) and no `plan`-tier caller exists yet — so this
was the only live instance, and the table above is where the next one appears.

## Deliberately not done here

- **A runtime size check in `_system_message`.** Tempting, and wrong at this stage: the seam has
  no tokenizer (an honest count needs `count_tokens` — a key and a network), so it could only
  check the byte lower bound, and it would have to decide between logging (ignorable) and raising
  (a caching hint that breaks the call). The eval is the right place: it runs on every PR, it can
  be rigorous about the bound it does assert, and it fails the build rather than a request. If a
  second `cache_prefix=True` caller appears, revisit — the guardrail should generalise to *all*
  cached prefixes rather than staying pinned to `RANKING_SYSTEM_PROMPT`.
- **Anything under `commons/`, `api/`, `web/`, `specs/`, or `planner/nodes/resolve_area.py`** —
  other sessions' files; nothing in them needed to change for this fix.

## Regression eval added

**Entry closed** — `evals/test_caching.py::test_the_cached_prefix_is_large_enough_to_be_cacheable`,
**eval tier**, merge-blocking via CI check 4 (`eval-quality.yml` job 4 `deterministic-evals`,
`uv run pytest evals/ -q`). It existed before this PR and was red-by-design; this PR is what turns
it green and strips its `xfail`.

It is a guardrail rather than a one-off assertion, on four counts:

1. **It names the tier, the pin, the minimum and the shortfall** in the failure message, instead
   of asserting `cache_read > 0` and leaving the reader to work out why the number is 0. The
   false-pass trap this avoids is real: an eval that only checked `cache_read > 0` against an
   undersized prefix fails permanently and gets "fixed" by deleting the assertion.
2. **Its sizing claim is a proof, not an estimate.** Byte-level BPE bottoms out at the 256 single
   bytes, so `tokens ≤ UTF-8 bytes` always, and `bytes < minimum` ⟹ `tokens < minimum`. No
   tokenizer, no key, no network. Clearing that bound is *necessary, not sufficient* — the
   message reports both the rigorous bound and the ~4 bytes/token working figure, and the fix
   targeted the latter (~1,150–1,440 tokens), not the former.
3. **It is proven to bite.** `test_the_size_check_bites` drives the sizing function with prefixes
   either side of the minimum, so a check that silently stopped refusing anything is itself
   caught.
4. **It survives a re-pin.** `minimum_prefix_tokens` raises `AssertionError` for a model family
   with no recorded minimum, and `test_every_routed_tier_has_a_recorded_minimum_prefix` asserts
   the table covers every tier in `ROUTING_TABLE`.

Verified red-on-reintroduction: restoring the v1 535-byte prompt turns
`test_the_cached_prefix_is_large_enough_to_be_cacheable` red with the model id, the 1,024-token
minimum, the 535-byte actual and the 489-token shortfall named in the message — while
`tests/test_planner_curate.py` stays entirely green, which is why this needed an eval of its own
rather than a unit test.
