---
# Article VII front-matter. The article's field list is exactly four items —
# "front-matter (version, model, date, linked eval score)" (.specify/memory/constitution.md,
# Article VII). They appear below under those names; everything else is annotation.
prompt: research
version: 1
date: 2026-08-01 # when this version was pinned / last re-verified against the code
status: production # Article VII label-based promotion — moves independently of app deploys

# ── model ──────────────────────────────────────────────────────────────────
# Mirrors `commons.llm.ROUTING_TABLE`; it does not define it. On any divergence the
# routing table is right and this block is the bug (see §5, "Drift").
model:
  research:
    tier: research
    pin: null # THE RESEARCH NODE MAKES NO MODEL CALL — see §2. Nothing is routed.
  curate:
    tier: curate
    provider: anthropic
    model_id: claude-sonnet-5
    dated_snapshot: false
    undated_reason: >-
      Anthropic publishes no dated snapshot id for Sonnet 5; the short id is the
      complete published identifier and a date suffix 404s. Re-pin to the dated
      form as soon as one is published. Ratified as a constructor-enforced
      exception to Article VII by ADR-0013 — this is a recorded absence, not a
      fabricated date, and not a floating `-latest`-style alias.
    pinned_on: 2026-08-01
    source: claude-api skill model catalog (Sonnet 5)

# ── linked eval score ──────────────────────────────────────────────────────
linked_eval_score:
  score: null # no numeric score exists yet — see `history` below. Not zero; absent.
  gate: .github/workflows/eval-quality.yml → job 4 `deterministic-evals` (`uv run pytest evals/ -q`)
  gate_status: required, PR-gating (check 4 of the 1–7 merge set)
  evals: evals/
  unit_tests: tests/test_planner_curate.py, tests/test_planner_research.py
  history: >-
    evals/history.csv does not exist yet. `.github/workflows/eval-quality.yml`
    job 8 (`llm-judge-evals`) is a documented green stub: the pinned-judge
    harness that appends scores lands at DU-04. Until then this prompt's
    `score` is honestly null and its gate is the deterministic tier plus the
    curate unit tests named above.
---

# Prompt registry — `research` (slice 001)

*Covers the two nodes of the research stage in `specs/001-research-cited-sites/`:
`planner/nodes/research.py` (T033) and `planner/nodes/curate.py` (T034 / T059).
Constitution **Article VII** governs this file.*

## 1. What this stage is

```
resolve_area → research → curate → …
                 │          │
                 │          └─ merge → transliterate → RANK (Sonnet tier, one model call)
                 └─ deterministic fan-out over source adapters (NO model call)
```

**Exactly one model call exists in this stage**, and it is the ranking call in `curate`.

## 2. The `research` node has no prompt — deliberately

`specs/001-research-cited-sites/tasks.md` T033 labels the research node *"Haiku tier via the
seam"*. **The shipped implementation makes no model call at all**, and that is the honest,
load-bearing fact about this document: there is no research prompt to register, because there
is no research prompt.

Three reasons, none of them incidental:

1. **There is nothing to decide.** `research()` is a one-pass fan-out over every configured
   `SourceAdapter`, concatenating results in adapter order and emitting one `SourceReport` per
   source. No ranking, no extraction, no judgement — a `for` loop with an exception handler.
2. **Every value is already stamped at the source boundary (ADR-0009).** The adapter protocol
   applies `source + license + bundleable` once, at ingestion, so what reaches `research()` is
   already `SourcedValue`-complete. A model in this position could only *add* unstamped values,
   which FR-003 requires the system to refuse anyway.
3. **This is the one pipeline path that carries coordinates.** FR-005 — "the system MUST NOT let
   the model emit or compute coordinates" — and `planner/AGENTS.md`'s determinism discipline
   ("the LLM ranks/curates and writes prose; it **never** emits coordinates") both point the same
   way. Putting an LLM on the geodata path buys nothing and costs the invariant.

The consequence is that `research()` is fully deterministic and offline-testable
(`tests/test_planner_research.py` runs with no key and no network), and that FR-012's
degradation behaviour — a raising adapter becomes `found=0, degraded=True` with the exception as
the reason, while the remaining sources still run — is a code path, not a prompt instruction.

Seam coverage is **not** lost by this: `curate` routes through `commons.llm.ModelRouter` at the
Sonnet tier, so ADR-0004's seam is exercised by this slice.

> **Recorded as a decision in ADR-0014.** T033's "Haiku tier" label in `tasks.md` is now
> inaccurate and needs amending; this session did not edit `tasks.md` (a sibling session owns it).

## 3. The `curate` ranking prompt

### 3.1 Ownership — the code is authoritative

**The single source of truth is the `RANKING_SYSTEM_PROMPT` constant in
`planner/nodes/curate.py`.** That constant is what `curate()` passes to
`ModelRouter.complete(TaskTier.CURATE, system=...)`; it is the text that actually ships.

The block in §3.2 is an **illustrative mirror, not an authority**. If it and the constant ever
disagree, the constant is right and this file is the bug. Registering the text here as a second
authoritative copy would create exactly the silent-drift failure this registry exists to prevent.

*Article VII's end-state is a registry that owns the text and promotes it by label independently
of app deploys — i.e. the code eventually loads from `prompts/`, rather than this file mirroring
the code. That inversion is a real gap and is deliberately out of scope for v1: closing it means
editing `planner/`, and slice 001 is not the moment to add a prompt-loading indirection for one
prompt. See §5, "Open gap".*

### 3.2 The text (mirror of `planner.nodes.curate.RANKING_SYSTEM_PROMPT`)

```text
You rank candidate places for a day tour by traveller salience: how likely a visitor is to
want to see this place, judged only from its names.

You are given a JSON array of objects with exactly two keys: `id` and `names`.
Reply with a JSON array of the given `id` strings, most salient first, and NOTHING else — no
prose, no code fence, no objects, no scores, no coordinates, no new or corrected field values.
Include every id exactly once. Any element that is not one of the given id strings is discarded
and reported as a refusal.
```

### 3.3 Call shape

| | |
|---|---|
| Tier | `TaskTier.CURATE` → Sonnet 5 (see front-matter `model.curate`) |
| System turn | `RANKING_SYSTEM_PROMPT`, sent with `cache_prefix=True` |
| User turn | `json.dumps([{"id": …, "names": [...]}, …])` — **ids and names only** |
| Expected reply | a JSON array of the given id strings |
| Effort | `medium` (`EFFORT_BY_TIER`); adaptive thinking on (Sonnet 5 supports it) |
| Failure mode | any exception → refusal recorded, merge order kept (FR-012) |

**No coordinate, no field value and no license stamp ever crosses the seam** — `_ranking_request()`
serialises `id` and `names` and nothing else.

### 3.4 The prompt is a courtesy; the guarantee is mechanical

Per Constitution Article VI (rules become checks, not vigilance), every constraint the prompt
*asks* for is also *enforced* after the fact in `_apply_ranking()`:

| The prompt asks for | Enforced by | On violation |
|---|---|---|
| only the given id strings | membership check against `by_id` | refused, reason recorded |
| each id exactly once | `seen` set | refused, reason recorded |
| no objects / scores / coordinates | `isinstance(element, str)` | refused on sight (FR-003 / FR-005) |
| every id included | omitted ids appended in input order | nothing is lost from the commons |
| a JSON array | `_decode_ranking()` | whole reply refused, merge order kept |

So the worst a compromised or degraded model can do to this stage is **change the order** of
records it was given. It cannot add one, drop one, or alter a value. A prompt-injection payload
riding in a source-derived `names` value therefore cannot escalate past reordering.

With `router=None` the node is fully deterministic and no prompt is sent at all — the mode every
unit test and the trajectory eval run in.

## 4. Versioning and lifecycle

### 4.1 When `version` bumps

`version` is a plain integer, bumped **in the same PR** as the change it describes.

- **Bump** on any change to the shipped text of `RANKING_SYSTEM_PROMPT`, to the call shape
  (tier, cached prefix, effort, what the user turn serialises), or to the pinned model for a
  tier used here.
- **Do not bump** for prose, formatting, or link fixes in this file that do not describe a
  behaviour change — edit in place and leave `date` alone.
- **Re-verification** with no change: update `date` and `model.*.pinned_on`, leave `version`.

A version bump resets `linked_eval_score.score` to `null` until the gate below re-scores it. A
carried-over score from the previous version is a lie about the current one.

### 4.2 What gates a change

A prompt change is a code change and goes through a PR like one (`agent-ops.md` §4). Merge
requires the same CI checks 1–7 as any other PR, of which these actually exercise this prompt:

1. **Check 4 — `deterministic-evals`** (`eval-quality.yml`, PR-gating): `uv run pytest evals/ -q`.
2. **Check 2 — `unit`** (`ci.yml`): `tests/test_planner_curate.py` is the real contract test today. It
   asserts the model sees ids and names only, that an unknown id / duplicate / non-string element
   is refused, and — the FR-005 case —
   `test_a_model_asserting_a_value_or_a_coordinate_is_refused`.
3. **Job 8 — `llm-judge-evals`**: non-blocking on PRs, blocking on `main` and nightly. A green
   stub today; it becomes the quality gate for this prompt when the judge harness lands (DU-04).

Because the enforcement in §3.4 is mechanical, a prompt regression shows up as a rise in
`CurateResult.rejected`, not as a corrupted commons. That is the signal to watch.

### 4.3 Where history lives

- **Scores:** `evals/history.csv`, appended by CI — the location `AGENTS.md` (course-feed) and
  `docs/planning/prd.md` (course-feed artifact table) name. **It does not exist yet**; `eval-quality.yml` job 8 documents it as landing with
  the judge harness at DU-04. This file's `score` stays `null` until then rather than carrying an
  invented number.
- **Text and version:** git history of this file and of `planner/nodes/curate.py`.
- **Model pins:** `commons/llm.py` `ROUTING_TABLE` (each pin carries `pinned_on` + `source`);
  changes to a pin are ADR-worthy when they change a tier's model family.
- **Promotion:** `status` in the front-matter is the Article VII label. Slice 001 ships one
  version, so `production` and `v1` are the same thing today; a future `candidate` version is
  added as a sibling section (or file) and promoted by flipping the label in a PR.

### 4.4 Model migration

Article VII's playbook applies when a tier's pin changes: **offline trace-replay → shadow →
canary**, and strip scaffolding the newer model no longer needs. For this prompt the obvious
scaffolding candidate is the explicit "no prose, no code fence, no objects" enumeration in
§3.2 — written for a model that needed it. It is removed only when a replay shows the removal
does not raise `rejected`, never on vibes. Tracking is active → retired, with notice.

## 5. Open items

- **Open gap — Article VII inversion (§3.1).** The registry mirrors the code; the article's
  end-state is the code loading from the registry. Needs a follow-up task; not in slice 001.
- **Drift.** Nothing mechanically asserts that §3.2 still equals `RANKING_SYSTEM_PROMPT`. The
  cheap guard is a test that reads the fenced block out of this file and compares it to the
  constant — it belongs in `tests/`, which this session does not own. Until it exists, §3.1's
  "the constant wins" rule is the mitigation.
- **`evals/history.csv`** does not exist (§4.3); it lands at DU-04.
- **`tasks.md` T033's "Haiku tier via the seam" label** is inaccurate (§2) — ADR-0014.
